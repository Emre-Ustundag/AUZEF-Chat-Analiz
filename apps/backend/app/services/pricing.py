"""OpenRouter model fiyat kataloğu — canlı kaynak, Redis cache, fallback.

Whitelist statiktir; dış servis yalnızca o listedeki modellerin fiyatlarını
güncelleyebilir. Böylece OpenRouter kataloğuna yeni bir model eklenmesi onu
uygulamada kendiliğinden kullanılabilir hâle getirmez.

Akış:

1. Taze Redis snapshot'ı varsa kullan.
2. Yoksa public ``GET /models`` kataloğunu kısa timeout ile yenile.
3. Yenileme başarısızsa Redis'teki bayat snapshot'ı kullan.
4. O da yoksa kodla birlikte sürümlenen doğrulanmış fallback fiyatlarına dön.

Bir analiz oluşturulurken seçilen fiyatlar ayrıca PostgreSQL job satırına
snapshot olarak yazılır. Bu modülün cache'i daha sonra yenilense bile kuyrukta
bekleyen işin maliyet matematiği değişmez.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx
from pydantic import ValidationError
from redis import Redis

from app.core.catalog import DEFAULT_MODEL, DEFAULT_PROMPT_VERSION, MODEL_CATALOG, MODEL_LIST
from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.analysis import ModelId, ModelList, ModelOption, PricingSnapshot

logger = get_logger(__name__)

_CACHE_KEY = "auzef:pricing-catalog:v1"
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_MILLION = Decimal(1_000_000)


def get_model_list(settings: Settings) -> ModelList:
    """Güncel fiyatları taşıyan whitelist'i döndürür; dış hata yaymaz."""
    if not settings.pricing_refresh_enabled:
        return MODEL_LIST

    cached = _read_cached(settings)
    if cached is not None and _is_fresh(cached, settings):
        return _with_current_defaults(cached)

    try:
        live = _fetch_live_model_list(settings)
    except (httpx.HTTPError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        logger.warning(
            "pricing_catalog_refresh_failed",
            extra={"exception_type": type(exc).__name__, "has_stale_cache": cached is not None},
        )
        return _with_current_defaults(cached) if cached is not None else MODEL_LIST

    _write_cached(live, settings)
    return _with_current_defaults(live)


def _with_current_defaults(catalog: ModelList) -> ModelList:
    """Fiyat cache'inin uygulama sürüm seçimini geri almasını engeller.

    Redis snapshot'ı yalnızca canlı fiyatları taşır. ``default_prompt_version``
    ise deploy edilen uygulama sözleşmesidir; eski bir cache yeni prompt
    sürümünü bir saat boyunca v2'ye düşürmemelidir.
    """
    return catalog.model_copy(
        update={
            "default_model": DEFAULT_MODEL,
            "default_prompt_version": DEFAULT_PROMPT_VERSION,
        }
    )


def get_pricing_snapshot(model_id: str | ModelId, settings: Settings) -> PricingSnapshot:
    """Seçilen modelin o andaki fiyatlarını job'a yazılabilir biçimde döndürür."""
    catalog = get_model_list(settings)
    option = next((item for item in catalog.models if item.id == model_id), None)
    if option is None:  # Whitelist doğrulamasından sonra programlama hatasıdır.
        option = next(item for item in MODEL_CATALOG if item.id == model_id)
    return snapshot_from_option(option)


def fallback_pricing_snapshot(model_id: str | ModelId) -> PricingSnapshot:
    option = next((item for item in MODEL_CATALOG if item.id == model_id), None)
    if option is None:
        raise KeyError(f"Katalogda olmayan model: {model_id}")
    return snapshot_from_option(option)


def snapshot_from_option(option: ModelOption) -> PricingSnapshot:
    return PricingSnapshot(
        input_cost_per_million=option.input_cost_per_million,
        output_cost_per_million=option.output_cost_per_million,
        cache_read_cost_per_million=option.cache_read_cost_per_million,
        cache_write_cost_per_million=option.cache_write_cost_per_million,
        source=option.pricing_source,
        fetched_at=option.pricing_updated_at,
    )


def _fetch_live_model_list(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ModelList:
    url = f"{settings.openrouter_base_url.rstrip('/')}/models"
    with httpx.Client(
        timeout=settings.pricing_request_timeout_seconds,
        transport=transport,
        headers={"Accept": "application/json", "X-Title": "AUZEF Chat Analiz"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ValueError("OpenRouter model kataloğu beklenenden büyük.")
        payload = response.json()

    raw_models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        raise ValueError("OpenRouter model kataloğu data listesi taşımıyor.")

    by_id = {
        item.get("id"): item
        for item in raw_models
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    fetched_at = datetime.now(UTC)
    models = [
        _live_or_fallback(option, by_id.get(option.id), fetched_at) for option in MODEL_CATALOG
    ]
    if not any(model.pricing_source == "openrouter" for model in models):
        raise ValueError("Whitelist modellerinin hiçbirinde geçerli canlı fiyat bulunamadı.")

    return ModelList(
        models=models,
        default_model=DEFAULT_MODEL,
        default_prompt_version=DEFAULT_PROMPT_VERSION,
    )


def _live_or_fallback(
    fallback: ModelOption,
    raw: object,
    fetched_at: datetime,
) -> ModelOption:
    if not isinstance(raw, dict) or not isinstance(raw.get("pricing"), dict):
        return fallback
    pricing = raw["pricing"]
    try:
        prompt = _per_million(pricing.get("prompt"), required=True)
        completion = _per_million(pricing.get("completion"), required=True)
        cache_read = _per_million(pricing.get("input_cache_read"), required=False)
        cache_write = _per_million(pricing.get("input_cache_write"), required=False)
    except ValueError:
        return fallback

    assert prompt is not None and completion is not None
    return fallback.model_copy(
        update={
            "input_cost_per_million": prompt,
            "output_cost_per_million": completion,
            "cache_read_cost_per_million": cache_read,
            "cache_write_cost_per_million": cache_write,
            "pricing_source": "openrouter",
            "pricing_updated_at": fetched_at,
        }
    )


def _per_million(value: object, *, required: bool) -> float | None:
    if value is None and not required:
        return None
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise ValueError("Fiyat sayısal değil.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Fiyat ayrıştırılamadı.") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("Fiyat negatif veya sonlu değil.")
    return float(amount * _MILLION)


def _is_fresh(catalog: ModelList, settings: Settings) -> bool:
    timestamps = [model.pricing_updated_at for model in catalog.models if model.pricing_updated_at]
    if not timestamps:
        return False
    age = (datetime.now(UTC) - min(timestamps)).total_seconds()
    return age <= settings.pricing_cache_ttl_seconds


def _redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def _read_cached(settings: Settings) -> ModelList | None:
    try:
        client = _redis_client(settings)
        try:
            raw = cast(str | bytes | bytearray | None, client.get(_CACHE_KEY))
        finally:
            client.close()
        if raw is None:
            return None
        return ModelList.model_validate_json(raw)
    except Exception as exc:
        logger.warning(
            "pricing_cache_read_failed",
            extra={"exception_type": type(exc).__name__},
        )
        return None


def _write_cached(catalog: ModelList, settings: Settings) -> None:
    try:
        client = _redis_client(settings)
        try:
            client.set(
                _CACHE_KEY,
                catalog.model_dump_json(),
                ex=settings.pricing_stale_ttl_seconds,
            )
        finally:
            client.close()
    except Exception as exc:
        # Canlı katalog yine de bu istek için kullanılabilir; cache arızası
        # modeli veya analizi gereksiz yere durdurmamalı.
        logger.warning(
            "pricing_cache_write_failed",
            extra={"exception_type": type(exc).__name__},
        )


__all__ = [
    "fallback_pricing_snapshot",
    "get_model_list",
    "get_pricing_snapshot",
    "snapshot_from_option",
]
