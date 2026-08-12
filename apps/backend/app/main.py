"""FastAPI uygulama fabrikası ve process giriş noktası."""

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Environment, get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.core.openapi import install_openapi
from app.core.tracing import TraceIdMiddleware
from app.services.health import ReadinessCheck


def create_app(*, readiness_checks: Sequence[ReadinessCheck] = ()) -> FastAPI:
    """Doğrulanmış config ile izole FastAPI uygulaması oluşturur."""
    settings = get_settings()
    configure_logging(settings)
    expose_docs = settings.environment is not Environment.PRODUCTION

    app = FastAPI(
        title="AUZEF Chat Analiz API",
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
            allow_headers=["Content-Type", "Idempotency-Key", "X-OpenRouter-Key", "X-Trace-Id"],
        )

    # FastAPI son eklenen middleware'i en dışta çalıştırır. Trace context'i
    # request logu ve exception handler'larından önce hazır olmalıdır.
    app.add_middleware(
        RequestLoggingMiddleware,
        environment=settings.environment.value,
    )
    app.add_middleware(TraceIdMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    install_openapi(app)
    return app


app = create_app()
