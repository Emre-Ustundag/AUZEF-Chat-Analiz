"""Ayar kaynakları, ortam ayrımı ve fail-fast doğrulama."""

import base64
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import MAX_ROWS, MAX_UPLOAD_BYTES, Environment, Settings


def test_env_dosyasi_checkoutta_repo_kokunden_okunur() -> None:
    env_file = config_module._repo_env_file()
    root = Path(config_module.__file__).resolve().parents[4]

    assert (root / "docs" / "mimari.md").is_file()
    assert (root / "apps" / "backend" / "pyproject.toml").is_file()
    # `.env` commit EDİLMİYOR; varsa repo kökünden okunmalı, yoksa None.
    assert env_file in {None, root / ".env"}


def test_sig_dizin_derinliginde_env_aranmaz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container yerleşimi: modül `/app/app/core/config.py`'de duruyor.

    `infra/docker/api.Dockerfile` `apps/backend/` içeriğini `/app`'e
    kopyaladığı için modülün yalnızca DÖRT parent'ı var. Sabit `parents[4]`
    indeksi burada `IndexError` veriyordu ve import zincirinin en başında
    olduğu için `migrate`, `api`, `worker` ve `beat` servislerinin dördünü
    birden düşürüyordu — `docker compose up` hiçbir şey başlatamıyordu.

    Bu testin var olma sebebi, eski testin YALNIZCA checkout yerleşimini
    ölçmesi ve container yerleşimini hiç görmemesiydi.
    """
    monkeypatch.setattr(config_module, "__file__", "/app/app/core/config.py")

    assert config_module._repo_env_file() is None


def test_defaults_match_adr_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.backend_master_key is None
    assert MAX_ROWS == 100_000
    assert MAX_UPLOAD_BYTES == 150 * 1024 * 1024
    assert settings.max_uncompressed_bytes == 4 * 1024 * 1024 * 1024
    assert settings.analysis_timeout_seconds == 45 * 60
    assert settings.idempotency_ttl_seconds == 24 * 60 * 60
    # Test süreci gerçek ağa çıkmasın diye conftest bu alanı env'den
    # kapatır; üretim varsayılanını alan tanımından doğruluyoruz.
    assert Settings.model_fields["pricing_refresh_enabled"].default is True
    assert settings.pricing_cache_ttl_seconds == 60 * 60
    assert settings.pricing_stale_ttl_seconds == 7 * 24 * 60 * 60


def test_pricing_stale_cache_taze_cacheden_kisa_olamaz() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            pricing_cache_ttl_seconds=3_600,
            pricing_stale_ttl_seconds=60,
        )


#: Ölçülmüş OOXML genişleme oranı. 130,9 MB'lık gerçekçi bir dosyanın
#: (2,8 M satır, 6 kolon, Türkçe metin) `xl/worksheets/sheet1.xml` üyesi
#: 1145 MB açılıyor → 8,7x. Daha dar kolonlu/metinsiz dosyalar daha yüksek
#: oran verebilir, bu yüzden 10 temkinli bir taban.
MEASURED_OOXML_EXPANSION = 10


def test_acilmis_boyut_tavani_donmus_upload_sinirini_karsilar() -> None:
    """Sözleşmenin iki sayısı birbiriyle ÇELİŞEMEZ.

    150 MB sıkıştırılmış dosya kabul edeceğini söyleyip, o dosyanın açılmış
    hâlini reddeden bir tavan koymak sessiz bir tuzaktır: kullanıcı sınırın
    altındaki bir dosyayla `UPLOAD_CORRUPT_OR_ENCRYPTED` alır ve mesaj
    "bozuk, şifrelenmiş veya makro içeren" der — üçü de yanlış.

    Bu tam olarak yaşandı: tavan 1 GiB'ken 130,9 MB'lık bir dosya (oran 8,7,
    bomba eşiği 200'ün çok altında) reddedildi. Yük testi (ADR §10 risk 1)
    bulmasaydı ilk gerçek 130 MB'lık dosyada üretimde ortaya çıkardı.
    """
    settings = Settings(_env_file=None)

    assert settings.max_uncompressed_bytes >= MAX_UPLOAD_BYTES * MEASURED_OOXML_EXPANSION


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # `_env_file=None`: dosyadaki diğer testler gibi. Geliştiricinin kök
    # `.env`'i okunursa (örn. AUZEF_ENVIRONMENT=production, master key'siz)
    # test lokalde patlar, CI'da `.env` olmadığı için yeşil kalırdı.
    monkeypatch.setenv("AUZEF_MAX_UNCOMPRESSED_BYTES", "2048")
    assert Settings(_env_file=None).max_uncompressed_bytes == 2048


def test_frontend_limits_are_frozen_and_not_environment_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUZEF_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("AUZEF_MAX_ROWS", "250000")

    assert MAX_UPLOAD_BYTES == 150 * 1024 * 1024
    assert MAX_ROWS == 100_000
    assert "max_upload_bytes" not in Settings.model_fields
    assert "max_rows" not in Settings.model_fields


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


def test_tests_are_isolated_from_the_developer_env_file() -> None:
    """`.env` testlere SIZMAMALI — regresyon.

    `core/config.py` repo kökündeki `.env`'i çalışma dizininden bağımsız
    okuyor. Bu geliştirici kolaylığı doğru, ama testlere sızdığında
    `test_master_key_degisirse_cozulemez` sessizce etkisizleşiyordu: `.env`
    içindeki `AUZEF_BACKEND_MASTER_KEY` iki Settings örneğine de aynı anahtarı
    türettiriyor ve "master key değişirse çözülemez" iddiası çözebiliyordu.

    CI'da `.env` bulunmadığı için orası yeşil kalıyor; kusur yalnızca
    `docker compose` çalıştıran geliştiricinin makinesinde görünüyordu. Bu
    test yalıtımın kendisini bekçiler (`tests/conftest.py`).
    """
    assert Settings.model_config.get("env_file") is None
    assert not [key for key in os.environ if key.startswith("AUZEF_BACKEND_MASTER_KEY")]
    assert Settings().backend_master_key is None
