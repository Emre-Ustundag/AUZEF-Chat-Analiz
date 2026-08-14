from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

EXPECTED = {
    "/api/v1/uploads": {"post"},
    "/api/v1/uploads/{upload_id}": {"get", "delete"},
    "/api/v1/models": {"get"},
    "/api/v1/analyses": {"post"},
    "/api/v1/analyses/{analysis_id}": {"get", "delete"},
    "/api/v1/analyses/{analysis_id}/result": {"get"},
    "/api/v1/analyses/{analysis_id}/export": {"get"},
}


def test_route_inventory_is_registered(client: TestClient) -> None:
    paths = cast(FastAPI, client.app).openapi()["paths"]
    for path, methods in EXPECTED.items():
        assert path in paths
        assert methods <= set(paths[path])


def test_upload_post_requires_multipart_file(client: TestClient) -> None:
    assert client.post("/api/v1/uploads").status_code == 422


def test_delete_endpoints_declare_204(client: TestClient) -> None:
    paths = cast(FastAPI, client.app).openapi()["paths"]
    for path in ("/api/v1/uploads/{upload_id}", "/api/v1/analyses/{analysis_id}"):
        assert "204" in paths[path]["delete"]["responses"], path


def test_no_route_stub_remains() -> None:
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    stubs = sum(
        source.read_text(encoding="utf-8").count("raise NotImplementedError")
        for source in api_dir.rglob("*.py")
    )
    assert stubs == 0
