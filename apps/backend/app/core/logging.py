"""JSON log yapılandırması, redaksiyon ve HTTP erişim logları."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from time import perf_counter
from typing import Final, TextIO, cast

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import EventDict, Processor, WrappedLogger

from app.core.config import Settings, get_settings
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

#: Serbest METİNDE anahtar benzeri dizileri yakalayan desenler.
#:
#: Anahtar bazlı maskeleme (`_SENSITIVE_EXACT`) yalnızca sözlük ANAHTARINA
#: bakar; anahtarın kendisi bir istisna metnine ya da `event` dizesine
#: düştüğünde yakalamaz. ADR-0001 §9 "API anahtarı loglara ASLA yazılmaz"
#: diyor ve o cümle değerin nereden geldiğine bakmıyor.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\b(x-openrouter-key|authorization)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
)


def redact_text(text: str) -> str:
    """Serbest metindeki anahtar benzeri dizileri maskeler."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


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
    if isinstance(value, str):
        # Anahtar bazlı maskelemenin göremediği yer: değerin KENDİSİ. Bir
        # istisna metni ya da `event` dizesi anahtarı taşıyorsa burada silinir.
        return redact_text(value)
    return value


def redact_sensitive(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Hassas anahtarları iç içe yapılarda da maskeleyen structlog processor."""
    return cast(EventDict, _redact(event_dict))


def configure_logging(settings: Settings | None = None) -> None:
    """Process logging'ini doğrulanmış settings ile tek biçime getirir."""
    settings = settings or get_settings()
    level = getattr(logging, settings.log_level)

    #: structlog ve stdlib kayıtlarının PAYLAŞTIĞI ön işlemler. Redaksiyonun
    #: burada olması şart: aksi hâlde yalnızca structlog çağrıları maskelenir.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Render etme, stdlib handler'ına devret: böylece structlog
            # kayıtları ile uvicorn/warnings kayıtları TEK formatter'dan geçer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # `foreign_pre_chain` olmadan structlog kullanmayan her kayıt (uvicorn.error
    # startup satırları, `warnings`, ileride SQLAlchemy/Celery) aynı stdout'a
    # DÜZ METİN düşerdi ve JSON parse eden log toplayıcıda akış bozulurdu.
    handler = _DynamicStdoutHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(sort_keys=True),
            ],
        )
    )
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # uvicorn.access INFO logları tam URL'i (query string dâhil) taşır;
    # uygulamanın kendi saf-ASGI request logu güvenli tek erişim kaydıdır.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx istemcileri istek URL'ini INFO'da loglar ve o URL rutin olarak
    # secret taşır. Logger'ı tamamen kapatmak provider HATALARINI da gizlerdi;
    # yalnızca INFO gürültüsü susturulur, WARNING ve üstü görünür kalır.
    for http_client_logger in ("httpx", "httpx2"):
        logging.getLogger(http_client_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Uygulama modülleri için yapılandırılmış logger döndürür."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


class RedactionFilter(logging.Filter):
    """Üçüncü taraf logging handler'larında da hassas alanları maskele."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in tuple(record.__dict__.items()):
            record.__dict__[key] = REDACTED if _is_sensitive_key(key) else _redact(value)
        return True


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
