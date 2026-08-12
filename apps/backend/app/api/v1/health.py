"""Process liveness ve eklenti tipi dependency readiness uçları."""

from fastapi import APIRouter, Request

from app.api.v1.responses import HEALTH_LIVE, HEALTH_READY
from app.core.errors import ServiceNotReadyError
from app.schemas.health import LivenessResponse, ReadinessCheckResponse, ReadinessResponse
from app.services.health import ReadinessCheck, run_readiness_checks

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse, responses=HEALTH_LIVE)
async def live() -> LivenessResponse:
    """Process çalışıyorsa başarılıdır; dış servis kontrol etmez."""
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse, responses=HEALTH_READY)
async def ready(request: Request) -> ReadinessResponse:
    """Kayıtlı zorunlu bağımlılıkların trafik almaya hazır olduğunu doğrular."""
    checks: tuple[ReadinessCheck, ...] = request.app.state.readiness_checks
    if not checks:
        raise ServiceNotReadyError("Servis trafiğe hazır değil.")

    results = await run_readiness_checks(checks)
    if any(not result.ready for result in results):
        raise ServiceNotReadyError("Servis trafiğe hazır değil.")
    return ReadinessResponse(
        checks=[ReadinessCheckResponse(name=result.name) for result in results]
    )
