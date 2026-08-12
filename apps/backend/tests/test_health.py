"""Liveness/readiness davranışı ve RFC 9457 hata sözleşmesi."""

from dataclasses import dataclass

from _pytest.capture import CaptureFixture
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode
from app.core.handlers import PROBLEM_MEDIA_TYPE
from app.core.tracing import TRACE_ID_HEADER
from app.main import create_app
from app.schemas.common import ProblemDetails
from app.schemas.health import LivenessResponse


@dataclass(frozen=True)
class FakeCheck:
    name: str
    result: bool = True
    failure: Exception | None = None

    async def check(self) -> bool:
        if self.failure is not None:
            raise self.failure
        return self.result


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
