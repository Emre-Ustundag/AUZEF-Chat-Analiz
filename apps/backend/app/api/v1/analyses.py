"""Analiz uçları — ADR-0001 §6.

Route iş mantıkları ilgili uygulama kartına kadar 501 döner.
"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.v1.deps import OpenRouterKey
from app.api.v1.responses import (
    ANALYSIS_CANCEL,
    ANALYSIS_CREATE,
    ANALYSIS_EXPORT,
    ANALYSIS_READ,
    ANALYSIS_RESULT,
)
from app.schemas.analysis import AnalysisCreated, AnalysisJob, AnalysisRequest, ExportFormat
from app.schemas.report import AnalysisReport

router = APIRouter(tags=["analyses"])


@router.post(
    "/analyses",
    response_model=AnalysisCreated,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ANALYSIS_CREATE,
    summary="Analiz başlat",
    description=(
        "Analizi kuyruğa alır. OpenRouter anahtarı yalnızca `X-OpenRouter-Key` "
        "header'ında taşınır; gövdeye veya sorgu parametresine asla konmaz, "
        "loglarda redakte edilir ve PostgreSQL'e yazılmaz.\n\n"
        "Maliyet ön tahmini `max_cost_usd` sınırını aşarsa istek burada "
        "`COST_LIMIT_EXCEEDED` ile reddedilir; çalışma sırasında aşılırsa aynı "
        "kod terminal job hatası olarak döner (ADR-0002 #10)."
    ),
)
async def create_analysis(
    payload: AnalysisRequest,
    openrouter_key: OpenRouterKey,
) -> AnalysisCreated:
    raise NotImplementedError


@router.get(
    "/analyses/{analysis_id}",
    response_model=AnalysisJob,
    responses=ANALYSIS_READ,
    summary="Analiz durumu",
    description=(
        "Frontend bu ucu 2,5 saniyede bir poll eder ve terminal durumda "
        "(`completed`/`failed`/`cancelled`) durur. `progress` düzenli "
        "artmayabilir: ilerleme her satırda değil, yalnızca aşama veya anlamlı "
        "yüzde değişiminde yazılır."
    ),
)
async def get_analysis(analysis_id: UUID) -> AnalysisJob:
    raise NotImplementedError


@router.get(
    "/analyses/{analysis_id}/result",
    response_model=AnalysisReport,
    responses=ANALYSIS_RESULT,
    summary="Tamamlanmış rapor",
    description=("Yalnızca `status` `completed` iken 200 döner. Aksi hâlde 409 `JOB_CONFLICT`."),
)
async def get_analysis_result(analysis_id: UUID) -> AnalysisReport:
    raise NotImplementedError


@router.delete(
    "/analyses/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ANALYSIS_CANCEL,
    summary="Analizi iptal et",
    description=(
        "Aktif job → 204 ve durum `cancelled`. Terminal job → 409 "
        "`JOB_CONFLICT`. Bilinmeyen id → 404 `JOB_NOT_FOUND` (ADR-0002 #9)."
    ),
)
async def cancel_analysis(analysis_id: UUID) -> None:
    raise NotImplementedError


@router.get(
    "/analyses/{analysis_id}/export",
    responses=ANALYSIS_EXPORT,
    summary="Raporu dışa aktar",
    description=(
        "Dosya adı her zaman `analiz-{analysis_id}.{format}` biçimindedir; "
        "kullanıcının yüklediği dosya adı kullanılmaz. Böylece "
        "`Content-Disposition` tanım gereği ASCII kalır ve RFC 5987 "
        "`filename*` gerekmez (ADR-0002 #11)."
    ),
)
async def export_analysis(
    analysis_id: UUID,
    export_format: ExportFormat = Query(ExportFormat.XLSX, alias="format"),
) -> Response:
    raise NotImplementedError
