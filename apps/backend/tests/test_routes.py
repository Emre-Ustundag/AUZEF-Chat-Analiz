"""Route envanteri — ADR-0001 §6'daki dokuz uç mevcut mu?"""

import pytest
from fastapi.testclient import TestClient

from app.schemas.common import ProblemDetails

UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
ANALYSIS_ID = "6b1cf3d2-0a44-4f1b-9d64-1c2a7e5f8b90"

ROUTES = [
    ("GET", f"/api/v1/uploads/{UPLOAD_ID}"),
    ("DELETE", f"/api/v1/uploads/{UPLOAD_ID}"),
    ("GET", "/api/v1/models"),
    ("GET", f"/api/v1/analyses/{ANALYSIS_ID}"),
    ("DELETE", f"/api/v1/analyses/{ANALYSIS_ID}"),
    ("GET", f"/api/v1/analyses/{ANALYSIS_ID}/result"),
    ("GET", f"/api/v1/analyses/{ANALYSIS_ID}/export"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_route_exists_and_returns_501(client: TestClient, method: str, path: str) -> None:
    """501 bekleniyor — 404/405 DEĞİL.

    404 yol yanlış demek, 405 method yanlış demek. Yalnızca 501, ucun doğru
    bağlandığını ve gövdesinin uygulama kartını beklediğini kanıtlar.
    """
    response = client.request(method, path)

    assert response.status_code == 501, f"{method} {path}"
    assert ProblemDetails.model_validate(response.json()).status == 501


def test_upload_post_requires_multipart_file(client: TestClient) -> None:
    # Dosya olmadan 422; dosyayla 501 (yani uç gerçekten multipart bekliyor).
    assert client.post("/api/v1/uploads").status_code == 422

    response = client.post("/api/v1/uploads", files={"file": ("veri.xlsx", b"PK\x03\x04")})
    assert response.status_code == 501


def test_delete_endpoints_declare_204(openapi: object) -> None:
    assert isinstance(openapi, dict)
    for path in ("/api/v1/uploads/{upload_id}", "/api/v1/analyses/{analysis_id}"):
        assert "204" in openapi["paths"][path]["delete"]["responses"], path


def test_stub_count_matches_endpoint_count() -> None:
    """Route iş mantığı kartlarının listesi tam olarak dokuz madde.

    `grep -rn "raise NotImplementedError" app/api` ile aynı sayıyı vermeli.
    """
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    stubs = sum(
        source.read_text(encoding="utf-8").count("raise NotImplementedError")
        for source in api_dir.rglob("*.py")
    )
    assert stubs == 9
