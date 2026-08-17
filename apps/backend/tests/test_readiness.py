"""Gerçek bağımlılık readiness kontrolleri — ADR §2.

`test_health.py` kontrol SÖZLEŞMESİNİ sahte kontrollerle ölçüyor (paralellik,
süre bütçesi, sızıntı yok). Bu dosya, sözleşmenin PostgreSQL/Redis/object
storage uygulamalarının GERÇEKTEN çalıştığını ölçüyor — ikisi ayrı sorular ve
ayrı ayrı yanlış olabilirler.

Tarihsel bağlam: bu kontroller kayıtlı değilken `/api/v1/health/ready` HER
ZAMAN `503` dönüyordu. Mekanizma yazılmış ve test edilmişti; eksik olan
uygulamaların kaydıydı, yani gerçek bir orchestrator'da readiness probe hiçbir
zaman geçmezdi.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.main import create_app
from app.services import storage
from app.services.readiness import (
    ObjectStorageReadiness,
    PostgresReadiness,
    RedisReadiness,
    default_readiness_checks,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _bucket() -> None:
    storage.ensure_bucket(get_settings())


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    await dispose_engine()
    app = create_app(readiness_checks=default_readiness_checks())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


def test_varsayilan_kontroller_uc_bagimliligi_kapsar() -> None:
    """Üçü de zorunlu: biri yoksa API job oluşturamaz ya da dosya yazamaz."""
    names = [check.name for check in default_readiness_checks()]

    assert names == ["postgres", "redis", "object-storage"]


async def test_postgres_kontrolu_gercek_baglantiyi_dogrular() -> None:
    assert await PostgresReadiness().check() is True


async def test_redis_kontrolu_gercek_baglantiyi_dogrular() -> None:
    assert await RedisReadiness().check() is True


async def test_object_storage_kontrolu_bucketi_dogrular() -> None:
    assert await ObjectStorageReadiness().check() is True


async def test_object_storage_kontrolu_olmayan_bucketta_duser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sadece TCP bağlantısı denenmiyor.

    Endpoint ayakta ama bucket yoksa upload'lar 500 ile düşer; readiness bunu
    "hazır" diye raporlasaydı orchestrator bozuk bir process'e trafik verirdi.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "s3_bucket", "bu-bucket-yok-12345", raising=False)

    with pytest.raises(Exception):  # noqa: B017 - boto3 ClientError alt tipi
        await ObjectStorageReadiness().check()


async def test_ready_ucu_gercek_bagimliliklarla_200_doner(client: AsyncClient) -> None:
    """Uçtan uca: kayıtlı kontroller + çalışan servisler = 200 + isim listesi."""
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": [
            {"name": "postgres", "status": "ok"},
            {"name": "redis", "status": "ok"},
            {"name": "object-storage", "status": "ok"},
        ],
    }


async def test_bagimlilik_dusunce_ready_503_doner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Erişilemeyen Redis `/ready`'i 503 yapar — ham hata metni sızmadan."""
    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0", raising=False)

    await dispose_engine()
    app = create_app(readiness_checks=default_readiness_checks())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/health/ready")
    finally:
        await dispose_engine()

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_NOT_READY"
    assert "127.0.0.1:1" not in response.text
