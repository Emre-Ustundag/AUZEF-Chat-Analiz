"""Model/prompt whitelist'i ve maliyet hesabı tek kaynaktan gelir."""

from app.core.catalog import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VERSION,
    KNOWN_PROMPT_VERSIONS,
    MODEL_CATALOG,
    MODEL_LIST,
    estimate_cost_usd,
)


def test_catalog_and_defaults_are_frozen() -> None:
    assert [model.id for model in MODEL_CATALOG] == [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4.1-mini",
        "google/gemini-2.5-flash",
    ]
    assert MODEL_LIST.default_model == DEFAULT_MODEL
    assert DEFAULT_MODEL in {model.id for model in MODEL_LIST.models}
    assert MODEL_LIST.default_prompt_version == DEFAULT_PROMPT_VERSION
    assert DEFAULT_PROMPT_VERSION in KNOWN_PROMPT_VERSIONS


def test_fixture_token_cost_is_derived_from_catalog() -> None:
    assert estimate_cost_usd(DEFAULT_MODEL, 1_284_000, 96_400) == 5.298
