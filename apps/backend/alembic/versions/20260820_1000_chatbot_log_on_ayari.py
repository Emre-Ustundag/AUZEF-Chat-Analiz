"""`analyses` tablosuna veri kümesi ön ayarı kolonlarını ekle.

CHATBOT_LOG ön ayarı (plan B2) analiz isteğine iki yeni parametre getiriyor:
`dataset_type` ve kolon eşlemesini taşıyan `chatbot_config`. Worker bu
değerleri istek anında değil İŞ KOŞARKEN okuduğu için kalıcı olmaları şart —
Redis'e koymak, kuyrukta bekleyen bir işin yapılandırmasını TTL'e emanet
etmek olurdu.

`dataset_type` String, Postgres enum DEĞİL: `analysis_status` enum'unun
aksine bu değer worker tarafında yalnızca dallanma anahtarı ve yeni bir ön
ayar eklemek şema migration'ı gerektirmemeli. Doğrulama Pydantic şemasında.

Mevcut satırlar `GENERIC` varsayılanını alır — bugüne kadarki her analiz
tanım gereği generic'ti.

Revision ID: 0004_chatbot_log_on_ayari
Revises: 0003_retention_indeksleri
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004_chatbot_log_on_ayari"
down_revision: str | None = "0003_retention_indeksleri"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("dataset_type", sa.String(32), nullable=False, server_default="GENERIC"),
    )
    op.add_column("analyses", sa.Column("chatbot_config", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "chatbot_config")
    op.drop_column("analyses", "dataset_type")
