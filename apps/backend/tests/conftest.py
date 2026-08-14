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

# Settings AUZEF_ önekini kullanır; uygulama modülleri import edilmeden önce
# host makineden erişilebilen test adreslerini kur.
os.environ.setdefault("AUZEF_DATABASE_URL", "postgresql+asyncpg://auzef:auzef@127.0.0.1:5432/auzef")
os.environ.setdefault("AUZEF_REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("AUZEF_CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("AUZEF_CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
os.environ.setdefault("AUZEF_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
os.environ.setdefault("AUZEF_S3_BUCKET", "auzef-test")
# Hiçbir test yanlışlıkla gerçek OpenRouter'a bağlanmasın.
os.environ.setdefault("AUZEF_OPENROUTER_BASE_URL", "http://127.0.0.1:9/api/v1")

from app.main import create_app


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


REQUIRED_SERVICES = {"postgres": 5432, "redis": 6379, "minio": 9000}


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
