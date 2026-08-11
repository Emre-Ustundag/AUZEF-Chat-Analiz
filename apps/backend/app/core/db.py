"""Veritabanı motoru ve oturum yönetimi.

Bir tasarım kararı burada açıkça yazılıyor:

Celery task'ları SENKRON çalışır, ama plan §3.2 (a) bağımlılık listesinde
yalnızca `asyncpg` var — senkron bir sürücü (psycopg) listede YOK. İki seçenek
vardı:

  1. Worker'a psycopg ekleyip senkron bir engine kurmak → plana bağımlılık
     eklemek.
  2. Task gövdesini `asyncio.run()` ile sarmak → mevcut bağımlılıklarla kalmak.

İkincisi seçildi. Bedeli, her task çağrısında yeni bir event loop ve yeni bir
engine kurulmasıdır; upload profilleme dakikalar süren bir iş olduğu için bu
kurulum maliyeti ölçülemeyecek kadar küçüktür. Buna karşılık API ve worker
tek bir veri erişim katmanı paylaşır (ADR §3: "ayrı mikroservis kod tabanları
oluşturulmaz").

Worker'ın engine'i process ömrü boyunca PAYLAŞILMAZ: asyncpg bağlantıları
oluşturuldukları event loop'a bağlıdır ve `asyncio.run()` her çağrıda loop'u
kapatır. Paylaşılan bir engine ikinci task'ta "attached to a different loop"
hatası verirdi.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """API process'i için paylaşılan engine (uzun ömürlü, havuzlu)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI bağımlılığı."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Worker için tek seferlik oturum.

    Kendi engine'ini kurar ve İŞİ BİTİNCE ATAR (modül başlığındaki event loop
    gerekçesi). API'nin havuzlu engine'i burada kullanılmaz.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=None, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def dispose_engine() -> None:
    """Uygulama kapanışında havuzu temizler."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
