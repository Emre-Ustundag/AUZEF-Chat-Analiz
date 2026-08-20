"""Uygulamanın doğrulanmış, tek kaynaklı çalışma yapılandırması."""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import AnyHttpUrl, BeforeValidator, Field, SecretStr, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_env_file() -> Path | None:
    """Repo kökündeki `.env` — YALNIZCA geliştirici kolaylığı.

    Checkout'ta bu dosya `apps/backend/app/core/config.py`'den dört seviye
    yukarıdadır (core, app, backend, apps). Container'da DEĞİLDİR:
    `infra/docker/api.Dockerfile` `apps/backend/` içeriğini `/app`'e
    kopyalıyor, yani modülün yolu `/app/app/core/config.py` ve yalnızca dört
    parent'ı var.

    Bu yüzden `parents[4]` sabit indeksi container'da `IndexError` veriyordu
    ve import zincirinin en başında olduğu için `migrate`, `api`, `worker` ve
    `beat` servislerinin DÖRDÜNÜ birden düşürüyordu — `docker compose up`
    hiçbir şey başlatamıyordu. Ölçüldü: `alembic upgrade head`,
    `config.py:16`'da `IndexError: 4` ile çıkıyordu.

    Dosyanın yokluğu container'da NORMAL: ortam değişkenleri compose'dan
    geliyor. Bulunamazsa `None` döner ve pydantic-settings yalnızca gerçek
    ortam değişkenlerini okur.
    """
    parents = Path(__file__).resolve().parents
    if len(parents) <= 4:
        return None

    candidate = parents[4] / ".env"
    return candidate if candidate.is_file() else None


_ENV_FILE = _repo_env_file()

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _normalize_log_level(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


NormalizedLogLevel = Annotated[LogLevel, BeforeValidator(_normalize_log_level)]

# Frontend `LIMITS` ile donmuş tel sözleşmesi. Environment'tan değiştirilemez.
MAX_UPLOAD_BYTES: Final[int] = 150 * 1024 * 1024
MAX_ROWS: Final[int] = 100_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUZEF_",
        extra="ignore",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
    )

    app_name: str = "AUZEF Chat Analiz API"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: NormalizedLogLevel = "INFO"
    cors_origins: list[str] | None = None
    backend_master_key: SecretStr | None = None

    database_url: str = "postgresql+asyncpg://auzef:auzef@postgres:5432/auzef"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "auzef-uploads"
    s3_region: str = "us-east-1"

    #: OOXML açılmış toplam boyut tavanı — ZIP bomba savunmasının İKİNCİL
    #: katmanı.
    #:
    #: 1 GiB'ti ve YÜK TESTİNDE DÜŞTÜ (ADR §10 risk 1'in istediği ölçüm,
    #: `scripts/load_test.py`). Ölçülen: 130,9 MB'lık gerçekçi bir dosya
    #: (2,8 M satır, 6 kolon, Türkçe metin) 1,12 GB'a açılıyor ve
    #: `declared_uncompressed_size_exceeds_limit` ile REDDEDİLİYORDU.
    #: Sıkıştırma oranı yalnızca 8,7 — bomba eşiğinin (200) çok altında,
    #: yani reddedilen şey meşru bir dosyaydı.
    #:
    #: Sözleşmenin iki sayısı birbiriyle çelişiyordu: 150 MB sıkıştırılmış
    #: upload sınırı, OOXML'in ~9x genişlemesiyle ~1,3 GB eder. İkisi aynı
    #: anda doğru olamazdı.
    #:
    #: 4 GiB, donmuş 150 MB sınırında ~27x genişlemeye izin verir — gerçek
    #: dosyaların üst sınırının rahatça üstünde. Asıl bomba savunması bu
    #: mutlak tavan DEĞİL, üye başına sıkıştırma oranı (200) ve akış
    #: sırasında sayan gerçek bayt kontrolüdür (`services/xlsx.py`); bu
    #: sayı yalnızca ikincil bir tavan.
    max_uncompressed_bytes: int = Field(default=4 * 1024 * 1024 * 1024, gt=0)
    max_compression_ratio: float = Field(default=200.0, gt=0)
    #: Profillemede taranacak azami satır — SON ÇARE tavanı.
    #:
    #: 200.000'di ve gerçek işi bozuyordu: satır sınırının (100.000) hemen
    #: üstündeki her dosya kırpılıyor, kolon istatistikleri ile `row_count`
    #: ayrışıyor ve profil şema doğrulamasından geçemiyordu (ayrıntı
    #: `services/xlsx.py`). ADR-0002 #13 ise dosyanın TAM profillenmesini
    #: şart koşuyor.
    #:
    #: Asıl bound bu sayı değil, açılmış boyut tavanı: 4 GiB ≈ 9,5 M satır
    #: ve ölçülen hız ~18.600 satır/sn (2,8 M satır → 151 sn), yani en kötü
    #: hâlde ~8,5 dakika — Celery soft time limit'inin (900 sn) altında.
    #: Buradaki 10 M yalnızca patolojik bir dosyada worker'ın süresiz
    #: dönmesini engeller.
    profile_max_scan_rows: int = Field(default=10_000_000, gt=0)
    sample_values_per_column: int = Field(default=3, ge=0)
    sample_value_max_length: int = Field(default=80, gt=0)

    analysis_timeout_seconds: int = Field(default=45 * 60, gt=0)
    analysis_soft_timeout_seconds: int = Field(default=44 * 60, gt=0)
    idempotency_ttl_seconds: int = Field(default=24 * 60 * 60, gt=0)
    openrouter_key_ttl_margin_seconds: int = Field(default=5 * 60, gt=0)
    secret_encryption_key: str | None = None
    analysis_progress_write_threshold: float = Field(default=5.0, gt=0, le=100)
    preprocess_min_message_length: int = Field(default=3, gt=0)
    report_examples_per_question: int = Field(default=3, ge=0)

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=120.0, gt=0)
    pricing_refresh_enabled: bool = True
    pricing_request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    pricing_cache_ttl_seconds: int = Field(default=60 * 60, gt=0)
    pricing_stale_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    openrouter_max_retries: int = Field(default=4, ge=0)
    openrouter_backoff_base_seconds: float = Field(default=1.0, ge=0)
    openrouter_backoff_max_seconds: float = Field(default=30.0, ge=0)
    openrouter_max_repair_attempts: int = Field(default=2, ge=0)
    llm_chunk_max_records: int = Field(default=120, gt=0)
    llm_chunk_max_prompt_tokens: int = Field(default=12_000, gt=0)
    #: Reduce aşamasında tek çağrıya gönderilecek kategori metninin yaklaşık
    #: token bütçesi. Map chunk sınırından ayrı tutulur: kayıt ve kategori
    #: şemalarının bağlam maliyeti farklıdır.
    llm_reduce_max_prompt_tokens: int = Field(default=12_000, gt=0)
    #: Tamamlanmış map chunk'larının Redis'te önbelleklenmesi (bulgu A3).
    #: Açıkken zaman aşımına uğrayan bir koşunun tamamladığı chunk'lar,
    #: aynı kayıt/model/prompt ile açılan YENİ analizde tekrar
    #: ücretlendirilmez. TTL `report_retention_hours` ile aynıdır.
    #: Kapatmak yalnızca sınıflandırmayı sıfırdan koşturmayı zorunlu kılar;
    #: doğruluğu etkilemez (`services/map_cache.py`).
    llm_map_cache_enabled: bool = True

    report_retention_hours: int = Field(default=24, gt=0)
    upload_retention_hours: int = Field(default=24, gt=0)
    orphan_object_retention_hours: int = Field(default=24, gt=0)
    storage_lifecycle_expiration_days: int = Field(default=1, gt=0)
    retention_sweep_interval_seconds: int = Field(default=3600, gt=0)

    upload_profile_soft_time_limit_seconds: int = Field(default=900, gt=0)
    upload_profile_time_limit_seconds: int = Field(default=1200, gt=0)
    contract_version: str = "1.0.0"

    @model_validator(mode="after")
    def validate_environment_security(self) -> Self:
        origins = self.cors_origins
        if origins is None:
            origins = (
                ["http://localhost:3000"] if self.environment is Environment.DEVELOPMENT else []
            )

        normalized_origins: list[str] = []
        for origin in origins:
            if "*" in origin:
                raise ValueError("CORS origin wildcard olamaz.")
            parsed = TypeAdapter(AnyHttpUrl).validate_python(origin)
            normalized_origins.append(str(parsed).rstrip("/"))
        object.__setattr__(self, "cors_origins", normalized_origins)

        if self.analysis_soft_timeout_seconds >= self.analysis_timeout_seconds:
            raise ValueError("analysis_soft_timeout_seconds hard timeout'tan küçük olmalıdır.")
        if self.pricing_stale_ttl_seconds < self.pricing_cache_ttl_seconds:
            raise ValueError("pricing_stale_ttl_seconds cache TTL'den kısa olamaz.")

        # Tarama tavanı satır sınırının ALTINA düşerse `exceeds_row_limit`
        # sessizce yalan söyler: profil tavanda kesilir, `row_count` tavana
        # eşit kalır ve sınır aşımı hiç işaretlenmez. Sözleşmenin donmuş
        # satır sınırı (ADR-0002 #13) buna bağlı.
        if self.profile_max_scan_rows <= MAX_ROWS:
            raise ValueError(
                f"profile_max_scan_rows satır sınırından ({MAX_ROWS}) büyük olmalıdır."
            )

        if self.environment is Environment.PRODUCTION:
            if self.backend_master_key is None:
                raise ValueError("Production ortamında backend_master_key zorunludur.")
            raw_key = self.backend_master_key.get_secret_value()
            try:
                decoded = base64.b64decode(raw_key, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("backend_master_key geçerli Base64 olmalıdır.") from exc
            if len(decoded) != 32:
                raise ValueError("backend_master_key Base64 kodlu 32 byte olmalıdır.")

        return self

    @property
    def analysis_hard_timeout_seconds(self) -> int:
        """Worker kodunun tarihsel adı; tek gerçek timeout alanına yönlenir."""
        return self.analysis_timeout_seconds

    @property
    def openrouter_key_ttl_seconds(self) -> int:
        return self.analysis_timeout_seconds + self.openrouter_key_ttl_margin_seconds

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "")

    def encryption_key_material(self) -> str:
        """AES anahtarı için üretimde doğrulanmış master key'i tercih eder."""
        if self.backend_master_key is not None:
            value = self.backend_master_key.get_secret_value()
            if value:
                return value
        return self.secret_encryption_key or "auzef-dev-master-key-degistirilmeli"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process başına tek doğrulanmış settings nesnesi döndürür."""
    return Settings()
