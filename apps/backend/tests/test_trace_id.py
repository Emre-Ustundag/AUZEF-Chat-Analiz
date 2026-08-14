"""Trace id üretimi, temizlenmesi ve yayılımı."""

from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.core.handlers import register_exception_handlers
from app.core.tracing import TRACE_ID_HEADER, TraceIdMiddleware, current_trace_id
from app.schemas.common import ProblemDetails

VALID = "9d8c7b6a-5e4f-4321-8abc-0123456789ab"


def test_trace_id_is_minted_when_absent(client: TestClient) -> None:
    response = client.get("/api/v1/models")
    trace_id = response.headers[TRACE_ID_HEADER]

    UUID(trace_id)  # geçerli UUID olmalı


def test_valid_inbound_trace_id_is_honoured(client: TestClient) -> None:
    response = client.get("/api/v1/models", headers={TRACE_ID_HEADER: VALID})
    assert response.headers[TRACE_ID_HEADER] == VALID


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "%0d%0aSet-Cookie:+evil=1",
        "x" * 4096,
        "",
        # Şekli doğru ama UUID olmayan; naif bir uzunluk kontrolünü geçerdi.
        "9d8c7b6a-5e4f-4321-8abc-0123456789zz",
    ],
)
def test_hostile_inbound_trace_id_is_replaced(client: TestClient, hostile: str) -> None:
    """UUID olmayan değer onurlandırılmaz.

    Bu guard olmadan istemci structured log'lara ve kullanıcıya görünen hata
    kartına (`ApiError.traceId`) serbest metin enjekte edebilirdi.
    """
    response = client.get("/api/v1/models", headers={TRACE_ID_HEADER: hostile})
    returned = response.headers[TRACE_ID_HEADER]

    UUID(returned)
    assert returned != hostile
    assert hostile not in response.text or not hostile


def test_trace_id_present_on_unhandled_error() -> None:
    """Handler'dan çıkan 500'de bile header ve gövde iz taşır."""
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/patla")
    async def patla() -> None:
        raise RuntimeError("gizli")

    with TestClient(app, raise_server_exceptions=False) as local:
        response = local.get("/patla")

    assert response.status_code == 500
    trace_id = response.headers[TRACE_ID_HEADER]
    UUID(trace_id)
    assert str(ProblemDetails.model_validate(response.json()).trace_id) == trace_id


def test_each_request_gets_a_distinct_trace_id(client: TestClient) -> None:
    first = client.get("/api/v1/models").headers[TRACE_ID_HEADER]
    second = client.get("/api/v1/models").headers[TRACE_ID_HEADER]
    assert first != second


@pytest.mark.asyncio
async def test_trace_context_is_reset_even_when_downstream_raises() -> None:
    async def failing_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        assert current_trace_id() == VALID
        raise RuntimeError("beklenen test hatası")

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: Message) -> None:
        return None

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [(TRACE_ID_HEADER.lower().encode(), VALID.encode())],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "state": {},
        },
    )

    with pytest.raises(RuntimeError, match="beklenen test hatası"):
        await TraceIdMiddleware(failing_app)(scope, receive, send)

    assert current_trace_id() == ""
