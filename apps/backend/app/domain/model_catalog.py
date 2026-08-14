"""Faz 3/4 kodunun dondurulmuş ana model kataloğuna uyumluluk yüzeyi."""

from __future__ import annotations

from app.core.catalog import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VERSION,
    MODEL_CATALOG,
    MODEL_LIST,
)
from app.schemas.analysis import ModelId, ModelList

# Pipeline'ın tarihsel adı; fiyat aritmetiği ve API aynı tuple'ı kullanır.
MODEL_WHITELIST = MODEL_CATALOG


def get_model_list() -> ModelList:
    return MODEL_LIST


def is_allowed_model(model_id: str | ModelId) -> bool:
    return any(option.id == model_id for option in MODEL_CATALOG)


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_PROMPT_VERSION",
    "MODEL_WHITELIST",
    "get_model_list",
    "is_allowed_model",
]
