"""JSON log yapılandırması, redaksiyon ve HTTP erişim logları."""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from time import perf_counter
from typing import Final, TextIO, cast

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import EventDict, WrappedLogger

from app.core.config import Settings
from app.core.tracing import current_trace_id

REDACTED: Final = "[REDACTED]"
_SENSITIVE_EXACT: Final = frozenset(
    {
        "authorization",
        "backend-master-key",
        "body",
        "cookie",
        "set-cookie",
        "x-openrouter-key",
    }
)
_SENSITIVE_PARTS: Final = ("api-key", "message-text", "password", "secret", "token")


class _DynamicStdoutHandler(logging.StreamHandler[TextIO]):
    """Pytest capture dâhil her emit'te güncel stdout'u kullanır."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("_", "-")
    return normalized in _SENSITIVE_EXACT or any(part in normalized for part in _SENSITIVE_PARTS)


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def redact_sensitive(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Hassas anahtarları iç içe yapılarda da maskeleyen structlog processor."""
    return cast(EventDict, _redact(event_dict))


def configure_logging(settings: Settings) -> None:
    """Process logging'ini doğrulanmış settings ile tek biçime getirir."""
    level = getattr(logging, settings.log_level)
    handler = _DynamicStdoutHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Bu istemcilerin INFO access logları tam URL (query string dâhil) taşır.
    # Uygulamanın kendi saf-ASGI request logu güvenli tek erişim kaydıdır.
    for noisy_logger in ("httpx", "httpx2", "uvicorn.access"):
        logging.getLogger(noisy_logger).disabled = True
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_sensitive,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


class RequestLoggingMiddleware:
    """Header, query string ve gövde okumadan güvenli HTTP erişim logu üretir."""

    def __init__(self, app: ASGIApp, *, environment: str) -> None:
        self.app = app
        self.environment = environment
        self.logger = structlog.get_logger("auzef.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            self.logger.info(
                "request_completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                environment=self.environment,
                trace_id=current_trace_id(),
            )
