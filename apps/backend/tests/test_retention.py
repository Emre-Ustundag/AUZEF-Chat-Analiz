"""Retention testleri — ADR §9, plan §4 Faz 4.

Postgres, Redis ve MinIO gerektirir (`docker compose up -d`).

İki şey ayrı ayrı kanıtlanıyor:

1. **İş sonunda ham dosya siliniyor** — başarı, hata ve iptal yollarının
   üçünde de. Bunun için MinIO'ya doğrudan soruluyor; DB'de bir bayrak
   görmek yetmez, ADR §9 dosyanın GERÇEKTEN gitmesini istiyor.
2. **Süpürücü çalışıyor** — kayıt elle yaşlandırılıp süpürülüyor.

YAŞLANDIRMA HAM SQL İLE YAPILIYOR. ORM üzerinden `updated_at` yazmak işe
YARAMAZ: sütunda `onupdate=func.now()` var, yani SQLAlchemy'nin ürettiği
UPDATE geçmişe attığımız değeri anında `now()` ile ezer ve test hiçbir
şey kanıtlamadan yeşil kalır.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.main import create_app
from app.models.analysis import Analysis
from app.models.upload import Upload
from app.services import retention, storage
from app.workers import tasks
from tests.test_analysis_integration import TEST_KEY, install_fake_provider

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _bucket() -> None:
    storage.ensure_bucket(get_settings())


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(tasks.profile_upload, "delay", lambda *a, **k: None)
    monkeypatch.setattr(tasks.run_analysis_task, "delay", lambda *a, **k: None)
    yield


@pytest.fixture(autouse=True)
def _provider(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_provider(monkeypatch)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    await dispose_engine()
    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


# ------------------------------------------------------------- yardımcılar


def object_exists(key: str) -> bool:
    """MinIO'ya DOĞRUDAN sorar — DB'deki bir bayrağa güvenmiyoruz."""
    settings = get_settings()
    s3 = storage.build_s3_client(settings)
    try:
        s3.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


async def ready_upload(client: AsyncClient) -> tuple[uuid.UUID, str]:
    path = FIXTURES / "valid_multi_sheet.xlsx"
    with path.open("rb") as handle:
        response = await client.post(
            "/api/v1/uploads",
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    upload_id = uuid.UUID(response.json()["upload_id"])
    assert await tasks.run_upload_profiling(upload_id) == "ready"

    async with session_scope() as session:
        upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
        assert upload is not None
        storage_key = upload.storage_key

    assert object_exists(storage_key), "profil sonrası ham dosya durmalı"
    return upload_id, storage_key


async def start_analysis(client: AsyncClient, upload_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    payload: dict[str, Any] = {
        "upload_id": str(upload_id),
        "sheet_name": "Mesajlar",
        "text_column": "mesaj",
        "model": "anthropic/claude-sonnet-4.6",
        "prompt_version": "faq_analysis/v1",
        "top_n": 20,
        "max_cost_usd": 5.0,
    }
    payload.update(overrides)
    response = await client.post(
        "/api/v1/analyses", json=payload, headers={"X-OpenRouter-Key": TEST_KEY}
    )
    return uuid.UUID(response.json()["analysis_id"])


async def age_row(table: str, row_id: uuid.UUID, hours: int) -> None:
    """Kaydı `hours` saat geçmişe atar.

    HAM SQL ZORUNLU: `updated_at` sütununda `onupdate=func.now()` var ve ORM
    üzerinden yazılan her değeri anında `now()` eziyor. ORM ile yaşlandıran
    bir test hiçbir şey kanıtlamadan yeşil kalırdı.
    """
    async with session_scope() as session:
        await session.execute(
            text(
                f"UPDATE {table} SET updated_at = now() - make_interval(hours => :h) WHERE id = :i"
            ),
            {"h": hours, "i": str(row_id)},
        )
        await session.commit()


# ================================================================
# 1. İş sonunda ham dosya siliniyor (ADR §9)
# ================================================================


async def test_basarili_is_ham_dosyayi_siler(client: AsyncClient) -> None:
    upload_id, key = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)

    assert await tasks.run_analysis(analysis_id) == "completed"

    assert not object_exists(key), "ADR §9: ham dosya iş sonunda silinmeli"
    # Rapor DURUYOR: silinen şey kaynak dosya, sonuç değil.
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 200

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_basarisiz_is_de_ham_dosyayi_siler(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR §9: "başarı/hata/iptal fark etmeksizin".

    Sağlayıcı kaynaklı hata kullanılıyor (`always_bad_json` →
    `PROVIDER_BAD_RESPONSE`): LLM çağrıları GERÇEKTEN yapıldı, token harcandı.
    Böyle bir işte kaynak dosyanın durması için bir gerekçe yok.

    Bu test eskiden maliyet tavanı aşımını kullanıyordu; o senaryo B4 ile
    kaynağı KORUYAN yola geçti (bkz. bir alttaki test), o yüzden ADR §9'un
    bu cümlesini artık sağlayıcı hatası kanıtlıyor.
    """
    install_fake_provider(monkeypatch, always_bad_json=True)
    upload_id, key = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)

    assert await tasks.run_analysis(analysis_id) == "failed"
    job = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
    assert job["error"]["code"] == "PROVIDER_BAD_RESPONSE"
    assert not object_exists(key)

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_maliyet_tavani_asiminda_kaynak_dosya_korunur(client: AsyncClient) -> None:
    """B4 — "sınırı yükseltip tekrar deneyin" gerçekten yapılabilir olmalı.

    Maliyet tavanı LLM çağrıları BAŞLAMADAN kontrol ediliyor; bu yolda hiçbir
    şey tüketilmiyor. Kullanıcıya gösterilen metin ("Maliyet sınırını
    yükseltin ya da daha ucuz bir model seçin") aynı upload üzerinde yeniden
    denemeyi öneriyor ve configure ekranı `/yuklemeler/{uploadId}` kalıcı bir
    URL — yani bu tamamen doğal bir kullanıcı hareketi.

    Kaynak dosya silinirse o öneri yalan oluyor: ikinci analiz
    `JOB_NOT_FOUND` ile düşüyor ve kullanıcı 130 MB'lık dosyayı, hiçbir
    maliyete yol açmamış bir ayar hatası yüzünden yeniden yüklemek zorunda
    kalıyor.

    Test yalnızca dosyanın durduğunu değil, ÖNERİLEN AKIŞIN İŞLEDİĞİNİ
    doğruluyor — asıl kanıt bu.
    """
    upload_id, key = await ready_upload(client)
    first = await start_analysis(client, upload_id, max_cost_usd=0.0000001)

    assert await tasks.run_analysis(first) == "failed"
    job = (await client.get(f"/api/v1/analyses/{first}")).json()
    assert job["error"]["code"] == "COST_LIMIT_EXCEEDED"
    assert object_exists(key), "hiçbir şey tüketilmedi; kaynak dosya durmalı"

    # Kullanıcı sınırı yükseltip aynı upload üzerinde yeniden deniyor.
    second = await start_analysis(client, upload_id, max_cost_usd=50.0)
    assert await tasks.run_analysis(second) == "completed"
    assert (await client.get(f"/api/v1/analyses/{second}/result")).status_code == 200

    # İkinci iş başarıyla bittiği için ADR §9 yine geçerli: dosya artık gitti.
    assert not object_exists(key)

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_iptal_edilen_is_de_ham_dosyayi_siler(client: AsyncClient) -> None:
    upload_id, key = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)

    assert (await client.delete(f"/api/v1/analyses/{analysis_id}")).status_code == 204
    assert await tasks.run_analysis(analysis_id) == "cancelled"
    assert not object_exists(key)

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_kaynagi_silinmis_upload_ikinci_analizde_temiz_hata_verir(
    client: AsyncClient,
) -> None:
    """Silinen kaynak `INTERNAL_ERROR` değil, anlaşılır bir hata olmalı.

    Bu, ham dosyayı iş sonunda silmenin doğrudan sonucu: aynı upload
    üzerinde ikinci bir analiz artık kaynağını bulamaz. "Beklenmeyen bir
    hata oluştu" demek kullanıcıyı yanıltırdı.
    """
    upload_id, key = await ready_upload(client)
    first = await start_analysis(client, upload_id)
    assert await tasks.run_analysis(first) == "completed"
    assert not object_exists(key)

    second = await start_analysis(client, upload_id)
    assert await tasks.run_analysis(second) == "failed"

    job = (await client.get(f"/api/v1/analyses/{second}")).json()
    assert job["error"]["code"] == "JOB_NOT_FOUND"
    assert job["error"]["status"] == 404
    assert "yeniden yükleyin" in job["error"]["detail"]

    await client.delete(f"/api/v1/uploads/{upload_id}")


# ================================================================
# 2. Süpürücü (Celery beat)
# ================================================================


async def test_supurucu_suresi_dolan_raporu_siler(client: AsyncClient) -> None:
    """Plan §4 Faz 4 ölçüt 5 — kayıt yaşlandırılıp süpürülüyor."""
    settings = get_settings()
    upload_id, _ = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)
    assert await tasks.run_analysis(analysis_id) == "completed"
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 200

    # Yaşlandırmadan ÖNCE süpürmek hiçbir şeyi silmemeli — yoksa test,
    # süpürücünün "her şeyi siliyor" olmasıyla da yeşil kalırdı.
    async with session_scope() as session:
        before = await retention.sweep_expired_analyses(session, settings)
    assert before == 0
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 200

    await age_row("analyses", analysis_id, settings.report_retention_hours + 1)

    async with session_scope() as session:
        removed = await retention.sweep_expired_analyses(session, settings)
    assert removed >= 1

    # Rapor GERÇEKTEN gitti: 409 değil 404 — kayıt yok.
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 404
    assert (await client.get(f"/api/v1/analyses/{analysis_id}")).status_code == 404

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_supurucu_devam_eden_isi_silmez(client: AsyncClient) -> None:
    """Terminal olmayan iş, ne kadar eski olursa olsun korunur.

    Aksi hâlde kuyrukta uzun bekleyen bir analiz süpürülür ve kullanıcı
    ilerlemeyen bir ekranı 404 alarak izlerdi.
    """
    settings = get_settings()
    upload_id, _ = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)  # queued

    await age_row("analyses", analysis_id, settings.report_retention_hours * 10)

    async with session_scope() as session:
        await retention.sweep_expired_analyses(session, settings)

    assert (await client.get(f"/api/v1/analyses/{analysis_id}")).status_code == 200

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_supurucu_suresi_dolan_uploadi_ve_dosyasini_siler(client: AsyncClient) -> None:
    settings = get_settings()
    upload_id, key = await ready_upload(client)
    assert object_exists(key)

    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        removed = await retention.sweep_expired_uploads(session, settings)
    assert removed >= 1

    assert not object_exists(key), "süresi dolan upload'ın ham dosyası da gitmeli"
    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 404


async def test_supurucu_devam_eden_analizi_olan_uploadi_silmez(client: AsyncClient) -> None:
    """CASCADE tuzağı: upload silmek ÇALIŞAN analizin satırını da uçurur.

    `analyses.upload_id` FK'si `ON DELETE CASCADE`. Worker bir gün kapalı
    kalıp kuyruk birikirse, korumasız bir upload süpürmesi iş aşama
    ortasındayken kaydı yok eder ve kullanıcı 404 poll etmeye başlar.
    """
    settings = get_settings()
    upload_id, key = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)  # queued — terminal DEĞİL

    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        await retention.sweep_expired_uploads(session, settings)

    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 200
    assert (await client.get(f"/api/v1/analyses/{analysis_id}")).status_code == 200
    assert object_exists(key), "çalışan işin kaynak dosyası da durmalı"

    # İş sonlandıktan sonra artık süpürülebilir olmalı — koruma kalıcı değil.
    assert (await client.delete(f"/api/v1/analyses/{analysis_id}")).status_code == 204
    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        assert await retention.sweep_expired_uploads(session, settings) >= 1

    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 404


async def test_supurucu_eski_upload_uzerindeki_taze_raporu_yok_etmez(client: AsyncClient) -> None:
    """B3 — rapor ömrü UPLOAD'ın yaşına bağlı kalmamalı.

    ADR §9 raporu 24 saat tutmayı vaat ediyor (`report_retention_hours`), ama
    süpürme yalnızca `Upload.updated_at`'e bakıyordu ve terminal analizi olan
    upload'ı silmekten çekinmiyordu. `analyses.upload_id` FK'si CASCADE
    olduğu için bu, taze bir raporu birlikte uçuruyordu.

    Senaryo: dosya T=0'da profillendi, kullanıcı T=23s'te analizi başlattı,
    T=23.5s'te tamamlandı. T=24s'te upload'ın süresi doldu → rapor henüz
    yarım saatlikken yok oldu.
    """
    settings = get_settings()
    upload_id, _ = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)
    assert await tasks.run_analysis(analysis_id) == "completed"
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 200

    # Upload eski, RAPOR taze.
    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        await retention.sweep_expired_uploads(session, settings)

    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 200, (
        "taze rapor, upload'ın yaşı yüzünden CASCADE ile silinmemeli"
    )
    assert (await client.get(f"/api/v1/analyses/{analysis_id}")).status_code == 200

    # Koruma KALICI OLMAMALI: rapor da yaşlanınca upload toplanabilir hâle
    # gelmeli, yoksa erken silmeyi bir sızıntıyla değiştirmiş olurduk.
    await age_row("analyses", analysis_id, settings.report_retention_hours + 1)
    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        await retention.sweep_expired_analyses(session, settings)
        assert await retention.sweep_expired_uploads(session, settings) >= 1

    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 404


async def test_supurucu_analizsiz_uploadi_siler(client: AsyncClient) -> None:
    """Koruma fazla geniş olmamalı: hiç analiz edilmemiş dosya ASIL hedef."""
    settings = get_settings()
    upload_id, key = await ready_upload(client)

    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    async with session_scope() as session:
        assert await retention.sweep_expired_uploads(session, settings) >= 1

    assert not object_exists(key)
    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 404


async def test_supurucu_kacak_nesneyi_siler() -> None:
    """ADR §9: veritabanında karşılığı olmayan nesne.

    MinIO `LastModified`'ı yazan taraf olduğu için nesneyi geçmişe
    atamıyoruz; bunun yerine kesim noktası ileri alınıyor. Ölçülen şey
    aynı: "cutoff'tan eski ve DB'de yok" koşulu.
    """
    settings = get_settings()
    s3 = storage.build_s3_client(settings)
    key = f"uploads/{uuid.uuid4()}/source.xlsx"
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"kacak nesne")
    assert object_exists(key)

    try:
        # Şimdiki zamanı geleceğe alarak nesneyi "eski" yapıyoruz.
        future = datetime.now(UTC) + timedelta(hours=settings.orphan_object_retention_hours + 1)

        async with session_scope() as session:
            removed = await retention.sweep_orphan_objects(session, settings, now=future)

        assert removed >= 1
        assert not object_exists(key)
    finally:
        s3.delete_object(Bucket=settings.s3_bucket, Key=key)


async def test_supurucu_taze_kacak_nesneye_dokunmaz() -> None:
    """Yaş kontrolü olmadan süpürücü, DB commit'i beklerken yüklenmiş bir
    dosyayı kullanıcının ayağının altından çekerdi."""
    settings = get_settings()
    s3 = storage.build_s3_client(settings)
    key = f"uploads/{uuid.uuid4()}/source.xlsx"
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"yeni yuklenmis")

    try:
        async with session_scope() as session:
            await retention.sweep_orphan_objects(session, settings)
        assert object_exists(key), "taze nesne silinmemeli"
    finally:
        s3.delete_object(Bucket=settings.s3_bucket, Key=key)


async def test_celery_task_sarmalayicisi_calisir(client: AsyncClient) -> None:
    """Beat'in tetiklediği yol: `run_retention_sweep` istisna FIRLATMAZ."""
    settings = get_settings()
    upload_id, _ = await ready_upload(client)
    analysis_id = await start_analysis(client, upload_id)
    assert await tasks.run_analysis(analysis_id) == "completed"

    await age_row("analyses", analysis_id, settings.report_retention_hours + 1)
    await age_row("uploads", upload_id, settings.upload_retention_hours + 1)

    counters = await tasks.run_retention_sweep()

    assert set(counters) == {"expired_analyses", "expired_uploads", "orphan_objects"}
    assert counters["expired_uploads"] >= 1

    async with session_scope() as session:
        assert await session.scalar(select(Upload).where(Upload.id == upload_id)) is None
        assert await session.scalar(select(Analysis).where(Analysis.id == analysis_id)) is None


async def test_beat_zamanlamasi_kayitli() -> None:
    """Süpürücü kodda var ama beat'te kayıtlı değilse HİÇ ÇALIŞMAZ."""
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "retention-sweep" in schedule
    entry = schedule["retention-sweep"]
    assert entry["task"] == "app.workers.tasks.sweep_retention"
    assert entry["schedule"] == float(get_settings().retention_sweep_interval_seconds)
    # Task gerçekten kayıtlı mı — ad yanlış yazılırsa beat boşa tetikler.
    assert entry["task"] in celery_app.tasks


async def test_bucket_lifecycle_kurali_kurulu() -> None:
    """ADR §9 "kaçak dosyalar için azami 24 saat lifecycle".

    Kural, süpürücünün YERİNE değil YANINA duruyor: S3 lifecycle gün
    granülaritesinde (gerçek silinme 24-48 saat arası), tam 24 saati
    uygulayan süpürücüdür.
    """
    settings = get_settings()
    storage.ensure_lifecycle_policy(settings)

    s3 = storage.build_s3_client(settings)
    config = s3.get_bucket_lifecycle_configuration(Bucket=settings.s3_bucket)
    rules = [r for r in config["Rules"] if r.get("ID") == storage.LIFECYCLE_RULE_ID]

    assert rules, "lifecycle kuralı kurulmamış"
    assert rules[0]["Status"] == "Enabled"
    assert rules[0]["Expiration"]["Days"] == settings.storage_lifecycle_expiration_days


async def test_retention_kolonlari_indeksli(client: AsyncClient) -> None:
    """B8 — süpürme sorgularının filtrelediği kolonda indeks olmalı.

    Migration'lar `created_at` üzerinde indeks kuruyordu ve yorumları bunların
    "Faz 4 retention işi" için olduğunu söylüyordu. Ama üç süpürme de
    `updated_at` filtreliyor (`retention.py`): terminal kayıtlarda `updated_at`
    etkin olarak `settled_at` ve retention'ın saydığı şey o. Yani indeksler
    yanlış kolondaydı ve tablolar büyüdükçe her süpürme sequential scan olurdu.

    Test kolon adını DOĞRUDAN sorguyor: `ix_*_updated_at` diye bir indeksin
    var olması yetmez, gerçekten `updated_at`'i kapsamalı.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tablename, indexdef FROM pg_indexes "
                    "WHERE tablename IN ('uploads','analyses')"
                )
            )
        ).all()

    for tablo in ("uploads", "analyses"):
        kapsayan = [
            indexdef
            for tablename, indexdef in rows
            if tablename == tablo and "updated_at" in indexdef
        ]
        assert kapsayan, f"{tablo}.updated_at indekssiz — retention süpürmesi seq scan"
