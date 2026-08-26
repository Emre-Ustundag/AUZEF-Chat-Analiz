"""Ortak fixture'lar ve altyapı gerektiren testlerin servis kontrolü."""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------- izolasyon
#
# TESTLER GELİŞTİRİCİNİN ORTAMINDAN TAMAMEN YALITILIR.
#
# `core/config.py` repo kökündeki `.env`'i ÇALIŞMA DİZİNİNDEN BAĞIMSIZ olarak
# okuyor (`_repo_env_file()`, modül yolundan türetiliyor). Bu geliştirici
# kolaylığı olarak doğru ama testlere sızıyordu ve bir GÜVENLİK testini
# sessizce etkisizleştiriyordu:
#
#   `test_master_key_degisirse_cozulemez` iki farklı master key ile aynı
#   kaydın çözülemediğini iddia ediyor. Ama ikinci Settings'i
#   `secret_encryption_key=...` ile kuruyor ve `config.py` `backend_master_key`
#   alanına ÖNCELİK veriyor. `.env` içinde `AUZEF_BACKEND_MASTER_KEY` varsa
#   (yani `docker compose` çalıştıran her geliştiricide) iki taraf da aynı
#   anahtarı türetiyor, kayıt çözülüyor ve test düşüyordu.
#
# CI'da `.env` olmadığı için orası yeşil kalıyordu; kırmızı olan yalnızca
# yerel makineydi. İki yönlü temizlik yapılıyor:
#
#   1. Kabuktan gelen `AUZEF_*` değişkenleri silinir (aşağıdaki
#      `setdefault` çağrıları ancak böyle GERÇEKTEN varsayılan olur).
#   2. `.env` dosyası devre dışı bırakılır.
#
# Tek istisna test altyapısının kendi port ayarıdır. Makinede 5432 başka bir
# projeye aitse test PostgreSQL'i farklı bir host portuna bağlanabilir; değer
# uygulama Settings'ine sızmadan önce ortamdan çıkarılır.
_test_postgres_port = int(os.environ.pop("AUZEF_TEST_POSTGRES_PORT", "5432"))
for _leaked in [key for key in os.environ if key.startswith("AUZEF_")]:
    del os.environ[_leaked]

from app.core.config import Settings, get_settings  # noqa: E402

Settings.model_config["env_file"] = None
get_settings.cache_clear()

# Settings AUZEF_ önekini kullanır; uygulama modülleri import edilmeden önce
# host makineden erişilebilen test adreslerini kur.
os.environ.setdefault(
    "AUZEF_DATABASE_URL",
    f"postgresql+asyncpg://auzef:auzef@127.0.0.1:{_test_postgres_port}/auzef",
)
os.environ.setdefault("AUZEF_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("AUZEF_CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("AUZEF_CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
os.environ.setdefault("AUZEF_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
os.environ.setdefault("AUZEF_S3_BUCKET", "auzef-test")
# Hiçbir test yanlışlıkla gerçek OpenRouter'a bağlanmasın.
os.environ.setdefault("AUZEF_OPENROUTER_BASE_URL", "http://127.0.0.1:9/api/v1")
# Birim/entegrasyon testleri dış fiyat kataloğuna bağlanmaz. Canlı
# katalog davranışı `test_pricing.py` içinde MockTransport ile ayrı test edilir.
os.environ.setdefault("AUZEF_PRICING_REFRESH_ENABLED", "false")

from app.main import create_app  # noqa: E402


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


FIXTURE_DIR = repo_root() / "tests" / "fixtures" / "contract"
OPENAPI_PATH = repo_root() / "docs" / "api" / "openapi.json"


def read_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def manifest() -> Any:
    return read_fixture("manifest.json")


@pytest.fixture(scope="session")
def openapi() -> Any:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


REQUIRED_SERVICES = {"postgres": _test_postgres_port, "redis": 6379, "minio": 9000}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    missing = [
        name for name, port in REQUIRED_SERVICES.items() if not _port_open("127.0.0.1", port)
    ]
    if not missing:
        return

    skip = pytest.mark.skip(
        reason=(
            f"Entegrasyon testleri için gerekli servisler kapalı: {', '.join(missing)}. "
            "`docker compose up -d` ile başlatın."
        )
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
