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

# Worker ilerlemesi aşama sınırlarında bu tek tablodan yazılır.
STAGE_PROGRESS: dict[AnalysisStatus, float] = {
    AnalysisStatus.QUEUED: 0.0,
    AnalysisStatus.VALIDATING: 5.0,
    AnalysisStatus.PREPROCESSING: 20.0,
    AnalysisStatus.ANALYZING: 40.0,
    AnalysisStatus.AGGREGATING: 90.0,
    AnalysisStatus.COMPLETED: 100.0,
    AnalysisStatus.FAILED: 100.0,
    AnalysisStatus.CANCELLED: 100.0,
}

# Faz 3/4 kodunun eski adları; public şema adları yukarıdaki dondurulmuş
# sözleşmede kalır.
TERMINAL_STATUSES = TERMINAL_ANALYSIS_STATUSES


class ExportFormat(StrEnum):
    XLSX = "xlsx"
    JSON = "json"


class DatasetType(StrEnum):
    """Veri kümesi ön ayarı.

    `GENERIC` düz metin kolonlu dosyalar içindir ve mevcut davranışı korur.
    `CHATBOT_LOG` gerçek chatbot dökümleri içindir: satırlar gönderen/rol
    kolonuna göre ön filtrelenir (bot cevapları ve sistem olayları analize
    girmez), oturum ve zaman kolonları rapora session/time-series metrikleri
    ekler.
    """

    GENERIC = "GENERIC"
    CHATBOT_LOG = "CHATBOT_LOG"


class ChatbotLogConfig(ApiRequestModel):
    """`CHATBOT_LOG` ön ayarının kolon eşlemesi.

    Metin kolonu burada DEĞİL: `AnalysisRequest.text_column` iki ön ayarda da
    aynı anlamı taşıyor, ikinci bir kopya sessizce ayrışabilirdi.
    """

    #: Gönderen/rol kolonu (örn. `direction`). Filtrelemenin temeli.
    role_column: str = Field(min_length=1)
    #: Kullanıcı satırı sayılan değerler (örn. `["Kullanıcı", "user"]`).
    #: Karşılaştırma boşluk kırpılıp küçük/büyük harf duyarsız yapılır.
    role_user_values: list[str] = Field(min_length=1, max_length=20)
    #: Oturum kimliği kolonu; verilirse rapora oturum sayıları eklenir.
    session_id_column: str | None = Field(default=None, min_length=1)
    #: Mesaj zamanı kolonu; verilirse rapora günlük zaman serisi eklenir.
    timestamp_column: str | None = Field(default=None, min_length=1)
    #: Mesaj tipi kolonu (örn. `message_type`); `allowed_message_types` ile
    #: birlikte verilir.
    message_type_column: str | None = Field(default=None, min_length=1)
    #: İzin verilen mesaj tipleri (örn. `["text"]`).
    allowed_message_types: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def _message_type_fields_pair(self) -> Self:
        if (self.message_type_column is None) is not (self.allowed_message_types is None):
            raise ValueError("message_type_column ve allowed_message_types birlikte verilmelidir.")
        for values in (self.role_user_values, self.allowed_message_types or []):
            if any(not value.strip() for value in values):
                raise ValueError("Filtre değerleri boş olamaz.")
        return self


class ModelId(StrEnum):
    """BE-01'de dondurulmuş, structured-output destekli model whitelist'i."""

    CLAUDE_SONNET_4_6 = "anthropic/claude-sonnet-4.6"
    GPT_4_1_MINI = "openai/gpt-4.1-mini"
    GEMINI_2_5_FLASH = "google/gemini-2.5-flash"
    GLM_5_2_FREE = "z-ai/glm-5.2:free"


class PromptVersion(StrEnum):
    """Backend'de sürümlenmiş ve dağıtıma dâhil prompt'lar."""

    FAQ_ANALYSIS_V1 = "faq_analysis/v1"


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
    model: ModelId
    prompt_version: PromptVersion
    top_n: int = Field(ge=1, le=100)
    #: gt, ge değil: Zod tarafı `.positive()`.
    max_cost_usd: float = Field(gt=0, le=100)
    #: Opsiyonel ön ayar; verilmezse davranış eskisiyle birebir aynı.
    dataset_type: DatasetType = DatasetType.GENERIC
    #: Yalnızca `CHATBOT_LOG` ile birlikte anlamlı ve o zaman zorunlu.
    chatbot_config: ChatbotLogConfig | None = None

    @model_validator(mode="after")
    def _preset_requires_config(self) -> Self:
        if self.dataset_type is DatasetType.CHATBOT_LOG and self.chatbot_config is None:
            raise ValueError("CHATBOT_LOG ön ayarı chatbot_config gerektirir.")
        if self.dataset_type is DatasetType.GENERIC and self.chatbot_config is not None:
            raise ValueError("chatbot_config yalnızca CHATBOT_LOG ön ayarında verilebilir.")
        return self


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

    id: ModelId
    label: str
    #: 1M girdi tokenı başına USD.
    input_cost_per_million: float = Field(ge=0)
    output_cost_per_million: float = Field(ge=0)
    context_window: int = Field(gt=0)


class ModelList(ApiModel):
    """GET /api/v1/models"""

    models: list[ModelOption]
    default_model: ModelId
    default_prompt_version: PromptVersion

    @model_validator(mode="after")
    def _defaults_are_available(self) -> Self:
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Model whitelist'i tekrarlı id taşıyamaz.")
        if set(ids) != set(ModelId):
            raise ValueError("Model whitelist'i dondurulmuş ModelId kümesiyle aynı olmalı.")
        if self.default_model not in ids:
            raise ValueError("default_model whitelist içinde bulunmalı.")
        return self


AnalysisCreate = AnalysisRequest
AnalysisJobRead = AnalysisJob
