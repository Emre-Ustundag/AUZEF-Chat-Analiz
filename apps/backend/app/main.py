"""FastAPI uygulama fabrikası.

BE-01 contract-only: route gövdeleri 501 döner. Buradaki middleware, hata
handler'ları ve OpenAPI düzeltmeleri BE-02'ye olduğu gibi devredilir.
"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.handlers import register_exception_handlers
from app.core.openapi import install_openapi
from app.core.tracing import TraceIdMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="AUZEF Chat Analiz API",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # En son eklenen middleware en dışta çalışır: trace id'nin hata
    # handler'larını da sarması için bu sonuncu olmalı.
    app.add_middleware(TraceIdMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    install_openapi(app)
    return app


app = create_app()
