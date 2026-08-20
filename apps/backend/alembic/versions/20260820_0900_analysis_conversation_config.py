"""Analiz modu ve konuşma eşleme yapılandırması.

Revision ID: 0006_conversation_config
Revises: 0005_analysis_pricing_snapshot
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_conversation_config"
down_revision: str | None = "0005_analysis_pricing_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mevcut analizlerin tamamı tek-mesaj semantiğiyle oluşturuldu. Hem eski
    # satırları doldurmak hem de rolling deploy sırasında eski API process'inin
    # insert yapabilmesini korumak için server default kalıcıdır.
    op.add_column(
        "analyses",
        sa.Column(
            "analysis_mode",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'message'"),
        ),
    )
    op.add_column(
        "analyses",
        sa.Column(
            "conversation_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "conversation_config")
    op.drop_column("analyses", "analysis_mode")
