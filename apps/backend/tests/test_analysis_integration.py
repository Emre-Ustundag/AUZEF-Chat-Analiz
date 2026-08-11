"""Analiz akışının uçtan uca testi — plan §4 ölçütleri.

Postgres, Redis ve MinIO gerektirir (`docker compose up -d`).

`test_api_integration.py` ile aynı desen: Celery'ye gerçekten iş
gönderilmez, iş mantığı doğrudan `await` edilir. Broker üzerinden çalışmak
testi zamanlamaya bağımlı kılar ve rastgele patlar.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.main import create_app
from app.schemas.analysis import AnalysisJobRead
from app.schemas.report import AnalysisReport
from app.services import secret_store, storage
from app.workers import tasks

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"

TEST_KEY = "sk-or-v1-testkey0123456789abcdef0123456789"


@pytest.fixture(scope="module", autouse=True)
def _bucket() -> None:
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


async def _ready_upload(client: AsyncClient) -> tuple[uuid.UUID, dict[str, Any]]:
    """Geçerli bir dosya yükleyip profilini hazır hâle getirir."""
    path = FIXTURES / "valid_multi_sheet.xlsx"
    with path.open("rb") as handle:
        response = await client.post(
            "/api/v1/uploads",
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    upload_id = uuid.UUID(response.json()["upload_id"])
    assert await tasks.run_upload_profiling(upload_id) == "ready"

    body = (await client.get(f"/api/v1/uploads/{upload_id}")).json()
    return upload_id, body["profile"]


def _request_body(upload_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "upload_id": str(upload_id),
        "sheet_name": "Mesajlar",
        "text_column": "mesaj",
        "model": "anthropic/claude-sonnet-4",
        "prompt_version": "faq_analysis/v1",
        "top_n": 20,
        "max_cost_usd": 5.0,
    }
    payload.update(overrides)
    return payload


async def _create(
    client: AsyncClient,
    upload_id: uuid.UUID,
    *,
    key: str | None = TEST_KEY,
    **overrides: Any,
) -> Any:
    headers = {"X-OpenRouter-Key": key} if key is not None else {}
    return await client.post(
        "/api/v1/analyses",
        json=_request_body(upload_id, **overrides),
        headers=headers,
    )


# ------------------------------------------------------------- mutlu yol


async def test_analiz_uctan_uca_calisir(client: AsyncClient) -> None:
    upload_id, _ = await _ready_upload(client)

    created = await _create(client, upload_id)
    assert created.status_code == 202
    assert set(created.json()) == {"analysis_id", "status"}
    assert created.json()["status"] == "queued"

    analysis_id = uuid.UUID(created.json()["analysis_id"])

    # Başlangıç durumu sözleşmeye uymalı.
    job = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
    AnalysisJobRead.model_validate(job)
    assert job["status"] == "queued"
    assert job["progress"] == 0
    assert job["error"] is None
    assert job["created_at"].endswith("Z")

    assert await tasks.run_analysis(analysis_id) == "completed"

    job = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["estimated_seconds_remaining"] is None

    result = await client.get(f"/api/v1/analyses/{analysis_id}/result")
    assert result.status_code == 200

    report = result.json()
    parsed = AnalysisReport.model_validate(report)
    assert parsed.status == "completed"
    assert str(parsed.analysis_id) == str(analysis_id)
    assert parsed.source_summary.sheet_name == "Mesajlar"
    assert parsed.source_summary.text_column == "mesaj"
    # Fixture: 40 satır, 5 farklı mesaj tekrarlanıyor.
    assert parsed.source_summary.total_rows == 40
    assert parsed.preprocessing_summary.analyzed_count == 40
    assert parsed.preprocessing_summary.unique_count == 5

    # ADR §4: oranlar adetten türetilir.
    analyzed = parsed.preprocessing_summary.analyzed_count
    for question in parsed.top_questions:
        assert question.percentage == round(question.count / analyzed * 100, 1)

    # Faz 2: gerçek token yok.
    assert parsed.token_usage.total_tokens == 0
    assert parsed.estimated_cost_usd == 0.0

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_rapor_pii_icermez(client: AsyncClient) -> None:
    """ADR §9: örnekler redakte edilmiş olarak rapora girer."""
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id, sheet_name="Iletisim", text_column="not")
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    assert await tasks.run_analysis(analysis_id) == "completed"
    report = (await client.get(f"/api/v1/analyses/{analysis_id}/result")).json()

    serialized = str(report)
    assert "ali@example.com" not in serialized
    assert "05551234567" not in serialized
    assert "12345678901" not in serialized
    assert "[EPOSTA]" in serialized
    assert report["preprocessing_summary"]["redacted_count"] > 0

    await client.delete(f"/api/v1/uploads/{upload_id}")


# ------------------------------------------------------------ hata yolları


async def test_anahtarsiz_istek_reddedilir(client: AsyncClient) -> None:
    upload_id, _ = await _ready_upload(client)
    response = await _create(client, upload_id, key=None)

    assert response.status_code == 422
    assert response.json()["code"] == "PROVIDER_AUTH_FAILED"

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_whitelist_disi_model_reddedilir(client: AsyncClient) -> None:
    """ADR §9 değişmez 4: model yalnızca backend whitelist'inden seçilir."""
    upload_id, _ = await _ready_upload(client)
    response = await _create(client, upload_id, model="uydurma/model-9")

    assert response.status_code == 422
    assert response.json()["code"] == "SHEET_OR_COLUMN_NOT_FOUND"

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_olmayan_kolon_reddedilir(client: AsyncClient) -> None:
    upload_id, _ = await _ready_upload(client)
    response = await _create(client, upload_id, text_column="olmayan_kolon")

    assert response.status_code == 422
    assert response.json()["code"] == "SHEET_OR_COLUMN_NOT_FOUND"

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_olmayan_upload_404(client: AsyncClient) -> None:
    response = await _create(client, uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"


async def test_bilinmeyen_analiz_404(client: AsyncClient) -> None:
    analysis_id = uuid.uuid4()
    assert (await client.get(f"/api/v1/analyses/{analysis_id}")).status_code == 404
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 404
    assert (await client.delete(f"/api/v1/analyses/{analysis_id}")).status_code == 404


async def test_tamamlanmamis_isin_raporu_409(client: AsyncClient) -> None:
    """Devam eden iş için boş veri değil AÇIK hata dönülür (mock ile aynı)."""
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    response = await client.get(f"/api/v1/analyses/{analysis_id}/result")
    assert response.status_code == 409
    assert response.json()["code"] == "JOB_CONFLICT"

    await client.delete(f"/api/v1/uploads/{upload_id}")


# ------------------------------------------------------------------ iptal


async def test_iptal_edilen_is_cancelled_kalir(client: AsyncClient) -> None:
    """Plan §4 ölçüt 2 — worker iptali EZEMEZ."""
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    cancelled = await client.delete(f"/api/v1/analyses/{analysis_id}")
    assert cancelled.status_code == 204
    assert cancelled.content == b""

    job = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
    assert job["status"] == "cancelled"

    # Worker işi ŞİMDİ alsa bile durumu geri alamaz.
    assert await tasks.run_analysis(analysis_id) == "cancelled"

    job = (await client.get(f"/api/v1/analyses/{analysis_id}")).json()
    assert job["status"] == "cancelled"
    assert job["error"] is None
    # İptal edilen iş için rapor da olmamalı.
    assert (await client.get(f"/api/v1/analyses/{analysis_id}/result")).status_code == 409

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_biten_isin_iptali_409(client: AsyncClient) -> None:
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    assert await tasks.run_analysis(analysis_id) == "completed"

    response = await client.delete(f"/api/v1/analyses/{analysis_id}")
    assert response.status_code == 409
    assert response.json()["code"] == "JOB_CONFLICT"

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_iki_kez_calisan_task_raporu_bozmaz(client: AsyncClient) -> None:
    """acks_late yeniden dağıtımı aynı işi iki kez gönderebilir."""
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    assert await tasks.run_analysis(analysis_id) == "completed"
    first = (await client.get(f"/api/v1/analyses/{analysis_id}/result")).json()

    assert await tasks.run_analysis(analysis_id) == "completed"
    second = (await client.get(f"/api/v1/analyses/{analysis_id}/result")).json()

    assert first == second

    await client.delete(f"/api/v1/uploads/{upload_id}")


# --------------------------------------------------------------- anahtar


async def test_anahtar_redise_sifreli_yazilir_ve_is_bitince_silinir(
    client: AsyncClient,
) -> None:
    """Plan §4 ölçüt 5 — PostgreSQL'de ve Redis'te düz anahtar YOK."""
    from redis import Redis

    settings = get_settings()
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    redis_client = Redis.from_url(settings.redis_url)
    try:
        stored = cast("bytes | None", redis_client.get(secret_store.redis_key(analysis_id)))
        assert stored is not None
        assert TEST_KEY.encode() not in stored
        ttl = cast("int", redis_client.ttl(secret_store.redis_key(analysis_id)))
        assert 0 < ttl <= settings.openrouter_key_ttl_seconds

        # PostgreSQL'de anahtarın hiçbir izi olmamalı.
        async with session_scope() as session:
            row = (
                await session.execute(
                    text("SELECT to_jsonb(a) AS data FROM analyses a WHERE a.id = :id"),
                    {"id": str(analysis_id)},
                )
            ).one()
        assert TEST_KEY not in str(row.data)
        assert "openrouter" not in str(row.data).lower()

        assert await tasks.run_analysis(analysis_id) == "completed"

        # ADR §9: iş bitince anahtar silinir.
        assert redis_client.get(secret_store.redis_key(analysis_id)) is None
    finally:
        redis_client.delete(secret_store.redis_key(analysis_id))
        redis_client.close()

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_iptal_de_anahtari_siler(client: AsyncClient) -> None:
    from redis import Redis

    settings = get_settings()
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])

    redis_client = Redis.from_url(settings.redis_url)
    try:
        assert redis_client.get(secret_store.redis_key(analysis_id)) is not None
        await client.delete(f"/api/v1/analyses/{analysis_id}")
        assert redis_client.get(secret_store.redis_key(analysis_id)) is None
    finally:
        redis_client.delete(secret_store.redis_key(analysis_id))
        redis_client.close()

    await client.delete(f"/api/v1/uploads/{upload_id}")
