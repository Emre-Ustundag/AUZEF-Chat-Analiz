"""`docs/api/openapi.json` kayıt artefaktının doğruluğu — ADR-0002 #8."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ErrorCode
from app.core.handlers import PROBLEM_MEDIA_TYPE
from app.schemas.analysis import (
    AnalysisCreated,
    AnalysisJob,
    AnalysisRequest,
    AnalysisStatus,
    ExportFormat,
    ModelId,
    ModelList,
    PromptVersion,
)
from app.schemas.common import ProblemDetails
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.schemas.report import AnalysisReport
from app.schemas.upload import Upload, UploadCreated, UploadStatus
from scripts.export_openapi import main as export_openapi_main

EXPECTED_ENDPOINTS = {
    ("get", "/api/v1/health/live"),
    ("get", "/api/v1/health/ready"),
    ("post", "/api/v1/uploads"),
    ("get", "/api/v1/uploads/{upload_id}"),
    ("delete", "/api/v1/uploads/{upload_id}"),
    ("get", "/api/v1/models"),
    ("post", "/api/v1/analyses"),
    ("get", "/api/v1/analyses/{analysis_id}"),
    ("delete", "/api/v1/analyses/{analysis_id}"),
    ("get", "/api/v1/analyses/{analysis_id}/result"),
    ("get", "/api/v1/analyses/{analysis_id}/export"),
}

EXPECTED_STATUSES = {
    ("get", "/api/v1/health/live"): {200},
    ("get", "/api/v1/health/ready"): {200, 503},
    ("post", "/api/v1/uploads"): {202, 409, 413, 415, 422, 500, 501},
    ("get", "/api/v1/uploads/{upload_id}"): {200, 404, 422, 500, 501},
    ("delete", "/api/v1/uploads/{upload_id}"): {204, 404, 422, 500, 501},
    ("get", "/api/v1/models"): {200, 500, 501},
    ("post", "/api/v1/analyses"): {202, 404, 409, 422, 500, 501},
    ("get", "/api/v1/analyses/{analysis_id}"): {200, 404, 422, 500, 501},
    ("delete", "/api/v1/analyses/{analysis_id}"): {204, 404, 409, 422, 500, 501},
    ("get", "/api/v1/analyses/{analysis_id}/result"): {200, 404, 409, 422, 500, 501},
    ("get", "/api/v1/analyses/{analysis_id}/export"): {200, 404, 409, 422, 500, 501},
}


def test_openapi_is_not_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["export_openapi.py", "--check"])
    assert export_openapi_main() == 0


def test_documents_exactly_the_expected_endpoints(openapi: Any) -> None:
    actual = {
        (method, path)
        for path, ops in openapi["paths"].items()
        for method in ops
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED_ENDPOINTS


@pytest.mark.parametrize(
    ("schema_name", "members"),
    [
        ("ErrorCode", {c.value for c in ErrorCode}),
        ("UploadStatus", {s.value for s in UploadStatus}),
        ("AnalysisStatus", {s.value for s in AnalysisStatus}),
        ("ExportFormat", {f.value for f in ExportFormat}),
        ("ModelId", {model.value for model in ModelId}),
        ("PromptVersion", {prompt.value for prompt in PromptVersion}),
    ],
)
def test_enum_members(openapi: Any, schema_name: str, members: set[str]) -> None:
    assert set(openapi["components"]["schemas"][schema_name]["enum"]) == members


@pytest.mark.parametrize("orphan", ["HTTPValidationError", "ValidationError"])
def test_fastapi_default_422_models_are_removed(openapi: Any, orphan: str) -> None:
    """Sunucu bu gövdeleri ASLA üretmiyor.

    RequestValidationError handler'ı hepsini ProblemDetails'e çeviriyor;
    şemada kalsalardı kayıt artefaktı ilk günden yalan söylerdi.
    """
    assert orphan not in openapi["components"]["schemas"]


def test_every_operation_documents_exact_status_set(openapi: Any) -> None:
    for path, ops in openapi["paths"].items():
        for method, operation in ops.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            documented = {int(s) for s in operation["responses"] if s.isdigit()}
            assert documented == EXPECTED_STATUSES[(method, path)], f"{method} {path}"


def test_problem_details_schema_is_fully_documented(openapi: Any) -> None:
    schema = openapi["components"]["schemas"]["ProblemDetails"]
    assert set(schema["properties"]) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "trace_id",
        "errors",
        "retry_after",
    }
    assert set(schema["required"]) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "trace_id",
        "errors",
    }
    assert schema["properties"]["code"]["$ref"].endswith("/ErrorCode")
    assert schema["properties"]["trace_id"]["format"] == "uuid"
    assert schema["properties"]["retry_after"]["type"] == "number"
    assert "anyOf" not in schema["properties"]["retry_after"]


def test_error_responses_only_use_problem_media_type(openapi: Any) -> None:
    for path, ops in openapi["paths"].items():
        for method, operation in ops.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not path.startswith("/api/v1/health/"):
                assert operation["responses"]["501"]["x-error-codes"] == ["NOT_IMPLEMENTED"]
            for status, response in operation["responses"].items():
                if status.isdigit() and int(status) >= 400:
                    assert set(response["content"]) == {PROBLEM_MEDIA_TYPE}, (
                        method,
                        path,
                        status,
                    )
                    media = response["content"][PROBLEM_MEDIA_TYPE]
                    assert media["schema"] == {"$ref": "#/components/schemas/ProblemDetails"}
                    assert media["examples"], (method, path, status)


def test_provider_failures_are_only_nested_job_errors(openapi: Any) -> None:
    for ops in openapi["paths"].values():
        for operation in ops.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            assert {"429", "502", "504"}.isdisjoint(operation["responses"])


def test_idempotency_key_only_on_the_two_posts(openapi: Any) -> None:
    with_header = {
        (method, path)
        for path, ops in openapi["paths"].items()
        for method, operation in ops.items()
        if any(p.get("name") == "Idempotency-Key" for p in operation.get("parameters", []))
    }
    assert with_header == {("post", "/api/v1/uploads"), ("post", "/api/v1/analyses")}


def test_openrouter_key_scheme_only_on_create_analysis(openapi: Any) -> None:
    secured = {
        (method, path)
        for path, ops in openapi["paths"].items()
        for method, operation in ops.items()
        if "security" in operation
    }
    assert secured == {("post", "/api/v1/analyses")}
    scheme = openapi["components"]["securitySchemes"]["OpenRouterKey"]
    assert scheme["name"] == "X-OpenRouter-Key"
    assert scheme["in"] == "header"


def test_openrouter_key_is_not_also_a_plain_parameter(openapi: Any) -> None:
    """Anahtar YALNIZCA security scheme olarak belgelenir.

    `require_openrouter_key` dependency'si `str | None` imzalı (zorunlu
    `Header(...)` REQUEST_VALIDATION üretirdi, biz PROVIDER_AUTH_FAILED
    istiyoruz). FastAPI bunu opsiyonel + nullable bir header parametresi
    olarak da belgeliyor; kalsaydı şema "bu header isteğe bağlı" derken
    sunucu header'sız isteğe 422 verirdi ve üretilmiş bir client anahtarı
    hiç göndermeyebilirdi.
    """
    for path, ops in openapi["paths"].items():
        for method, operation in ops.items():
            names = {p.get("name") for p in operation.get("parameters", [])}
            assert "X-OpenRouter-Key" not in names, f"{method} {path}"


def test_export_documents_content_disposition(openapi: Any) -> None:
    responses = openapi["paths"]["/api/v1/analyses/{analysis_id}/export"]["get"]["responses"]
    assert "Content-Disposition" in responses["200"]["headers"]


def test_export_media_schemas_and_json_example_are_exact(openapi: Any) -> None:
    response = openapi["paths"]["/api/v1/analyses/{analysis_id}/export"]["get"]["responses"]["200"]
    content = response["content"]
    xlsx = content["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
    assert xlsx["schema"] == {"type": "string", "format": "binary"}

    json_export = content["application/json"]
    assert json_export["schema"] == {"$ref": "#/components/schemas/AnalysisReport"}
    examples = json_export["examples"]
    assert set(examples) == {"JSON export"}
    AnalysisReport.model_validate(examples["JSON export"]["value"])


def test_trace_id_header_documented_everywhere(openapi: Any) -> None:
    for path, ops in openapi["paths"].items():
        for method, operation in ops.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for status, response in operation["responses"].items():
                header = response.get("headers", {}).get("X-Trace-Id")
                assert header, f"{method} {path} {status}"
                assert header["schema"]["format"] == "uuid"
                assert header["example"]


def test_request_and_success_examples_are_documented(openapi: Any) -> None:
    upload_request = openapi["paths"]["/api/v1/uploads"]["post"]["requestBody"]["content"]
    analysis_request = openapi["paths"]["/api/v1/analyses"]["post"]["requestBody"]["content"]
    assert next(iter(upload_request.values()))["examples"]
    analysis_examples = next(iter(analysis_request.values()))["examples"]
    AnalysisRequest.model_validate(next(iter(analysis_examples.values()))["value"])

    response_models: dict[tuple[str, str], type[BaseModel]] = {
        ("get", "/api/v1/health/live"): LivenessResponse,
        ("get", "/api/v1/health/ready"): ReadinessResponse,
        ("post", "/api/v1/uploads"): UploadCreated,
        ("get", "/api/v1/uploads/{upload_id}"): Upload,
        ("get", "/api/v1/models"): ModelList,
        ("post", "/api/v1/analyses"): AnalysisCreated,
        ("get", "/api/v1/analyses/{analysis_id}"): AnalysisJob,
        ("get", "/api/v1/analyses/{analysis_id}/result"): AnalysisReport,
    }
    for (method, path), model in response_models.items():
        success = openapi["paths"][path][method]["responses"]
        status = "202" if method == "post" else "200"
        media = next(iter(success[status]["content"].values()))
        assert media["examples"], (method, path)
        for example in media["examples"].values():
            model.model_validate(example["value"])

    validation = openapi["paths"]["/api/v1/analyses"]["post"]["responses"]["422"]
    examples = validation["content"][PROBLEM_MEDIA_TYPE]["examples"]
    assert {"REQUEST_VALIDATION", "INVALID_MODEL", "INVALID_PROMPT", "COST_LIMIT_EXCEEDED"} <= set(
        examples
    )
    for example in examples.values():
        ProblemDetails.model_validate(example["value"])


def test_contract_version_is_pinned(openapi: Any) -> None:
    # Paket sürümünden bağımsız: bir bağımlılık yükseltmesi openapi.json'ı
    # değiştirmemeli (ADR-0002 #12).
    assert openapi["info"]["version"] == "1.0.0"


def test_served_openapi_matches_committed_file(client: TestClient, openapi: Any) -> None:
    assert client.get("/openapi.json").json() == openapi
