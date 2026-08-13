"""Liveness/readiness davranışı ve RFC 9457 hata sözleşmesi."""

import asyncio
from dataclasses import dataclass
from time import perf_counter

import pytest
from _pytest.capture import CaptureFixture
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode
from app.core.handlers import PROBLEM_MEDIA_TYPE
from app.core.tracing import TRACE_ID_HEADER
from app.main import create_app
from app.schemas.common import ProblemDetails
from app.schemas.health import LivenessResponse
from app.services import health as health_service


@dataclass(frozen=True)
class FakeCheck:
    name: str
    result: bool = True
    failure: Exception | None = None

    async def check(self) -> bool:
        if self.failure is not None:
            raise self.failure
        return self.result


@dataclass(frozen=True)
class HangingCheck:
    """Asla dönmeyen bağımlılık — timeout olmadan `/ready`'i süresiz bloklar."""

    name: str

    async def check(self) -> bool:
        await asyncio.Event().wait()
        return True


def test_liveness_does_not_check_dependencies(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    LivenessResponse.model_validate(response.json())


def test_readiness_without_checks_fails_safely(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.SERVICE_NOT_READY
    assert response.headers[TRACE_ID_HEADER] == str(problem.trace_id)


def test_readiness_lists_all_successful_checks() -> None:
    app = create_app(readiness_checks=[FakeCheck("postgres"), FakeCheck("redis")])
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": [
            {"name": "postgres", "status": "ok"},
            {"name": "redis", "status": "ok"},
        ],
    }


def test_failed_readiness_check_returns_service_not_ready() -> None:
    app = create_app(readiness_checks=[FakeCheck("postgres", result=False)])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.SERVICE_NOT_READY
    assert response.headers[TRACE_ID_HEADER] == str(problem.trace_id)


def test_readiness_exception_does_not_leak_raw_message(capsys: CaptureFixture[str]) -> None:
    sentinel = "db-password=super-secret-readiness-value"
    app = create_app(readiness_checks=[FakeCheck("postgres", failure=RuntimeError(sentinel))])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert sentinel not in response.text
    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err


def test_hanging_check_times_out_instead_of_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asılı bağımlılık `/ready`'i bloklamaz, süre bütçesi dolunca 503 döner."""
    monkeypatch.setattr(health_service, "CHECK_TIMEOUT_SECONDS", 0.05)
    app = create_app(readiness_checks=[HangingCheck("postgres")])

    started = perf_counter()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health/ready")
    elapsed = perf_counter() - started

    assert response.status_code == 503
    assert ProblemDetails.model_validate(response.json()).code is ErrorCode.SERVICE_NOT_READY
    assert elapsed < 1.0


async def test_checks_run_in_parallel_not_sequentially() -> None:
    """Toplam süre kontrollerin toplamı değil, en yavaşı kadar olmalı."""

    @dataclass(frozen=True)
    class SlowCheck:
        name: str

        async def check(self) -> bool:
            await asyncio.sleep(0.1)
            return True

    checks = (SlowCheck("a"), SlowCheck("b"), SlowCheck("c"))

    started = perf_counter()
    results = await health_service.run_readiness_checks(checks)
    elapsed = perf_counter() - started

    assert [result.name for result in results] == ["a", "b", "c"]
    assert all(result.ready for result in results)
    assert elapsed < 0.25
