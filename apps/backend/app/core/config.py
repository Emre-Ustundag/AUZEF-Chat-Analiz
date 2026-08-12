"""Çalışma sınırları — ADR-0001 §9.

Operasyonel sınırlar `Settings` içinde ve environment ile değiştirilebilir.
TEK istisna `MAX_ROWS`: o bir operasyon düğmesi değil, sözleşmenin parçası
(aşağıdaki gerekçeye bakın).
"""

from pathlib import Path
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

#: apps/backend/app/core/config.py -> repo kökü (core, app, backend, apps).
#: .env kökte duruyor (Docker Compose de oradan okuyor) ama backend
#: apps/backend içinden çalıştırılıyor; göreli bir yol yanlış dosyaya
#: bakardı. `apps/backend/.env` OKUNMAZ.
_REPO_ROOT = Path(__file__).resolve().parents[4]

MAX_ROWS: Final[int] = 100_000
"""Analize giren azami satır sayısı — SÖZLEŞMEDE DONMUŞ, env ile değişmez.

ADR-0002 #2: sınır hard reject değil; üstü kırpılır ve rapora
`ROW_LIMIT_TRUNCATED` uyarısı eklenir.

Neden `Settings` alanı değil: bu sayı yalnızca bir limit değil, iki dilde
yazılmış cevap invariant'larının parçası —
`analyzed_count + discarded_count == min(total_rows, MAX_ROWS)`,
`exceeds_row_limit` ve `ROW_LIMIT_TRUNCATED`'ın varlığı hepsi ona bağlı.
Frontend aynası (`lib/api/schemas/common.ts` → `LIMITS.MAX_ROWS`) derleme
zamanı sabiti olduğu için, backend tarafını env ile oynatmak sunucunun DOĞRU
ürettiği cevapları Zod'a reddettirir ve kullanıcı sentetik bir INTERNAL_ERROR
görür — üstelik hiçbir drift kontrolü kırmızıya dönmeden, çünkü artefaktlar
varsayılan env ile üretiliyor. Değeri değiştirmek bir sözleşme değişikliğidir:
`contract_version` bump + artefakt yeniden üretimi + Zod tarafının güncellenmesi
gerekir. `manifest.json`'daki `limits.max_rows` iki tarafı birbirine kilitler.
"""


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
    #: ADR-0002 #3: Idempotency-Key kaydının saklama süresi (24 saat).
    idempotency_ttl_seconds: int = 24 * 60 * 60
    #: ADR-0001 §2: analiz hard timeout'u 45 dakika.
    analysis_timeout_seconds: int = 45 * 60

    #: Sözleşme sürümü. Paket sürümünden BİLEREK ayrı: bir bağımlılık
    #: yükseltmesi openapi.json'ı değiştirmesin (ADR-0002 #12).
    contract_version: str = "1.0.0"


settings = Settings()
