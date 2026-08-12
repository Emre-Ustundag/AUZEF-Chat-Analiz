"""Ayar kaynakları — ADR-0001 §9 "tüm sınırlar environment config'tir"."""

import pytest

from app.core.config import _REPO_ROOT, Settings


def test_repo_root_resolves_to_the_actual_repo_root() -> None:
    """Yol hesabı sessizce kayarsa .env hiç okunmaz, varsayılanlar kullanılır.

    Yanlış bir `parents[n]` hata vermez — yalnızca var olmayan bir dosyaya
    bakar ve her şey çalışıyormuş gibi görünür. Bu yüzden ankraj olarak
    repoda kesin bulunan iki yol kullanılıyor.
    """
    assert (_REPO_ROOT / "docs" / "mimari.md").is_file()
    assert (_REPO_ROOT / "apps" / "backend" / "pyproject.toml").is_file()


def test_defaults_match_adr_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_rows == 100_000
    assert settings.max_upload_bytes == 150 * 1024 * 1024
    assert settings.max_uncompressed_bytes == 1024 * 1024 * 1024
    assert settings.analysis_timeout_seconds == 45 * 60
    assert settings.idempotency_ttl_seconds == 24 * 60 * 60


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUZEF_MAX_ROWS", "250000")
    assert Settings().max_rows == 250_000


def test_contract_version_is_independent_of_package_version() -> None:
    # ADR-0002 #12: bir bağımlılık yükseltmesi openapi.json'ı değiştirmemeli.
    assert Settings(_env_file=None).contract_version == "1.0.0"
