"""uploads tablosu

Faz 1'in tek tablosu (plan §3.2 c). Analiz tabloları Faz 2'de eklenecek.

Revision ID: 0001_uploads
Revises:
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_uploads"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Durum enum'u sözleşmedeki KÜÇÜK HARFLİ değerleri taşır
# (apps/web/src/lib/api/schemas/upload.ts → uploadStatusSchema).
UPLOAD_STATUS = postgresql.ENUM(
    "queued",
    "validating",
    "ready",
    "failed",
    name="upload_status",
    create_type=False,
)


def upgrade() -> None:
    UPLOAD_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("status", UPLOAD_STATUS, nullable=False, server_default="queued"),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        # JSONB: sorgulanabilir ve sıkıştırılmış saklama (ADR §2).
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Durum sorgulaması polling'in sıcak yolu.
    op.create_index("ix_uploads_status", "uploads", ["status"])
    # ADR §9 retention: 24 saati geçen kayıtları temizleyecek Faz 4 işi bu
    # indeksi kullanacak.
    op.create_index("ix_uploads_created_at", "uploads", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_uploads_created_at", table_name="uploads")
    op.drop_index("ix_uploads_status", table_name="uploads")
    op.drop_table("uploads")
    UPLOAD_STATUS.drop(op.get_bind(), checkfirst=True)
