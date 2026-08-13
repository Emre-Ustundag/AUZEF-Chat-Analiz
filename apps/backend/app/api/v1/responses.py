"""Uç bazlı hata cevabı bildirimleri.

Her uç yalnızca GERÇEKTEN üretebildiği HTTP hatalarını belgeler. Router
seviyesinde ortak bir liste paylaşmak, `GET /models`'in `UPLOAD_TOO_LARGE`
(413) ya da `PROVIDER_TIMEOUT` (504) yayabildiğini söylüyordu.

Sağlayıcı hataları (429/502/504) hiçbir uçta yok: bunlar asenkron job
hatalarıdır, dış HTTP cevabı `GET /analyses/{id}` → 200'dür ve hata
`job.error` içinde `ProblemDetails` olarak taşınır (ADR-0001 §2).
"""

from typing import Any

from app.core.errors import ERROR_STATUS, ErrorCode
from app.core.handlers import PROBLEM_MEDIA_TYPE
from app.schemas.common import ProblemDetails


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """Verilen hata kodlarını HTTP statülerine gruplayarak belgeler.

    `model` veriliyor ki `ProblemDetails` bileşen olarak üretilsin ve
    referanslansın; content-type ise `core/openapi.py` içinde tek başına
    `application/problem+json`'a indiriliyor (FastAPI ayrıca
    `application/json` ekliyor, oysa handler'lar onu asla döndürmüyor).
    """
    by_status: dict[int, list[ErrorCode]] = {}
    for code in codes:
        by_status.setdefault(ERROR_STATUS[code], []).append(code)

    return {
        status: {
            "description": (
                "RFC 9457 Problem Details — olası kodlar: "
                + ", ".join(sorted(code.value for code in status_codes))
            ),
            "model": ProblemDetails,
            "x-error-codes": [code.value for code in status_codes],
        }
        for status, status_codes in sorted(by_status.items())
    }


# --------------------------------------------------------- uç bazlı setler
#
# 422 her uçta var: yol parametresi UUID değilse veya sorgu/gövde
# doğrulaması düşerse REQUEST_VALIDATION üretilir.
# 500 her uçta var: yakalanmamış hata.

UPLOAD_CREATE = error_responses(
    ErrorCode.JOB_CONFLICT,
    ErrorCode.UPLOAD_TOO_LARGE,
    ErrorCode.UPLOAD_INVALID_TYPE,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
"""409 idempotency gövde uyuşmazlığı (ADR-0002 #3) — bu uç Idempotency-Key destekliyor."""

UPLOAD_READ = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
UPLOAD_DELETE = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)

MODELS_LIST = error_responses(ErrorCode.NOT_IMPLEMENTED, ErrorCode.INTERNAL_ERROR)
"""Statik whitelist; parametresi yok, dolayısıyla 422 bile üretemez."""

ANALYSIS_CREATE = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.JOB_CONFLICT,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.INVALID_MODEL,
    ErrorCode.INVALID_PROMPT,
    ErrorCode.COST_LIMIT_EXCEEDED,
    ErrorCode.PROVIDER_AUTH_FAILED,
    ErrorCode.SHEET_OR_COLUMN_NOT_FOUND,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
"""404 bilinmeyen upload_id; 409 idempotency; 422 REQUEST_VALIDATION,
INVALID_MODEL, INVALID_PROMPT, COST_LIMIT_EXCEEDED, PROVIDER_AUTH_FAILED,
SHEET_OR_COLUMN_NOT_FOUND."""

ANALYSIS_READ = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
ANALYSIS_CANCEL = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.JOB_CONFLICT,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
"""409 terminal job iptali (ADR-0002 #9)."""

ANALYSIS_RESULT = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.JOB_CONFLICT,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
"""409 iş henüz tamamlanmadı."""

ANALYSIS_EXPORT = error_responses(
    ErrorCode.JOB_NOT_FOUND,
    ErrorCode.JOB_CONFLICT,
    ErrorCode.REQUEST_VALIDATION,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.INTERNAL_ERROR,
)
"""409 rapor hazır değil."""

__all__ = [
    "ANALYSIS_CANCEL",
    "ANALYSIS_CREATE",
    "ANALYSIS_EXPORT",
    "ANALYSIS_READ",
    "ANALYSIS_RESULT",
    "MODELS_LIST",
    "PROBLEM_MEDIA_TYPE",
    "UPLOAD_CREATE",
    "UPLOAD_DELETE",
    "UPLOAD_READ",
    "error_responses",
]
