"""Upload ve profil şemaları.

SÖZLEŞME KAYNAĞI: `apps/web/src/lib/api/schemas/upload.ts`. Alan adları,
zorunluluk ve nullable durumları oradaki Zod şemalarıyla BİREBİR aynıdır.
Buradaki bir alan adını değiştirmek frontend'i çalışır görünüp veri
gösteremeyen bir duruma sokar.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import ProblemDetails
from app.schemas.common import UtcDatetime


class UploadStatus(StrEnum):
    """ADR §6: queued/validating/ready/failed."""

    QUEUED = "queued"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"


class ColumnProfile(BaseModel):
    """Tek bir kolonun veri profili — `columnProfileSchema` karşılığı."""

    name: str
    #: Sayfadaki 0 TABANLI kolon sırası.
    index: int = Field(ge=0)
    non_empty_count: int = Field(ge=0)
    empty_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    avg_length: float = Field(ge=0)
    is_likely_text: bool
    #: ADR §9: redakte edilmiş ve kırpılmış örnekler. Ham öğrenci mesajı
    #: kolon seçim ekranında gösterilmez.
    sample_values: list[str] = Field(default_factory=list)


class SheetProfile(BaseModel):
    name: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)


class UploadProfile(BaseModel):
    sheets: list[SheetProfile] = Field(default_factory=list)
    total_row_count: int = Field(ge=0)
    #: ADR §9 satır sınırı aşıldıysa true. İş BAŞARISIZ OLMAZ, işaretlenir.
    exceeds_row_limit: bool = False


class UploadRead(BaseModel):
    """GET /api/v1/uploads/{upload_id} cevabı.

    `profile` yalnızca status "ready" iken, `error` yalnızca "failed" iken
    doludur. İkisi de frontend'de `.nullable()` — yani açık `null` göndermek
    doğrudur (ProblemDetails'ın `retry_after` alanının aksine).
    """

    upload_id: UUID
    status: UploadStatus
    filename: str
    size_bytes: int = Field(ge=0)
    created_at: UtcDatetime
    profile: UploadProfile | None = None
    error: ProblemDetails | None = None


class UploadCreated(BaseModel):
    """POST /api/v1/uploads — 202 Accepted cevabı."""

    upload_id: UUID
    status: UploadStatus
