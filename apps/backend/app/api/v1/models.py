"""Model whitelist ucu — ADR-0002 #1.

ADR-0001 §6 "model yalnızca backend whitelist'inden seçilebilir" diyordu ama
listeyi döndüren endpoint'i tanımlamıyordu; frontend açılır listeyi
dolduramadığı için configure ekranı hiç render olmuyordu. Bu kart ile
`GET /api/v1/models` sözleşmeye girdi.
"""

from fastapi import APIRouter

from app.api.v1.responses import MODELS_LIST
from app.schemas.analysis import ModelList

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    response_model=ModelList,
    responses=MODELS_LIST,
    summary="İzin verilen modeller ve varsayılanlar",
    description=(
        "Yalnızca JSON Schema structured output desteği doğrulanmış modeller "
        "listelenir. `default_model` ve `default_prompt_version` formun "
        "başlangıç değerlerini besler."
    ),
)
async def list_models() -> ModelList:
    raise NotImplementedError
