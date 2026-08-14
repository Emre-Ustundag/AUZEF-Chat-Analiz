"""Retention süpürmesinin filtrelediği kolona indeks ekle.

0001 ve 0002 `created_at` üzerinde indeks kuruyor ve yorumları bunların Faz 4
retention işi için olduğunu söylüyordu. Yanlış kolon: üç süpürme de
`updated_at` filtreliyor (`services/retention.py`).

Gerekçe `retention.py` modül başlığında yazılı — terminal kayıtlarda
`updated_at` etkin olarak `settled_at`'tir ve retention'ın saydığı şey "kayıt
ne zamandan beri hareketsiz". `created_at` retention için yanlış saat.

Sonuç, tablolar büyüdükçe her süpürmenin sequential scan olmasıydı; süpürücü
saatte bir koştuğu için bu sessizce birikirdi.

`created_at` indeksleri DÜŞÜRÜLMÜYOR: bu revizyon davranışı düzeltmekle
sınırlı. O kolon şu an hiçbir sorguda filtre değil (yalnızca cevap
serileştirmesinde okunuyor), yani düşürülebilir — ama bu ayrı bir karar ve
ayrı bir revizyona ait.

Revision ID: 0003_retention_indeksleri
Revises: 0002_analyses
"""

from __future__ import annotations

from alembic import op

revision: str = "0003_retention_indeksleri"
down_revision: str | None = "0002_analyses"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ADR §9 retention: süpürme sorguları `updated_at < cutoff` filtreliyor
    # (`retention.py` içindeki üç `sweep_*` fonksiyonu).
    op.create_index("ix_uploads_updated_at", "uploads", ["updated_at"])
    op.create_index("ix_analyses_updated_at", "analyses", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_analyses_updated_at", table_name="analyses")
    op.drop_index("ix_uploads_updated_at", table_name="uploads")
