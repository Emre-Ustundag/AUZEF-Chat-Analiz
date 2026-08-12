"""Model ve prompt whitelist'i — TEK KAYNAK.

ADR-0001 §6: model yalnızca structured output desteği doğrulanmış backend
whitelist'inden seçilebilir. Bu liste daha önce hem fixture üreticisinde hem
de frontend mock'unda elle tekrarlanıyordu; ikisi ayrıştığında hiçbir test
düşmüyordu.

Artık kaynak burası. Fixture üreticisi bunu okuyor, frontend mock'u da
üretilmiş `models.list.200.json` ile karşılaştırılıyor
(`catalog.test.ts`).
"""

from typing import Final

from app.schemas.analysis import ModelId, ModelList, ModelOption, PromptVersion

DEFAULT_MODEL: Final = ModelId.CLAUDE_SONNET_4_6
DEFAULT_PROMPT_VERSION: Final = PromptVersion.FAQ_ANALYSIS_V1

#: Backend'de sürümlenmiş prompt'lar (ADR-0001 §9).
KNOWN_PROMPT_VERSIONS: Final[tuple[PromptVersion, ...]] = tuple(PromptVersion)

MODEL_CATALOG: Final[tuple[ModelOption, ...]] = (
    ModelOption(
        id=ModelId.CLAUDE_SONNET_4_6,
        label="Claude Sonnet 4.6",
        input_cost_per_million=3,
        output_cost_per_million=15,
        context_window=1_000_000,
    ),
    ModelOption(
        id=ModelId.GPT_4_1_MINI,
        label="GPT-4.1 mini",
        input_cost_per_million=0.4,
        output_cost_per_million=1.6,
        context_window=1_047_576,
    ),
    ModelOption(
        id=ModelId.GEMINI_2_5_FLASH,
        label="Gemini 2.5 Flash",
        input_cost_per_million=0.3,
        output_cost_per_million=2.5,
        context_window=1_048_576,
    ),
)

MODEL_LIST: Final = ModelList(
    models=list(MODEL_CATALOG),
    default_model=DEFAULT_MODEL,
    default_prompt_version=DEFAULT_PROMPT_VERSION,
)


def find_model(model_id: str | ModelId) -> ModelOption | None:
    return next((model for model in MODEL_CATALOG if model.id == model_id), None)


def estimate_cost_usd(
    model_id: str | ModelId,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Token sayılarından USD maliyet. Rapor `estimated_cost_usd` ile aynı kural.

    Sabit yazılmış bir maliyet, katalog fiyatı değiştiğinde sessizce yanlış
    olurdu; fixture'lar bu fonksiyondan besleniyor.
    """
    model = find_model(model_id)
    if model is None:
        raise KeyError(f"Katalogda olmayan model: {model_id}")

    cost = (
        prompt_tokens / 1_000_000 * model.input_cost_per_million
        + completion_tokens / 1_000_000 * model.output_cost_per_million
    )
    return round(cost, 4)


# Kataloğun kendi içinde tutarlı olduğu import anında doğrulanır: bozuk bir
# varsayılan, uygulamayı ilk çağrıda değil hemen düşürsün.
if find_model(DEFAULT_MODEL) is None:  # pragma: no cover - import invariant
    raise RuntimeError(f"DEFAULT_MODEL katalogda yok: {DEFAULT_MODEL}")
if DEFAULT_PROMPT_VERSION not in KNOWN_PROMPT_VERSIONS:  # pragma: no cover
    raise RuntimeError(f"DEFAULT_PROMPT_VERSION bilinmiyor: {DEFAULT_PROMPT_VERSION}")
