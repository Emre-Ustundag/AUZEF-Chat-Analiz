"""Alembic ortamı.

Asenkron sürücüyle çalışır: plan §3.2 (a) bağımlılık listesinde yalnızca
`asyncpg` var, senkron bir Postgres sürücüsü yok. Bu yüzden migration'lar
`async_engine_from_config` yerine doğrudan `create_async_engine` ile
kuruluyor ve `run_sync` içinde uygulanıyor.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings

# Modeller import EDİLMELİ: Base.metadata ancak sınıflar yüklendikten sonra
# tabloları içerir, aksi hâlde autogenerate boş migration üretir.
from app.models.base import Base
from app.models.upload import Upload

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
