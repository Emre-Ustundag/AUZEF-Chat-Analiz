"""Yapılandırılmış logların izinli alanları ve redaksiyonu."""

import json

from _pytest.capture import CaptureFixture
from fastapi.testclient import TestClient
from structlog.typing import EventDict

from app.core.logging import REDACTED, redact_sensitive
from app.main import create_app


def test_nested_redaction_masks_all_sensitive_names() -> None:
    raw: EventDict = {
        "event": "test",
        "authorization": "Bearer secret-one",
        "nested": {
            "cookie": "secret-two",
            "x_openrouter_key": "secret-three",
            "api_key": "secret-four",
            "token_value": "secret-five",
            "secret": "secret-six",
            "password": "secret-seven",
            "backend_master_key": "secret-master-key",
            "body": {"safe": "still-secret"},
            "items": [{"message_text": "secret-eight"}],
        },
        "safe": "visible",
    }

    redacted = redact_sensitive(None, "info", raw)
    assert redacted["authorization"] == REDACTED
    assert redacted["nested"] == {
        "cookie": REDACTED,
        "x_openrouter_key": REDACTED,
        "api_key": REDACTED,
        "token_value": REDACTED,
        "secret": REDACTED,
        "password": REDACTED,
        "backend_master_key": REDACTED,
        "body": REDACTED,
        "items": [{"message_text": REDACTED}],
    }
    assert redacted["safe"] == "visible"


def test_request_log_contains_only_safe_request_metadata(capsys: CaptureFixture[str]) -> None:
    sentinels = [
        "authorization-sentinel",
        "cookie-sentinel",
        "openrouter-sentinel",
        "body-sentinel",
        "query-sentinel",
    ]
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/analyses?unsafe=query-sentinel",
            headers={
                "Authorization": "Bearer authorization-sentinel",
                "Cookie": "session=cookie-sentinel",
                "X-OpenRouter-Key": "openrouter-sentinel",
            },
            json={"message_text": "body-sentinel"},
        )

    assert response.status_code == 422
    output = capsys.readouterr().out
    assert all(sentinel not in output for sentinel in sentinels)
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    request_record = next(
        record for record in records if record.get("event") == "request_completed"
    )
    assert set(request_record) == {
        "duration_ms",
        "environment",
        "event",
        "method",
        "path",
        "status_code",
        "trace_id",
    }
    assert request_record["path"] == "/api/v1/analyses"


def test_unhandled_exception_log_does_not_include_raw_message(
    capsys: CaptureFixture[str],
) -> None:
    sentinel = "provider-url?api_key=exception-secret-sentinel"
    app = create_app()

    @app.get("/unsafe-test-route")
    async def unsafe_test_route() -> None:
        raise RuntimeError(sentinel)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unsafe-test-route")

    output = capsys.readouterr().out
    assert response.status_code == 500
    assert sentinel not in response.text
    assert sentinel not in output
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    error_record = next(
        record for record in records if record.get("event") == "unhandled_exception"
    )
    assert error_record["exception_type"] == "RuntimeError"
    assert error_record["trace_id"] == response.headers["X-Trace-Id"]
