"""Hata kodu kaydı — ADR-0001 §7 + ADR-0002 #1.

`ErrorCode` üyeleri frontend'in `errorCodeSchema` enum'uyla **küme olarak**
aynı olmak zorundadır (`apps/web/src/lib/api/schemas/common.ts`). Bilinmeyen
bir kod gelirse `client.ts:80` cevabı sentetik bir INTERNAL_ERROR'a düşürür ve
kullanıcı yanlış Türkçe mesajı görür; parity `contract-openapi.test.ts` ve
`test_error_registry.py` ile iki taraftan da kilitlenmiştir.
"""

from enum import StrEnum
from typing import Final


class ErrorCode(StrEnum):
    """Dondurulmuş hata sözlüğü ve BE-02 readiness genişletmesi."""

    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UPLOAD_INVALID_TYPE = "UPLOAD_INVALID_TYPE"
    UPLOAD_CORRUPT_OR_ENCRYPTED = "UPLOAD_CORRUPT_OR_ENCRYPTED"
    SHEET_OR_COLUMN_NOT_FOUND = "SHEET_OR_COLUMN_NOT_FOUND"
    REQUEST_VALIDATION = "REQUEST_VALIDATION"
    INVALID_MODEL = "INVALID_MODEL"
    INVALID_PROMPT = "INVALID_PROMPT"
    COST_LIMIT_EXCEEDED = "COST_LIMIT_EXCEEDED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_CONFLICT = "JOB_CONFLICT"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.UPLOAD_TOO_LARGE: 413,
    ErrorCode.UPLOAD_INVALID_TYPE: 415,
    ErrorCode.UPLOAD_CORRUPT_OR_ENCRYPTED: 422,
    ErrorCode.SHEET_OR_COLUMN_NOT_FOUND: 422,
    ErrorCode.REQUEST_VALIDATION: 422,
    ErrorCode.INVALID_MODEL: 422,
    ErrorCode.INVALID_PROMPT: 422,
    ErrorCode.COST_LIMIT_EXCEEDED: 422,
    ErrorCode.PROVIDER_AUTH_FAILED: 422,
    ErrorCode.PROVIDER_RATE_LIMITED: 429,
    ErrorCode.PROVIDER_BAD_RESPONSE: 502,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.JOB_CONFLICT: 409,
    ErrorCode.SERVICE_NOT_READY: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}

ERROR_TITLES: Final[dict[ErrorCode, str]] = {
    ErrorCode.UPLOAD_TOO_LARGE: "Dosya boyutu sınırı aşıldı",
    ErrorCode.UPLOAD_INVALID_TYPE: "Desteklenmeyen dosya türü",
    ErrorCode.UPLOAD_CORRUPT_OR_ENCRYPTED: "Dosya okunamadı",
    ErrorCode.SHEET_OR_COLUMN_NOT_FOUND: "Sayfa veya kolon bulunamadı",
    ErrorCode.REQUEST_VALIDATION: "İstek doğrulanamadı",
    ErrorCode.INVALID_MODEL: "Geçersiz model",
    ErrorCode.INVALID_PROMPT: "Geçersiz prompt sürümü",
    ErrorCode.COST_LIMIT_EXCEEDED: "Maliyet sınırı aşıldı",
    ErrorCode.PROVIDER_AUTH_FAILED: "Sağlayıcı kimlik doğrulaması başarısız",
    ErrorCode.PROVIDER_RATE_LIMITED: "Sağlayıcı istek sınırına ulaşıldı",
    ErrorCode.PROVIDER_BAD_RESPONSE: "Sağlayıcıdan geçersiz yanıt",
    ErrorCode.PROVIDER_TIMEOUT: "Sağlayıcı zaman aşımı",
    ErrorCode.JOB_NOT_FOUND: "İşlem bulunamadı",
    ErrorCode.JOB_CONFLICT: "İşlem durumu bu isteğe uygun değil",
    ErrorCode.SERVICE_NOT_READY: "Servis hazır değil",
    ErrorCode.INTERNAL_ERROR: "Beklenmeyen hata",
}


def error_type_uri(code: ErrorCode) -> str:
    """RFC 9457 `type` alanı.

    Mock'un `problem()` fabrikası ile karakter karakter aynı üretim kuralı
    (`apps/web/src/mocks/store.ts`). İki dil arasındaki en ucuz invariant;
    frontend bu alanı doğrulamasa da iki taraftan da test edilir.
    """
    return f"/errors/{code.value.lower().replace('_', '-')}"


class AppError(Exception):
    """RFC 9457 problem gövdesine çevrilen taban hata.

    Alt sınıflar `code` sınıf değişkenini bildirir ve otomatik olarak
    `ERROR_SUBCLASSES` kaydına girer. Dinamik üretilmiyorlar: mypy ve editör
    hepsini görsün.
    """

    code: ErrorCode

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        code = getattr(cls, "code", None)
        if code is None:
            return
        if code in ERROR_SUBCLASSES:
            raise RuntimeError(f"{code} için birden fazla AppError alt sınıfı: {cls.__name__}")
        ERROR_SUBCLASSES[code] = cls

    def __init__(
        self,
        detail: str,
        *,
        errors: list[tuple[str | None, str]] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or []
        self.retry_after = retry_after

    @property
    def status(self) -> int:
        return ERROR_STATUS[self.code]

    @property
    def title(self) -> str:
        return ERROR_TITLES[self.code]

    @property
    def type_uri(self) -> str:
        return error_type_uri(self.code)


ERROR_SUBCLASSES: Final[dict[ErrorCode, type[AppError]]] = {}


class UploadTooLargeError(AppError):
    code = ErrorCode.UPLOAD_TOO_LARGE


class UploadInvalidTypeError(AppError):
    code = ErrorCode.UPLOAD_INVALID_TYPE


class UploadCorruptOrEncryptedError(AppError):
    code = ErrorCode.UPLOAD_CORRUPT_OR_ENCRYPTED


class SheetOrColumnNotFoundError(AppError):
    code = ErrorCode.SHEET_OR_COLUMN_NOT_FOUND


class RequestValidationFailedError(AppError):
    code = ErrorCode.REQUEST_VALIDATION


class InvalidModelError(AppError):
    code = ErrorCode.INVALID_MODEL


class InvalidPromptError(AppError):
    code = ErrorCode.INVALID_PROMPT


class CostLimitExceededError(AppError):
    code = ErrorCode.COST_LIMIT_EXCEEDED


class ProviderAuthFailedError(AppError):
    code = ErrorCode.PROVIDER_AUTH_FAILED


class ProviderRateLimitedError(AppError):
    code = ErrorCode.PROVIDER_RATE_LIMITED

    def __init__(
        self,
        detail: str,
        retry_after: float,
        *,
        errors: list[tuple[str | None, str]] | None = None,
    ) -> None:
        # retry_after ZORUNLU pozisyonel: ADR-0002 #6 gereği 429 onsuz
        # üretilemez. Kuralı yapısal olarak imkânsız kılıyoruz, teste
        # bırakmıyoruz.
        super().__init__(detail, errors=errors, retry_after=retry_after)


class ProviderBadResponseError(AppError):
    code = ErrorCode.PROVIDER_BAD_RESPONSE


class ProviderTimeoutError(AppError):
    code = ErrorCode.PROVIDER_TIMEOUT


class JobNotFoundError(AppError):
    code = ErrorCode.JOB_NOT_FOUND


class JobConflictError(AppError):
    code = ErrorCode.JOB_CONFLICT


class ServiceNotReadyError(AppError):
    code = ErrorCode.SERVICE_NOT_READY


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
