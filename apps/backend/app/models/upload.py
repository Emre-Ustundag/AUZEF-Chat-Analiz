"""`uploads` tablosu — plan §3.2 (c).

ADR §2 değişmezi: "PostgreSQL job durumunun kalıcı gerçek kaynağıdır; Redis
yalnızca kuyruk, kısa süreli lock ve TTL secret için kullanılır." Yani durum
Celery'nin result backend'inden değil, HER ZAMAN bu tablodan okunur.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.upload import UploadStatus


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    status: Mapped[UploadStatus] = mapped_column(
        # `values_callable` olmadan SQLAlchemy Python enum ADLARINI ("QUEUED")
        # yazar; sözleşme ise küçük harfli DEĞERLERİ ("queued") gerektiriyor.
        Enum(
            UploadStatus,
            name="upload_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=UploadStatus.QUEUED,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)

    #: BigInteger: 150 MB bugün int32'ye sığar ama sınır config'tir ve
    #: büyütülebilir; tip yüzünden migration gerekmesin.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Object storage anahtarı. ASLA kullanıcının dosya adından türetilmez
    #: (path traversal); UUID öneki kullanılır — bkz. services/storage.py.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    #: Yalnızca status "ready" iken dolu. UploadProfile şemasına uyar.
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: Yalnızca status "failed" iken dolu. ProblemDetails şemasına uyar.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - hata ayıklama kolaylığı
        # Dosya adı bilinçli olarak yok: log'a kullanıcı verisi sızmasın.
        return f"<Upload id={self.id} status={self.status}>"
