"""Analiz job şemaları.

SÖZLEŞME KAYNAĞI: `apps/web/src/lib/api/schemas/analysis.ts`. Alan adları,
zorunluluk ve nullable durumları oradaki Zod şemalarıyla BİREBİR aynıdır.

Dikkat edilen iki nokta:

* `estimated_seconds_remaining` ve `error` frontend'de `.nullable()` — yani
  açık `null` göndermek DOĞRUDUR (ProblemDetails'ın `retry_after` alanının
  aksine, orada alan hiç gönderilmemelidir).
* `progress` `z.number().min(0).max(100)`; tamsayı zorunluluğu yok ama
  sınırların dışına çıkmak Zod'da hata demektir.

OpenRouter anahtarı bu şemaların HİÇBİRİNDE yer almaz (ADR §6/§9): yalnızca
`X-OpenRouter-Key` header'ında taşınır.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import ProblemDetails
from app.schemas.common import UtcDatetime


class AnalysisStatus(StrEnum):
    """ADR §6 job durum makinesi.

    `queued → validating → preprocessing → analyzing → aggregating → completed`
    Terminal: `failed`, `cancelled`.
    """

    QUEUED = "queued"
    VALIDATING = "validating"
    PREPROCESSING = "preprocessing"
    ANALYZING = "analyzing"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Terminal durumlar; frontend `isAnalysisSettled` ile aynı küme.
TERMINAL_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {AnalysisStatus.COMPLETED, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}
)

#: İlerleme ekranındaki adım sırası (`ANALYSIS_ACTIVE_STAGES`). Değerler her
#: aşamanın BAŞLANGICINDAKİ yüzdedir; bir aşamanın içinde ilerleme, o aşama
#: ile bir sonrakinin değeri arasında doğrusal olarak yazılır.
#: Gerçek iş yükü preprocessing/analyzing'de yoğunlaştığı için ağırlıklar
#: eşit değil.
STAGE_PROGRESS: dict[AnalysisStatus, float] = {
    AnalysisStatus.QUEUED: 0.0,
    AnalysisStatus.VALIDATING: 10.0,
    AnalysisStatus.PREPROCESSING: 35.0,
    AnalysisStatus.ANALYZING: 75.0,
    AnalysisStatus.AGGREGATING: 95.0,
    AnalysisStatus.COMPLETED: 100.0,
}


class AnalysisCreate(BaseModel):
    """POST /api/v1/analyses gövdesi — `analysisRequestSchema` karşılığı."""

    upload_id: UUID
    sheet_name: str = Field(min_length=1)
    text_column: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    #: Zod: `z.int().min(1).max(100)`.
    top_n: int = Field(ge=1, le=100)
    #: Zod: `z.number().positive().max(100)`.
    max_cost_usd: float = Field(gt=0, le=100)


class AnalysisCreated(BaseModel):
    """POST /api/v1/analyses — 202 Accepted cevabı."""

    analysis_id: UUID
    status: AnalysisStatus


class AnalysisJobRead(BaseModel):
    """GET /api/v1/analyses/{analysis_id} — `analysisJobSchema` karşılığı."""

    analysis_id: UUID
    status: AnalysisStatus
    progress: float = Field(ge=0, le=100)
    created_at: UtcDatetime
    updated_at: UtcDatetime
    #: Terminal durumlarda `null`. Frontend `.nullable()` ilan ediyor.
    estimated_seconds_remaining: float | None = None
    #: Yalnızca status "failed" iken dolu.
    error: ProblemDetails | None = None
