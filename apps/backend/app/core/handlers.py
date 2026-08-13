"""Merkezi RFC 9457 hata üretimi.

Tüm hata cevapları BURADAN çıkar. Route'lar `AppError` fırlatır, gövdeyi elle
kurmaz; böylece `type/title/status/code/detail/trace_id` alanlarının her zaman
dolu olduğu tek yerden garanti edilir (ADR-0002 #6).
"""

from collections.abc import Sequence
from typing import Any

import structlog
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
from app.core.tracing import TRACE_ID_HEADER, current_trace_id, new_trace_id
from app.schemas.common import ErrorItem, ProblemDetails

logger = structlog.get_logger(__name__)

#: RFC 9457 zorunlu media type.
PROBLEM_MEDIA_TYPE = "application/problem+json"

#: Çıplak HTTP status'ları koda eşler (Starlette'in kendi HTTPException'ları).
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    404: ErrorCode.JOB_NOT_FOUND,
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
_REQUEST_INVALID_DETAIL = "İstek gövdesi veya parametreleri doğrulanamadı."


def _request_trace_id(request: Request | None = None) -> str:
    """State'teki istek kimliğini ContextVar'a tercih eder.

    ServerErrorMiddleware user middleware'in dışındadır. Trace middleware'i
    exception yayılırken ContextVar'ı reset etse bile request state yaşamaya
    devam eder; unhandled-error cevabı böylece ilk isteğin kimliğini korur.
    """
    state_trace_id = getattr(request.state, "trace_id", "") if request is not None else ""
    return state_trace_id or current_trace_id() or new_trace_id()


def problem_response(
    code: ErrorCode,
    detail: str,
    *,
    errors: Sequence[ErrorItem] = (),
    retry_after: float | None = None,
    trace_id: str | None = None,
) -> JSONResponse:
    resolved_trace_id = trace_id or _request_trace_id()
    problem = ProblemDetails(
        type=error_type_uri(code),
        title=ERROR_TITLES[code],
        status=ERROR_STATUS[code],
        code=code,
        detail=detail,
        trace_id=resolved_trace_id,
        errors=list(errors),
        retry_after=retry_after,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_wire(),
        media_type=PROBLEM_MEDIA_TYPE,
        # Middleware zaten ekliyor; ServerErrorMiddleware'den kaçan yol için
        # burada da set ediyoruz.
        headers={TRACE_ID_HEADER: resolved_trace_id},
    )


async def app_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AppError)
    return problem_response(
        exc.code,
        exc.detail,
        errors=[ErrorItem(field=field, message=message) for field, message in exc.errors],
        retry_after=exc.retry_after,
        trace_id=_request_trace_id(request),
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


async def request_validation_handler(request: Request, exc: Exception) -> Response:
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
        return problem_response(code, detail, errors=errors, trace_id=_request_trace_id(request))
    return problem_response(
        ErrorCode.REQUEST_VALIDATION,
        _REQUEST_INVALID_DETAIL,
        errors=errors,
        trace_id=_request_trace_id(request),
    )


async def response_validation_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ResponseValidationError)
    # `logger.exception` kullanılmaz: traceback içindeki `str(exc)`, geçersiz
    # response input'unu (PII/secret dahil) aynen loga yazabilir. Yalnızca
    # güvenli sınıf adı ve trace id kaydedilir.
    logger.error(
        "response_validation_failed",
        trace_id=_request_trace_id(request),
        exception_type=type(exc).__name__,
    )
    return problem_response(
        ErrorCode.INTERNAL_ERROR,
        _RESPONSE_INVALID_DETAIL,
        trace_id=_request_trace_id(request),
    )


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    # python-multipart bozuk veya boundary'siz gövdeleri route validation'dan
    # önce 400 üretir. Public sözleşmede bozuk istekler 422'dir ve parser'ın
    # İngilizce detail metni istemciye yansıtılmaz. Diğer bilinmeyen 4xx'ler de
    # aynı güvenli request-validation sınıfına normalize edilir.
    if 400 <= exc.status_code < 500 and exc.status_code not in _STATUS_TO_CODE:
        code = ErrorCode.REQUEST_VALIDATION
    else:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = _REQUEST_INVALID_DETAIL if code is ErrorCode.REQUEST_VALIDATION else ERROR_TITLES[code]
    # 429 sözleşmesi retry_after zorunlu kılıyor; çıplak bir HTTPException'da
    # değer olmadığı için muhafazakâr bir varsayılan veriyoruz.
    retry_after = 60.0 if code is ErrorCode.PROVIDER_RATE_LIMITED else None
    return problem_response(
        code,
        detail,
        retry_after=retry_after,
        trace_id=_request_trace_id(request),
    )


async def not_implemented_handler(request: Request, exc: Exception) -> Response:
    """BE-01 route stub'ları — public NOT_IMPLEMENTED/501."""
    assert isinstance(exc, NotImplementedError)
    return problem_response(
        ErrorCode.NOT_IMPLEMENTED,
        _NOT_IMPLEMENTED_DETAIL,
        trace_id=_request_trace_id(request),
    )


def _cors_headers(request: Request) -> dict[str, str]:
    """Bu handler'ın 500'ü için CORS header'larını elle kurar.

    `ServerErrorMiddleware` TÜM user middleware'in — dolayısıyla
    `CORSMiddleware`'in de — dışında çalışır, o yüzden buradan çıkan cevaba
    CORS header'ı eklenmez. Sonuç: cross-origin bir istemci ne problem
    gövdesini ne `X-Trace-Id`'yi okuyabilir, düpedüz network error görür —
    yani hatanın izini sürmenin tek yolu kaybolur.

    Origin yalnızca yapılandırılmış allow-list'e karşı doğrulanır; eşleşme
    yoksa header eklenmez ve tarayıcı cevabı zaten bloklar.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    settings = getattr(request.app.state, "settings", None)
    allowed: Sequence[str] = getattr(settings, "cors_origins", None) or ()
    if origin.rstrip("/") not in allowed:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Expose-Headers": TRACE_ID_HEADER,
        "Vary": "Origin",
    }


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    # ASLA `str(exc)`, `logger.exception` veya `exc_info=True` kullanma:
    # traceback de exception metnini içerir. httpx hata metinleri rutin
    # olarak URL/secret taşır; yalnızca güvenli metadata loglanır.
    logger.error(
        "unhandled_exception",
        trace_id=_request_trace_id(request),
        exception_type=type(exc).__name__,
    )
    response = problem_response(
        ErrorCode.INTERNAL_ERROR,
        _UNEXPECTED_DETAIL,
        trace_id=_request_trace_id(request),
    )
    for header, value in _cors_headers(request).items():
        response.headers[header] = value
    return response


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
