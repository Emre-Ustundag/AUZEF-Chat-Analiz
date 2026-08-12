"""Model/prompt whitelist'i ve maliyet hesabı tek kaynaktan gelir."""

from app.core.catalog import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VERSION,
    KNOWN_PROMPT_VERSIONS,
    MODEL_CATALOG,
    MODEL_LIST,
    estimate_cost_usd,
)
from app.schemas.analysis import ModelId, PromptVersion


def test_catalog_and_defaults_are_frozen() -> None:
    assert [model.id for model in MODEL_CATALOG] == [
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-4.1-mini",
        "google/gemini-2.5-flash",
    ]
    assert set(ModelId) == {model.id for model in MODEL_CATALOG}
    assert set(PromptVersion) == set(KNOWN_PROMPT_VERSIONS)
    assert DEFAULT_MODEL == ModelId.CLAUDE_SONNET_4_6
    assert MODEL_LIST.default_model == DEFAULT_MODEL
    assert DEFAULT_MODEL in {model.id for model in MODEL_LIST.models}
    assert MODEL_LIST.default_prompt_version == DEFAULT_PROMPT_VERSION
    assert DEFAULT_PROMPT_VERSION in KNOWN_PROMPT_VERSIONS


def test_catalog_matches_verified_openrouter_metadata() -> None:
    actual = {
        model.id: (
            model.context_window,
            model.input_cost_per_million,
            model.output_cost_per_million,
        )
        for model in MODEL_CATALOG
    }
    assert actual == {
        ModelId.CLAUDE_SONNET_4_6: (1_000_000, 3, 15),
        ModelId.GPT_4_1_MINI: (1_047_576, 0.4, 1.6),
        ModelId.GEMINI_2_5_FLASH: (1_048_576, 0.3, 2.5),
    }


def test_fixture_token_cost_is_derived_from_catalog() -> None:
    assert estimate_cost_usd(DEFAULT_MODEL, 1_284_000, 96_400) == 5.298
