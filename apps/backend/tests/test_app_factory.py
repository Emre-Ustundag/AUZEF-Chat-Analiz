"""App factory ortam davranışı ve dokümantasyon görünürlüğü."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.tracing import TRACE_ID_HEADER
from app.main import create_app

CORS_ORIGIN = "http://localhost:3000"


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


def test_cross_origin_response_exposes_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """`X-Trace-Id` expose edilmezse frontend `ApiError.traceId`'yi okuyamaz.

    Safelist dışındaki her cevap header'ı, `Access-Control-Expose-Headers`
    saymadıkça cross-origin JS'e görünmez; header cevaba yazılmış olsa bile.
    """
    monkeypatch.setenv("AUZEF_ENVIRONMENT", "development")
    monkeypatch.setenv("AUZEF_CORS_ORIGINS", f'["{CORS_ORIGIN}"]')
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health/live", headers={"Origin": CORS_ORIGIN})
    finally:
        get_settings.cache_clear()

    assert response.headers["access-control-allow-origin"] == CORS_ORIGIN
    exposed = {
        header.strip().lower()
        for header in response.headers["access-control-expose-headers"].split(",")
    }
    assert TRACE_ID_HEADER.lower() in exposed
    assert response.headers[TRACE_ID_HEADER]


def test_unhandled_error_keeps_cors_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """500 `ServerErrorMiddleware`'den çıkar — CORS middleware'inin dışından.

    Header'lar elle eklenmezse cross-origin istemci problem gövdesini de
    trace id'yi de okuyamaz ve hatanın izi kaybolur.
    """
    monkeypatch.setenv("AUZEF_ENVIRONMENT", "development")
    monkeypatch.setenv("AUZEF_CORS_ORIGINS", f'["{CORS_ORIGIN}"]')
    get_settings.cache_clear()
    try:
        app = create_app()

        @app.get("/patlayan-test-ucu")
        async def patlayan_test_ucu() -> None:
            raise RuntimeError("beklenmeyen")

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/patlayan-test-ucu", headers={"Origin": CORS_ORIGIN})
            foreign = client.get("/patlayan-test-ucu", headers={"Origin": "http://kotu.example"})
    finally:
        get_settings.cache_clear()

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == CORS_ORIGIN
    assert TRACE_ID_HEADER.lower() in response.headers["access-control-expose-headers"].lower()

    # Allow-list dışındaki origin header almaz; tarayıcı cevabı zaten bloklar.
    assert foreign.status_code == 500
    assert "access-control-allow-origin" not in foreign.headers
