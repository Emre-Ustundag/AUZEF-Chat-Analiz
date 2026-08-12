"""Hata kodu kaydının bütünlüğü — ADR-0001 §7 + ADR-0002 #1."""

import pytest
from pydantic import ValidationError

from app.core.errors import (
    ERROR_STATUS,
    ERROR_SUBCLASSES,
    ERROR_TITLES,
    ErrorCode,
    ProviderRateLimitedError,
    error_type_uri,
)
from app.schemas.common import ProblemDetails

#: ADR-0001 §7'deki tablo + ADR-0002 #1 ile eklenen dört 422.
#: Sabit yazılmış: kaydı kendi kendisiyle karşılaştırmak hiçbir şey ispat etmez.
EXPECTED_STATUS = {
    "UPLOAD_TOO_LARGE": 413,
    "UPLOAD_INVALID_TYPE": 415,
    "UPLOAD_CORRUPT_OR_ENCRYPTED": 422,
    "SHEET_OR_COLUMN_NOT_FOUND": 422,
    "REQUEST_VALIDATION": 422,
    "INVALID_MODEL": 422,
    "INVALID_PROMPT": 422,
    "COST_LIMIT_EXCEEDED": 422,
    "PROVIDER_AUTH_FAILED": 422,
    "PROVIDER_RATE_LIMITED": 429,
    "PROVIDER_BAD_RESPONSE": 502,
    "PROVIDER_TIMEOUT": 504,
    "JOB_NOT_FOUND": 404,
    "JOB_CONFLICT": 409,
    "INTERNAL_ERROR": 500,
}


def test_status_table_matches_adr() -> None:
    assert {code.value: status for code, status in ERROR_STATUS.items()} == EXPECTED_STATUS


def test_every_code_has_status_title_and_subclass() -> None:
    codes = set(ErrorCode)
    assert set(ERROR_STATUS) == codes
    assert set(ERROR_TITLES) == codes
    assert set(ERROR_SUBCLASSES) == codes


def test_titles_are_non_empty() -> None:
    assert all(title.strip() for title in ERROR_TITLES.values())


@pytest.mark.parametrize("code", list(ErrorCode))
def test_type_uri_derivation(code: ErrorCode) -> None:
    """Mock'un `problem()` fabrikasıyla birebir aynı kural.

    Frontend tarafındaki aynası: `mocks/store.test.ts`.
    """
    assert error_type_uri(code) == f"/errors/{code.value.lower().replace('_', '-')}"


def test_rate_limited_requires_retry_after() -> None:
    # retry_after zorunlu pozisyonel: onsuz 429 üretmek yapısal olarak
    # imkânsız olmalı.
    error = ProviderRateLimitedError("sınır aşıldı", 60)
    assert error.retry_after == 60
    assert error.status == 429


def _problem(code: ErrorCode, retry_after: float | None) -> ProblemDetails:
    return ProblemDetails(
        type=error_type_uri(code),
        title=ERROR_TITLES[code],
        status=ERROR_STATUS[code],
        code=code,
        detail="detay",
        trace_id="9d8c7b6a-5e4f-4321-8abc-0123456789ab",
        retry_after=retry_after,
    )


def test_retry_after_required_on_429() -> None:
    with pytest.raises(ValidationError):
        _problem(ErrorCode.PROVIDER_RATE_LIMITED, None)


def test_retry_after_forbidden_elsewhere() -> None:
    with pytest.raises(ValidationError):
        _problem(ErrorCode.JOB_NOT_FOUND, 60)


def test_retry_after_omitted_not_null() -> None:
    """ADR-0002 #6'nın kalbi.

    Zod'un `.optional()`'ı `null` KABUL ETMEZ. Backend 429 dışında
    `"retry_after": null` yollarsa `problemDetailsSchema` düşer ve
    `client.ts` her hatayı sentetik INTERNAL_ERROR'a çevirir — bütün Türkçe
    hata tablosu sessizce ölür.
    """
    dumped = _problem(ErrorCode.JOB_NOT_FOUND, None).model_dump(mode="json")
    assert "retry_after" not in dumped

    rate_limited = _problem(ErrorCode.PROVIDER_RATE_LIMITED, 60).model_dump(mode="json")
    assert rate_limited["retry_after"] == 60
