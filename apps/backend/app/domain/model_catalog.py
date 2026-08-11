"""Model whitelist'i — plan §1.1.

ADR §6/§9 değişmezi: "Model yalnızca JSON Schema structured output desteği
DOĞRULANMIŞ backend whitelist'inden seçilebilir."

Liste OpenRouter'dan DİNAMİK ÇEKİLMEZ. Whitelist'in varlık sebebi tam olarak
budur: sağlayıcının katalogunda structured output desteklemeyen yüzlerce model
var ve bunlardan biri seçilirse Faz 3'teki Pydantic doğrulaması her seferinde
başarısız olur, iş `PROVIDER_BAD_RESPONSE` ile biter. Listeye yeni model
eklemek bilinçli bir karar ve bir doğrulama adımı gerektirir.

Değerler `apps/web/src/app/api/mock/v1/models/route.ts` ile BİREBİR aynıdır:
kullanıcı `NEXT_PUBLIC_API_BASE_URL`'i mock'a geri çevirdiğinde arayüzün
aynı kalması gerekiyor.
"""

from __future__ import annotations

from app.schemas.catalog import ModelList, ModelOption

#: Faz 3'te kullanılacak varsayılan prompt sürümü. Backend'de sürümlenir
#: (ADR §9); frontend bunu analiz isteğinde geri gönderir.
DEFAULT_PROMPT_VERSION = "faq_analysis/v1"

DEFAULT_MODEL = "anthropic/claude-sonnet-4"

MODEL_WHITELIST: tuple[ModelOption, ...] = (
    ModelOption(
        id="anthropic/claude-sonnet-4",
        label="Claude Sonnet 4",
        input_cost_per_million=3.0,
        output_cost_per_million=15.0,
        context_window=200_000,
    ),
    ModelOption(
        id="openai/gpt-4.1-mini",
        label="GPT-4.1 mini",
        input_cost_per_million=0.4,
        output_cost_per_million=1.6,
        context_window=128_000,
    ),
    ModelOption(
        id="google/gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        input_cost_per_million=0.3,
        output_cost_per_million=2.5,
        context_window=1_000_000,
    ),
)


def get_model_list() -> ModelList:
    return ModelList(
        models=list(MODEL_WHITELIST),
        default_model=DEFAULT_MODEL,
        default_prompt_version=DEFAULT_PROMPT_VERSION,
    )


def is_allowed_model(model_id: str) -> bool:
    """Faz 2'de `POST /analyses` bunu kullanacak."""
    return any(option.id == model_id for option in MODEL_WHITELIST)
