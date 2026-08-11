"""AnalysisReport şeması — ADR §8.

SÖZLEŞME KAYNAĞI: `apps/web/src/lib/api/schemas/report.ts`.

ADR §4'ün en kritik kararı burada görünür hâle geliyor: bu gövdedeki
sayısal alanların tamamı backend'de mesajların GERÇEK frekanslarından
hesaplanır. Sınıflandırıcı (Faz 2'de deterministik vekil, Faz 3'te LLM)
yalnızca kayıt→kategori eşlemesi üretir; `count`, `percentage`,
`analyzed_count` gibi hiçbir alana dokunmaz.

`status` alanı `Literal["completed"]`: frontend `z.literal("completed")`
bekliyor, yani tamamlanmamış bir iş için bu gövde ÜRETİLEMEZ. `/result`
endpoint'i tamamlanmamış işte 409 döner.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

#: Rapor gövdesinin sürümü. Alan eklendiğinde/çıkarıldığında artırılır.
REPORT_SCHEMA_VERSION = "1.0"


class SourceSummary(BaseModel):
    filename: str
    sheet_name: str
    text_column: str
    total_rows: int = Field(ge=0)


class PreprocessingSummary(BaseModel):
    """Ön işleme sayaçları — plan §4 "Ön işleme".

    Aralarındaki değişmez ilişki (`test_aggregate.py` bunu doğruluyor):

        analyzed_count + discarded_count == total_rows
        duplicate_count == analyzed_count - unique_count
        redacted_count <= analyzed_count
    """

    #: Boş/sistem kayıtları elendikten SONRA analize giren kayıt sayısı.
    analyzed_count: int = Field(ge=0)
    discarded_count: int = Field(ge=0)
    #: Tekilleştirmeyle elenen tekrar sayısı; frekansları korunur.
    duplicate_count: int = Field(ge=0)
    #: İçinde PII maskelenmiş kayıt sayısı.
    redacted_count: int = Field(ge=0)
    #: Tekilleştirme sonrası sınıflandırıcıya giden benzersiz kayıt sayısı.
    unique_count: int = Field(ge=0)


class TopQuestion(BaseModel):
    id: str
    canonical_question: str
    count: int = Field(ge=0)
    #: 0-100. `count / analyzed_count` üzerinden hesaplanır, modelden gelmez.
    percentage: float = Field(ge=0, le=100)
    #: 0-1. Faz 2'de grup içi tutarlılıktan türetilir (bkz. pipeline/classifier.py).
    confidence: float = Field(ge=0, le=1)
    redacted_examples: list[str] = Field(default_factory=list)


class Theme(BaseModel):
    id: str
    name: str
    #: Temaya düşen TÜM mesajların sayısı. Plan §1.2: `top_n` kırpması bu
    #: değeri DEĞİŞTİRMEZ, yalnızca `related_question_ids`'i filtreler.
    count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    #: Plan §1.2: yalnızca raporda gerçekten yer alan soru kimlikleri.
    related_question_ids: list[str] = Field(default_factory=list)


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AnalysisWarning(BaseModel):
    code: str
    message: str


class AnalysisReport(BaseModel):
    """GET /api/v1/analyses/{analysis_id}/result gövdesi."""

    schema_version: str = REPORT_SCHEMA_VERSION
    analysis_id: UUID
    status: Literal["completed"] = "completed"
    generated_at: UtcDatetime

    source_summary: SourceSummary
    preprocessing_summary: PreprocessingSummary

    top_questions: list[TopQuestion] = Field(default_factory=list)
    themes: list[Theme] = Field(default_factory=list)

    executive_summary: str
    warnings: list[AnalysisWarning] = Field(default_factory=list)

    model: str
    prompt_version: str
    prompt_hash: str

    token_usage: TokenUsage
    #: Faz 2'de gerçek token tüketimi yok; 0 raporlanır (plan §4).
    estimated_cost_usd: float = Field(ge=0)
