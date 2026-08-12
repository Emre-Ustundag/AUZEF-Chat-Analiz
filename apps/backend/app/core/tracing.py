"""Trace id üretimi ve yayılımı.

Her cevap — hata cevapları dâhil — `X-Trace-Id` taşır ve aynı değer problem
gövdesindeki `trace_id` alanına yazılır. Frontend bunu `ApiError.traceId`
üzerinde tutar; sunucu loglarıyla eşleştirmenin tek yolu budur.
"""

from contextvars import ContextVar
from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TRACE_ID_HEADER = "X-Trace-Id"

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def current_trace_id() -> str:
    """O anki isteğin trace id'si; istek dışında boş string."""
    return _trace_id.get()


def new_trace_id() -> str:
    return str(uuid4())


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def _sanitize(raw: str | None) -> str:
    """Gelen trace id'yi yalnızca UUID ise onurlandırır.

    Bu guard olmadan istemci structured log'lara ve kullanıcıya görünen hata
    kartına (`ApiError.traceId`) serbest metin enjekte edebilir.
    """
    if not raw:
        return new_trace_id()
    try:
        return str(UUID(raw))
    except ValueError:
        return new_trace_id()


class TraceIdMiddleware:
    """Saf ASGI middleware.

    `BaseHTTPMiddleware` DEĞİL: o streaming response'ları ve background
    task'ları bozar; `GET /export` ileride binary stream edecek.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        trace_id = _sanitize(headers.get(TRACE_ID_HEADER.lower()))
        set_trace_id(trace_id)
        scope.setdefault("state", {})["trace_id"] = trace_id

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[TRACE_ID_HEADER] = trace_id
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
