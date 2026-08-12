"""Ayar kaynakları — ADR-0001 §9.

Operasyonel sınırlar environment'tan okunur; `MAX_ROWS` okunmaz (ADR-0002 #13).
"""

import pytest

from app.core.config import _REPO_ROOT, MAX_ROWS, Settings


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

    assert settings.max_upload_bytes == 150 * 1024 * 1024
    assert settings.max_uncompressed_bytes == 1024 * 1024 * 1024
    assert settings.analysis_timeout_seconds == 45 * 60
    assert settings.idempotency_ttl_seconds == 24 * 60 * 60


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUZEF_MAX_UPLOAD_BYTES", "1024")
    assert Settings().max_upload_bytes == 1024


def test_max_rows_is_frozen_and_not_environment_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0002 #13: `MAX_ROWS` bir operasyon düğmesi değil, sözleşme sabiti.

    Env'den okunabilseydi, backend'in doğru ürettiği cevap frontend'in derleme
    zamanı sabitine dayanan Zod invariant'larında düşerdi ve hiçbir drift
    kontrolü kırmızıya dönmezdi (artefaktlar CI'da varsayılan env ile
    üretiliyor). `Settings` alanı olmadığını da doğruluyoruz: alan geri
    eklenirse bu test düşer.
    """
    monkeypatch.setenv("AUZEF_MAX_ROWS", "250000")

    assert MAX_ROWS == 100_000
    assert "max_rows" not in Settings.model_fields


def test_contract_version_is_independent_of_package_version() -> None:
    # ADR-0002 #12: bir bağımlılık yükseltmesi openapi.json'ı değiştirmemeli.
    assert Settings(_env_file=None).contract_version == "1.0.0"
