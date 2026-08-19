"""Celery task'ları.

* Faz 1 — `run_upload_profiling`: upload doğrulama ve profilleme (ADR §5 A).
* Faz 2 — `run_analysis`: analiz job'ı (ADR §5 B).
* Faz 4 — `run_retention_sweep`: periyodik retention süpürücüsü (ADR §9).

İş mantığı asenkron fonksiyonlarda tutulur; Celery task'ları yalnızca ince
sarmalayıcılardır. Bunun iki sebebi var:

* Test edilebilirlik — testler task'ı doğrudan `await` edebilir, Celery
  broker'ı veya `task_always_eager` gerekmez.
* Hata yönetimi — profil işi başarısız olduğunda kullanıcıya HTTP hatası
  dönemeyiz (istek çoktan 202 ile kapandı). Hata, sözleşmeye uygun bir
  ProblemDetails olarak `uploads.error` sütununa YAZILIR ve frontend'in
  polling'i onu oradan okur.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from itertools import islice
from pathlib import Path
from typing import Any, cast

from celery.exceptions import SoftTimeLimitExceeded
from pydantic import ValidationError
from sqlalchemy import CursorResult, Executable, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.errors import ErrorCode, build_problem
from app.core.logging import get_logger
from app.domain.model_catalog import is_allowed_model
from app.models.analysis import Analysis
from app.models.upload import Upload
from app.pipeline.aggregate import AggregationError, aggregate
from app.pipeline.classifier import RecordClassifier
from app.pipeline.cost import cost_for_tokens, estimate_cost
from app.pipeline.llm_classifier import (
    ClassificationCancelledError,
    CostLimitExceededError,
    OpenRouterClassifier,
    ProgressCallback,
)
from app.pipeline.preprocess import Preprocessor, PreprocessResult
from app.prompts.faq_analysis import UnknownPromptVersionError, get_prompt
from app.schemas.analysis import STAGE_PROGRESS, TERMINAL_STATUSES, AnalysisStatus, RowFilter
from app.schemas.report import AnalysisWarning, TokenUsage
from app.schemas.upload import UploadProfile, UploadStatus
from app.services import retention, secret_store, storage
from app.services.openrouter import OpenRouterClient, OpenRouterError
from app.services.xlsx import (
    SheetOrColumnNotFoundError,
    XlsxRejectedError,
    iter_column_values,
    validate_and_profile,
    validate_xlsx,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _validation_locations(exc: BaseException) -> list[str] | None:
    """`ValidationError`'ın `loc` yollarını döndürür; başka istisnada `None`.

    YALNIZCA `loc` ve hata `type`'ı alınır: `msg` ve özellikle `input`
    kullanıcı verisi (hücre içeriği, kolon adı) taşıyabilir ve ADR §9 bunun
    loglara girmesini yasaklıyor. `loc` ise şema yolundan gelir — "sheets.0"
    gibi — ve dosyadan hiçbir şey içermez.
    """
    if not isinstance(exc, ValidationError):
        return None
    return [
        f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
        for error in exc.errors(include_url=False, include_input=False)
    ]


async def run_upload_profiling(upload_id: uuid.UUID) -> str:
    """Bir upload'ı doğrular, profilini çıkarır ve sonucu veritabanına yazar.

    Dönen değer son durumdur ("ready" veya "failed"). İstisna FIRLATMAZ:
    kullanıcıya görünen hata, kayıttaki `error` sütunudur.
    """
    settings = get_settings()

    async with session_scope() as session:
        upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
        if upload is None:
            # Kullanıcı iş kuyrukta beklerken DELETE çağırmış olabilir.
            logger.info("profile_upload_missing", extra={"upload_id": str(upload_id)})
            return "missing"

        if upload.status in {UploadStatus.READY, UploadStatus.FAILED}:
            # acks_late + yeniden dağıtım yüzünden aynı iş iki kez gelebilir.
            logger.info("profile_upload_already_settled", extra={"upload_id": str(upload_id)})
            return str(upload.status.value)

        upload.status = UploadStatus.VALIDATING
        storage_key = upload.storage_key
        await session.commit()

    problem_payload: dict[str, object] | None = None
    profile_payload: dict[str, object] | None = None

    # Geçici dizin `with` bloğuyla yönetiliyor: hata yolunda da 130 MB'lık
    # dosya diskte kalmasın (ADR §9 retention).
    with tempfile.TemporaryDirectory(prefix="auzef-upload-") as tmpdir:
        local_path = Path(tmpdir) / "source.xlsx"
        try:
            storage.download_to_path(storage_key, local_path, settings)
            raw_profile = validate_and_profile(local_path, settings)
            # Şemadan geçiriyoruz: veritabanına sözleşmeye uymayan bir gövde
            # yazmaktansa burada patlamak yeğdir.
            profile_payload = UploadProfile.model_validate(raw_profile).model_dump(mode="json")
        except XlsxRejectedError as exc:
            logger.warning(
                "upload_rejected",
                extra={"upload_id": str(upload_id), "reason": exc.reason},
            )
            problem_payload = build_problem(
                "UPLOAD_CORRUPT_OR_ENCRYPTED",
                "Dosya okunamadı. Bozuk, şifrelenmiş veya makro içeren dosyalar desteklenmez.",
            ).to_payload()
        except Exception as exc:
            # ADR §9: hata cevabına dosya içeriği veya iz sızmaz. İstisna
            # yalnızca loglanır, kullanıcıya genel bir mesaj gider.
            #
            # Log kaydı istisna TİPİNİ ve varsa şema doğrulama KONUMLARINI da
            # taşır. Sebebi ölçülmüş: yük testinde bu satır tek başına
            # `{"event": "upload_profiling_failed", "exc_info": true}`
            # üretiyordu ve kök nedeni (bir `ValidationError`) bulmak için
            # hatayı yerelde yeniden üretmek gerekti. Traceback loglanamaz
            # (`core/logging.py`: istisna mesajı sır taşıyabilir), ama tip ve
            # `loc` yolu kullanıcı verisi içermez ve teşhis için yeterli.
            logger.exception(
                "upload_profiling_failed",
                extra={
                    "upload_id": str(upload_id),
                    "exception_type": type(exc).__name__,
                    "validation_locations": _validation_locations(exc),
                },
            )
            problem_payload = build_problem(
                "INTERNAL_ERROR",
                "Dosya işlenirken beklenmeyen bir hata oluştu.",
            ).to_payload()

    async with session_scope() as session:
        upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
        if upload is None:
            return "missing"

        if problem_payload is not None:
            upload.status = UploadStatus.FAILED
            upload.error = problem_payload
            upload.profile = None
        else:
            upload.status = UploadStatus.READY
            upload.profile = profile_payload
            upload.error = None

        await session.commit()
        final_status = str(upload.status.value)

    logger.info(
        "upload_profiling_finished",
        extra={"upload_id": str(upload_id), "status": final_status},
    )
    return final_status


@celery_app.task(name="app.workers.tasks.profile_upload")  # type: ignore[untyped-decorator]
def profile_upload(upload_id: str) -> str:
    """Celery giriş noktası.

    `asyncio.run` gerekçesi `app/core/db.py` modül başlığında açıklanmıştır:
    plan bağımlılık listesinde senkron bir Postgres sürücüsü yok.
    """
    return asyncio.run(run_upload_profiling(uuid.UUID(upload_id)))


# =====================================================================
# Faz 2 — analiz job'ı
# =====================================================================
#
# Durum makinesi (ADR §6):
#   queued → validating → preprocessing → analyzing → aggregating → completed
#   terminal: failed, cancelled
#
# İKİ TASARIM KARARI burada yaşıyor:
#
# 1. İPTAL AŞAMA SINIRINDA kontrol edilir. Durum yazımlarının HEPSİ koşullu
#    bir UPDATE'tir: `cancel_requested = false` VE durum terminal değil.
#    Etkilenen satır sayısı 0 ise iş iptal edilmiş demektir ve worker geri
#    döner. Böylece DELETE'in yazdığı `cancelled`, worker'ın bir sonraki
#    yazımıyla EZİLEMEZ. Bayrak yerine yalnızca duruma bakmak yetmezdi:
#    worker aşamanın ortasındayken DELETE gelirse, aşama bitiminde yazılan
#    durum iptali sessizce geri alırdı.
#
# 2. PROGRESS HER SATIRDA YAZILMAZ (ADR §2). Yalnızca aşama değişiminde ve
#    aşama içinde `analysis_progress_write_threshold` puanlık anlamlı bir
#    sıçrama olduğunda yazılır. Her satırda yazmak 100.000 satırlık bir
#    dosyada 100.000 UPDATE demekti.


class _AnalysisCancelledError(Exception):
    """İş aşama/parti sınırında iptal edilmiş olarak bulundu."""


#: Ön işlemede bir seferde okunacak satır sayısı. Hem iptal kontrolünün hem
#: de ilerleme yazımının granülaritesi budur. Çok küçük olursa thread'e geçiş
#: maliyeti artar, çok büyük olursa iptal geç fark edilir.
PREPROCESS_BATCH_ROWS = 2_000


def build_classifier(
    *,
    api_key: str,
    model: str,
    prompt_version: str,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
    max_cost_usd: float | None = None,
) -> RecordClassifier:
    """Sınıflandırıcıyı kuran TEK değişim noktası (Faz 2 → Faz 3).

    Faz 2'de `DeterministicClassifier` dönüyordu; Faz 3'te OpenRouter
    uygulaması dönüyor. `run_analysis`'in geri kalanı ve
    `pipeline/aggregate.py` DEĞİŞMEDİ — Faz 2'de ampirik olarak doğrulanan
    toplama matematiği aynen çalışıyor.

    `DeterministicClassifier` SİLİNMEDİ: testlerin ve toplama matematiğinin
    LLM'siz koşabilmesi hâlâ değerli.
    """
    client = OpenRouterClient(api_key=api_key, model=model, settings=settings)
    return OpenRouterClassifier(
        client=client,
        prompt=get_prompt(prompt_version),
        model=model,
        settings=settings,
        on_progress=on_progress,
        max_cost_usd=max_cost_usd,
    )


def _close_classifier(classifier: RecordClassifier) -> None:
    """Sınıflandırıcının HTTP bağlantılarını kapatır (varsa).

    Protokolde `close` YOK — `DeterministicClassifier`'ın kapatacak bir şeyi
    yok ve protokole zorunlu bir metot eklemek Faz 2'de doğrulanmış sınıfı
    değiştirmeyi gerektirirdi.
    """
    closer = getattr(classifier, "close", None)
    if callable(closer):
        closer()


def _actual_cost(usage: TokenUsage, model_id: str) -> float:
    """GERÇEKTEN harcanan tahmini tutar — sağlayıcının token sayacından.

    `pipeline/cost.py`'deki `estimate_cost` ile karıştırılmamalı: o, çağrılar
    BAŞLAMADAN ÖNCE tavan kontrolü için bir tahmin üretir. Bu ise iş bitince,
    sağlayıcının bildirdiği gerçek token sayısından hesaplanır. İkisini aynı
    alana yazmak raporu yalancı yapardı.

    AYRIMI TAŞIYAN ŞEY TOKEN SAYISI, FİYAT ARİTMETİĞİ DEĞİL: çarpma ve
    yuvarlama `cost_for_tokens`'ta tek yerde duruyor (Faz 4). Faz 3'te aynı
    hesap burada bir kez daha yazılıydı ve iki kopyanın ayrışması, tavan
    kontrolü ile raporlanan tutarı sessizce birbirinden koparabilirdi.
    """
    return cost_for_tokens(usage.prompt_tokens, usage.completion_tokens, model_id)


def _chunk_progress_callback(
    analysis_id: uuid.UUID,
    loop: asyncio.AbstractEventLoop,
) -> ProgressCallback:
    """Map aşamasında chunk başına ilerleme yazan ve iptali kontrol eden geri çağrı.

    NEDEN `run_coroutine_threadsafe`: sınıflandırıcı SENKRON ve
    `asyncio.to_thread` içinde koşuyor (protokolü async yapmak Faz 2'de
    doğrulanmış `DeterministicClassifier`'ı da değiştirmeyi gerektirirdi).
    İlerleme yazımı ise async DB oturumu istiyor. Worker thread'inden ana
    event loop'a güvenli geçişin yolu bu.

    Bu olmadan `analyzing` aşaması dakikalarca donmuş tek bir blok olurdu:
    ne ADR §2'nin "anlamlı ilerleme" kuralı işlerdi, ne de iptal aşama
    ortasında fark edilirdi.
    """
    start = STAGE_PROGRESS[AnalysisStatus.ANALYZING]
    end = STAGE_PROGRESS[AnalysisStatus.AGGREGATING]

    def callback(done: int, total: int) -> bool:
        fraction = done / total if total > 0 else 1.0
        progress = round(start + (end - start) * min(1.0, fraction), 1)

        async def write() -> bool:
            async with session_scope() as session:
                return await _write_progress(session, analysis_id, progress)

        future = asyncio.run_coroutine_threadsafe(write(), loop)
        try:
            return future.result()
        except Exception:
            # İlerleme yazamamak işi öldürmemeli; iptal kontrolü bir sonraki
            # aşama sınırında yine yapılacak.
            logger.exception(
                "analysis_progress_write_failed",
                extra={"analysis_id": str(analysis_id)},
            )
            return True

    return callback


async def _guarded_update(session: AsyncSession, statement: Executable) -> int:
    """Koşullu UPDATE çalıştırır ve etkilenen satır sayısını döndürür."""
    result = cast("CursorResult[Any]", await session.execute(statement))
    await session.commit()
    return int(result.rowcount)


def _live_analysis_filter(analysis_id: uuid.UUID) -> tuple[Any, ...]:
    """İptal edilmemiş ve sonlanmamış kaydı seçen WHERE koşulları."""
    return (
        Analysis.id == analysis_id,
        Analysis.cancel_requested.is_(False),
        Analysis.status.not_in(TERMINAL_STATUSES),
    )


async def _advance_stage(
    session: AsyncSession,
    analysis_id: uuid.UUID,
    stage: AnalysisStatus,
) -> bool:
    """Aşamayı ilerletir. İş iptal edilmiş/sonlanmışsa `False` döner."""
    affected = await _guarded_update(
        session,
        update(Analysis)
        .where(*_live_analysis_filter(analysis_id))
        .values(status=stage, progress=STAGE_PROGRESS[stage], updated_at=func.now()),
    )
    if affected != 1:
        logger.info(
            "analysis_stage_skipped_cancelled_or_settled",
            extra={"analysis_id": str(analysis_id), "stage": stage.value},
        )
    return affected == 1


async def _write_progress(
    session: AsyncSession,
    analysis_id: uuid.UUID,
    progress: float,
) -> bool:
    """Aşama içi ilerleme yazar. İptal edilmişse `False`."""
    affected = await _guarded_update(
        session,
        update(Analysis)
        .where(*_live_analysis_filter(analysis_id))
        .values(progress=progress, updated_at=func.now()),
    )
    return affected == 1


async def _fail(
    analysis_id: uuid.UUID,
    code: ErrorCode | str,
    detail: str,
    *,
    retry_after: float | None = None,
) -> str:
    """İşi sözleşmeye uygun bir hata gövdesiyle sonlandırır.

    İptal edilmiş bir işi `failed` YAPMAZ: kullanıcı iptal ettiyse gördüğü
    şey "iptal edildi" olmalı, "başarısız" değil.

    `retry_after` yalnızca `PROVIDER_RATE_LIMITED` için doludur (ADR §7).
    `ProblemDetails` serileştirmesi `None` olduğunda alanı gövdeden tamamen
    çıkarıyor — frontend bu alanı `.optional()` ilan ediyor, `.nullable()`
    değil, yani `null` göndermek doğrulamayı patlatırdı.
    """
    resolved = ErrorCode(code)
    problem = build_problem(resolved, detail, retry_after=retry_after).to_payload()
    async with session_scope() as session:
        affected = await _guarded_update(
            session,
            update(Analysis)
            .where(*_live_analysis_filter(analysis_id))
            .values(
                status=AnalysisStatus.FAILED,
                error=problem,
                report=None,
                progress=100.0,
                updated_at=func.now(),
            ),
        )
    if affected != 1:
        return AnalysisStatus.CANCELLED.value
    logger.info(
        "analysis_failed",
        extra={"analysis_id": str(analysis_id), "code": resolved},
    )
    return AnalysisStatus.FAILED.value


async def _preprocess_in_batches(
    *,
    analysis_id: uuid.UUID,
    local_path: Path,
    sheet_name: str,
    text_column: str,
    row_filters: list[RowFilter],
    expected_rows: int,
    settings: Settings,
) -> PreprocessResult:
    """Kolonu parti parti okuyup ön işlemeden geçirir.

    ADR §2'nin iki kuralı burada uygulanıyor:

    * İlerleme HER SATIRDA YAZILMAZ. Yalnızca
      `analysis_progress_write_threshold` puanlık anlamlı bir değişimde bir
      UPDATE atılır: 100.000 satırlık bir dosyada 100.000 yerine en fazla
      birkaç yazım.
    * İptal parti sınırında kontrol edilir; kullanıcı iptal ettiğinde worker
      dosyanın sonunu beklemez.

    openpyxl senkron ve CPU yoğun olduğu için hem okuma hem işleme thread'e
    taşınıyor; aksi hâlde event loop dakikalarca bloklanırdı.
    """
    preprocessor = Preprocessor(settings)
    filter_map = {
        row_filter.column: frozenset(row_filter.allowed_values) for row_filter in row_filters
    }
    values = iter_column_values(local_path, sheet_name, text_column, filter_map)

    start = STAGE_PROGRESS[AnalysisStatus.PREPROCESSING]
    end = STAGE_PROGRESS[AnalysisStatus.ANALYZING]
    last_written = start

    while True:
        batch = await asyncio.to_thread(lambda: list(islice(values, PREPROCESS_BATCH_ROWS)))
        if not batch:
            break

        await asyncio.to_thread(preprocessor.consume, batch)

        if expected_rows > 0:
            fraction = min(1.0, preprocessor.rows_seen / expected_rows)
            progress = start + (end - start) * fraction
            if progress - last_written < settings.analysis_progress_write_threshold:
                continue
            last_written = progress
        else:
            # Payda bilinmiyor (profil yok): ilerleme aşama sınırında kalır,
            # yalnızca iptal kontrolü yapılır.
            progress = start

        async with session_scope() as session:
            if not await _write_progress(session, analysis_id, round(progress, 1)):
                raise _AnalysisCancelledError

    return preprocessor.finish()


async def run_analysis(analysis_id: uuid.UUID) -> str:
    """Bir analizi baştan sona yürütür ve son durumu döndürür.

    İstisna FIRLATMAZ: kullanıcıya görünen hata, kayıttaki `error` sütunudur
    (istek çoktan 202 ile kapandı, HTTP hatası dönemeyiz).

    Dönen değerler: `completed`, `failed`, `cancelled`, `missing`.
    """
    settings = get_settings()
    try:
        return await _run_analysis_inner(analysis_id, settings)
    except SoftTimeLimitExceeded:
        # ADR §2: hard timeout 45 dk. Celery soft limiti (varsayılan 2640s)
        # hard limitten önce gelir ki iş sözleşmeye uygun bir hata ile
        # kapanabilsin — `core/config.py` bunu zaten böyle tarif ediyor.
        #
        # NEDEN AYRI HANDLER: `SoftTimeLimitExceeded` bir `Exception` alt
        # sınıfı, yani aşamaları saran geniş handler'lar onu zaten yakalıyordu
        # ve iş asılı KALMIYORDU. Sorun yazılan koddu: sınıflandırmada
        # `PROVIDER_BAD_RESPONSE`, ön işlemede `INTERNAL_ERROR`. Kullanıcı 45
        # dakika bekledikten sonra "Dil modelinden geçerli bir yanıt alınamadı"
        # ya da "Beklenmeyen bir hata oluştu" görüyordu; oysa yapılandırılmış
        # bir zaman aşımı ne beklenmeyen ne de bozuk bir yanıttır.
        #
        # Aşamalardaki geniş handler'lar bu istisnayı artık yeniden fırlatıyor
        # ve karar tek yerde, burada veriliyor. Böylece aşama sınırları gibi
        # try kapsamı DIŞINDA kalan noktalarda tetiklenen bir timeout da
        # terminal duruma yazılıyor.
        #
        # `_fail` bu `finally`'den ÖNCE koşmak zorunda: DB oturumu gerekiyor
        # ve hard limit SIGKILL'ine 60 saniye var.
        logger.warning("analysis_soft_timeout", extra={"analysis_id": str(analysis_id)})
        return await _fail(
            analysis_id,
            "PROVIDER_TIMEOUT",
            "Analiz izin verilen süre içinde tamamlanamadı.",
        )
    finally:
        # ADR §9: iş bitince BAŞARI/HATA/İPTAL fark etmeksizin anahtar silinir.
        # `finally` bilinçli: beklenmeyen bir istisnada bile anahtar kalmamalı.
        await asyncio.to_thread(secret_store.delete_key, analysis_id, settings)
        # ADR §9: "Ham upload ve Parquet ara dosya işlem sonunda silinir."
        # Aynı `finally` gerekçesi: hata veya iptal yolunda 130 MB'lık
        # öğrenci verisi storage'da kalmamalı. Ara dosyalar zaten
        # `tempfile.TemporaryDirectory` ile yönetiliyor.
        if not await _source_must_survive(analysis_id):
            await _delete_source_object(analysis_id, settings)


#: Kaynak dosyanın İŞ SONUNDA SİLİNMEDİĞİ hata kodları.
#:
#: Ortak noktaları: LLM'e hiç gidilmedi, hiçbir şey tüketilmedi ve kullanıcıya
#: gösterilen çözüm "aynı upload üzerinde seçimini düzelt" — maliyet sınırını
#: yükselt, daha ucuz model seç, başka sayfa/kolon seç. Configure ekranı
#: `/yuklemeler/{uploadId}` kalıcı bir URL olduğu için bu tamamen doğal bir
#: kullanıcı hareketi.
#:
#: Kaynağı silmek o öneriyi yalana çeviriyordu: ikinci analiz `JOB_NOT_FOUND`
#: ile düşüyor ve kullanıcı, hiçbir maliyete yol açmamış bir ayar hatası
#: yüzünden 130 MB'lık dosyayı yeniden yüklemek zorunda kalıyordu.
#:
#: ADR §9 İHLALİ DEĞİL: "işlem sonunda silinir" cümlesinin amacı veri
#: minimizasyonu ve dosya burada da süresiz kalmıyor — upload satırının kendi
#: `upload_retention_hours` penceresi (varsayılan 24 saat) ve bucket
#: lifecycle'ı onu yine topluyor. Değişen şey, silmenin işin sonuna değil
#: upload'ın kendi penceresine bağlanması.
#:
#: Bu kümeye SAĞLAYICI hataları GİRMEZ: orada token harcandı ve frontend'in
#: "Tekrar dene" düğmesi `/`'a (yeni upload) götürüyor, aynı upload'a değil.
_SOURCE_PRESERVING_CODES = frozenset(
    {
        "COST_LIMIT_EXCEEDED",
        # Model artık whitelist'te değil, prompt sürümü tanımsız, sayfa/kolon
        # bulunamadı — üçü de bu kodu kullanıyor ve üçünün de çözümü aynı
        # upload üzerinde seçimi değiştirmek.
        "SHEET_OR_COLUMN_NOT_FOUND",
    }
)


async def _source_must_survive(analysis_id: uuid.UUID) -> bool:
    """Kaynak dosya bu işin sonunda korunmalı mı?

    Karar kayda YAZILMIŞ hata kodundan okunuyor; ayrı bir bayrak ya da
    kontrol akışı eklemeye gerek yok. İptal yolunda `_fail`'in koşullu
    UPDATE'i tutmadığı için `error` boş kalır — yani iptal edilen iş bu
    testten `False` ile geçer ve dosyası silinmeye devam eder.

    ŞÜPHEDE KALIRSA SİLER: sorgu patlarsa `False` dönülür. Veri
    minimizasyonu tarafında hata yapmak, öğrenci verisini storage'da
    bırakmaktan iyidir.
    """
    try:
        async with session_scope() as session:
            error = await session.scalar(select(Analysis.error).where(Analysis.id == analysis_id))
    except Exception:
        logger.exception(
            "analysis_source_policy_lookup_failed", extra={"analysis_id": str(analysis_id)}
        )
        return False

    if not isinstance(error, dict):
        return False
    return error.get("code") in _SOURCE_PRESERVING_CODES


async def _delete_source_object(analysis_id: uuid.UUID, settings: Settings) -> None:
    """Analizin kaynak dosyasını object storage'dan siler.

    İstisna YUTULUR: buradaki bir arıza işin sonucunu değiştirmemeli —
    kullanıcının raporu hazırsa hazırdır. Silinemeyen nesneyi retention
    süpürücüsü ve bucket lifecycle kuralı yine toplar.

    Upload SATIRI silinmez, yalnızca nesne. Satır profili taşıyor ve
    frontend rapor ekranındayken hâlâ `GET /uploads/{id}` çağırabilir;
    satırı burada silmek 404'e yol açardı.
    """
    try:
        async with session_scope() as session:
            storage_key = await session.scalar(
                select(Upload.storage_key)
                .join(Analysis, Analysis.upload_id == Upload.id)
                .where(Analysis.id == analysis_id)
            )
        if storage_key is None:
            return
        await asyncio.to_thread(storage.delete_object, storage_key, settings)
        logger.info("analysis_source_deleted", extra={"analysis_id": str(analysis_id)})
    except Exception:
        logger.exception("analysis_source_delete_failed", extra={"analysis_id": str(analysis_id)})


async def _run_analysis_inner(analysis_id: uuid.UUID, settings: Settings) -> str:
    # ---------------------------------------------------- kaydı yükle
    async with session_scope() as session:
        analysis = await session.scalar(select(Analysis).where(Analysis.id == analysis_id))
        if analysis is None:
            # Kullanıcı iş kuyrukta beklerken upload'ı silmiş olabilir
            # (analiz kaydı CASCADE ile gider).
            logger.info("analysis_missing", extra={"analysis_id": str(analysis_id)})
            return "missing"

        if analysis.status in TERMINAL_STATUSES:
            # acks_late + yeniden dağıtım aynı işi iki kez gönderebilir.
            logger.info("analysis_already_settled", extra={"analysis_id": str(analysis_id)})
            return str(analysis.status.value)

        upload = await session.scalar(select(Upload).where(Upload.id == analysis.upload_id))

        sheet_name = analysis.sheet_name
        text_column = analysis.text_column
        row_filters = [RowFilter.model_validate(item) for item in analysis.row_filters]
        model = analysis.model
        prompt_version = analysis.prompt_version
        top_n = analysis.top_n
        max_cost_usd = analysis.max_cost_usd
        storage_key = upload.storage_key if upload else None
        filename = upload.filename if upload else ""
        upload_profile = upload.profile if upload else None

    if storage_key is None:
        return await _fail(analysis_id, "JOB_NOT_FOUND", "Analiz edilecek yükleme bulunamadı.")

    warnings: list[AnalysisWarning] = []
    #: İlerleme yüzdesinin paydası. Profilden gelir; profil yoksa 0 kalır ve
    #: aşama içi ilerleme yazılmaz (aşama sınırları yine yazılır).
    expected_rows = 0
    if upload_profile is not None:
        profile = UploadProfile.model_validate(upload_profile)
        selected = next((s for s in profile.sheets if s.name == sheet_name), None)
        if selected is not None:
            expected_rows = selected.row_count
        # `exceeds_row_limit` burada UYARIYA DÖNÜŞMÜYOR. Eskiden
        # `ROW_LIMIT_TRUNCATED` üretiliyordu ama mesajı bile "analiz tüm
        # satırlar üzerinde çalıştı" diyordu: kod adı ile içeriği çelişiyordu
        # ve raporda kesme olduğunu ima ediyordu. Kesme yok; bayrak artık
        # yalnızca yükleme ekranında "bu dosya büyük" bilgisi.

    # Geçici dizin `with` ile yönetiliyor: hata yolunda da kaynak dosya
    # diskte kalmasın (ADR §9 retention).
    with tempfile.TemporaryDirectory(prefix="auzef-analysis-") as tmpdir:
        local_path = Path(tmpdir) / "source.xlsx"

        # ------------------------------------------------------ validating
        async with session_scope() as session:
            if not await _advance_stage(session, analysis_id, AnalysisStatus.VALIDATING):
                return AnalysisStatus.CANCELLED.value

        try:
            await asyncio.to_thread(storage.download_to_path, storage_key, local_path, settings)
            # Dosya Faz 1'de doğrulandı ama ARADA DEĞİŞMİŞ olabilir. Güvenlik
            # kontrolünü atlamak, doğrulanmamış bir zip'i açmak demek (ADR §9).
            await asyncio.to_thread(validate_xlsx, local_path, settings)
        except storage.StorageObjectMissingError:
            # BEKLENEN yol, arıza değil: ADR §9 ham dosyayı iş bitiminde
            # siliyor, dolayısıyla aynı upload üzerinde ikinci kez analiz
            # başlatan kullanıcı buraya düşer. `INTERNAL_ERROR` demek
            # ("beklenmeyen bir hata") kullanıcıyı yanıltır ve gereksiz bir
            # traceback üretirdi; olan biten, kaynağın artık saklanmaması.
            logger.info("analysis_source_gone", extra={"analysis_id": str(analysis_id)})
            return await _fail(
                analysis_id,
                "JOB_NOT_FOUND",
                "Kaynak dosya saklama süresi dolduğu için silinmiş. Dosyayı yeniden yükleyin.",
            )
        except XlsxRejectedError as exc:
            logger.warning(
                "analysis_source_rejected",
                extra={"analysis_id": str(analysis_id), "reason": exc.reason},
            )
            return await _fail(
                analysis_id, "UPLOAD_CORRUPT_OR_ENCRYPTED", "Kaynak dosya okunamadı."
            )
        except SoftTimeLimitExceeded:
            # Zaman aşımı bu aşamanın hatası değil; kararı `run_analysis`
            # veriyor (gerekçe orada). Geniş handler'dan önce gelmek ZORUNDA:
            # `Exception` alt sınıfı olduğu için aksi hâlde yutulur.
            raise
        except Exception as exc:
            # ADR §9: hata cevabına dosya içeriği veya iz sızmaz.
            logger.exception(
                "analysis_download_failed",
                extra={"analysis_id": str(analysis_id), "exception_type": type(exc).__name__},
            )
            return await _fail(analysis_id, "INTERNAL_ERROR", "Kaynak dosya alınamadı.")

        # --------------------------------------------------- preprocessing
        async with session_scope() as session:
            if not await _advance_stage(session, analysis_id, AnalysisStatus.PREPROCESSING):
                return AnalysisStatus.CANCELLED.value

        try:
            preprocess_result = await _preprocess_in_batches(
                analysis_id=analysis_id,
                local_path=local_path,
                sheet_name=sheet_name,
                text_column=text_column,
                row_filters=row_filters,
                expected_rows=expected_rows,
                settings=settings,
            )
        except _AnalysisCancelledError:
            return AnalysisStatus.CANCELLED.value
        except SheetOrColumnNotFoundError as exc:
            logger.warning(
                "analysis_selection_not_found",
                extra={"analysis_id": str(analysis_id), "reason": exc.reason},
            )
            return await _fail(
                analysis_id,
                "SHEET_OR_COLUMN_NOT_FOUND",
                "Seçilen sayfa veya kolon dosyada bulunamadı.",
            )
        except XlsxRejectedError:
            return await _fail(
                analysis_id, "UPLOAD_CORRUPT_OR_ENCRYPTED", "Kaynak dosya okunamadı."
            )
        except SoftTimeLimitExceeded:
            raise  # bkz. `run_analysis`
        except Exception as exc:
            logger.exception(
                "analysis_preprocess_failed",
                extra={
                    "analysis_id": str(analysis_id),
                    "exception_type": type(exc).__name__,
                    "validation_locations": _validation_locations(exc),
                },
            )
            return await _fail(analysis_id, "INTERNAL_ERROR", "Veri hazırlanırken hata oluştu.")

    # Log satırında MESAJ İÇERİĞİ YOK; yalnızca sayaçlar (ADR §9).
    logger.info(
        "analysis_preprocessed",
        extra={
            "analysis_id": str(analysis_id),
            "total_rows": preprocess_result.total_rows,
            "analyzed": preprocess_result.analyzed_count,
            "unique": preprocess_result.unique_count,
            "redacted": preprocess_result.redacted_count,
        },
    )

    # ---- modeli BURADA da doğrula (ADR §9 değişmez 4) ----
    # `POST /analyses` bunu zaten kontrol etti, ama bu KOPYA BİR KONTROL
    # DEĞİL: iş kuyrukta beklerken whitelist değişip deploy edilebilir ve
    # `estimate_cost` whitelist dışı bir model için fiyat bulamayınca 0.0
    # döndürür — yani maliyet tavanı SESSİZCE devre dışı kalır ve iş
    # fiyatı bilinmeyen bir modele gider. Güvenlik kontrolünün hemen
    # altındaki bu tuzağı kapatıyoruz.
    if not is_allowed_model(model):
        logger.warning(
            "analysis_model_not_allowed",
            extra={"analysis_id": str(analysis_id), "model": model},
        )
        return await _fail(
            analysis_id,
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Seçilen model artık kullanılabilir modeller listesinde değil.",
        )

    # ---- prompt'u BURADA çöz ----
    # Maliyet tahmini prompt'un boyutunu bilmek zorunda: map system prompt'u
    # her chunk'ta yeniden gönderiliyor ve tahminin en büyük eksik kalemi
    # oydu. Sınıflandırıcı kurulumu (aşağıda) prompt'u yine çözecek; burada
    # erken çözmek, tanımsız bir sürümün maliyet hesabından ÖNCE fark
    # edilmesini de sağlıyor — sırası değişen tek şey bu ve doğrusu da bu:
    # tanımsız prompt sürümü, maliyetten bağımsız bir istek hatası.
    try:
        prompt_bundle = get_prompt(prompt_version)
    except UnknownPromptVersionError:
        logger.warning(
            "analysis_unknown_prompt_version",
            extra={"analysis_id": str(analysis_id), "prompt_version": prompt_version},
        )
        return await _fail(
            analysis_id,
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Seçilen prompt sürümü tanımlı değil.",
        )

    # ---- maliyet tavanı: LLM ÇAĞRILARI BAŞLAMADAN (ADR §9) ----
    decision = estimate_cost(
        preprocess_result.groups,
        model,
        max_cost_usd,
        settings=settings,
        prompt=prompt_bundle,
    )
    logger.info(
        "analysis_cost_estimated",
        extra={
            "analysis_id": str(analysis_id),
            "estimated_cost_usd": decision.estimated_cost_usd,
            "max_cost_usd": decision.max_cost_usd,
        },
    )
    if decision.exceeds:
        # ADR §9: "Token ve tahmini maliyet üst sınırı aşılırsa LLM çağrısı
        # başlamadan iş güvenli biçimde durur." Faz 2'de burada yalnızca bir
        # uyarı yazılıyordu çünkü ortada harcanacak para yoktu; Faz 3'te
        # gerçek çağrı var, dolayısıyla iş DURUYOR.
        #
        # BURANIN YERİ ÖNEMLİ: `_advance_stage(ANALYZING)`'den ve
        # `build_classifier()`'dan ÖNCE. Tek bir OpenRouter çağrısı bile
        # yapılmadan dönülüyor — "başlamadan durur" bunu gerektiriyor.
        #
        # HATA KODU — SÖZLEŞME EKLEMESİ (bkz. core/errors.py). ADR §7'nin on
        # bir kodunda maliyet tavanı için ayrılmış bir kod yoktu. Önce
        # `JOB_CONFLICT` denendi ve YANLIŞ çıktı: frontend kullanıcı metnini
        # KOD BAŞINA tutuyor ve backend'in `detail` alanını bilinçli olarak
        # göstermiyor (progress-screen'de bunu sabitleyen bir test var), yani
        # kullanıcı maliyet sınırına çarptığında "Bu işlem için zaten devam
        # eden bir analiz var" görürdü. Ayrı anlam → ayrı kod.
        logger.info(
            "analysis_cost_ceiling_exceeded",
            extra={
                "analysis_id": str(analysis_id),
                "estimated_cost_usd": decision.estimated_cost_usd,
                "max_cost_usd": decision.max_cost_usd,
            },
        )
        return await _fail(
            analysis_id,
            "COST_LIMIT_EXCEEDED",
            f"Tahmini maliyet ({decision.estimated_cost_usd:.4f} USD) belirlediğiniz "
            f"{decision.max_cost_usd} USD sınırının üzerinde. Analiz başlatılmadı. "
            "Maliyet sınırını yükseltebilir veya daha ucuz bir model seçebilirsiniz.",
        )

    # -------------------------------------------------------- analyzing
    async with session_scope() as session:
        if not await _advance_stage(session, analysis_id, AnalysisStatus.ANALYZING):
            return AnalysisStatus.CANCELLED.value

    # ---- anahtarı şifreli Redis kaydından oku (ADR §10 risk 7) ----
    # Task payload'ında düz anahtar YOK; ihtiyaç duyulan tek an burası.
    api_key = await asyncio.to_thread(secret_store.load_key, analysis_id, settings)
    if not api_key:
        # TTL dolmuş (kuyrukta 50 dk beklemiş iş) veya kayıt silinmiş.
        # Bu GERÇEK bir yol ve traceback'e dönüşmemeli.
        logger.warning("analysis_key_unavailable", extra={"analysis_id": str(analysis_id)})
        return await _fail(
            analysis_id,
            "PROVIDER_AUTH_FAILED",
            "OpenRouter anahtarının süresi doldu. Analizi yeniden başlatın.",
        )

    try:
        classifier = build_classifier(
            api_key=api_key,
            model=model,
            prompt_version=prompt_version,
            settings=settings,
            on_progress=_chunk_progress_callback(analysis_id, asyncio.get_running_loop()),
            # ADR §9 tavanı koşu sırasında da uygulanıyor (B5b): uçuş öncesi
            # kontrol bir TAHMİNE dayanıyor ve onarım gerektiren bir chunk
            # gerçek tüketimi tahminin üstüne çıkarabiliyor. Fark doğrudan
            # kullanıcının kendi anahtarından çıktığı için tavan harcandıkça
            # da bakılıyor.
            max_cost_usd=max_cost_usd,
        )
    except UnknownPromptVersionError:
        logger.warning(
            "analysis_unknown_prompt_version",
            extra={"analysis_id": str(analysis_id), "prompt_version": prompt_version},
        )
        return await _fail(
            analysis_id,
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Seçilen prompt sürümü tanımlı değil.",
        )
    except Exception as exc:
        # Sınıflandırıcı KURULAMADI. Buradan bir istisnanın kaçmasına izin
        # verilemez: `run_analysis`'te yalnızca `finally` var, dolayısıyla
        # istisna Celery'ye kadar gider ve DB satırı sonsuza kadar
        # `analyzing` durumunda kalırdı — kullanıcı ilerlemeyen bir çubuğu
        # poll etmeye devam ederdi. Bu fonksiyonun sözleşmesi "istisna
        # FIRLATMAZ".
        logger.exception(
            "analysis_classifier_build_failed",
            extra={"analysis_id": str(analysis_id), "exception_type": type(exc).__name__},
        )
        return await _fail(analysis_id, "INTERNAL_ERROR", "Analiz başlatılamadı.")
    finally:
        # Bu çerçevedeki referansı bırakıyoruz. SINIRLI BİR ÖNLEM: anahtar
        # istemcinin `Authorization` başlığında yaşamaya devam eder, yani
        # bu satır anahtarı bellekten SİLMEZ. Yaptığı tek şey, bu
        # fonksiyondan kaynaklanan bir traceback'in yerel değişkenler
        # arasında anahtarı taşımasını engellemek.
        del api_key

    try:
        classification = await asyncio.to_thread(classifier.classify, preprocess_result.groups)
    except ClassificationCancelledError:
        return AnalysisStatus.CANCELLED.value
    except CostLimitExceededError as exc:
        # Harcanmış parayı geri alamayız; kalan chunk'lar gönderilmedi.
        # `detail` gerçek tutarı taşıyor (tahmini değil) — kullanıcıya
        # gösterilen metin frontend'de, bu kayıt destek/inceleme için.
        logger.warning(
            "analysis_cost_limit_reached",
            extra={
                "analysis_id": str(analysis_id),
                "spent_usd": exc.spent_usd,
                "max_cost_usd": exc.max_cost_usd,
            },
        )
        return await _fail(
            analysis_id,
            "COST_LIMIT_EXCEEDED",
            f"Analiz {exc.spent_usd:.4f} USD harcandığında {exc.max_cost_usd} USD "
            "sınırına ulaştığı için durduruldu.",
        )
    except OpenRouterError as exc:
        # Sağlayıcı hataları ZATEN sözleşmedeki koda çevrilmiş durumda;
        # `exc.detail` ham yanıt veya anahtar taşımıyor (services/openrouter.py).
        logger.warning(
            "analysis_provider_failed",
            extra={"analysis_id": str(analysis_id), "code": exc.code},
        )
        return await _fail(analysis_id, exc.code, exc.detail, retry_after=exc.retry_after)
    except SoftTimeLimitExceeded:
        raise  # bkz. `run_analysis`
    except Exception as exc:
        logger.exception(
            "analysis_classify_failed",
            extra={
                "analysis_id": str(analysis_id),
                "exception_type": type(exc).__name__,
                "validation_locations": _validation_locations(exc),
            },
        )
        return await _fail(analysis_id, "PROVIDER_BAD_RESPONSE", "Sınıflandırma tamamlanamadı.")
    finally:
        await asyncio.to_thread(_close_classifier, classifier)

    # Gerçek token tüketimi sağlayıcının `usage` bloğundan geliyor; modelin
    # metninden DEĞİL (ADR §4). Faz 2'de burada 0 raporlanıyordu.
    usage = getattr(classifier, "usage", None)
    token_usage = (
        TokenUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )
        if usage is not None
        else TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    )
    spent_usd = _actual_cost(token_usage, model)

    # ------------------------------------------------------- aggregating
    async with session_scope() as session:
        if not await _advance_stage(session, analysis_id, AnalysisStatus.AGGREGATING):
            return AnalysisStatus.CANCELLED.value

    try:
        report = aggregate(
            analysis_id=analysis_id,
            preprocess_result=preprocess_result,
            classification=classification,
            filename=filename,
            sheet_name=sheet_name,
            text_column=text_column,
            row_filters=row_filters,
            model=model,
            prompt_version=prompt_version,
            classifier_id=classifier.identifier,
            top_n=top_n,
            settings=settings,
            extra_warnings=warnings,
            token_usage=token_usage,
            estimated_cost_usd=spent_usd,
        )
    except AggregationError as exc:
        # Değişmez ihlali: sınıflandırıcı aynı kaydı iki kez eşlemiş olabilir.
        # Yanlış sayı göstermektense işi başarısız saymak doğrudur (ADR §4).
        logger.exception(
            "analysis_aggregation_invalid",
            extra={"analysis_id": str(analysis_id), "exception_type": type(exc).__name__},
        )
        return await _fail(analysis_id, "PROVIDER_BAD_RESPONSE", "Sonuç doğrulanamadı.")
    except SoftTimeLimitExceeded:
        raise  # bkz. `run_analysis`
    except Exception as exc:
        # `run_upload_profiling` ile AYNI gerekçe: traceback loglanamıyor
        # (`core/logging.py`), o yüzden tip ve şema konumları ayrı alanlara
        # yazılır. Burada eksikti ve bedeli ölçüldü: rapor değişmezini ihlal
        # eden bir `ValidationError` çıktıya yalnızca `"exc_info": true`
        # olarak düştü, kök nedeni bulmak için boru hattını worker dışında
        # yeniden koşturmak gerekti.
        logger.exception(
            "analysis_aggregate_failed",
            extra={
                "analysis_id": str(analysis_id),
                "exception_type": type(exc).__name__,
                "validation_locations": _validation_locations(exc),
            },
        )
        return await _fail(analysis_id, "INTERNAL_ERROR", "Sonuç üretilirken hata oluştu.")

    # --------------------------------------------------------- completed
    payload = report.model_dump(mode="json")
    async with session_scope() as session:
        affected = await _guarded_update(
            session,
            update(Analysis)
            .where(*_live_analysis_filter(analysis_id))
            .values(
                status=AnalysisStatus.COMPLETED,
                progress=STAGE_PROGRESS[AnalysisStatus.COMPLETED],
                report=payload,
                error=None,
                updated_at=func.now(),
            ),
        )
    if affected != 1:
        return AnalysisStatus.CANCELLED.value

    logger.info(
        "analysis_completed",
        extra={
            "analysis_id": str(analysis_id),
            "questions": len(report.top_questions),
            "themes": len(report.themes),
        },
    )
    return AnalysisStatus.COMPLETED.value


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.run_analysis",
    soft_time_limit=get_settings().analysis_soft_timeout_seconds,
    time_limit=get_settings().analysis_hard_timeout_seconds,
)
def run_analysis_task(analysis_id: str) -> str:
    """Celery giriş noktası.

    Payload'da YALNIZCA kimlik var (ADR §10 risk 7): OpenRouter anahtarı
    kuyruğa düz metin olarak GİRMEZ; worker gerektiğinde şifreli Redis
    kaydından okur.
    """
    return asyncio.run(run_analysis(uuid.UUID(analysis_id)))


# =====================================================================
# Faz 4 — retention süpürücüsü
# =====================================================================


async def run_retention_sweep() -> dict[str, int]:
    """Süresi dolmuş rapor, upload ve kaçak nesneleri temizler (ADR §9).

    İstisna FIRLATMAZ ve fırlatmamalı: beat her saat bunu tetikliyor,
    tek bir arızalı koşunun kuyruğa traceback yığması ve retry döngüsüne
    girmesi işe yaramaz. Sayaçlar loglanır.
    """
    settings = get_settings()
    try:
        async with session_scope() as session:
            result = await retention.sweep(session, settings)
    except Exception:
        logger.exception("retention_sweep_failed")
        return {"expired_analyses": 0, "expired_uploads": 0, "orphan_objects": 0}

    return {
        "expired_analyses": result.expired_analyses,
        "expired_uploads": result.expired_uploads,
        "orphan_objects": result.orphan_objects,
    }


@celery_app.task(name="app.workers.tasks.sweep_retention")  # type: ignore[untyped-decorator]
def sweep_retention() -> dict[str, int]:
    """Celery beat giriş noktası (zamanlama `workers/celery_app.py`'de)."""
    return asyncio.run(run_retention_sweep())
