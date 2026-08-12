"""Uygulama ayarları ve çalışma sınırları.

Tek kaynak Pydantic Settings'tir. `get_settings()` cache'i hem API'nin hem
OpenAPI/fixture üreticilerinin aynı değerleri görmesini sağlar.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, BeforeValidator, Field, SecretStr, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: apps/backend/app/core/config.py -> repo kökü (core, app, backend, apps).
_REPO_ROOT = Path(__file__).resolve().parents[4]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _normalize_log_level(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


NormalizedLogLevel = Annotated[LogLevel, BeforeValidator(_normalize_log_level)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUZEF_",
        extra="ignore",
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: NormalizedLogLevel = "INFO"
    cors_origins: list[str] | None = None
    backend_master_key: SecretStr | None = None

    #: Sıkıştırılmış upload sınırı: 150 MB.
    max_upload_bytes: int = Field(default=150 * 1024 * 1024, gt=0)
    #: OOXML açılmış toplam boyut sınırı: 1 GB.
    max_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    #: ADR-0002 #2: hard reject DEĞİL — bu sınırın üstü kırpılır ve uyarılır.
    max_rows: int = Field(default=100_000, gt=0)
    #: ADR-0002 #3: Idempotency-Key kaydının saklama süresi (24 saat).
    idempotency_ttl_seconds: int = Field(default=24 * 60 * 60, gt=0)
    #: ADR-0001 §2: analiz hard timeout'u 45 dakika.
    analysis_timeout_seconds: int = Field(default=45 * 60, gt=0)

    #: Sözleşme sürümü paket sürümünden bilinçli olarak ayrıdır.
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process başına tek doğrulanmış settings nesnesi döndürür."""
    return Settings()
