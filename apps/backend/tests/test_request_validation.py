"""FastAPI'nin varsayılan 422 gövdesinin ezilmesi — ADR-0002 #7."""

import json

from fastapi.testclient import TestClient

from app.core.errors import ErrorCode
from app.schemas.common import ProblemDetails

ANALYSIS_ID = "6b1cf3d2-0a44-4f1b-9d64-1c2a7e5f8b90"

VALID_BODY = {
    "upload_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
    "sheet_name": "Mesajlar",
    "text_column": "mesaj",
    "model": "anthropic/claude-sonnet-4.6",
    "prompt_version": "faq_analysis/v1",
    "top_n": 8,
    "max_cost_usd": 10,
}
HEADERS = {"X-OpenRouter-Key": "sk-or-test"}


def _post(client: TestClient, **overrides: object) -> tuple[int, dict[str, object]]:
    response = client.post("/api/v1/analyses", json=VALID_BODY | overrides, headers=HEADERS)
    return response.status_code, response.json()


def test_out_of_range_field_reports_field_path(client: TestClient) -> None:
    status, body = _post(client, top_n=0)

    assert status == 422
    problem = ProblemDetails.model_validate(body)
    assert problem.code is ErrorCode.REQUEST_VALIDATION
    assert [e.field for e in problem.errors] == ["top_n"]


def test_nonempty_unknown_model_has_specific_error_code(client: TestClient) -> None:
    status, body = _post(client, model="provider/model-not-allowed")

    assert status == 422
    problem = ProblemDetails.model_validate(body)
    assert problem.code is ErrorCode.INVALID_MODEL
    assert [e.field for e in problem.errors] == ["model"]


def test_nonempty_unknown_prompt_has_specific_error_code(client: TestClient) -> None:
    status, body = _post(client, prompt_version="faq_analysis/v999")

    assert status == 422
    problem = ProblemDetails.model_validate(body)
    assert problem.code is ErrorCode.INVALID_PROMPT
    assert [e.field for e in problem.errors] == ["prompt_version"]


def test_empty_whitelist_value_remains_general_validation(client: TestClient) -> None:
    status, body = _post(client, model="")

    assert status == 422
    assert ProblemDetails.model_validate(body).code is ErrorCode.REQUEST_VALIDATION


def test_other_invalid_field_keeps_general_validation_precedence(client: TestClient) -> None:
    status, body = _post(client, model="provider/model-not-allowed", top_n=0)

    assert status == 422
    assert ProblemDetails.model_validate(body).code is ErrorCode.REQUEST_VALIDATION


def test_validation_body_never_echoes_input(client: TestClient) -> None:
    """En kritik iddia.

    FastAPI'nin varsayılan 422 gövdesi `input` alanını içerir. POST /analyses
    için bu canlı bir sır sızıntısı yolu: istemci OpenRouter anahtarını
    yanlışlıkla gövdeye koyarsa varsayılan handler onu aynen geri yansıtır ve
    hem frontend hata state'ine hem de gövde yakalayan proxy loglarına düşer.
    """
    _, body = _post(client, model="sk-or-v1-cok-gizli-anahtar", top_n=0)
    serialized = json.dumps(body, ensure_ascii=False)

    assert "input" not in serialized
    assert "sk-or-v1-cok-gizli-anahtar" not in serialized


def test_unknown_field_is_rejected(client: TestClient) -> None:
    # ApiRequestModel extra="forbid": yazım hatası sessizce yutulmasın.
    status, body = _post(client, bilinmeyen_alan="x")

    assert status == 422
    assert ProblemDetails.model_validate(body).code is ErrorCode.REQUEST_VALIDATION


def test_malformed_json_is_422_not_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analyses",
        content=b"{bozuk",
        headers=HEADERS | {"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert ProblemDetails.model_validate(response.json()).code is ErrorCode.REQUEST_VALIDATION


def test_malformed_multipart_is_safe_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        content=b"bu bir multipart govdesi degil",
        headers={"Content-Type": "multipart/form-data; boundary=sinir"},
    )

    assert response.status_code == 422
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.REQUEST_VALIDATION
    assert problem.detail == "İstek gövdesi veya parametreleri doğrulanamadı."
    assert "multipart" not in response.text.lower()
    assert "boundary" not in response.text.lower()


def test_missing_multipart_boundary_is_safe_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        content=b"dosya",
        headers={"Content-Type": "multipart/form-data"},
    )

    assert response.status_code == 422
    problem = ProblemDetails.model_validate(response.json())
    assert problem.code is ErrorCode.REQUEST_VALIDATION
    assert problem.detail == "İstek gövdesi veya parametreleri doğrulanamadı."


def test_missing_api_key_returns_provider_auth_failed(client: TestClient) -> None:
    """ADR-0002: eksik anahtar REQUEST_VALIDATION değil PROVIDER_AUTH_FAILED.

    Teknik olarak eksik bir zorunlu header bir istek doğrulama hatasıdır, ama
    boş anahtar alanı için PROVIDER_AUTH_FAILED'in Türkçe mesajı belirgin
    şekilde daha yardımcı. Mock da aynısını döner.
    """
    response = client.post("/api/v1/analyses", json=VALID_BODY)

    assert response.status_code == 422
    assert ProblemDetails.model_validate(response.json()).code is ErrorCode.PROVIDER_AUTH_FAILED


def test_blank_api_key_returns_provider_auth_failed(client: TestClient) -> None:
    response = client.post("/api/v1/analyses", json=VALID_BODY, headers={"X-OpenRouter-Key": "  "})

    assert response.status_code == 422
    assert ProblemDetails.model_validate(response.json()).code is ErrorCode.PROVIDER_AUTH_FAILED


def test_api_key_value_never_appears_in_error_body(client: TestClient) -> None:
    """Anahtar geçerli ama gövde bozuk: anahtar hiçbir yere sızmamalı."""
    response = client.post(
        "/api/v1/analyses",
        json=VALID_BODY | {"top_n": 0},
        headers=HEADERS,
    )

    assert HEADERS["X-OpenRouter-Key"] not in response.text


def test_invalid_export_format_reports_query_field(client: TestClient) -> None:
    response = client.get(f"/api/v1/analyses/{ANALYSIS_ID}/export", params={"format": "pdf"})

    assert response.status_code == 422
    problem = ProblemDetails.model_validate(response.json())
    assert problem.errors[0].field == "query.format"


def test_invalid_uuid_in_path_reports_path_field(client: TestClient) -> None:
    response = client.get("/api/v1/analyses/uuid-degil")

    assert response.status_code == 422
    problem = ProblemDetails.model_validate(response.json())
    assert problem.errors[0].field == "path.analysis_id"


def test_valid_body_reaches_the_stub(client: TestClient) -> None:
    """Doğrulama stub'tan ÖNCE çalışır.

    Bu yüzden bozuk gövde 422, geçerli gövde 501 verir — route gövdeleri
    doldurunca yukarıdaki 422 iddiaları aynen geçerli kalır.
    """
    status, body = _post(client)

    assert status == 501
    assert ProblemDetails.model_validate(body).code is ErrorCode.NOT_IMPLEMENTED
