"""analyses tablosu

Faz 2 (plan §4). Job durum makinesi ve rapor gövdesi burada saklanır.

BURADA OLMAYAN SÜTUN: OpenRouter anahtarı. ADR §9 değişmezi gereği anahtar
PostgreSQL'e hiçbir biçimde yazılmaz; yalnızca Redis'te AES-GCM ile şifreli
ve TTL'li durur.

Revision ID: 0002_analyses
Revises: 0001_uploads
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_analyses"
down_revision: str | None = "0001_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Durum enum'u sözleşmedeki KÜÇÜK HARFLİ değerleri taşır
# (apps/web/src/lib/api/schemas/analysis.ts → analysisStatusSchema).
ANALYSIS_STATUS = postgresql.ENUM(
    "queued",
    "validating",
    "preprocessing",
    "analyzing",
    "aggregating",
    "completed",
    "failed",
    "cancelled",
    name="analysis_status",
    create_type=False,
)


def upgrade() -> None:
    ANALYSIS_STATUS.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", ANALYSIS_STATUS, nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        # İptal bayrağı: DELETE set eder, worker aşama sınırlarında okur.
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("sheet_name", sa.String(length=512), nullable=False),
        sa.Column("text_column", sa.String(length=512), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("top_n", sa.Integer(), nullable=False),
        sa.Column("max_cost_usd", sa.Float(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
    op.create_index("ix_analyses_status", "analyses", ["status"])
    op.create_index("ix_analyses_upload_id", "analyses", ["upload_id"])
    # ADR §9 retention: raporu 24 saat sonra silecek Faz 4 işi bunu kullanacak.
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_index("ix_analyses_upload_id", table_name="analyses")
    op.drop_index("ix_analyses_status", table_name="analyses")
    op.drop_table("analyses")
    ANALYSIS_STATUS.drop(op.get_bind(), checkfirst=True)
