from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.catalog import MODEL_LIST
from app.core.config import Settings
from app.pipeline.cost import cost_for_usage
from app.schemas.analysis import ModelId
from app.services import pricing


def _settings() -> Settings:
    return Settings(
        pricing_refresh_enabled=True,
        openrouter_base_url="https://openrouter.test/api/v1",
    )


def test_canli_katalog_token_fiyatlarini_milyon_birimine_cevirir() -> None:
    payload = {
        "data": [
            {
                "id": "anthropic/claude-sonnet-4.6",
                "pricing": {
                    "prompt": "0.000003",
                    "completion": "0.000015",
                    "input_cache_read": "0.0000003",
                    "input_cache_write": "0.00000375",
                },
            },
            {
                "id": "openai/gpt-4.1-mini",
                "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
            },
            {
                "id": "google/gemini-2.5-flash",
                "pricing": {"prompt": "0.0000003", "completion": "0.0000025"},
            },
        ]
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    catalog = pricing._fetch_live_model_list(_settings(), transport=transport)
    claude = next(model for model in catalog.models if model.id is ModelId.CLAUDE_SONNET_4_6)

    assert claude.input_cost_per_million == 3
    assert claude.output_cost_per_million == 15
    assert claude.cache_read_cost_per_million == 0.3
    assert claude.cache_write_cost_per_million == 3.75
    assert claude.pricing_source == "openrouter"
    assert claude.pricing_updated_at is not None


def test_gecersiz_model_fiyati_yalniz_o_model_icin_fallback_kullanir() -> None:
    payload = {
        "data": [
            {
                "id": "anthropic/claude-sonnet-4.6",
                "pricing": {"prompt": "bozuk", "completion": "0.000015"},
            },
            {
                "id": "google/gemini-2.5-flash",
                "pricing": {"prompt": "0.00000031", "completion": "0.0000026"},
            },
        ]
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    catalog = pricing._fetch_live_model_list(_settings(), transport=transport)
    claude = next(model for model in catalog.models if model.id is ModelId.CLAUDE_SONNET_4_6)
    gemini = next(model for model in catalog.models if model.id is ModelId.GEMINI_2_5_FLASH)

    assert claude.pricing_source == "fallback"
    assert gemini.pricing_source == "openrouter"
    assert gemini.input_cost_per_million == pytest.approx(0.31)


def test_yenileme_hatasinda_bayat_cache_kullanilir(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = MODEL_LIST.model_copy(deep=True)
    stale.models[0] = stale.models[0].model_copy(
        update={
            "pricing_source": "openrouter",
            "pricing_updated_at": datetime.now(UTC) - timedelta(days=1),
        }
    )
    monkeypatch.setattr(pricing, "_read_cached", lambda settings: stale)
    monkeypatch.setattr(
        pricing,
        "_fetch_live_model_list",
        lambda settings: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )

    assert pricing.get_model_list(_settings()) is stale


def test_cache_tokenlari_ayri_oranlarla_hesaplanir() -> None:
    snapshot = pricing.fallback_pricing_snapshot(ModelId.CLAUDE_SONNET_4_6)

    cost = cost_for_usage(
        prompt_tokens=1_000_000,
        completion_tokens=100_000,
        cached_tokens=400_000,
        cache_write_tokens=100_000,
        model_id=ModelId.CLAUDE_SONNET_4_6,
        pricing_snapshot=snapshot,
    )

    assert cost == pytest.approx(3.495)
