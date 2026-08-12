"""Çalışma sınırları — ADR-0001 §9. Hepsi environment ile değiştirilebilir."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: apps/backend/app/core/config.py -> repo kökü (core, app, backend, apps).
#: .env kökte duruyor (Docker Compose de oradan okuyor) ama backend
#: apps/backend içinden çalıştırılıyor; göreli bir yol yanlış dosyaya
#: bakardı.
_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUZEF_",
        extra="ignore",
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
    )

    #: Sıkıştırılmış upload sınırı: 150 MB.
    max_upload_bytes: int = 150 * 1024 * 1024
    #: OOXML açılmış toplam boyut sınırı: 1 GB.
    max_uncompressed_bytes: int = 1024 * 1024 * 1024
    #: ADR-0002 #2: hard reject DEĞİL — bu sınırın üstü kırpılır ve uyarılır.
    max_rows: int = 100_000
    #: ADR-0002 #3: Idempotency-Key kaydının saklama süresi (24 saat).
    idempotency_ttl_seconds: int = 24 * 60 * 60
    #: ADR-0001 §2: analiz hard timeout'u 45 dakika.
    analysis_timeout_seconds: int = 45 * 60

    #: Sözleşme sürümü. Paket sürümünden BİLEREK ayrı: bir bağımlılık
    #: yükseltmesi openapi.json'ı değiştirmesin (ADR-0002 #12).
    contract_version: str = "1.0.0"


settings = Settings()
