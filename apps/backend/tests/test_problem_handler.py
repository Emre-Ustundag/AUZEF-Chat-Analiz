"""Merkezi RFC 9457 üretimi — her hata cevabı bu handler'lardan çıkar."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    ERROR_STATUS,
    ERROR_SUBCLASSES,
    AppError,
    ErrorCode,
    ProviderRateLimitedError,
)
from app.core.handlers import PROBLEM_MEDIA_TYPE, register_exception_handlers
from app.core.tracing import TRACE_ID_HEADER, TraceIdMiddleware
from app.schemas.common import ProblemDetails

SECRET_SENTINEL = "SENSITIVE_INPUT_SENTINEL_9472"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_produces_valid_problem(client: TestClient, code: ErrorCode) -> None:
    """Her AppError alt sınıfı doğru status ve geçerli gövde üretir."""
    # PROVIDER_RATE_LIMITED tek istisna: retry_after zorunlu pozisyonel
    # olduğu için (ADR-0002 #6) ayrı çağrılıyor.
    error: AppError = (
        ProviderRateLimitedError("örnek detay", 60)
        if code is ErrorCode.PROVIDER_RATE_LIMITED
        else ERROR_SUBCLASSES[code]("örnek detay")
    )

    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/patla")
    async def patla() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as local:
        response = local.get("/patla")

    assert response.status_code == ERROR_STATUS[code]
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)

    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is code
    assert problem.status == ERROR_STATUS[code]
    assert problem.trace_id
    assert str(problem.trace_id) == response.headers[TRACE_ID_HEADER]
    assert problem.type == f"/errors/{code.value.lower().replace('_', '-')}"


def test_stub_routes_return_501(client: TestClient) -> None:
    response = client.get("/api/v1/models")

    assert response.status_code == 501
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.INTERNAL_ERROR
    assert problem.trace_id


def test_unhandled_exception_does_not_leak_internals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR-0001 §7: ham hata metni gövdeye ve loglara yazılmaz.

    `str(exc)` kullanmak cazip ama httpx hatalarının str()'i rutin olarak
    istek URL'ini (ve dolayısıyla sorgu parametrelerini) içerir.
    """
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/bol")
    async def bol() -> None:
        raise ZeroDivisionError(f"division by zero in secret_module {SECRET_SENTINEL}")

    caplog.set_level(logging.ERROR, logger="app.core.handlers")
    with TestClient(app, raise_server_exceptions=False) as local:
        response = local.get("/bol")

    assert response.status_code == 500
    body = response.text
    assert "division" not in body
    assert "secret_module" not in body
    assert "ZeroDivisionError" not in body
    assert SECRET_SENTINEL not in caplog.text
    assert "division by zero" not in caplog.text
    assert any(
        getattr(record, "exception_type", None) == "ZeroDivisionError" for record in caplog.records
    )

    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.INTERNAL_ERROR
    # Hata gövdesi bozuk olsa bile iz sürülebilmeli.
    assert problem.trace_id
    assert response.headers[TRACE_ID_HEADER] == str(problem.trace_id)


def test_response_validation_error_does_not_log_invalid_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/bozuk-cevap", response_model=dict[str, int])
    async def bozuk_cevap() -> dict[str, str]:
        return {"secret": SECRET_SENTINEL}

    caplog.set_level(logging.ERROR, logger="app.core.handlers")
    with TestClient(app, raise_server_exceptions=False) as local:
        response = local.get("/bozuk-cevap")

    assert response.status_code == 500
    assert SECRET_SENTINEL not in response.text
    assert SECRET_SENTINEL not in caplog.text
    assert any(
        getattr(record, "exception_type", None) == "ResponseValidationError"
        for record in caplog.records
    )


def test_unknown_path_returns_problem_body(client: TestClient) -> None:
    """404 bile RFC 9457 gövdesi döner; ham Starlette JSON'ı değil."""
    response = client.get("/api/v1/boyle-bir-sey-yok")

    assert response.status_code == 404
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.JOB_NOT_FOUND
    assert problem.trace_id


def test_method_not_allowed_returns_problem_body(client: TestClient) -> None:
    response = client.post("/api/v1/models")

    assert response.status_code == 405
    ProblemDetails.model_validate(response.json())
