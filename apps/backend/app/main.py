"""FastAPI uygulama fabrikası ve process giriş noktası."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import MAX_UPLOAD_BYTES, Environment, get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.core.openapi import install_openapi
from app.core.tracing import TRACE_ID_HEADER, TraceIdMiddleware
from app.services.health import ReadinessCheck
from app.services.readiness import default_readiness_checks


def create_app(*, readiness_checks: Sequence[ReadinessCheck] = ()) -> FastAPI:
    """Doğrulanmış config ile izole FastAPI uygulaması oluşturur.

    `readiness_checks` VARSAYILAN OLARAK BOŞTUR ve öyle kalmalı: testlerin
    büyük kısmı `create_app()` ile uygulama kuruyor ve gerçek kontrollerin
    varsayılan olması hepsini Postgres/Redis/MinIO'ya bağımlı yapardı.
    Gerçek kontroller process giriş noktasında (dosyanın sonu) enjekte edilir.
    """
    settings = get_settings()
    configure_logging(settings)
    expose_docs = settings.environment is not Environment.PRODUCTION

    app = FastAPI(
        title=settings.app_name,
        version=settings.contract_version,
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = settings
    app.state.readiness_checks = tuple(readiness_checks)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "Idempotency-Key",
                "X-OpenRouter-Key",
                TRACE_ID_HEADER,
            ],
            expose_headers=[TRACE_ID_HEADER],
        )

    # Starlette son eklenen middleware'i en dışta çalıştırır. Trace context,
    # request logu ve exception handler'larından önce kurulmalıdır.
    app.add_middleware(
        RequestLoggingMiddleware,
        environment=settings.environment.value,
    )
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/health", include_in_schema=False)
    async def compose_health() -> dict[str, str]:
        return {"status": "ok"}

    install_openapi(app)
    return app


#: `uvicorn app.main:app` bunu yükler. Gerçek bağımlılık kontrolleri YALNIZCA
#: burada kayıtlı: `/api/v1/health/ready` çalışan bir process'te Postgres,
#: Redis ve object storage'a gerçekten dokunur ve hiçbiri cevap vermiyorsa
#: `503 SERVICE_NOT_READY` döner.
app = create_app(readiness_checks=default_readiness_checks())
