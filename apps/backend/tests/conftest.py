"""Ortak test fixture'ları."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app


def repo_root() -> Path:
    """apps/backend/tests/conftest.py -> repo kökü.

    `pyproject.toml` apps/backend altında olduğu için (backend Docker
    context'ini node_modules'tan uzak tutmak adına) paylaşılan fixture
    dizinine ulaşmak bu helper'ı gerektiriyor.
    """
    return Path(__file__).resolve().parents[3]


FIXTURE_DIR = repo_root() / "tests" / "fixtures" / "contract"
OPENAPI_PATH = repo_root() / "docs" / "api" / "openapi.json"


def read_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False: yakalanmamış hata handler'ının gerçekten
    # 500 problem gövdesi ürettiğini görebilmek için.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def manifest() -> Any:
    return read_fixture("manifest.json")


@pytest.fixture(scope="session")
def openapi() -> Any:
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
