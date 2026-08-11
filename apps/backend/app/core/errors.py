"""RFC 9457 Problem Details hata modeli ve FastAPI exception handler'ları.

ADR §7. Bu modül backend'in yazılan İLK parçasıdır: sözleşmenin en kırılgan
yeri burasıdır ve her endpoint buna dayanır.

Frontend eşleşmesi — `apps/web/src/lib/api/schemas/common.ts`:

* `code` alanı `errorCodeSchema` enum'uyla BİREBİR aynı 11 değeri alır.
* `status` `z.int()`; float serileştirilirse Zod reddeder.
* `retry_after` frontend'de `.optional()` — `.nullable()` DEĞİL. Yani
  `null` gönderilirse doğrulama patlar; alan ya var olmalı ya da hiç
  bulunmamalıdır. Bu yüzden serileştirme `exclude_none` ile yapılır.
* `errors` her zaman gönderilir (boşsa `[]`).

ADR §9 değişmezi: ham OpenRouter yanıtı, API anahtarı, dosya içeriği veya
stack trace hata cevabına ASLA girmez. `detail` alanına yalnızca bu modülde
tanımlı sabit metinler veya çağıranın açıkça güvenli saydığı kısa açıklamalar
konur; istisna metinleri doğrudan aktarılmaz.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

ErrorCode = Literal[
    "UPLOAD_TOO_LARGE",
    "UPLOAD_INVALID_TYPE",
    "UPLOAD_CORRUPT_OR_ENCRYPTED",
    "SHEET_OR_COLUMN_NOT_FOUND",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_BAD_RESPONSE",
    "PROVIDER_TIMEOUT",
    "JOB_NOT_FOUND",
    "JOB_CONFLICT",
    "INTERNAL_ERROR",
]

#: ADR §7'deki kod → HTTP durum eşlemesi. Tek kaynak burasıdır.
ERROR_STATUS: dict[ErrorCode, int] = {
    "UPLOAD_TOO_LARGE": 413,
    "UPLOAD_INVALID_TYPE": 415,
    "UPLOAD_CORRUPT_OR_ENCRYPTED": 422,
    "SHEET_OR_COLUMN_NOT_FOUND": 422,
    "PROVIDER_AUTH_FAILED": 422,
    "PROVIDER_RATE_LIMITED": 429,
    "PROVIDER_BAD_RESPONSE": 502,
    "PROVIDER_TIMEOUT": 504,
    "JOB_NOT_FOUND": 404,
    "JOB_CONFLICT": 409,
    "INTERNAL_ERROR": 500,
}

#: Kullanıcıya gösterilecek metin frontend'in kendi Türkçe tablosundan gelir
#: (`ERROR_MESSAGES_TR`). Buradaki başlıklar log ve API dokümantasyonu içindir.
ERROR_TITLES: dict[ErrorCode, str] = {
    "UPLOAD_TOO_LARGE": "Dosya boyutu sınırı aşıldı",
    "UPLOAD_INVALID_TYPE": "Desteklenmeyen dosya türü",
    "UPLOAD_CORRUPT_OR_ENCRYPTED": "Dosya okunamadı",
    "SHEET_OR_COLUMN_NOT_FOUND": "Sayfa veya kolon bulunamadı",
    "PROVIDER_AUTH_FAILED": "Sağlayıcı kimlik doğrulaması başarısız",
    "PROVIDER_RATE_LIMITED": "İstek sınırına ulaşıldı",
    "PROVIDER_BAD_RESPONSE": "Geçersiz model yanıtı",
    "PROVIDER_TIMEOUT": "Sağlayıcı zaman aşımı",
    "JOB_NOT_FOUND": "İşlem bulunamadı",
    "JOB_CONFLICT": "İşlem çakışması",
    "INTERNAL_ERROR": "Beklenmeyen hata",
}


def error_type_uri(code: ErrorCode) -> str:
    """`UPLOAD_TOO_LARGE` → `/errors/upload-too-large` (mock ile aynı biçim)."""
    return f"/errors/{code.lower().replace('_', '-')}"


class FieldError(BaseModel):
    field: str | None = None
    message: str


class ProblemDetails(BaseModel):
    """RFC 9457 Problem Details gövdesi."""

    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str
    trace_id: str
    errors: list[FieldError] = Field(default_factory=list)
    #: Yalnızca PROVIDER_RATE_LIMITED cevaplarında gönderilir.
    retry_after: float | None = None

    @model_serializer(mode="wrap")
    def _drop_absent_retry_after(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """`retry_after` None ise alanı gövdeden tamamen çıkarır.

        NEDEN model_serializer, neden `exclude_none` değil: bu model iki yerde
        serileştiriliyor — hata cevabının kökünde ve `UploadRead.error` alanının
        İÇİNDE. `exclude_none=True` yalnızca birinci durumda uygulanabilirdi;
        ikinci durumda ise `profile: null` gibi frontend'in BEKLEDİĞİ null'ları
        da silerdi.

        Frontend `retry_after`'ı `.optional()` ilan ediyor, `.nullable()`
        değil: `null` gönderilirse `problemDetailsSchema.safeParse` başarısız
        olur ve kullanıcı hata mesajı yerine "beklenmeyen hata biçimi" görür.
        """
        data = handler(self)
        if data.get("retry_after") is None:
            data.pop("retry_after", None)
        return data

    def to_payload(self) -> dict[str, Any]:
        """Frontend'in Zod şemasına uyan JSON gövdesi."""
        return self.model_dump(mode="json")


def build_problem(
    code: ErrorCode,
    detail: str,
    *,
    trace_id: str | None = None,
    title: str | None = None,
    errors: list[FieldError] | None = None,
    retry_after: float | None = None,
) -> ProblemDetails:
    return ProblemDetails(
        type=error_type_uri(code),
        title=title or ERROR_TITLES[code],
        status=ERROR_STATUS[code],
        code=code,
        detail=detail,
        trace_id=trace_id or str(uuid.uuid4()),
        errors=errors or [],
        retry_after=retry_after,
    )


class ApiError(Exception):
    """Uygulama içinde fırlatılan, sözleşmeye çevrilebilir hata."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str,
        *,
        errors: list[FieldError] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.errors = errors or []
        self.retry_after = retry_after

    @property
    def status_code(self) -> int:
        return ERROR_STATUS[self.code]

    def to_problem(self, trace_id: str | None = None) -> ProblemDetails:
        return build_problem(
            self.code,
            self.detail,
            trace_id=trace_id,
            errors=self.errors,
            retry_after=self.retry_after,
        )


def problem_response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_payload(),
        # RFC 9457 media type. Frontend `response.json()` kullandığı için
        # içerik türü davranışı değiştirmiyor ama standart bunu şart koşuyor.
        media_type="application/problem+json",
    )


def _trace_id(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    return str(existing) if existing else str(uuid.uuid4())


def register_exception_handlers(app: FastAPI) -> None:
    """Tüm hata yollarını RFC 9457 biçimine bağlar.

    Bu handler'lar olmadan FastAPI kendi `{"detail": ...}` gövdesini döner ve
    frontend'in `problemDetailsSchema.safeParse` çağrısı başarısız olur;
    kullanıcı hata mesajı yerine boş ekran görür.
    """

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        trace_id = _trace_id(request)
        logger.warning(
            "api_error",
            extra={"code": exc.code, "trace_id": trace_id, "path": request.url.path},
        )
        return problem_response(exc.to_problem(trace_id))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        trace_id = _trace_id(request)
        code: ErrorCode = "JOB_NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR"
        # 404 dışındaki ham HTTP hatalarında (405, 406 ...) durum kodunu
        # koruyoruz ama gövde yine sözleşmeye uygun kalır.
        problem = build_problem(
            code,
            "İstek işlenemedi." if code == "INTERNAL_ERROR" else "Kayıt bulunamadı.",
            trace_id=trace_id,
        )
        if code == "INTERNAL_ERROR" and exc.status_code != 500:
            problem.status = exc.status_code
        return problem_response(problem)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        trace_id = _trace_id(request)
        # Doğrulama hatalarında yalnızca alan adı ve tip mesajı taşınır;
        # `input` alanı kullanıcı verisi içerebileceği için ASLA aktarılmaz.
        field_errors = [
            FieldError(
                field=".".join(str(part) for part in err.get("loc", ())[1:]) or None,
                message=str(err.get("msg", "Geçersiz değer.")),
            )
            for err in exc.errors()
        ]
        problem = build_problem(
            "SHEET_OR_COLUMN_NOT_FOUND"
            if request.url.path.endswith("/analyses")
            else "UPLOAD_INVALID_TYPE",
            "İstek gövdesi doğrulanamadı.",
            trace_id=trace_id,
            errors=field_errors,
        )
        return problem_response(problem)

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = _trace_id(request)
        # Stack trace YALNIZCA loga gider. Cevaba istisna metni bile konmaz:
        # bir dosya yolu, SQL parçası veya anahtar sızabilir (ADR §9).
        logger.exception(
            "unhandled_error",
            extra={"trace_id": trace_id, "path": request.url.path},
        )
        return problem_response(
            build_problem(
                "INTERNAL_ERROR",
                "Beklenmeyen bir hata oluştu.",
                trace_id=trace_id,
            )
        )


TraceMiddleware = Callable[[Request, Callable[[Request], Awaitable[Any]]], Awaitable[Any]]
