"""Merkezi RFC 9457 hata üretimi.

Tüm hata cevapları BURADAN çıkar. Route'lar `AppError` fırlatır, gövdeyi elle
kurmaz; böylece `type/title/status/code/detail/trace_id` alanlarının her zaman
dolu olduğu tek yerden garanti edilir (ADR-0002 #6).
"""

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import (
    ERROR_STATUS,
    ERROR_TITLES,
    AppError,
    ErrorCode,
    error_type_uri,
)
from app.core.tracing import TRACE_ID_HEADER, current_trace_id, new_trace_id, set_trace_id
from app.schemas.common import ErrorItem, ProblemDetails

logger = logging.getLogger(__name__)

#: RFC 9457 zorunlu media type.
PROBLEM_MEDIA_TYPE = "application/problem+json"

#: Çıplak HTTP status'ları koda eşler (Starlette'in kendi HTTPException'ları).
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    404: ErrorCode.JOB_NOT_FOUND,
    405: ErrorCode.INTERNAL_ERROR,
    409: ErrorCode.JOB_CONFLICT,
    413: ErrorCode.UPLOAD_TOO_LARGE,
    415: ErrorCode.UPLOAD_INVALID_TYPE,
    422: ErrorCode.REQUEST_VALIDATION,
    429: ErrorCode.PROVIDER_RATE_LIMITED,
}

#: Kullanıcıya/istemciye giden sabit metinler. ADR-0001 §7 ham provider
#: yanıtını, anahtarı ve mesaj içeriğini hata gövdesinde yasaklıyor.
_UNEXPECTED_DETAIL = "Beklenmeyen bir sunucu hatası oluştu."
_NOT_IMPLEMENTED_DETAIL = "Bu uç nokta henüz uygulanmadı (BE-02)."
_RESPONSE_INVALID_DETAIL = "Sunucu cevabı iç sözleşmeye uymadı."


def _ensure_trace_id() -> str:
    """Middleware çalışmadıysa (ServerErrorMiddleware yolu) taze üretir."""
    trace_id = current_trace_id()
    if not trace_id:
        trace_id = new_trace_id()
        set_trace_id(trace_id)
    return trace_id


def problem_response(
    code: ErrorCode,
    detail: str,
    *,
    status: int | None = None,
    errors: Sequence[ErrorItem] = (),
    retry_after: float | None = None,
) -> JSONResponse:
    trace_id = _ensure_trace_id()
    problem = ProblemDetails(
        type=error_type_uri(code),
        title=ERROR_TITLES[code],
        status=status if status is not None else ERROR_STATUS[code],
        code=code,
        detail=detail,
        trace_id=trace_id,
        errors=list(errors),
        retry_after=retry_after,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_wire(),
        media_type=PROBLEM_MEDIA_TYPE,
        # Middleware zaten ekliyor; ServerErrorMiddleware'den kaçan yol için
        # burada da set ediyoruz.
        headers={TRACE_ID_HEADER: trace_id},
    )


async def app_error_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AppError)
    return problem_response(
        exc.code,
        exc.detail,
        errors=[ErrorItem(field=field, message=message) for field, message in exc.errors],
        retry_after=exc.retry_after,
    )


def _field_path(loc: Sequence[Any]) -> str | None:
    """Pydantic `loc` demetini noktalı yola çevirir.

    Baştaki "body" atılır (frontend alan adlarını gövde köküne göre bilir);
    diğer konumlar önekli kalır: `query.format`, `header.x-openrouter-key`,
    `path.upload_id`.
    """
    parts = [str(p) for p in loc]
    if parts and parts[0] == "body":
        parts = parts[1:]
    return ".".join(parts) if parts else None


def _specialized_request_error(
    validation_errors: Sequence[dict[str, Any]],
) -> tuple[ErrorCode, str] | None:
    """Whitelist enum hatalarını genel 422 kodlarından ayırır.

    `AnalysisRequest` alanları gerçek enum'dur; bu sayede Pydantic ve OpenAPI
    aynı whitelist'i doğrular. FastAPI ise enum ihlalini normalde her zaman
    `REQUEST_VALIDATION` yapardı. Public sözleşmedeki `INVALID_MODEL` ve
    `INVALID_PROMPT` kodlarını korumak için yalnızca boş olmayan string enum
    girdilerini özelleştiriyoruz. Yanlış tip ve boş string genel doğrulama
    hatası olarak kalır.
    """
    specialized = (
        (
            "model",
            ErrorCode.INVALID_MODEL,
            "Seçilen model backend whitelist'inde bulunmuyor.",
        ),
        (
            "prompt_version",
            ErrorCode.INVALID_PROMPT,
            "Seçilen prompt sürümü desteklenmiyor.",
        ),
    )
    specialized_locations = {("body", field) for field, _, _ in specialized}
    if any(tuple(error.get("loc", ())) not in specialized_locations for error in validation_errors):
        # Enum hatasına ek olarak başka bir alan da bozuksa genel request
        # doğrulaması kazanır. Bu, whitelist alanları düz string iken route
        # seviyesindeki semantik kontrollerden önceki davranışı korur ve
        # Next.js mock ile hata önceliğini aynı tutar.
        return None

    for field, code, detail in specialized:
        for error in validation_errors:
            raw_input = error.get("input")
            if (
                tuple(error.get("loc", ())) == ("body", field)
                and error.get("type") == "enum"
                and isinstance(raw_input, str)
                and bool(raw_input.strip())
            ):
                return code, detail
    return None


async def request_validation_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    validation_errors = exc.errors()
    errors = [
        ErrorItem(field=_field_path(err.get("loc", ())), message=str(err.get("msg", "")))
        # err["input"] BİLEREK kopyalanmıyor. FastAPI'nin varsayılan 422
        # gövdesi onu içerir; POST /analyses için bu canlı bir sır sızıntısı
        # yoludur — istemci OpenRouter anahtarını yanlışlıkla gövdeye koyarsa
        # varsayılan handler onu aynen geri yansıtır ve hem frontend hata
        # state'ine hem de gövde yakalayan her proxy log'una düşer.
        for err in validation_errors
    ]
    specialized = _specialized_request_error(validation_errors)
    if specialized is not None:
        code, detail = specialized
        return problem_response(code, detail, errors=errors)
    return problem_response(
        ErrorCode.REQUEST_VALIDATION,
        "İstek gövdesi veya parametreleri doğrulanamadı.",
        errors=errors,
    )


async def response_validation_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ResponseValidationError)
    # `logger.exception` kullanılmaz: traceback içindeki `str(exc)`, geçersiz
    # response input'unu (PII/secret dahil) aynen loga yazabilir. Yalnızca
    # güvenli sınıf adı ve trace id kaydedilir.
    logger.error(
        "Cevap doğrulaması başarısız",
        extra={
            "trace_id": current_trace_id(),
            "exception_type": type(exc).__name__,
        },
    )
    return problem_response(ErrorCode.INTERNAL_ERROR, _RESPONSE_INVALID_DETAIL)


async def http_exception_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail else ERROR_TITLES[code]
    # 429 sözleşmesi retry_after zorunlu kılıyor; çıplak bir HTTPException'da
    # değer olmadığı için muhafazakâr bir varsayılan veriyoruz.
    retry_after = 60.0 if code is ErrorCode.PROVIDER_RATE_LIMITED else None
    return problem_response(code, detail, status=exc.status_code, retry_after=retry_after)


async def not_implemented_handler(_request: Request, exc: Exception) -> Response:
    """BE-01 route stub'ları — 501.

    Kod->status tablosunu saf tutmak için status burada override ediliyor;
    tabloya "bazen 501" diye bir istisna eklemiyoruz.
    """
    assert isinstance(exc, NotImplementedError)
    return problem_response(ErrorCode.INTERNAL_ERROR, _NOT_IMPLEMENTED_DETAIL, status=501)


async def unhandled_exception_handler(_request: Request, exc: Exception) -> Response:
    # ASLA `str(exc)`, `logger.exception` veya `exc_info=True` kullanma:
    # traceback de exception metnini içerir. httpx hata metinleri rutin
    # olarak URL/secret taşır; yalnızca güvenli metadata loglanır.
    logger.error(
        "Yakalanmamış hata",
        extra={
            "trace_id": current_trace_id(),
            "exception_type": type(exc).__name__,
        },
    )
    return problem_response(ErrorCode.INTERNAL_ERROR, _UNEXPECTED_DETAIL)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(ResponseValidationError, response_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(NotImplementedError, not_implemented_handler)
    # Starlette'in ServerErrorMiddleware'i tüm user middleware'in dışında;
    # bu handler olmadan oradan kaçan bir exception trace id'siz, problem
    # gövdesiz çıplak bir 500 üretir.
    app.add_exception_handler(Exception, unhandled_exception_handler)
