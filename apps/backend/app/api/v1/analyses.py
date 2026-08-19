"""`/api/v1/analyses` — plan §4.

Üç şeyi aynı anda doğru yapmak zorunda:

1. **Anahtar hiçbir zaman kalıcı olmamalı.** `X-OpenRouter-Key` gövdeye değil
   header'a konur (ADR §6), buradan doğrudan `secret_store`'a gider ve
   PostgreSQL'e YAZILMAZ. Bu modülde anahtarın değeri hiçbir log satırına,
   hiçbir istisna metnine ve hiçbir DB alanına girmez.
2. **İptal aşama sınırında çalışmalı.** DELETE hem `cancel_requested`
   bayrağını hem de durumu `cancelled` yapar. Yalnız bayrağı set etmek,
   worker bir sonraki aşamaya gelene kadar arayüze "hâlâ çalışıyor"
   göstermek demekti; yalnız durumu yazmak ise worker'ın bir sonraki aşama
   yazımıyla iptali EZMESİNE izin verirdi. İkisi birlikte gerekli
   (worker tarafındaki koruma: `workers/tasks.py` → `_advance_stage`).
3. **Rapor yalnızca tamamlanmış işte dönmeli.** `/result` ve `/export`
   devam eden işte 409 döner — mock'un davranışı da budur. İkisi aynı
   kapıyı (`_completed_report`) kullanır; ayrı yazılsalardı biri
   gevşediğinde sözleşme sessizce ikiye ayrılırdı.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.v1.deps import (
    IdempotencyKey,
    claim_idempotency,
    release_idempotency,
    remember_idempotency,
    replayed_response,
)
from app.api.v1.responses import (
    ANALYSIS_CANCEL,
    ANALYSIS_CREATE,
    ANALYSIS_EXPORT,
    ANALYSIS_READ,
    ANALYSIS_RESULT,
)
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.domain.model_catalog import is_allowed_model
from app.models.analysis import Analysis
from app.models.upload import Upload
from app.pipeline.cost import estimate_profile_cost
from app.schemas.analysis import (
    TERMINAL_STATUSES,
    AnalysisCreate,
    AnalysisCreated,
    AnalysisJobRead,
    AnalysisStatus,
    ExportFormat,
)
from app.schemas.common import ProblemDetails
from app.schemas.report import AnalysisReport
from app.schemas.upload import ColumnProfile, UploadProfile, UploadStatus
from app.services import idempotency, report_export, secret_store

logger = get_logger(__name__)

router = APIRouter(prefix="/analyses", tags=["analyses"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Header adı sözleşmede sabit (ADR §6). `alias` ile birebir yazılıyor;
#: FastAPI'nin otomatik snake_case dönüşümüne bırakılırsa `x_openrouter_key`
#: beklenir ve frontend'in gönderdiği başlık eşleşmez.
OpenRouterKeyDep = Annotated[str | None, Header(alias="X-OpenRouter-Key")]


def _validate_selection(profile_payload: dict[str, object], body: AnalysisCreate) -> ColumnProfile:
    """Seçilen sayfa ve kolonun profilde GERÇEKTEN bulunduğunu doğrular.

    Bu kontrol olmadan hata ancak worker dosyayı açtığında ortaya çıkar ve
    kullanıcı 40 saniye bekledikten sonra "kolon yok" görür. Profil zaten
    elimizde; şimdi bakmak bedava.
    """
    profile = UploadProfile.model_validate(profile_payload)

    sheet = next((s for s in profile.sheets if s.name == body.sheet_name), None)
    if sheet is None:
        raise ApiError(
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Seçilen sayfa dosyada bulunamadı.",
        )

    column = next((column for column in sheet.columns if column.name == body.text_column), None)
    if column is None:
        raise ApiError(
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Seçilen kolon bu sayfada bulunamadı.",
        )

    available_columns = {profile_column.name for profile_column in sheet.columns}
    missing_filter_columns = [
        row_filter.column
        for row_filter in body.row_filters
        if row_filter.column not in available_columns
    ]
    if missing_filter_columns:
        raise ApiError(
            "SHEET_OR_COLUMN_NOT_FOUND",
            "Satır filtresinde seçilen kolon bu sayfada bulunamadı.",
        )
    return column


async def _get_analysis_or_404(session: AsyncSession, analysis_id: uuid.UUID) -> Analysis:
    analysis = await session.scalar(select(Analysis).where(Analysis.id == analysis_id))
    if analysis is None:
        raise ApiError("JOB_NOT_FOUND", "Bu kimlikle bir analiz bulunamadı.")
    return analysis


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisCreated,
    responses=ANALYSIS_CREATE,
    summary="Analizi başlat ve job'ı kuyruğa al",
)
async def create_analysis(
    request: Request,
    body: AnalysisCreate,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey,
    x_openrouter_key: OpenRouterKeyDep = None,
) -> AnalysisCreated | Response:
    # ---- anahtar: gövdede DEĞİL, header'da (ADR §6/§9) ----
    api_key = (x_openrouter_key or "").strip()
    if not api_key:
        raise ApiError(
            "PROVIDER_AUTH_FAILED",
            "X-OpenRouter-Key header'ı zorunludur.",
        )

    # ---- Idempotency (ADR-0002 #3) ----
    # Anahtar kontrolünden SONRA (kimliksiz istek her hâlükârda reddedilir),
    # doğrulamaların ve job oluşturmanın ÖNCESİNDE. Replay bu noktadan
    # döndüğü için `secret_store.store_key`'e hiç ulaşılmaz: istekle gelen
    # YENİ anahtar orijinal job'ınkini ezmez — sözleşmenin "replay yeni
    # X-OpenRouter-Key'i yok sayar" cümlesi mekanik olarak bu.
    #
    # Fingerprint DOĞRULANMIŞ gövdeden türüyor ve header'ları içermiyor.
    claimed: idempotency.Claim | None = None
    if idempotency_key is not None:
        fingerprint = idempotency.analysis_fingerprint(body)
        outcome = await claim_idempotency(request, idempotency_key, fingerprint, settings)
        if isinstance(outcome, idempotency.StoredResponse):
            logger.info("analysis_idempotent_replay")
            return replayed_response(outcome)
        claimed = outcome

    try:
        # ADR §9 değişmezi 4: model yalnızca backend whitelist'inden seçilebilir.
        if not is_allowed_model(body.model):
            raise ApiError(
                "SHEET_OR_COLUMN_NOT_FOUND",
                "Seçilen model kullanılabilir modeller listesinde yok.",
            )

        upload = await session.scalar(select(Upload).where(Upload.id == body.upload_id))
        if upload is None:
            raise ApiError("JOB_NOT_FOUND", "Analiz edilecek yükleme bulunamadı.")

        if upload.status is not UploadStatus.READY or upload.profile is None:
            raise ApiError(
                "JOB_CONFLICT",
                "Yükleme henüz analiz edilebilir durumda değil.",
            )

        column = _validate_selection(upload.profile, body)

        # ADR-0002 #10: pahalı olduğu profilden belli olan istek job ve Redis
        # secret oluşturmadan senkron reddedilir. Worker gerçek hücreleri
        # işledikten sonra dedupe-aware tahmini ve koşu içi gerçek tüketimi de
        # ayrıca denetler; bu ilk kapı kullanıcıya anında geri bildirim verir.
        #
        # KIRPMA YOK: burada eskiden `min(column.non_empty_count, MAX_ROWS)`
        # yazıyordu, yani 100.000 satırın üstündeki her dosya SANKİ 100.000
        # satırmış gibi fiyatlanıyordu. Analiz hiçbir zaman kırpmadığı için
        # (bkz. `schemas/report.py` değişmezi) bu kapı büyük dosyalarda
        # sistematik olarak DÜŞÜK tahmin veriyordu — üstelik tahmin zaten
        # ayrı bir sebepten düşük (`pipeline/cost.py`
        # `OUTPUT_TOKENS_PER_RECORD`, ölçülen sapma 5x). İki hata üst üste
        # binince tavan kapısı büyük dosyalarda pratikte hiç kapanmıyordu.
        # Profil filtre kombinasyonunun kaç satır tuttuğunu bilmez. Filtreli
        # isteği filtresiz `non_empty_count` ile reddetmek, tam da maliyeti
        # azaltan özelliği kullanılamaz hale getirirdi. Bu durumda senkron
        # kapı atlanır; worker filtreyi uygulayıp dedupe ettikten sonra daha
        # kesin tahmini, İLK model çağrısından önce zaten zorlar.
        if not body.row_filters:
            estimated_cost_usd = estimate_profile_cost(
                column.non_empty_count,
                column.avg_length,
                body.model,
            )
            if estimated_cost_usd > body.max_cost_usd:
                raise ApiError(
                    "COST_LIMIT_EXCEEDED",
                    f"Tahmini maliyet ({estimated_cost_usd:.4f} USD) belirlediğiniz "
                    f"{body.max_cost_usd} USD sınırının üzerinde. Maliyet sınırını "
                    "yükseltin ya da daha ucuz bir model seçin.",
                )

        analysis_id = uuid.uuid4()
        analysis = Analysis(
            id=analysis_id,
            upload_id=upload.id,
            status=AnalysisStatus.QUEUED,
            progress=0.0,
            cancel_requested=False,
            sheet_name=body.sheet_name,
            text_column=body.text_column,
            row_filters=[row_filter.model_dump(mode="json") for row_filter in body.row_filters],
            model=body.model,
            prompt_version=body.prompt_version,
            top_n=body.top_n,
            max_cost_usd=body.max_cost_usd,
        )
        session.add(analysis)
        await session.commit()

        # ---- anahtarı şifreli ve TTL'li olarak Redis'e yaz ----
        # DB commit'inden SONRA: kayıt yoksa saklanacak bir anahtar da yoktur.
        # `redis-py` senkron; event loop'u bloklamamak için thread'e taşınıyor.
        try:
            ttl = await run_in_threadpool(secret_store.store_key, analysis_id, api_key, settings)
        except Exception:
            # Anahtar saklanamadıysa iş başlatılamaz. Kaydı geri alıyoruz ki
            # kullanıcı asla ilerlemeyecek bir job'ı poll etmesin.
            logger.exception("openrouter_key_store_failed", extra={"analysis_id": str(analysis_id)})
            await session.delete(analysis)
            await session.commit()
            raise ApiError(
                "INTERNAL_ERROR",
                "Analiz başlatılamadı.",
            ) from None

        # Import burada: `workers.tasks` Celery uygulamasını yükler ve API'nin
        # import zincirinde döngü oluşturmasını istemiyoruz.
        from app.workers.tasks import run_analysis_task

        # ADR §10 risk 7: task payload'ında düz anahtar YOK, yalnızca kimlik.
        run_analysis_task.delay(str(analysis_id))
    except BaseException:
        # 202 DIŞINDA her sonuç talebi bırakır. En görünür senaryo
        # COST_LIMIT_EXCEEDED: hatanın kendi metni "sınırı yükseltin ya da
        # daha ucuz bir model seçin" diyor — talep tutulsaydı kullanıcı tam
        # da bunu yapınca gövdesi değiştiği için 409 alırdı.
        if claimed is not None:
            await release_idempotency(claimed, settings)
        raise

    created = AnalysisCreated(analysis_id=analysis_id, status=AnalysisStatus.QUEUED)
    if claimed is not None:
        await remember_idempotency(
            claimed,
            created,
            getattr(request.state, "trace_id", ""),
            settings,
        )

    logger.info(
        "analysis_created",
        extra={
            "analysis_id": str(analysis_id),
            "upload_id": str(upload.id),
            "model": body.model,
            "key_ttl_seconds": ttl,
            "trace_id": getattr(request.state, "trace_id", None),
        },
    )
    return created


def _estimated_seconds_remaining(analysis: Analysis, settings: Settings) -> float | None:
    """Kalan süre tahmini. Terminal durumlarda `None`.

    Tahmin GEÇEN SÜREDEN türetiliyor: sabit bir toplam süre varsaymak,
    5 satırlık bir dosyada da 100.000 satırlık bir dosyada da aynı sayıyı
    gösterirdi. `progress` henüz 0'ken elde veri yok; hard timeout'un
    onda biri makul bir başlangıç tahmini olarak kullanılıyor.
    """
    if analysis.status in TERMINAL_STATUSES:
        return None

    created = analysis.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    elapsed = max(0.0, (datetime.now(UTC) - created).total_seconds())

    if analysis.progress <= 0:
        return float(settings.analysis_hard_timeout_seconds // 10)

    remaining = elapsed * (100.0 - analysis.progress) / analysis.progress
    return round(min(remaining, float(settings.analysis_hard_timeout_seconds)), 1)


@router.get(
    "/{analysis_id}",
    response_model=AnalysisJobRead,
    responses=ANALYSIS_READ,
    summary="Analiz durumu ve ilerlemesi",
)
async def get_analysis(
    analysis_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> AnalysisJobRead:
    analysis = await _get_analysis_or_404(session, analysis_id)

    return AnalysisJobRead(
        analysis_id=analysis.id,
        status=analysis.status,
        progress=analysis.progress,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
        estimated_seconds_remaining=_estimated_seconds_remaining(analysis, settings),
        # JSONB'den şemadan geçiriliyor: eski biçimli bir kayıt sözleşmeyi
        # sessizce bozmasın.
        error=ProblemDetails.model_validate(analysis.error) if analysis.error else None,
    )


@router.get(
    "/{analysis_id}/result",
    response_model=AnalysisReport,
    responses=ANALYSIS_RESULT,
    summary="Tamamlanmış analizin raporu",
)
async def get_analysis_result(analysis_id: uuid.UUID, session: SessionDep) -> AnalysisReport:
    return await _completed_report(session, analysis_id)


async def _completed_report(session: AsyncSession, analysis_id: uuid.UUID) -> AnalysisReport:
    """Yalnızca tamamlanmış analizin raporunu döndürür; değilse 409.

    `/result` ve `/export` aynı kapıyı kullanır. Ayrı ayrı yazılsalardı
    biri gevşediğinde ("export'ta 404 dönsün") sözleşme sessizce ikiye
    ayrılırdı; mock da ikisinde aynı davranışı taklit ediyor.
    """
    analysis = await _get_analysis_or_404(session, analysis_id)

    if analysis.status is not AnalysisStatus.COMPLETED or analysis.report is None:
        # Bilinçli: devam eden iş için boş veri değil AÇIK bir hata dönülür.
        raise ApiError(
            "JOB_CONFLICT",
            "Rapor yalnızca tamamlanmış analiz için alınabilir.",
        )

    # JSONB'den şemadan geçiriliyor: `generated_at` buradan `...Z` olarak
    # çıkar (schemas/common.py::to_iso_z), yani export gövdesi `/result`
    # gövdesiyle BİREBİR aynı biçimde serileşir.
    return AnalysisReport.model_validate(analysis.report)


@router.get(
    "/{analysis_id}/export",
    summary="Raporu dosya olarak indir",
    response_class=Response,
    responses=ANALYSIS_EXPORT
    | {
        200: {
            "content": {
                report_export.JSON_MEDIA_TYPE: {},
                report_export.XLSX_MEDIA_TYPE: {},
            },
            "description": "Rapor dosyası (`Content-Disposition: attachment`).",
        }
    },
)
async def export_analysis(
    analysis_id: uuid.UUID,
    session: SessionDep,
    export_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.JSON,
) -> Response:
    """ADR §6: `?format=xlsx|json`.

    BİLİNMEYEN FORMAT 422 DEĞİL, `json`'dır. Sebep sözleşmede: mock
    `exportFormatSchema.catch("json")` kullanıyor ve FastAPI'nin
    `Literal[...]` doğrulaması buradan `RequestValidationError`'a düşerdi —
    o handler yol `/analyses` ile bitmediği için `UPLOAD_INVALID_TYPE`
    (415) üretir ve kullanıcı "Desteklenmeyen dosya türü" görürdü. Yüklediği
    dosyayla hiç ilgisi olmayan bir mesaj. Frontend zaten yalnızca iki
    geçerli değerden birini gönderiyor.
    """
    report = await _completed_report(session, analysis_id)

    if export_format is ExportFormat.XLSX:
        # openpyxl senkron ve CPU yoğun; event loop'u bloklamamak için
        # thread'e taşınıyor (upload yolundaki boto3 ile aynı gerekçe).
        body = await run_in_threadpool(report_export.build_xlsx, report)
        media_type = report_export.XLSX_MEDIA_TYPE
        extension = "xlsx"
    else:
        body = report.model_dump_json(indent=2).encode("utf-8")
        media_type = report_export.JSON_MEDIA_TYPE
        extension = "json"

    logger.info(
        "analysis_exported",
        extra={"analysis_id": str(analysis_id), "format": extension, "bytes": len(body)},
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": report_export.content_disposition(str(analysis_id), extension)
        },
    )


@router.delete(
    "/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ANALYSIS_CANCEL,
    summary="Analizi iptal et",
)
async def cancel_analysis(
    analysis_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    analysis = await _get_analysis_or_404(session, analysis_id)

    if analysis.status in TERMINAL_STATUSES:
        # Plan §4: "iş bitmişse JOB_CONFLICT (409)".
        raise ApiError(
            "JOB_CONFLICT",
            "Tamamlanmış veya zaten sonlanmış bir analiz iptal edilemez.",
        )

    analysis.cancel_requested = True
    analysis.status = AnalysisStatus.CANCELLED
    # `progress`'e DOKUNULMUYOR: iş nereye kadar geldiyse orada kalır.
    # Eskiden burada `STAGE_PROGRESS[QUEUED]` (0.0) yazılıyordu, yani %75'te
    # iptal edilen bir işin kaydı "hiç başlamamış" gibi görünüyordu. Hata yolu
    # (`_fail`) ise 100.0 yazıyor; iki terminal yolun ayrışması yanıltıcıydı.
    await session.commit()

    # ADR §9: iş bitince anahtar BAŞARI/HATA/İPTAL fark etmeksizin silinir.
    # Worker da kendi `finally` bloğunda siliyor; iki kez silmek zararsız ve
    # burada silmek, worker işi hiç almadıysa anahtarın TTL boyunca
    # beklemesini engelliyor.
    await run_in_threadpool(secret_store.delete_key, analysis_id, settings)

    logger.info("analysis_cancelled", extra={"analysis_id": str(analysis_id)})

    # 204 gövdesi GERÇEKTEN boş olmalı: frontend client.ts 204'te gövdeyi
    # hiç okumuyor.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
