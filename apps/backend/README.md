# AUZEF Chat Analiz — Backend

Bu paket AUZEF Chat Analiz'in Python 3.13 ve FastAPI tabanlı API temelidir. Uygulama
factory yapısı, Pydantic Settings, güvenli JSON loglama, RFC 9457 hata cevapları,
trace ID, health endpoint'leri ve Pydantic/OpenAPI sözleşmesi çalışır durumdadır.
Upload/model/analysis uçlarının iş mantığı sonraki kartlara aittir ve şimdilik
`501` döndürür.

API kararları için [`ADR-0002`](../../docs/adr/0002-api-contract-freeze.md), genel
mimari için [`docs/mimari.md`](../../docs/mimari.md) dosyasına bakın.

## Kurulum ve çalıştırma

```bash
cd apps/backend
uv sync --locked --dev
uv run uvicorn app.main:app --reload --port 8000
```

Development ortamında API dokümanı [http://localhost:8000/docs](http://localhost:8000/docs)
adresindedir. Production ortamında `/docs` ve `/openapi.json` kapalıdır.

Health endpoint'leri:

- `GET /api/v1/health/live`: process liveness, dış bağımlılık kontrol etmez.
- `GET /api/v1/health/ready`: kayıtlı readiness kontrollerini çalıştırır.
  Hiç kontrol kayıtlı değilse veya kontrollerden biri başarısızsa RFC 9457
  `503 SERVICE_NOT_READY` döndürür. En az bir kontrolün tamamı başarılıysa `200`
  döner.

## Ortam ayarları

Backend **yalnızca repo kökündeki `.env` dosyasını** okur; `apps/backend/.env`
OKUNMAZ. Şablon repo kökündedir ve web ile Docker Compose değişkenlerini de
içerir, dolayısıyla kökten kopyalayın:

```bash
cp .env.example .env   # repo kökünde
```

Tüm backend değişkenleri `AUZEF_` öneklidir.

| Değişken                   | Varsayılan    | Açıklama                                           |
| -------------------------- | ------------- | -------------------------------------------------- |
| `AUZEF_ENVIRONMENT`        | `development` | `development`, `test` veya `production`            |
| `AUZEF_LOG_LEVEL`          | `INFO`        | Standart Python log seviyesi                       |
| `AUZEF_CORS_ORIGINS`       | Ortama göre   | JSON URL listesi; wildcard reddedilir              |
| `AUZEF_BACKEND_MASTER_KEY` | boş           | Production'da zorunlu Base64 kodlu 32 byte anahtar |

Gerçek anahtarları veya kurum verisini Git'e eklemeyin.

150 MB upload sınırı ile 100.000 satır analiz sınırı frontend ve backend
arasında donmuş sözleşme sabitleridir; `AUZEF_*` ortam değişkenleriyle
değiştirilemez. Bu değerlerin değişmesi contract version artışı ile OpenAPI,
fixture ve frontend sabitlerinin birlikte güncellenmesini gerektirir.

## Kalite kapıları

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Sözleşme artefaktları

`docs/api/openapi.json` ve `tests/fixtures/contract/` üretilmiş dosyalardır; elle
düzenlenmez. Pydantic sözleşmesi değiştiğinde:

```bash
uv run python scripts/export_openapi.py
uv run python scripts/export_fixtures.py
```

CI aynı script'leri `--check` ile çalıştırır ve fixture'ları frontend Zod
şemalarından geçirir.
