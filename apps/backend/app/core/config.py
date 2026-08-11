"""Uygulama yapılandırması.

ADR §9 değişmezi: sınırlar environment config'tir, sabit kodlanmaz. Bu modül
o sınırların TEK kaynağıdır; başka hiçbir yerde 150 MB / 1 GB / 100.000 gibi
bir sayı literal olarak geçmez.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------- genel
    app_name: str = "AUZEF Chat Analiz API"
    environment: str = "development"
    debug: bool = False

    # ADR §9: geliştirme CORS allowlist'i yalnızca açıkça tanımlı local
    # origin'leri kabul eder. Üretimde frontend ve API aynı origin altında
    # çalıştığı için bu liste boş bırakılabilir.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ------------------------------------------------------------ altyapı
    database_url: str = "postgresql+asyncpg://auzef:auzef@postgres:5432/auzef"
    redis_url: str = "redis://redis:6379/0"

    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "auzef-uploads"
    s3_region: str = "us-east-1"

    # ------------------------------------------------------ upload sınırları
    # ADR §9: varsayılan sıkıştırılmış upload sınırı 150 MB.
    max_upload_bytes: int = 150 * 1024 * 1024

    # ADR §9: OOXML açılmış toplam boyut sınırı 1 GB. ZIP bomba savunmasının
    # birinci ayağı.
    max_uncompressed_bytes: int = 1024 * 1024 * 1024

    # ZIP bomba savunmasının ikinci ayağı: tek bir üyenin sıkıştırma oranı.
    # Gerçek xlsx'lerde XML çok iyi sıkışır (60-80x görülür), bu yüzden sınır
    # cömert tutuldu; asıl koruma açılmış toplam boyuttur.
    max_compression_ratio: float = 200.0

    # ADR §9: varsayılan satır sınırı 100.000. Aşılırsa iş BAŞARISIZ OLMAZ,
    # profilde exceeds_row_limit ile işaretlenir (plan §3.2 g).
    max_rows: int = 100_000

    # Profil çıkarırken bir sayfadan okunacak azami satır. Satır sınırının
    # üstünde bir tavan: 5 milyon satırlık bir sayfa yüzünden worker'ın
    # saatlerce dönmesini engeller.
    profile_max_scan_rows: int = 200_000

    # Kolon başına toplanacak örnek değer sayısı ve karakter kırpma sınırı.
    # ADR §9: örnekler redakte edilir ve kırpılır.
    sample_values_per_column: int = 3
    sample_value_max_length: int = 80

    # ---------------------------------------------------- analiz sınırları
    # ADR §2: varsayılan analiz hard timeout'u 45 dakikadır ve environment ile
    # değiştirilebilir. Celery'nin `task_time_limit`'i de buradan beslenir.
    analysis_hard_timeout_seconds: int = 45 * 60

    #: Soft limit hard limitin altında kalmalı: worker önce SoftTimeLimitExceeded
    #: alıp işi sözleşmeye uygun bir hata ile kapatabilsin, sonra sert kesim gelsin.
    analysis_soft_timeout_seconds: int = 44 * 60

    # ADR §9: BYOK anahtarının Redis TTL'i HER ZAMAN hard timeout + 5 dakikadır
    # (varsayılan 50 dk). Bu bir türetilmiş değerdir, ayrı bir ayar DEĞİL —
    # ikisini bağımsız yapmak, TTL'in timeout'un altına düşüp işi ortada
    # bırakmasına izin verirdi.
    openrouter_key_ttl_margin_seconds: int = 5 * 60

    #: AES-GCM master key. ADR §9: yalnızca secret manager/environment içinde
    #: bulunur. Geliştirme varsayılanı bilinçli olarak açıkça "dev" etiketli:
    #: üretimde bu değerin değiştirilmemesi gözden kaçmasın.
    secret_encryption_key: str = "auzef-dev-master-key-degistirilmeli"

    #: Analizde her aşamada değil, yalnızca bu kadar puanlık ilerleme
    #: değişiminde PostgreSQL'e yazılır (ADR §2).
    analysis_progress_write_threshold: float = 5.0

    #: Ön işlemede bir kaydın anlamlı sayılması için gereken asgari uzunluk.
    #: Bunun altındakiler ("ok", "??", "sa") elenir.
    preprocess_min_message_length: int = 3

    #: Rapora eklenecek örnek mesaj sayısı (redakte + kırpılmış).
    report_examples_per_question: int = 3

    # ----------------------------------------------------------- worker
    upload_profile_soft_time_limit_seconds: int = 900
    upload_profile_time_limit_seconds: int = 1200

    @property
    def openrouter_key_ttl_seconds(self) -> int:
        """ADR §9: TTL = job hard timeout + 5 dakika. Türetilir, ayarlanmaz."""
        return self.analysis_hard_timeout_seconds + self.openrouter_key_ttl_margin_seconds

    @property
    def sync_database_url(self) -> str:
        """Alembic ve senkron araçlar için asyncpg sürücüsü olmadan URL."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
