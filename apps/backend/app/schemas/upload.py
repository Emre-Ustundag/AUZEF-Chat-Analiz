"""Upload şemaları — ADR-0001 §5 "Aşama A".

Frontend aynası: `apps/web/src/lib/api/schemas/upload.ts`.
"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.config import MAX_ROWS
from app.schemas.base import ApiModel, UtcDateTime
from app.schemas.common import ProblemDetails


class UploadStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"


class ColumnProfile(ApiModel):
    name: str
    #: 0 tabanlı kolon indeksi.
    index: int = Field(ge=0)
    non_empty_count: int = Field(ge=0)
    empty_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    avg_length: float = Field(ge=0)
    is_likely_text: bool
    sample_values: list[str] = Field(default_factory=list)


class SheetProfile(ApiModel):
    name: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile]

    @model_validator(mode="after")
    def _counts_match_rows(self) -> Self:
        if self.column_count != len(self.columns):
            raise ValueError("column_count, columns uzunluğuyla aynı olmalı.")
        for column in self.columns:
            if column.non_empty_count + column.empty_count != self.row_count:
                raise ValueError(
                    f"{column.name}: non_empty_count + empty_count, row_count'a eşit olmalı."
                )
            if column.unique_count > column.non_empty_count:
                raise ValueError(f"{column.name}: unique_count, non_empty_count'u aşamaz.")
        return self


class UploadProfile(ApiModel):
    sheets: list[SheetProfile]
    total_row_count: int = Field(ge=0)
    exceeds_row_limit: bool = False
    """ADR-0002 #2: satır sınırı hard reject DEĞİL.

    Upload her zaman tam profillenir ve bu bayrak set edilir. Bu yüzden sınır
    aşımı için ayrı bir ErrorCode yoktur.

    ANLAMI: "bu dosya büyük — analiz uzun sürer ve pahalıdır". KIRPMA DEĞİL.
    Eskiden "analiz ilk `MAX_ROWS` satırı işler" deniyordu ama worker hiçbir
    zaman öyle çalışmadı; çelişki 100.000 satırı aşan her dosyada rapor
    doğrulamasını düşürüyordu. Analiz her zaman tüm satırları işler.
    """

    @model_validator(mode="after")
    def _profile_invariants(self) -> Self:
        if self.total_row_count != sum(sheet.row_count for sheet in self.sheets):
            raise ValueError("total_row_count, tüm sheet satırlarının toplamı olmalı.")
        exceeds = any(sheet.row_count > MAX_ROWS for sheet in self.sheets)
        if self.exceeds_row_limit is not exceeds:
            raise ValueError("exceeds_row_limit, sheet satır limitlerinden türetilmeli.")
        return self


class Upload(ApiModel):
    """GET /api/v1/uploads/{upload_id}"""

    upload_id: UUID
    status: UploadStatus
    filename: str
    size_bytes: int = Field(ge=0)
    created_at: UtcDateTime
    #: Yalnızca status "ready" iken dolu.
    profile: UploadProfile | None = None
    #: Yalnızca status "failed" iken dolu.
    error: ProblemDetails | None = None

    @model_validator(mode="after")
    def _payload_matches_status(self) -> Self:
        if self.status is UploadStatus.READY:
            if self.profile is None or self.error is not None:
                raise ValueError("ready upload profile taşımalı ve error taşımamalı.")
        elif self.status is UploadStatus.FAILED:
            if self.error is None or self.profile is not None:
                raise ValueError("failed upload error taşımalı ve profile taşımamalı.")
        elif self.profile is not None or self.error is not None:
            raise ValueError("Aktif upload durumları profile veya error taşıyamaz.")
        return self


class UploadCreated(ApiModel):
    """POST /api/v1/uploads — 202 Accepted."""

    upload_id: UUID
    status: Literal[UploadStatus.QUEUED] = UploadStatus.QUEUED


UploadRead = Upload
