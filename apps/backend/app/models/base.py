"""SQLAlchemy 2 taban sınıfı."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Tüm ORM modellerinin ortak tabanı.

    Alembic'in `target_metadata`'sı buradan okunur; bir model bu tabandan
    türemezse autogenerate onu görmez.
    """
