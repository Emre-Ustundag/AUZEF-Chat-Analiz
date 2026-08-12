"""Ayar kaynakları, ortam ayrımı ve fail-fast doğrulama."""

import base64

import pytest
from pydantic import ValidationError

from app.core.config import _REPO_ROOT, Environment, Settings


def test_repo_root_resolves_to_the_actual_repo_root() -> None:
    assert (_REPO_ROOT / "docs" / "mimari.md").is_file()
    assert (_REPO_ROOT / "apps" / "backend" / "pyproject.toml").is_file()


def test_defaults_match_adr_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.backend_master_key is None
    assert settings.max_rows == 100_000
    assert settings.max_upload_bytes == 150 * 1024 * 1024
    assert settings.max_uncompressed_bytes == 1024 * 1024 * 1024
    assert settings.analysis_timeout_seconds == 45 * 60
    assert settings.idempotency_ttl_seconds == 24 * 60 * 60


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUZEF_MAX_ROWS", "250000")
    assert Settings().max_rows == 250_000


def test_contract_version_is_independent_of_package_version() -> None:
    assert Settings(_env_file=None).contract_version == "1.0.0"


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PRODUCTION])
def test_non_development_cors_defaults_to_empty(environment: Environment) -> None:
    if environment is Environment.PRODUCTION:
        settings = Settings(
            _env_file=None,
            environment=environment,
            backend_master_key=base64.b64encode(b"k" * 32).decode(),
        )
    else:
        settings = Settings(_env_file=None, environment=environment)
    assert settings.cors_origins == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("AUZEF_ENVIRONMENT", "staging"),
        ("AUZEF_LOG_LEVEL", "VERBOSE"),
        ("AUZEF_CORS_ORIGINS", '["*"]'),
        ("AUZEF_CORS_ORIGINS", '["https://*.example.com"]'),
    ],
)
def test_invalid_runtime_configuration_fails_fast(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "master_key",
    [None, "base64-degil!", base64.b64encode(b"too-short").decode()],
)
def test_production_requires_base64_encoded_32_byte_key(master_key: str | None) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            backend_master_key=master_key,
        )


def test_production_accepts_valid_master_key() -> None:
    settings = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        backend_master_key=base64.b64encode(b"k" * 32).decode(),
    )
    assert settings.environment is Environment.PRODUCTION


def test_log_level_is_case_insensitive() -> None:
    assert Settings(_env_file=None, log_level="warning").log_level == "WARNING"
