"""analyses tablosuna satır filtreleri

Revision ID: 0004_analysis_row_filters
Revises: 0003_retention_indeksleri
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_analysis_row_filters"
down_revision: str | None = "0003_retention_indeksleri"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mevcut analizler filtresizdir. server_default migration sırasında
    # eski satırları doldurur ve eski API process'lerinin kısa bir rolling
    # deploy penceresinde insert yapabilmesini de korur.
    op.add_column(
        "analyses",
        sa.Column(
            "row_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "row_filters")
