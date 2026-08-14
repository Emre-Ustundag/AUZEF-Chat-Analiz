"""Veritabanı motoru ve oturum yönetimi.

Bir tasarım kararı burada açıkça yazılıyor:

Celery task'ları SENKRON çalışır, ama plan §3.2 (a) bağımlılık listesinde
yalnızca `asyncpg` var — senkron bir sürücü (psycopg) listede YOK. İki seçenek
vardı:

  1. Worker'a psycopg ekleyip senkron bir engine kurmak → plana bağımlılık
     eklemek.
  2. Task gövdesini `asyncio.run()` ile sarmak → mevcut bağımlılıklarla kalmak.

İkincisi seçildi. Buna karşılık API ve worker tek bir veri erişim katmanı
paylaşır (ADR §3: "ayrı mikroservis kod tabanları oluşturulmaz").

BEDELİ NE — ÖLÇÜLDÜ. Burada eskiden "her task çağrısında yeni bir engine
kurulur, maliyeti ölçülemeyecek kadar küçüktür" yazıyordu. İkisi de yanlıştı:

* Engine TASK BAŞINA değil, `session_scope()` ÇAĞRISI BAŞINA kuruluyor.
  `workers/tasks.py::_chunk_progress_callback` bunu LLM chunk'ı başına
  çağırıyor; 4.000 kayıtlık bir analizde ölçülen engine sayısı **44**.
  100.000 satırlık bir dosyada bu birkaç yüz olur.
* Tek satırlık bir UPDATE, engine/çağrı ile **23.9 ms**, paylaşılan bir
  engine ile **0.8 ms** — yaklaşık **30 kat**. 250 chunk'lık bir işte ~5.8
  saniye ek yük.

Yine de bu YAPI DEĞİŞTİRİLMEDİ, çünkü 45 dakikalık bir işte 5.8 saniye %0.2
eder. İki alternatif de daha kötü:

* Process ömrü boyunca paylaşılan engine İMKANSIZ (aşağıdaki loop notu).
* `asyncio.run` kapsamı başına tek engine mümkün ama birkaç fonksiyonda
  imza değişikliği ister; %0.2 için değmez.
* İlerleme yazımını eşiğe bağlamak (ön işleme döngüsündeki gibi) bağlantı
  sayısını düşürürdü AMA iptal kontrolü de o çağrıda yapılıyor: eşik 5.0 ve
  `analyzing` aşaması 20 puan olduğundan iptal tüm aşama boyunca yalnızca 4
  kez bakılırdı. 45 dakikalık bir işte ~10 dakikada bir iptal kontrolü,
  kazanılan yarım saniyeye değmez.

Kısacası bu bilinçli bir takas; sayılar burada dursun ki bir dahaki sefere
yeniden ölçülmesin.

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

    ÇAĞRI BAŞINA bir engine demektir, task başına değil — sıcak bir döngüden
    çağırmanın bedeli ölçülü olarak modül başlığında yazılı (~23.9 ms/çağrı).
    Yeni bir çağrı yeri eklemeden önce oraya bakın.
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
