"""Analiz fiyat snapshot'ı.

Revision ID: 0005_analysis_pricing_snapshot
Revises: 0004_analysis_row_filters
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_analysis_pricing_snapshot"
down_revision: str | None = "0004_analysis_row_filters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "pricing_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "pricing_snapshot")
