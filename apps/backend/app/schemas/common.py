"""Şemalar arasında paylaşılan yardımcı tipler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def to_iso_z(value: datetime) -> str:
    """Datetime'ı `2026-08-11T12:34:56.789Z` biçimine çevirir.

    NEDEN GEREKLİ: frontend `created_at` alanını `z.iso.datetime()` ile
    doğruluyor ve bu doğrulayıcı VARSAYILAN OLARAK offset kabul etmiyor —
    yalnızca `Z` ile biten UTC damgasını geçerli sayıyor.

    Python'un `datetime.isoformat()` çıktısı ise:
      * tz-aware UTC değerde  → `...+00:00`  (Zod REDDEDER)
      * naive değerde         → zaman dilimi hiç yok (Zod REDDEDER)

    Yani bu dönüşüm olmadan upload polling'i sessizce çöker: HTTP 200 döner,
    Zod gövdeyi reddeder ve kullanıcı boş ekran görür.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


#: Sözleşmede geçen tüm zaman damgaları bu tiple işaretlenir.
UtcDatetime = Annotated[datetime, PlainSerializer(to_iso_z, return_type=str)]


def utc_now() -> datetime:
    return datetime.now(UTC)
