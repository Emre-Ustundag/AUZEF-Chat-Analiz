"""AnalysisReport — ADR-0001 §8 "Sonuç modeli".

Frontend aynası: `apps/web/src/lib/api/schemas/report.ts`.

Sayısal alanların tamamı backend'de mesajların gerçek frekanslarından
deterministik olarak hesaplanır (ADR-0001 §4). LLM yalnızca kayıt kimliklerini
kategorilere eşler, sayı üretmez.
"""

from math import isclose
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.catalog import estimate_cost_usd, find_model
from app.core.config import get_settings
from app.schemas.base import ApiModel, UtcDateTime
from app.schemas.common import WarningCode


class SourceSummary(ApiModel):
    filename: str
    sheet_name: str
    text_column: str
    total_rows: int = Field(ge=0)


class PreprocessingSummary(ApiModel):
    #: Analize giren kayıt sayısı (boş/sistem kayıtları elendikten sonra).
    analyzed_count: int = Field(ge=0)
    discarded_count: int = Field(ge=0)
    #: Exact hash ile tekilleştirilen kayıt sayısı; frekansları korunur.
    duplicate_count: int = Field(ge=0)
    redacted_count: int = Field(ge=0)
    #: Tekilleştirme sonrası LLM'e giden benzersiz kayıt sayısı.
    unique_count: int = Field(ge=0)


class TopQuestion(ApiModel):
    id: str
    canonical_question: str
    count: int = Field(ge=0)
    #: 0-100 aralığında; `preprocessing_summary.analyzed_count`'a göre hesaplanır.
    percentage: float = Field(ge=0, le=100)
    #: 0-1 aralığında model güven skoru.
    confidence: float = Field(ge=0, le=1)
    #: PII redakte edilmiş, kırpılmış gerçek kullanıcı mesajları.
    redacted_examples: list[str] = Field(default_factory=list)


class Theme(ApiModel):
    id: str
    name: str
    #: Temaya düşen TÜM mesajlar — top_n kırpmasından etkilenmez.
    count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    related_question_ids: list[str] = Field(default_factory=list)
    """ADR-0002 #5: `top_questions` içinde GERÇEKTEN yer alan id'lere filtrelenir.

    ADR-0001 §8 bu alanın top_n kırpmasından sonra ne göstereceğini
    tanımlamıyordu. Karar: yalnızca raporda bulunan sorulara bağlanır; aksi
    hâlde arayüz çözemeyeceği bir kimliğe bağlantı verirdi. `count` ve
    `percentage` ise temanın gerçek büyüklüğünü yansıtmaya devam eder, yani
    bir temanın adedi listelenen sorularının toplamından büyük olabilir.
    """


class TokenUsage(ApiModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AnalysisWarning(ApiModel):
    """İşi durdurmayan uyarı."""

    code: str = Field(
        json_schema_extra={"examples": [c.value for c in WarningCode]},
    )
    """Tel üstünde serbest `str`, üretimde `WarningCode` sözlüğüyle sınırlı.

    Üretici-kapalı, tüketici-açık (ADR-0002 #2): yeni bir uyarı kodu eklemek
    frontend'i kırmaz.
    """

    message: str
    """KULLANICIYA HAZIR TÜRKÇE metin.

    `common.ts` "ham backend metni kullanıcıya doğrudan basılmaz" diyor;
    uyarılar bunun belgelenmiş tek istisnasıdır (ADR-0002 #2). Kod serbest
    string olduğu için frontend bilinmeyen bir koda mesaj uyduramaz ve
    gizlenen bir uyarı, kusurlu bir uyarıdan kötüdür.
    """


class AnalysisReport(ApiModel):
    """GET /api/v1/analyses/{analysis_id}/result — yalnızca "completed" iken."""

    #: Rapor GÖVDESİNİ sürümler; API `/api/v1` + openapi.info.version ile
    #: sürümlenir (ADR-0002 #12).
    schema_version: str
    analysis_id: UUID
    status: Literal["completed"] = "completed"
    generated_at: UtcDateTime

    source_summary: SourceSummary
    preprocessing_summary: PreprocessingSummary

    top_questions: list[TopQuestion]
    themes: list[Theme]

    executive_summary: str
    warnings: list[AnalysisWarning] = Field(default_factory=list)

    #: İzlenebilirlik: hangi model ve hangi prompt sürümü bu sonucu üretti.
    model: str
    prompt_version: str
    prompt_hash: str

    token_usage: TokenUsage
    estimated_cost_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def _report_invariants(self) -> Self:
        prep = self.preprocessing_summary
        considered = min(self.source_summary.total_rows, get_settings().max_rows)
        if prep.analyzed_count + prep.discarded_count != considered:
            raise ValueError(
                "analyzed_count + discarded_count, işlenen satır sayısına eşit olmalı."
            )
        if prep.unique_count + prep.duplicate_count != prep.analyzed_count:
            raise ValueError("unique_count + duplicate_count, analyzed_count'a eşit olmalı.")

        usage = self.token_usage
        if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
            raise ValueError("total_tokens, prompt_tokens + completion_tokens olmalı.")

        if find_model(self.model) is None:
            raise ValueError("Rapor modeli backend whitelist'inde bulunmalı.")
        expected_cost = estimate_cost_usd(
            self.model,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        if not isclose(self.estimated_cost_usd, expected_cost, abs_tol=0.0001):
            raise ValueError("estimated_cost_usd, katalog fiyatlarından hesaplanmalı.")

        present_ids = {question.id for question in self.top_questions}
        if any(not set(theme.related_question_ids) <= present_ids for theme in self.themes):
            raise ValueError("related_question_ids yalnızca top_questions id'lerini içerebilir.")

        truncated = self.source_summary.total_rows > get_settings().max_rows
        has_warning = any(
            warning.code == WarningCode.ROW_LIMIT_TRUNCATED for warning in self.warnings
        )
        if truncated is not has_warning:
            raise ValueError("ROW_LIMIT_TRUNCATED uyarısı satır sınırıyla uyumlu olmalı.")
        return self
