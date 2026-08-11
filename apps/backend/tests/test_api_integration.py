"""Upload akışının uçtan uca testi — plan §3.3.

Postgres, Redis ve MinIO gerektirir (`docker compose up -d`).

Worker BROKER ÜZERİNDEN çalıştırılmaz: `profile_upload.delay` devre dışı
bırakılıp iş mantığı doğrudan `await` edilir. Sebebi, `task_always_eager`
veya gerçek bir worker kullanmanın testi zamanlamaya bağımlı kılması —
o zaman test "yeterince bekledim mi" sorusuna dönüşür ve rastgele patlar.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import ProblemDetails
from app.main import create_app
from app.schemas.upload import UploadRead
from app.services import storage
from app.workers import tasks

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"

#: `common.ts` → `errorCodeSchema`. Cevapların bu kümenin dışına çıkmaması
#: frontend'in hata ekranının çalışmasının ön koşulu.
FRONTEND_ERROR_CODES = {
    "UPLOAD_TOO_LARGE",
    "UPLOAD_INVALID_TYPE",
    "UPLOAD_CORRUPT_OR_ENCRYPTED",
    "SHEET_OR_COLUMN_NOT_FOUND",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_BAD_RESPONSE",
    "PROVIDER_TIMEOUT",
    "JOB_NOT_FOUND",
    "JOB_CONFLICT",
    "INTERNAL_ERROR",
}


@pytest.fixture(scope="module", autouse=True)
def _bucket() -> None:
    storage.ensure_bucket(get_settings())


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Celery'ye gerçekten iş göndermeyi engeller.

    Aksi hâlde compose'daki worker AYNI kaydı işler ve testin kendi
    çağrısıyla yarışır: iki taraf da aynı satırı güncellemeye çalışır.
    """
    monkeypatch.setattr(tasks.profile_upload, "delay", lambda *a, **k: None)
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Test istemcisi.

    Her testten SONRA engine atılıyor. Sebebi: `app.core.db` API için
    paylaşılan bir engine cache'liyor, pytest-asyncio ise her teste YENİ bir
    event loop veriyor. asyncpg bağlantıları oluşturuldukları loop'a bağlı
    olduğu için ikinci test "attached to a different loop" ile patlıyordu —
    üretimde tek ve uzun ömürlü bir loop olduğundan bu yalnızca test
    ortamına özgü bir durum.
    """
    await dispose_engine()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


async def _upload(client: AsyncClient, path: Path, filename: str | None = None) -> Any:
    with path.open("rb") as handle:
        response = await client.post(
            "/api/v1/uploads",
            files={"file": (filename or path.name, handle, "application/octet-stream")},
        )
    return response


# ------------------------------------------------------------------ /models


async def test_models_endpoint_sozlesmeye_uyar(client: AsyncClient) -> None:
    response = await client.get("/api/v1/models")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"models", "default_model", "default_prompt_version"}
    assert body["default_model"] in {m["id"] for m in body["models"]}

    for model in body["models"]:
        assert set(model) == {
            "id",
            "label",
            "input_cost_per_million",
            "output_cost_per_million",
            "context_window",
        }
        assert model["context_window"] > 0


# ------------------------------------------------------------ mutlu yol


async def test_gecerli_dosya_yuklenip_profillenir(client: AsyncClient) -> None:
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx")

    assert response.status_code == 202
    created = response.json()
    assert set(created) == {"upload_id", "status"}
    assert created["status"] == "queued"

    upload_id = uuid.UUID(created["upload_id"])

    # Worker'ı doğrudan çalıştır (broker yok, zamanlama yok).
    final_status = await tasks.run_upload_profiling(upload_id)
    assert final_status == "ready"

    read = await client.get(f"/api/v1/uploads/{upload_id}")
    assert read.status_code == 200

    body = read.json()
    assert body["status"] == "ready"
    assert body["error"] is None
    assert body["profile"] is not None

    # Cevap gerçekten sözleşme şemasına oturmalı.
    parsed = UploadRead.model_validate(body)
    assert parsed.profile is not None
    assert len(parsed.profile.sheets) == 3

    # created_at Z ile bitmeli — Zod'un z.iso.datetime()'ı offset kabul etmez.
    assert body["created_at"].endswith("Z")

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_profil_ornekleri_redakte_edilmis_gelir(client: AsyncClient) -> None:
    """ADR §9: ham öğrenci verisi kolon seçim ekranına ulaşmamalı."""
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx")
    upload_id = uuid.UUID(response.json()["upload_id"])
    await tasks.run_upload_profiling(upload_id)

    body = (await client.get(f"/api/v1/uploads/{upload_id}")).json()
    iletisim = next(s for s in body["profile"]["sheets"] if s["name"] == "Iletisim")
    joined = " ".join(iletisim["columns"][0]["sample_values"])

    assert "ali@example.com" not in joined
    assert "05551234567" not in joined
    assert "[EPOSTA]" in joined

    await client.delete(f"/api/v1/uploads/{upload_id}")


# ------------------------------------------------------------- hata yolları


@pytest.mark.parametrize("fixture_name", ["corrupt.xlsx", "encrypted.xlsx", "macro_enabled.xlsx"])
async def test_bozuk_dosyalar_failed_olur(client: AsyncClient, fixture_name: str) -> None:
    """Bozuk/şifreli/makrolu dosya POST'ta değil, WORKER'da reddedilir.

    POST 202 döner (dosya henüz açılmadı); hata polling ile GET'ten gelir.
    Mock'un davranışı da budur — sözleşme aynı kalmalı.
    """
    response = await _upload(client, FIXTURES / fixture_name)
    assert response.status_code == 202

    upload_id = uuid.UUID(response.json()["upload_id"])
    final_status = await tasks.run_upload_profiling(upload_id)
    assert final_status == "failed"

    body = (await client.get(f"/api/v1/uploads/{upload_id}")).json()
    assert body["status"] == "failed"
    assert body["profile"] is None

    error = body["error"]
    assert error["code"] == "UPLOAD_CORRUPT_OR_ENCRYPTED"
    assert error["status"] == 422
    assert error["code"] in FRONTEND_ERROR_CODES
    # retry_after `.optional()` — null gönderilirse Zod reddeder.
    assert "retry_after" not in error

    ProblemDetails.model_validate(error)

    await client.delete(f"/api/v1/uploads/{upload_id}")


async def test_xlsx_olmayan_uzanti_415(client: AsyncClient) -> None:
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx", filename="veri.csv")

    assert response.status_code == 415
    body = response.json()
    assert body["code"] == "UPLOAD_INVALID_TYPE"
    assert body["code"] in FRONTEND_ERROR_CODES
    assert "retry_after" not in body


async def test_bos_dosya_reddedilir(client: AsyncClient, tmp_path: Path) -> None:
    empty = tmp_path / "bos.xlsx"
    empty.write_bytes(b"")

    response = await _upload(client, empty)
    assert response.status_code == 415
    assert response.json()["code"] == "UPLOAD_INVALID_TYPE"


async def test_boyut_siniri_asilirsa_413(client: AsyncClient, tmp_path: Path) -> None:
    """Sınır ayarlardan okunuyor; sabit kodlanmadığının kanıtı."""
    settings = get_settings()
    oversize = tmp_path / "buyuk.xlsx"
    oversize.write_bytes(b"\x00" * (settings.max_upload_bytes + 1024))

    response = await _upload(client, oversize)
    assert response.status_code == 413

    body = response.json()
    assert body["code"] == "UPLOAD_TOO_LARGE"
    assert body["code"] in FRONTEND_ERROR_CODES


async def test_bilinmeyen_id_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/uploads/{uuid.uuid4()}")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "JOB_NOT_FOUND"
    assert set(body) >= {"type", "title", "status", "code", "detail", "trace_id", "errors"}


async def test_gecersiz_uuid_bicimi_sozlesmeye_uyan_hata_doner(client: AsyncClient) -> None:
    """Yol parametresi UUID değilse bile gövde RFC 9457 olmalı.

    FastAPI'nin varsayılan `{"detail": [...]}` gövdesi frontend'in
    `problemDetailsSchema` doğrulamasından geçemezdi.
    """
    response = await client.get("/api/v1/uploads/gecersiz-kimlik")

    assert response.status_code in {415, 422}
    body = response.json()
    assert body["code"] in FRONTEND_ERROR_CODES
    assert "trace_id" in body


# ------------------------------------------------------------------ DELETE


async def test_delete_204_ve_bos_govde(client: AsyncClient) -> None:
    """Frontend client.ts status===204'te gövdeyi HİÇ okumuyor."""
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx")
    upload_id = uuid.UUID(response.json()["upload_id"])

    deleted = await client.delete(f"/api/v1/uploads/{upload_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""

    assert (await client.get(f"/api/v1/uploads/{upload_id}")).status_code == 404


async def test_silinen_upload_worker_da_cokmez(client: AsyncClient) -> None:
    """Kullanıcı iş kuyrukta beklerken DELETE çağırabilir."""
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx")
    upload_id = uuid.UUID(response.json()["upload_id"])

    await client.delete(f"/api/v1/uploads/{upload_id}")

    assert await tasks.run_upload_profiling(upload_id) == "missing"


async def test_iki_kez_calisan_task_sonucu_bozmaz(client: AsyncClient) -> None:
    """acks_late yeniden dağıtımı aynı işi iki kez gönderebilir."""
    response = await _upload(client, FIXTURES / "valid_multi_sheet.xlsx")
    upload_id = uuid.UUID(response.json()["upload_id"])

    assert await tasks.run_upload_profiling(upload_id) == "ready"
    # İkinci çalıştırma terminal durumu görüp erken dönmeli.
    assert await tasks.run_upload_profiling(upload_id) == "ready"

    body = (await client.get(f"/api/v1/uploads/{upload_id}")).json()
    assert body["status"] == "ready"
    assert body["profile"] is not None

    await client.delete(f"/api/v1/uploads/{upload_id}")
