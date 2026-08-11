"""Log yapılandırması ve redaksiyon filtresi.

ADR §9 değişmezi: OpenRouter anahtarı loglara ASLA yazılmaz. Faz 1'de henüz
`X-OpenRouter-Key` alan bir endpoint yok, ama filtre şimdi kuruluyor: Faz 2'de
endpoint eklendiğinde "redaksiyonu da eklemeyi hatırlamak" gerekmesin. Güvenlik
kontrolünü sonraya bırakmak, onu hiç yapmamakla aynı şeydir.

Filtre iki katmanlı çalışır:

1. Bilinen hassas alan adları (`api_key`, `authorization`, ...) `extra` ile
   gelen log kaydı alanlarından temizlenir.
2. Log metninde anahtar benzeri desenler (`sk-...`, `Bearer ...`,
   `X-OpenRouter-Key: ...`) maskelenir.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

REDACTED = "[REDACTED]"

#: `extra=` ile geçirilen sözlük anahtarlarından temizlenecek adlar.
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "openrouter_key",
        "x-openrouter-key",
        "x_openrouter_key",
        "password",
        "secret",
        "token",
    }
)

#: Serbest metinde anahtar benzeri dizileri yakalayan desenler.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(x-openrouter-key|authorization)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
)


def redact_text(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactionFilter(logging.Filter):
    """Her log kaydını yayınlanmadan önce hassas veriden arındırır."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub(None, v) for v in record.args)

        for key in list(record.__dict__):
            if key.lower() in SENSITIVE_KEYS:
                record.__dict__[key] = REDACTED

        return True

    @staticmethod
    def _scrub(key: str | None, value: Any) -> Any:
        if key is not None and key.lower() in SENSITIVE_KEYS:
            return REDACTED
        if isinstance(value, str):
            return redact_text(value)
        return value


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Kök logger'ı kurar. Birden fazla çağrı zararsızdır (API + worker)."""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    # Logger'a da ekleniyor: worker'da Celery kendi handler'ını kurabiliyor ve
    # o handler'da filtre bulunmuyor.
    if not any(isinstance(f, RedactionFilter) for f in logger.filters):
        logger.addFilter(RedactionFilter())
    return logger
