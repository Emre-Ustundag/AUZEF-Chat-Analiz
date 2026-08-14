"""`GET /api/v1/models` — plan §1.1.

ADR §6 "model yalnızca backend whitelist'inden seçilebilir" diyor ama listeyi
döndüren endpoint tanımlamıyor. Bu boşluk plan §1.1'de karara bağlandı.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.responses import MODELS_LIST
from app.domain.model_catalog import get_model_list
from app.schemas.analysis import ModelList

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    response_model=ModelList,
    responses=MODELS_LIST,
    summary="Structured output desteği doğrulanmış model whitelist'i",
)
async def list_models() -> ModelList:
    return get_model_list()
