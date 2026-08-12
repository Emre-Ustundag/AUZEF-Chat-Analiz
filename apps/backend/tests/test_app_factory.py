"""App factory ortam davranışı ve dokümantasyon görünürlüğü."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.mark.parametrize("environment", ["development", "test"])
def test_docs_are_enabled_outside_production(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("AUZEF_ENVIRONMENT", environment)
    monkeypatch.delenv("AUZEF_BACKEND_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
    finally:
        get_settings.cache_clear()


def test_docs_are_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUZEF_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "AUZEF_BACKEND_MASTER_KEY",
        base64.b64encode(b"k" * 32).decode(),
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
    finally:
        get_settings.cache_clear()
