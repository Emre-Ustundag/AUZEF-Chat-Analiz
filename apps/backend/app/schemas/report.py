"""AnalysisReport — ADR-0001 §8 "Sonuç modeli".

Frontend aynası: `apps/web/src/lib/api/schemas/report.ts`.

Sayısal alanların tamamı backend'de mesajların gerçek frekanslarından
deterministik olarak hesaplanır (ADR-0001 §4). LLM yalnızca kayıt kimliklerini
kategorilere eşler, sayı üretmez.
"""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.config import MAX_ROWS
from app.schemas.analysis import PromptVersion
from app.schemas.base import ApiModel, UtcDateTime
from app.schemas.common import WarningCode

REPORT_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def percentage_half_up(count: int, total: int) -> float:
    """`count / total * 100` değerini bir ondalığa half-up yuvarlar.

    Python `round()` tie durumunda half-even kullanır; JavaScript'in pozitif
    sayılardaki `Math.round()` davranışıyla ayrışır. Tam sayı aritmetiği hem bu
    farkı hem de binary float sınır vakalarını ortadan kaldırır.
    """
    if total == 0:
        return 0.0
    tenths = (2 * count * 1000 + total) // (2 * total)
    return tenths / 10


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
    id: str = Field(min_length=1)
    canonical_question: str
    count: int = Field(ge=0)
    #: 0-100 aralığında; `preprocessing_summary.analyzed_count`'a göre hesaplanır.
    percentage: float = Field(ge=0, le=100)
    #: 0-1 aralığında model güven skoru.
    confidence: float = Field(ge=0, le=1)
    #: PII redakte edilmiş, kırpılmış gerçek kullanıcı mesajları.
    redacted_examples: list[str] = Field(default_factory=list)


class Theme(ApiModel):
    id: str = Field(min_length=1)
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

    @field_validator("code")
    @classmethod
    def _producer_uses_known_code(cls, value: str) -> str:
        """Backend üreticisi kapalı; tel/Zod tüketicisi bilinmeyene açıktır."""
        try:
            WarningCode(value)
        except ValueError as exc:
            raise ValueError("Backend yalnızca kayıtlı WarningCode üyelerini üretebilir.") from exc
        return value


class AnalysisReport(ApiModel):
    """GET /api/v1/analyses/{analysis_id}/result — yalnızca "completed" iken."""

    #: Rapor GÖVDESİNİ sürümler; API `/api/v1` + openapi.info.version ile
    #: sürümlenir (ADR-0002 #12).
    schema_version: Literal["1.0"] = REPORT_SCHEMA_VERSION
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
    #:
    #: Request tarafının aktif `ModelId` whitelist'inden bilinçli olarak ayrı:
    #: bir model emekliye ayrıldığında onunla üretilmiş tarihsel raporlar hâlâ
    #: okunabilmeli.
    model: str = Field(min_length=1)
    prompt_version: PromptVersion
    prompt_hash: str

    token_usage: TokenUsage
    estimated_cost_usd: float = Field(ge=0)
    """Analizi üreten çalışmanın maliyeti — BİLEREK katalogla doğrulanmıyor.

    Değer, raporun üretildiği ANDAKİ fiyatlarla hesaplanır ve rapora yazılır
    (BE-02, yazma yolu). Burada `catalog.estimate_cost_usd` ile yeniden
    hesaplayıp karşılaştırmak, OpenRouter fiyatı her değiştiğinde geçmiş
    raporların tamamını okunamaz hale getirirdi: cevap doğrulaması düşer,
    `GET /analyses/{id}/result` kalıcı 500 döner. Aynı gerekçe modelin
    whitelist'te olma şartı için de geçerli — bir modeli kullanımdan
    kaldırmak, onunla üretilmiş raporları silmek anlamına gelmemeli.
    """

    @model_validator(mode="after")
    def _report_invariants(self) -> Self:
        prep = self.preprocessing_summary
        considered = min(self.source_summary.total_rows, MAX_ROWS)
        if prep.analyzed_count + prep.discarded_count != considered:
            raise ValueError(
                "analyzed_count + discarded_count, işlenen satır sayısına eşit olmalı."
            )
        if prep.unique_count + prep.duplicate_count != prep.analyzed_count:
            raise ValueError("unique_count + duplicate_count, analyzed_count'a eşit olmalı.")
        if prep.redacted_count > prep.analyzed_count:
            raise ValueError("redacted_count, analyzed_count'u aşamaz.")

        usage = self.token_usage
        if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
            raise ValueError("total_tokens, prompt_tokens + completion_tokens olmalı.")

        question_ids = [question.id for question in self.top_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("top_questions id'leri benzersiz olmalı.")

        theme_ids = [theme.id for theme in self.themes]
        if len(theme_ids) != len(set(theme_ids)):
            raise ValueError("themes id'leri benzersiz olmalı.")

        for question in self.top_questions:
            if question.count > prep.analyzed_count:
                raise ValueError("Soru/tema count değeri analyzed_count'u aşamaz.")
            if question.percentage != percentage_half_up(question.count, prep.analyzed_count):
                raise ValueError("Soru/tema percentage değeri count'tan half-up türetilmeli.")
        for theme in self.themes:
            if theme.count > prep.analyzed_count:
                raise ValueError("Soru/tema count değeri analyzed_count'u aşamaz.")
            if theme.percentage != percentage_half_up(theme.count, prep.analyzed_count):
                raise ValueError("Soru/tema percentage değeri count'tan half-up türetilmeli.")

        present_ids = set(question_ids)
        if any(not set(theme.related_question_ids) <= present_ids for theme in self.themes):
            raise ValueError("related_question_ids yalnızca top_questions id'lerini içerebilir.")
        if any(
            len(theme.related_question_ids) != len(set(theme.related_question_ids))
            for theme in self.themes
        ):
            raise ValueError("related_question_ids aynı soru id'sini tekrarlayamaz.")

        truncated = self.source_summary.total_rows > MAX_ROWS
        has_warning = any(
            warning.code == WarningCode.ROW_LIMIT_TRUNCATED for warning in self.warnings
        )
        if truncated is not has_warning:
            raise ValueError("ROW_LIMIT_TRUNCATED uyarısı satır sınırıyla uyumlu olmalı.")
        return self
