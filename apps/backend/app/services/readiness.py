"""Gerçek bağımlılık readiness kontrolleri — ADR §2 "health endpoint'leri".

`services/health.py` kontrol SÖZLEŞMESİNİ (paralel çalıştırma, süre bütçesi,
hata sızdırmama) tanımlıyor; bu modül o sözleşmeyi PostgreSQL, Redis ve object
storage için uyguluyor.

## Neden ayrı bir modül

Kontroller `create_app`'in içine gömülseydi, uygulama fabrikası üç istemci
kütüphanesini de import etmek zorunda kalırdı ve testler `/ready`'i gerçek
bağımlılıklar olmadan koşturamazdı. `create_app(readiness_checks=...)`
enjeksiyonu bilinçli: process giriş noktası (`main.py` sonundaki `app`)
buradaki gerçek kontrolleri geçirir, testler kendi sahtelerini.

## Neden her kontrol kendi istemcisini kuruyor

Readiness'in cevaplaması gereken soru "ŞU AN bağlanabiliyor muyum". Uzun
ömürlü, açık bir bağlantıyı yeniden kullanmak bu soruyu cevaplamaz: havuzdaki
bağlantı canlı görünürken sunucu çoktan gitmiş olabilir. Postgres tarafında
paylaşılan engine'in havuzu kullanılıyor ama `pool_pre_ping=True` (bkz.
`core/db.py`) ölü bağlantıyı kontrolden önce eliyor.

Senkron istemciler (`redis-py`, `boto3`) `asyncio.to_thread` ile çağrılıyor:
event loop'u bloklamak, tek bir yavaş bağımlılığın TÜM isteklere yayılması
demekti. Süre bütçesi zaten `run_readiness_checks` tarafından uygulanıyor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from redis import Redis
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.services.health import ReadinessCheck
from app.services.storage import build_s3_client


@dataclass(frozen=True, slots=True)
class PostgresReadiness:
    """`select 1` — job durumu ve raporlar olmadan API iş göremez."""

    name: str = "postgres"

    async def check(self) -> bool:
        async with get_engine().connect() as connection:
            await connection.execute(text("select 1"))
        return True


@dataclass(frozen=True, slots=True)
class RedisReadiness:
    """`PING` — kuyruk, BYOK secret'ı ve idempotency kayıtları burada."""

    name: str = "redis"

    async def check(self) -> bool:
        settings = get_settings()

        def ping() -> bool:
            return bool(Redis.from_url(settings.redis_url).ping())

        return await asyncio.to_thread(ping)


@dataclass(frozen=True, slots=True)
class ObjectStorageReadiness:
    """`head_bucket` — upload'ın yazılacağı bucket gerçekten erişilebilir mi.

    Yalnızca endpoint'e TCP bağlantısı denenmiyor: kimlik bilgileri yanlışsa
    veya bucket yoksa upload'lar 500 ile düşerdi ve readiness bunu "hazır"
    diye raporlardı.
    """

    name: str = "object-storage"

    async def check(self) -> bool:
        settings = get_settings()

        def head() -> bool:
            build_s3_client(settings).head_bucket(Bucket=settings.s3_bucket)
            return True

        return await asyncio.to_thread(head)


def default_readiness_checks(settings: Settings | None = None) -> tuple[ReadinessCheck, ...]:
    """API process'inin zorunlu bağımlılıkları.

    Sıra cevaptaki `checks` listesine yansır (`run_readiness_checks` sırayı
    korur); en temelden en türevine doğru okunacak şekilde sıralı.
    """
    del settings  # Kontroller ayarı çağrı anında okur; imza simetri için var.
    return (PostgresReadiness(), RedisReadiness(), ObjectStorageReadiness())
