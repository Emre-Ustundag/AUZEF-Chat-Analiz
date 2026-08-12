"""Analiz job şemaları — ADR-0001 §5 "Aşama B" ve §6.

Frontend aynası: `apps/web/src/lib/api/schemas/analysis.ts`.
"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, ApiRequestModel, UtcDateTime
from app.schemas.common import ProblemDetails


class AnalysisStatus(StrEnum):
    """queued -> validating -> preprocessing -> analyzing -> aggregating -> completed

    Terminal: completed, failed, cancelled.
    """

    QUEUED = "queued"
    VALIDATING = "validating"
    PREPROCESSING = "preprocessing"
    ANALYZING = "analyzing"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_ANALYSIS_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {AnalysisStatus.COMPLETED, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}
)


class ExportFormat(StrEnum):
    XLSX = "xlsx"
    JSON = "json"


class AnalysisRequest(ApiRequestModel):
    """POST /api/v1/analyses gövdesi.

    OpenRouter anahtarı BU GÖVDEDE YER ALMAZ — yalnızca `X-OpenRouter-Key`
    header'ında taşınır (ADR-0001 §6/§9). `RequestValidationError` handler'ı
    `errors[].input` alanını asla yankılamaz; anahtar yanlışlıkla gövdeye
    konursa geri sızdırılmaması için (ADR-0002 #7).
    """

    upload_id: UUID
    sheet_name: str = Field(min_length=1)
    text_column: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    top_n: int = Field(ge=1, le=100)
    #: gt, ge değil: Zod tarafı `.positive()`.
    max_cost_usd: float = Field(gt=0, le=100)


class AnalysisCreated(ApiModel):
    """POST /api/v1/analyses — 202 Accepted."""

    analysis_id: UUID
    status: Literal[AnalysisStatus.QUEUED] = AnalysisStatus.QUEUED


class AnalysisJob(ApiModel):
    """GET /api/v1/analyses/{analysis_id} — durum sorgulaması.

    ADR-0001 §2: ilerleme her satırda değil, yalnızca aşama veya anlamlı yüzde
    değişiminde yazılır; `progress` düzenli artmayabilir.
    """

    analysis_id: UUID
    status: AnalysisStatus
    progress: float = Field(ge=0, le=100)
    created_at: UtcDateTime
    updated_at: UtcDateTime
    #: Terminal olmayan durumlarda kalan süre tahmini, saniye.
    estimated_seconds_remaining: float | None = Field(default=None, ge=0)
    #: Yalnızca status "failed" iken dolu.
    error: ProblemDetails | None = None

    @model_validator(mode="after")
    def _payload_matches_status(self) -> Self:
        if self.status is AnalysisStatus.FAILED:
            if self.error is None:
                raise ValueError("failed analysis job error taşımak zorunda.")
        elif self.error is not None:
            raise ValueError("Yalnızca failed analysis job error taşıyabilir.")

        if (
            self.status in TERMINAL_ANALYSIS_STATUSES
            and self.estimated_seconds_remaining is not None
        ):
            raise ValueError("Terminal analysis job kalan süre tahmini taşıyamaz.")
        return self


class ModelOption(ApiModel):
    """GET /api/v1/models öğesi.

    ADR-0001 §6 "model yalnızca backend whitelist'inden seçilebilir" diyordu
    ama listeyi döndüren endpoint'i tanımlamıyordu. ADR-0002 #1 ile
    `GET /api/v1/models` sözleşmeye dâhil edildi.
    """

    id: str
    label: str
    #: 1M girdi tokenı başına USD.
    input_cost_per_million: float = Field(ge=0)
    output_cost_per_million: float = Field(ge=0)
    context_window: int = Field(gt=0)


class ModelList(ApiModel):
    """GET /api/v1/models"""

    models: list[ModelOption]
    default_model: str
    default_prompt_version: str

    @model_validator(mode="after")
    def _defaults_are_available(self) -> Self:
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Model whitelist'i tekrarlı id taşıyamaz.")
        if self.default_model not in ids:
            raise ValueError("default_model whitelist içinde bulunmalı.")
        if not self.default_prompt_version:
            raise ValueError("default_prompt_version boş olamaz.")
        return self
