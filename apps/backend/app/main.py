"""FastAPI uygulaması.

ADR §9: frontend ve API üretimde AYNI ORIGIN altında çalışır (Next.js rewrite
üzerinden). CORS allowlist'i yalnızca geliştirme içindir ve açıkça tanımlı
local origin'lerle sınırlıdır — `allow_origins=["*"]` ADR ihlalidir.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.v1 import analyses as analyses_router
from app.api.v1 import models as models_router
from app.api.v1 import uploads as uploads_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.services import storage

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        # MinIO ilk açılışta boş gelir; bucket olmadan ilk upload 500 verirdi.
        await run_in_threadpool(storage.ensure_bucket, settings)
    except Exception:
        # Bucket kurulamazsa API yine de ayağa kalkar: /health cevap verir ve
        # sorun compose loglarında görünür. Burada çökmek, MinIO'nun birkaç
        # saniye geç açıldığı her durumda API'yi restart döngüsüne sokardı.
        logger.exception("bucket_bootstrap_failed")

    yield

    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        # ADR §6: "OpenAPI şeması backend'den üretilir".
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            # X-OpenRouter-Key Faz 2'de kullanılacak; şimdiden izin veriliyor
            # ki tarayıcı preflight'ı o zaman sessizce reddetmesin.
            allow_headers=["Content-Type", "X-OpenRouter-Key", "Idempotency-Key"],
        )

    @app.middleware("http")
    async def _trace_id_middleware(request: Request, call_next: Any) -> Any:
        """Her isteğe bir iz kimliği bağlar.

        Hata cevabındaki `trace_id` ile log satırlarındaki değerin aynı olması
        için: kullanıcı "şu kimliği aldım" dediğinde loglarda karşılığı
        bulunabilmeli.
        """
        trace_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    register_exception_handlers(app)

    app.include_router(uploads_router.router, prefix="/api/v1")
    app.include_router(analyses_router.router, prefix="/api/v1")
    app.include_router(models_router.router, prefix="/api/v1")

    @app.get("/health", tags=["health"], summary="Compose healthcheck")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
