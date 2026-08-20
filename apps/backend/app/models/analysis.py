"""`analyses` tablosu — plan §4.

ADR §2: PostgreSQL job durumunun KALICI GERÇEK KAYNAĞIDIR. Durum Celery'nin
result backend'inden değil her zaman bu tablodan okunur.

Burada OLMAYAN bir sütun bilinçli bir karardır: OpenRouter anahtarı. ADR §9
değişmezi "OpenRouter key PostgreSQL'e veya loglara yazılmaz" diyor; anahtar
yalnızca Redis'te AES-GCM ile şifreli ve TTL'li durur
(`app/services/secret_store.py`). Bu tabloda anahtarın kendisi değil,
yalnızca Redis kaydına işaret eden dolaylı bir referans (`id`) vardır.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.analysis import AnalysisStatus


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: Upload silinirse analiz de gider: rapor kaynağı olmadan anlamsızdır ve
    #: ADR §9'un retention kuralı zaten ikisini birlikte siler.
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[AnalysisStatus] = mapped_column(
        # `values_callable` olmadan SQLAlchemy Python enum ADLARINI yazar;
        # sözleşme küçük harfli DEĞERLERİ gerektiriyor.
        Enum(
            AnalysisStatus,
            name="analysis_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AnalysisStatus.QUEUED,
        index=True,
    )

    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: İptal bayrağı. DELETE bunu set eder; worker AŞAMA SINIRLARINDA okur.
    #: Durumu doğrudan "cancelled" yapmak yetmezdi: worker o sırada bir aşamanın
    #: ortasındaysa kendi durum yazımıyla iptali ezerdi.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ---- istek parametreleri (sözleşmedeki AnalysisRequest alanları) ----
    sheet_name: Mapped[str] = mapped_column(String(512), nullable=False)
    text_column: Mapped[str] = mapped_column(String(512), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)

    #: Veri kümesi ön ayarı (`DatasetType` değeri). Enum değil String: yeni
    #: bir ön ayar eklemek migration gerektirmemeli — doğrulama şemada.
    dataset_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="GENERIC", server_default="GENERIC"
    )

    #: Yalnızca `CHATBOT_LOG` işlerinde dolu. `ChatbotLogConfig` şemasına uyar.
    chatbot_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: Yalnızca status "completed" iken dolu. AnalysisReport şemasına uyar.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

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
        # Kolon adı ve dosya adı bilinçli olarak yok: log'a kullanıcı verisi
        # sızmasın.
        return f"<Analysis id={self.id} status={self.status}>"
