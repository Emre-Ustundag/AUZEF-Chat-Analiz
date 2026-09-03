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

DEFAULT_MODEL: Final = ModelId.GEMINI_2_5_FLASH
DEFAULT_PROMPT_VERSION: Final = PromptVersion.FAQ_ANALYSIS_V3

#: Backend'de sürümlenmiş prompt'lar (ADR-0001 §9).
KNOWN_PROMPT_VERSIONS: Final[tuple[PromptVersion, ...]] = tuple(PromptVersion)

MODEL_CATALOG: Final[tuple[ModelOption, ...]] = (
    ModelOption(
        id=ModelId.CLAUDE_SONNET_4_6,
        label="Claude Sonnet 4.6",
        input_cost_per_million=3,
        output_cost_per_million=15,
        cache_read_cost_per_million=0.3,
        cache_write_cost_per_million=3.75,
        context_window=1_000_000,
    ),
    ModelOption(
        id=ModelId.GPT_4_1_MINI,
        label="GPT-4.1 mini",
        input_cost_per_million=0.4,
        output_cost_per_million=1.6,
        cache_read_cost_per_million=0.1,
        context_window=1_047_576,
    ),
    ModelOption(
        id=ModelId.GEMINI_2_5_FLASH,
        label="Gemini 2.5 Flash",
        input_cost_per_million=0.3,
        output_cost_per_million=2.5,
        cache_read_cost_per_million=0.03,
        cache_write_cost_per_million=0.0833333333333,
        context_window=1_048_576,
    ),
    ModelOption(
        id=ModelId.GPT_5_6_LUNA,
        label="GPT-5.6 Luna",
        input_cost_per_million=0.2,
        output_cost_per_million=1.2,
        cache_read_cost_per_million=0.02,
        cache_write_cost_per_million=0.25,
        context_window=1_050_000,
    ),
    ModelOption(
        id=ModelId.GPT_5_6_LUNA_PRO,
        label="GPT-5.6 Luna Pro",
        input_cost_per_million=0.2,
        output_cost_per_million=1.2,
        cache_read_cost_per_million=0.02,
        cache_write_cost_per_million=0.25,
        context_window=1_050_000,
    ),
)

#: `temperature` gönderilebilecek modeller.
#:
#: İstemci `provider={"require_parameters": True}` yolluyor: OpenRouter o
#: bayrakla, İSTENEN HER parametreyi destekleyen sağlayıcıyı arar. Parametreyi
#: desteklemeyen bir modele `temperature` eklemek bu yüzden isteği düşürmez,
#: 404 "No endpoints found that can handle the requested parameters" ile
#: KOMPLE reddettirir. OpenRouter `/models` çıktısında doğrulandı: Gemini ve
#: Claude `supported_parameters` içinde `temperature` taşıyor, GPT-5.6 Luna
#: ailesi taşımıyor.
MODELS_SUPPORTING_TEMPERATURE: Final[frozenset[str]] = frozenset(
    {
        ModelId.CLAUDE_SONNET_4_6.value,
        ModelId.GPT_4_1_MINI.value,
        ModelId.GEMINI_2_5_FLASH.value,
    }
)


def supports_temperature(model_id: str | ModelId) -> bool:
    """Modelin `temperature` parametresini kabul edip etmediği."""
    return str(model_id) in MODELS_SUPPORTING_TEMPERATURE


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
