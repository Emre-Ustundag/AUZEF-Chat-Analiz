"""v1 router — tüm uçlar `/api/v1` altında.

Hata cevapları uç bazında belgelenir. Router seviyesinde tek bir liste
paylaşmak, `GET /models`'in `UPLOAD_TOO_LARGE` (413) veya `PROVIDER_TIMEOUT`
(504) yayabildiğini söylüyordu — sözleşme belgesinin en görünür yalanı.
"""

from fastapi import APIRouter

from app.api.v1 import analyses, models, uploads
from app.core.errors import ERROR_STATUS, ErrorCode

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(uploads.router)
api_router.include_router(models.router)
api_router.include_router(analyses.router)

#: Sağlayıcı hataları ASENKRON job hatalarıdır: dış HTTP cevabı
#: `GET /analyses/{id}` → 200'dür, hata `job.error` içinde taşınır. Bu yüzden
#: hiçbir uç 429/502/504'ü HTTP durumu olarak belgelemez.
ASYNC_ONLY_CODES = frozenset(
    {
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.PROVIDER_BAD_RESPONSE,
        ErrorCode.PROVIDER_TIMEOUT,
    }
)

ASYNC_ONLY_STATUSES = frozenset(ERROR_STATUS[code] for code in ASYNC_ONLY_CODES)
