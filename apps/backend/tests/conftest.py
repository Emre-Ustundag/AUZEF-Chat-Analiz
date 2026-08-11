"""Pytest yapılandırması.

Testler iki kümeye ayrılır:

* ALTYAPISIZ testler (`test_xlsx.py`, `test_contract.py`, `test_redaction.py`)
  — hiçbir servis gerektirmez, her yerde çalışır. Faz 1'in asıl değeri
  bunlarda: güvenlik kontrolleri ve sözleşme uyumu.
* ENTEGRASYON testleri (`integration` işaretli) — Postgres, Redis ve MinIO
  ister. `docker compose up -d` sonrası çalışırlar; servisler kapalıysa
  SESSİZCE ATLANMAZ, açık bir skip mesajıyla atlanır.

Servis adresleri compose'un dışarı açtığı localhost portlarına çevrilir:
compose ağı içindeki `postgres`/`minio` adları host makineden çözülmez.
"""

from __future__ import annotations

import os
import socket

import pytest

# Ayarlar lru_cache'li olduğu için uygulama modülleri import EDİLMEDEN ÖNCE
# yazılmalı; sonrasında yazmak hiçbir şeyi değiştirmez.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://auzef:auzef@127.0.0.1:5432/auzef")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
os.environ.setdefault("S3_ENDPOINT_URL", "http://127.0.0.1:9000")
os.environ.setdefault("S3_BUCKET", "auzef-test")


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


REQUIRED_SERVICES = {
    "postgres": 5432,
    "redis": 6379,
    "minio": 9000,
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
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
