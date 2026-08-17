"""`Idempotency-Key` — ADR-0001 §6, ADR-0002 #3.

Sözleşme BE-01'den beri yazılıydı (`docs/api/openapi.json` header'ı iki POST
ucunda da belgeliyor, `responses.py` 409'u belgeliyor, `Settings` TTL'i
tanımlıyor) ama DAVRANIŞ yoktu. Bu dosya davranışın kendisini ölçüyor.

Redis/Postgres/MinIO gerektiren testler `integration` işaretli; `conftest.py`
servisler kapalıyken açık bir mesajla atlıyor. Fingerprint ve canonical JSON
kuralları servissiz de koşar — sözleşmenin en sürüklenmeye açık kısmı orası.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy import text

from app.api.v1.deps import optional_idempotency_key
from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.core.errors import RequestValidationFailedError
from app.main import create_app
from app.services import idempotency, secret_store, storage
from app.workers import tasks

FIXTURES = Path(__file__).parent / "fixtures"

TEST_KEY = "test-key-aaaaaaaaaaaaaaaaaaaaaaaa"

integration = pytest.mark.integration


# ================================================== servissiz birim testleri


def test_canonical_json_anahtarlari_siralar_ve_bosluk_birakmaz() -> None:
    assert idempotency.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert idempotency.canonical_json([1, "x", None, True]) == '[1,"x",null,true]'


def test_canonical_json_tam_sayili_floatlari_js_gibi_yazar() -> None:
    """`JSON.stringify(5.0)` → `5`; Python'un varsayılanı `5.0` olurdu.

    `max_cost_usd` tam sayı değerli bir float olduğu için bu fark
    sözleşmedeki EN TİPİK gövdede ortaya çıkar ve iki dilin fingerprint'ini
    ayrıştırırdı.
    """
    assert idempotency.canonical_json(5.0) == "5"
    assert idempotency.canonical_json(2.5) == "2.5"
    assert idempotency.canonical_json({"max_cost_usd": 5.0}) == '{"max_cost_usd":5}'


def test_upload_fingerprint_dort_alanin_hepsine_duyarli() -> None:
    """ADR-0002 #3 dosya hash'ini TEK BAŞINA yeterli saymıyor.

    Yalnızca içerik hash'lense, `ocak.xlsx` yerine `subat.xlsx` yükleyen
    kullanıcı ilk dosyanın upload_id'sini geri alır ve raporun
    `source_summary`'sinde yanlış dosya adını görürdü.
    """
    base = {
        "file_sha256": "a" * 64,
        "filename": "veri.xlsx",
        "mime_type": "application/vnd.ms-excel",
        "size": 1024,
    }
    reference = idempotency.upload_fingerprint(**base)  # type: ignore[arg-type]

    for field, changed in (
        ("file_sha256", "b" * 64),
        ("filename", "baska.xlsx"),
        ("mime_type", "text/plain"),
        ("size", 1025),
    ):
        altered = idempotency.upload_fingerprint(**{**base, field: changed})  # type: ignore[arg-type]
        assert altered != reference, field


def test_saklama_anahtari_yolu_normalize_eder() -> None:
    """`/uploads` ve `/uploads/` aynı uç; ayrışsalardı koruma yarım çalışırdı."""
    assert idempotency.storage_key("POST", "/api/v1/uploads", "k") == idempotency.storage_key(
        "post", "//api/v1/uploads/", "k"
    )


def test_saklama_anahtari_ucu_ve_anahtari_ayirir() -> None:
    assert idempotency.storage_key("POST", "/api/v1/uploads", "k") != idempotency.storage_key(
        "POST", "/api/v1/analyses", "k"
    )
    assert idempotency.storage_key("POST", "/api/v1/uploads", "k1") != idempotency.storage_key(
        "POST", "/api/v1/uploads", "k2"
    )


def test_uzun_anahtar_dependency_seviyesinde_reddedilir() -> None:
    """Sınır ve alan adı mock ile birebir (`apps/web/src/mocks/validation.ts`)."""
    with pytest.raises(RequestValidationFailedError) as exc_info:
        optional_idempotency_key("k" * 256)

    assert exc_info.value.errors == [("header.Idempotency-Key", "En fazla 255 karakter olabilir.")]


def test_sinirdaki_ve_bos_anahtarlar() -> None:
    """255 kapsayıcı; whitespace "gönderilmemiş" sayılır.

    Boş değer normalize edilmeseydi, header'ı boş gönderen TÜM istemcilerin
    istekleri tek bir saklama anahtarında toplanır ve ikincisi 409 alırdı.
    """
    assert optional_idempotency_key("k" * 255) == "k" * 255
    assert optional_idempotency_key("   ") is None
    assert optional_idempotency_key(None) is None


# ================================================================ entegrasyon


@pytest.fixture(autouse=True)
def _bucket(request: pytest.FixtureRequest) -> None:
    """Bucket yalnızca entegrasyon testleri için kuruluyor.

    Modül seviyesinde autouse olsaydı, yukarıdaki servissiz testler de MinIO
    isterdi ve `conftest.py`'nin skip mekanizması onları kapsamıyor.
    """
    if request.node.get_closest_marker("integration"):
        storage.ensure_bucket(get_settings())


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Compose'daki worker testin kendi çağrısıyla yarışmasın."""
    monkeypatch.setattr(tasks.profile_upload, "delay", lambda *a, **k: None)
    monkeypatch.setattr(tasks.run_analysis_task, "delay", lambda *a, **k: None)
    yield


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


@pytest.fixture
def redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def _fresh_key() -> str:
    return f"test-{uuid.uuid4()}"


async def _upload(
    client: AsyncClient,
    *,
    idempotency_key: str | None = None,
    fixture: str = "valid_multi_sheet.xlsx",
    filename: str | None = None,
) -> Any:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    path = FIXTURES / fixture
    with path.open("rb") as handle:
        return await client.post(
            "/api/v1/uploads",
            files={"file": (filename or path.name, handle, "application/octet-stream")},
            headers=headers,
        )


async def _ready_upload(client: AsyncClient) -> uuid.UUID:
    response = await _upload(client)
    upload_id = uuid.UUID(response.json()["upload_id"])
    assert await tasks.run_upload_profiling(upload_id) == "ready"
    return upload_id


def _analysis_body(upload_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
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
    return payload


async def _create_analysis(
    client: AsyncClient,
    upload_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
    api_key: str = TEST_KEY,
    **overrides: Any,
) -> Any:
    headers = {"X-OpenRouter-Key": api_key}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        "/api/v1/analyses",
        json=_analysis_body(upload_id, **overrides),
        headers=headers,
    )


async def _row_count(table: str, row_id: str) -> int:
    async with session_scope() as session:
        result = await session.execute(
            text(f"select count(*) from {table} where id = :id"),
            {"id": row_id},
        )
        return int(result.scalar_one())


# ------------------------------------------------------------------ /uploads


@integration
async def test_ayni_anahtar_ayni_dosya_tek_upload_uretir(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sözleşmenin ana cümlesi: replay ilk 202'yi AYNEN döndürür.

    Ölçülen şey yalnızca gövdenin eşitliği DEĞİL — ikinci istekte ikinci bir
    upload satırı ve ikinci bir worker job'ı oluşmadığı. Bu koruma olmadan ağ
    kesintisinden sonraki bir retry ~130 MB'lık ikinci bir nesne yazardı.
    """
    key = _fresh_key()
    enqueued: list[str] = []
    monkeypatch.setattr(tasks.profile_upload, "delay", lambda *a, **k: enqueued.append(a[0]))

    first = await _upload(client, idempotency_key=key)
    second = await _upload(client, idempotency_key=key)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()

    upload_id = first.json()["upload_id"]
    assert await _row_count("uploads", upload_id) == 1
    assert enqueued == [upload_id], "replay ikinci kez kuyruğa almamalı"


@integration
async def test_ayni_anahtar_farkli_dosya_409_uretir(client: AsyncClient) -> None:
    key = _fresh_key()
    assert (await _upload(client, idempotency_key=key)).status_code == 202

    conflict = await _upload(client, idempotency_key=key, fixture="corrupt.xlsx")

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "JOB_CONFLICT"


@integration
async def test_ayni_dosya_farkli_ad_ile_409_uretir(client: AsyncClient) -> None:
    key = _fresh_key()
    assert (await _upload(client, idempotency_key=key)).status_code == 202

    conflict = await _upload(client, idempotency_key=key, filename="baska-ad.xlsx")

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "JOB_CONFLICT"


@integration
async def test_anahtarsiz_istek_kayit_birakmaz(client: AsyncClient, redis_client: Redis) -> None:
    """Header opsiyonel: göndermeyen istemci bugünkü davranışı aynen alır."""
    before = len(list(redis_client.scan_iter(match="idempotency:*")))

    first = await _upload(client)
    second = await _upload(client)

    assert first.json()["upload_id"] != second.json()["upload_id"]
    assert len(list(redis_client.scan_iter(match="idempotency:*"))) == before


@integration
async def test_uzun_anahtar_http_seviyesinde_422_uretir(client: AsyncClient) -> None:
    response = await _upload(client, idempotency_key="k" * 256)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "REQUEST_VALIDATION"
    assert body["errors"][0]["field"] == "header.Idempotency-Key"


@integration
async def test_kayit_ttl_i_ayardan_gelir(client: AsyncClient, redis_client: Redis) -> None:
    """TTL sabit kodlanmıyor; `Settings.idempotency_ttl_seconds` bu commit'e
    kadar hiçbir yerde OKUNMUYORDU."""
    key = _fresh_key()
    await _upload(client, idempotency_key=key)

    ttl = redis_client.ttl(idempotency.storage_key("POST", "/api/v1/uploads", key))
    configured = get_settings().idempotency_ttl_seconds

    assert isinstance(ttl, int)
    assert configured - 60 < ttl <= configured


@integration
async def test_islenmekte_olan_istek_ikinci_cagriyi_409_ile_reddeder(
    client: AsyncClient, redis_client: Redis
) -> None:
    """İki fazlı kaydın var olma sebebi.

    Tek fazlı olsaydı (yalnızca 202'de yaz) aynı anahtarla eşzamanlı gelen
    iki istek de kontrolü geçer ve İKİSİ de job açardı. Kaydı elle "pending"e
    çekmek, o yarışın deterministik karşılığı.
    """
    key = _fresh_key()
    stored = idempotency.storage_key("POST", "/api/v1/uploads", key)

    await _upload(client, idempotency_key=key)
    record = cast("bytes | None", redis_client.get(stored))
    assert record is not None

    pending = json.loads(record)
    pending["state"] = "pending"
    redis_client.set(stored, json.dumps(pending), keepttl=True)

    inflight = await _upload(client, idempotency_key=key)

    assert inflight.status_code == 409
    assert inflight.json()["code"] == "JOB_CONFLICT"


# ----------------------------------------------------------------- /analyses


@integration
async def test_ayni_anahtar_ayni_govde_tek_analiz_uretir(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _fresh_key()
    upload_id = await _ready_upload(client)
    enqueued: list[str] = []
    monkeypatch.setattr(tasks.run_analysis_task, "delay", lambda *a, **k: enqueued.append(a[0]))

    first = await _create_analysis(client, upload_id, idempotency_key=key)
    second = await _create_analysis(client, upload_id, idempotency_key=key)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()

    analysis_id = first.json()["analysis_id"]
    assert await _row_count("analyses", analysis_id) == 1
    assert enqueued == [analysis_id], "replay ikinci LLM koşusu başlatmamalı"


@integration
async def test_ayni_anahtar_farkli_govde_409_uretir(client: AsyncClient) -> None:
    key = _fresh_key()
    upload_id = await _ready_upload(client)

    assert (await _create_analysis(client, upload_id, idempotency_key=key)).status_code == 202
    conflict = await _create_analysis(client, upload_id, idempotency_key=key, top_n=10)

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "JOB_CONFLICT"


@integration
async def test_replay_yeni_openrouter_anahtarini_yok_sayar(client: AsyncClient) -> None:
    """ADR-0002 #3: "replay yeni `X-OpenRouter-Key` header'ını YOK SAYAR".

    Anahtar orijinal job'a bağlı. Yok sayılmasaydı, anahtar rotasyonundan
    sonraki bir retry çalışan job'ın Redis'teki secret'ını ezerdi.
    """
    key = _fresh_key()
    upload_id = await _ready_upload(client)

    first = await _create_analysis(client, upload_id, idempotency_key=key)
    analysis_id = uuid.UUID(first.json()["analysis_id"])

    second = await _create_analysis(
        client,
        upload_id,
        idempotency_key=key,
        api_key="test-key-bbbbbbbbbbbbbbbbbbbbbbbb",
    )

    assert second.json() == first.json()
    assert secret_store.load_key(analysis_id) == TEST_KEY


@integration
async def test_basarisiz_istek_talebi_birakir(client: AsyncClient) -> None:
    """`COST_LIMIT_EXCEEDED` hatasının kendi metni "sınırı yükseltin" diyor.

    Talep tutulsaydı kullanıcı tam da bunu yapınca gövdesi değiştiği için 409
    alırdı: hatanın önerdiği düzeltmeyi uygulamak imkânsız olurdu.
    """
    key = _fresh_key()
    upload_id = await _ready_upload(client)

    rejected = await _create_analysis(client, upload_id, idempotency_key=key, max_cost_usd=0.000001)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "COST_LIMIT_EXCEEDED"

    recovered = await _create_analysis(client, upload_id, idempotency_key=key, max_cost_usd=50.0)
    assert recovered.status_code == 202


@integration
async def test_replay_orijinal_trace_idyi_dondurur(client: AsyncClient) -> None:
    """Saklanan değer "ilk 202'nin status/body/HEADER metadata'sı" (ADR-0002 #3).

    Mock da aynı kuralı uyguluyor (`apps/web/src/mocks/responses.ts`), bu
    yüzden `TraceIdMiddleware` mevcut bir trace header'ını ezmiyor.
    """
    key = _fresh_key()
    upload_id = await _ready_upload(client)

    first = await _create_analysis(client, upload_id, idempotency_key=key)
    second = await _create_analysis(client, upload_id, idempotency_key=key)

    assert first.headers["X-Trace-Id"] == second.headers["X-Trace-Id"]
