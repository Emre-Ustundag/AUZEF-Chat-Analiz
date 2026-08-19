"""`GET /api/v1/models` — plan §1.1.

ADR §6 "model yalnızca backend whitelist'inden seçilebilir" diyor ama listeyi
döndüren endpoint tanımlamıyor. Bu boşluk plan §1.1'de karara bağlandı.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.v1.responses import MODELS_LIST
from app.core.config import Settings, get_settings
from app.schemas.analysis import ModelList
from app.services import pricing

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    response_model=ModelList,
    responses=MODELS_LIST,
    summary="Structured output desteği doğrulanmış model whitelist'i",
)
async def list_models(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelList:
    # OpenRouter/Redis istemcileri senkron; API event loop'unu bloklamasın.
    return await run_in_threadpool(pricing.get_model_list, settings)
