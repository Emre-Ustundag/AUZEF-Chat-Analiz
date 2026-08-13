# ADR-0001 — AUZEF Chat Analiz MVP Mimarisi

- **Durum:** Karar verildi — uygulamaya hazır
- **Tarih:** 11 Ağustos 2026
- **Güncelleme:** API sözleşmesinin kesinleşen ayrıntıları için bkz.
  [ADR-0002 — API sözleşmesinin dondurulması](adr/0002-api-contract-freeze.md).
  §6, §7, §8 ve §9'daki ilgili maddeler orada netleştirildi.
- **Karar sahibi:** Architecture owner
- **Reviewer:** Backend + Data/LLM ekip üyesi
- **Bağımlılık:** Yok
- **Repo:** <https://github.com/Emre-Ustundag/AUZEF-Chat-Analiz>

## 1. Bağlam ve hedef

MVP, kullanıcının büyük bir `.xlsx` dosyası yüklemesini; mesajların güvenli biçimde doğrulanıp temizlenmesini; OpenRouter üzerinden LLM ile sık sorulara ve temalara ayrılmasını ve sonucu dashboard'da adet, oran ve özet rapor olarak göstermesini sağlar.

Gerçek örnek dosya yaklaşık 130 MB olabildiği için dosya okuma ve LLM analizi tek bir HTTP isteğinde tamamlanmayacaktır. Mimari, asenkron job/worker modelini kullanacaktır.

## 2. Kesin teknoloji yığını

### Frontend

- Next.js App Router + React + TypeScript (`strict`)
- Tailwind CSS + shadcn/ui
- TanStack Query: API cache, polling ve yeniden deneme
- React Hook Form + Zod: form ve istemci doğrulaması
- Recharts: SSS/tema grafiklerinin ilk sürümü
- Frontend Excel'i parse etmez; dosyayı aynı origin altındaki `/api` reverse proxy üzerinden FastAPI'ye stream eder

### Backend/API

- Python + FastAPI
- Pydantic v2: request, response ve LLM çıktı doğrulaması
- SQLAlchemy 2 + Alembic: PostgreSQL erişimi ve migration
- `httpx`: OpenRouter çağrıları
- `openpyxl` `read_only/data_only`: `.xlsx` satırlarını belleğe tamamen almadan okuma
- Polars + PyArrow: temizlenmiş verinin hızlı işlenmesi ve Parquet ara formatı
- `uv`: Python bağımlılık ve ortam yönetimi

### Asenkron işleme

- Celery + Redis broker
- Ayrı worker process/container
- PostgreSQL job durumunun kalıcı gerçek kaynağıdır; Redis yalnızca kuyruk, kısa süreli lock ve TTL secret için kullanılır
- API `202 Accepted` döndürür; frontend 2–3 saniyede bir status endpoint'ini poll eder
- Progress her satırda değil, yalnızca aşama veya anlamlı yüzde değişimlerinde PostgreSQL'e yazılır
- Varsayılan analiz hard timeout'u 45 dakikadır ve environment ile değiştirilebilir
- İlk MVP'de WebSocket/SSE kullanılmaz

### Veri ve dosya saklama

- PostgreSQL: upload/analysis metadata, durum, hata, token-maliyet ve yapılandırılmış sonuç JSONB
- S3 uyumlu object storage: geliştirmede MinIO, üretimde kurumun S3 uyumlu servisi
- Redis: Celery kuyruğu, idempotency lock ve şifreli kısa ömürlü OpenRouter anahtarı
- Docker Compose: `web`, `api`, `worker`, `postgres`, `redis` ve `minio` servisleri

### Test ve kalite

- Backend: pytest + pytest-asyncio
- Frontend: Vitest + React Testing Library
- Uçtan uca: Playwright
- Lint/format: Ruff + mypy; ESLint + Prettier
- CI: GitHub Actions

## 3. Monorepo klasör yapısı

```text
AUZEF-Chat-Analiz/
├── apps/
│   ├── web/
│   │   ├── src/app/
│   │   ├── src/components/
│   │   ├── src/features/analysis/
│   │   └── src/lib/api/
│   └── backend/
│       ├── pyproject.toml          # uv projesi, ruff/mypy/pytest config
│       ├── app/api/v1/
│       ├── app/core/               # errors, handlers, tracing, openapi
│       ├── app/domain/
│       ├── app/models/
│       ├── app/schemas/
│       ├── app/services/
│       ├── app/pipeline/
│       ├── app/workers/
│       ├── app/prompts/faq_analysis/
│       ├── scripts/                # openapi ve fixture üreticileri
│       └── tests/
├── tests/
│   ├── fixtures/
│   │   └── contract/               # üretilmiş, iki dilde doğrulanan gövdeler
│   ├── integration/
│   └── e2e/
├── infra/
│   ├── docker/
│   └── scripts/
├── docs/
│   ├── mimari.md                   # bu ADR (ADR-0001)
│   ├── adr/0002-api-contract-freeze.md
│   └── api/openapi.json            # üretilmiş sözleşme artefaktı
├── docker-compose.yml
├── Makefile
└── README.md
```

Backend API ve Celery worker aynı Python domain/pipeline kodunu paylaşır; ayrı mikroservis kod tabanları oluşturulmaz.

## 4. Kesin veri akışı

```text
Browser
  → POST /uploads
  → FastAPI dosyayı stream ederek object storage'a yazar
  → Celery validate/profile job
  → güvenli .xlsx kontrolü + sheet/kolon/veri profili
  → kullanıcı sheet ve message_text kolonunu seçer
  → POST /analyses + X-OpenRouter-Key
  → API anahtarı AES-GCM ile şifrelenip Redis'e kısa TTL ile yazılır
  → Celery analysis job
  → preprocess + PII redaksiyonu + exact dedupe/frequency
  → token sınırına göre chunk
  → OpenRouter map çağrıları
  → kategori eşleme/reduce
  → backend deterministik adet/oran aggregation
  → Pydantic result validation
  → PostgreSQL JSONB report
  → GET /analyses/{id} polling
  → dashboard + export
```

### Önemli analiz kararı

LLM doğrudan toplam sayı üretmez. Her temizlenmiş mesaj veya benzersiz mesaj kimliğini bir kanonik SSS ya da tema kimliğine eşler. Nihai adet ve oranları backend, mesajların gerçek frekanslarından deterministik olarak hesaplar. Böylece LLM'in sayı uydurması engellenir.

## 5. İki aşamalı upload/analysis modeli

### Aşama A — Upload ve profil

1. Dosya object storage'a stream edilir.
2. Magic bytes, uzantı, OOXML ZIP yapısı ve sıkıştırma oranı kontrol edilir.
3. Sheet'ler, kolonlar, satır sayısı, boş ve tekrar oranı çıkarılır.
4. UI kullanıcıdan sheet ve metin kolonunu seçmesini ister.

### Aşama B — Analiz

1. Seçilen kolon satır satır okunur.
2. Boş, sistem ve tekrar kayıtları işaretlenir.
3. Telefon, e-posta, T.C./öğrenci no gibi PII LLM'den önce maskelenir.
4. Normalize edilmiş metinler exact hash ile deduplicate edilir ve gerçek frekans korunur.
5. Benzersiz kayıtlar token bütçesine göre chunk edilir.
6. LLM her kayıt kimliğini SSS/tema kimliğine map eder.
7. İkinci reduce aşaması benzer chunk kategorilerini birleştirir.
8. Backend adet, oran, Top N ve uyarıları hesaplar.
9. Kırpılmış ve redakte edilmiş örnekler rapora eklenir.

## 6. API sözleşmesi

### Upload

- `POST /api/v1/uploads` — multipart file; `202 Accepted`
- `GET /api/v1/uploads/{upload_id}` — `queued/validating/ready/failed` + sheet/kolon profili
- `DELETE /api/v1/uploads/{upload_id}` — iptal ve cleanup

### Model listesi

- `GET /api/v1/models` — izin verilen modeller, `default_model`, `default_prompt_version` (ADR-0002 #1)
- Whitelist: `anthropic/claude-sonnet-4.6`, `openai/gpt-4.1-mini`, `google/gemini-2.5-flash`; varsayılan model `google/gemini-2.5-flash`, varsayılan prompt `faq_analysis/v1`; tüm whitelist üyeleri structured output destekler

### Analysis

- `POST /api/v1/analyses` — `upload_id`, `sheet_name`, `text_column`, `model`, `prompt_version`, `top_n`, `max_cost_usd`
- Model yalnızca JSON Schema structured output desteği doğrulanmış backend whitelist'inden seçilebilir
- OpenRouter anahtarı yalnızca `X-OpenRouter-Key` header'ında taşınır; reverse proxy ve uygulama loglarında bu header zorunlu olarak redakte edilir
- `GET /api/v1/analyses/{analysis_id}` — durum, progress, aşama ve güvenli hata
- `GET /api/v1/analyses/{analysis_id}/result` — tamamlanmış `AnalysisReport`; iş tamamlanmamışsa `409 JOB_CONFLICT`
- `DELETE /api/v1/analyses/{analysis_id}` — iptal: aktif job `204`, terminal job `409 JOB_CONFLICT`, bilinmeyen id `404 JOB_NOT_FOUND` (ADR-0002 #9)
- `GET /api/v1/analyses/{analysis_id}/export?format=xlsx|json` — rapor export. `Content-Disposition` her zaman `attachment; filename="analiz-{analysis_id}.{format}"`; kullanıcının dosya adı kullanılmaz, dolayısıyla ad tanım gereği ASCII'dir (ADR-0002 #11)

### Tarih biçimi

Tüm API tarihleri UTC ISO 8601'dir ve `Z` ile biter: `YYYY-MM-DDTHH:MM:SS.sssZ`. Girişte her RFC 3339 instant kabul edilip normalize edilir; çıkışta tek biçim üretilir (ADR-0002 #4).

### Job durumları

`queued → validating/preprocessing → analyzing → aggregating → completed`

Terminal durumlar: `failed`, `cancelled`.

### Idempotency

`POST` uçları opsiyonel `Idempotency-Key` header'ı destekler (ADR-0002 #3):

- aynı anahtar + aynı gövde → ilk isteğin `202` cevabı aynen döner
- aynı anahtar + farklı gövde → `409 JOB_CONFLICT`
- kayıt 24 saat saklanır
- replay, istekle gelen yeni `X-OpenRouter-Key` header'ını yok sayar; anahtar orijinal job'a bağlıdır
- analiz fingerprint'i doğrulanmış request'in canonical JSON SHA-256'sıdır; upload fingerprint'i dosya SHA-256 + filename + MIME + size canonical JSON SHA-256'sıdır; secret/header'lar fingerprint'e girmez

OpenAPI şeması backend'den üretilir ve `docs/api/openapi.json` olarak commit edilir. Frontend client'ının bu şemadan otomatik üretilmesi halef karardır; MVP'de sözleşme uyumu iki dilli fixture doğrulamasıyla zorlanır (ADR-0002 §4).

## 7. Standart hata modeli

RFC 9457 Problem Details kullanılacaktır:

```json
{
  "type": "/errors/upload-too-large",
  "title": "Dosya boyutu sınırı aşıldı",
  "status": 413,
  "code": "UPLOAD_TOO_LARGE",
  "detail": "En fazla 150 MB .xlsx yüklenebilir.",
  "trace_id": "uuid",
  "errors": []
}
```

Temel hata kodları:

- `UPLOAD_TOO_LARGE` — 413
- `UPLOAD_INVALID_TYPE` — 415
- `UPLOAD_CORRUPT_OR_ENCRYPTED` — 422
- `SHEET_OR_COLUMN_NOT_FOUND` — 422
- `REQUEST_VALIDATION` — 422 (ADR-0002 #1)
- `INVALID_MODEL` — 422 (ADR-0002 #1)
- `INVALID_PROMPT` — 422 (ADR-0002 #1)
- `COST_LIMIT_EXCEEDED` — 422 (ADR-0002 #1, #10)
- `PROVIDER_AUTH_FAILED` — 422
- `PROVIDER_RATE_LIMITED` — 429 + `retry_after`
- `PROVIDER_BAD_RESPONSE` — 502
- `PROVIDER_TIMEOUT` — 504
- `JOB_NOT_FOUND` — 404
- `JOB_CONFLICT` — 409
- `NOT_IMPLEMENTED` — 501 (contract-only stub; route uygulanınca kaldırılır)
- `INTERNAL_ERROR` — 500

Ek kurallar (ADR-0002 #6, #7):

- `type`, `title`, `status`, `code`, `detail`, `trace_id` **her** hata cevabında bulunur; `errors` her zaman vardır (boş olabilir)
- `retry_after` **yalnızca 429** cevaplarında bulunur; diğerlerinde `null` olarak dahi yer almaz
- `status`, hata kodunun kayıtlı HTTP statüsüyle birebir aynı olmak zorundadır
- `type` URI'si koddan türetilir: `/errors/` + küçük harf + `_` → `-`
- `errors[].input` asla yankılanmaz; gövdeye yanlışlıkla konmuş bir API anahtarını geri sızdırma yolu kapalıdır
- hatalar tek merkezî handler'dan üretilir (`app/core/handlers.py`)

Ham OpenRouter yanıtı, API anahtarı veya mesaj içeriği hata cevabına ve loglara yazılmaz.

## 8. Sonuç modeli

`AnalysisReport` en az şu alanları içerir:

- `schema_version`, `analysis_id`, `status`, `generated_at`
- `source_summary`: dosya, sheet ve satır özeti
- `preprocessing_summary`: analiz edilen, elenen, tekrar ve redakte edilen kayıt sayıları
- `top_questions[]`: `id`, `canonical_question`, `count`, `percentage`, `confidence`, `redacted_examples`
- `themes[]`: `id`, `name`, `count`, `percentage`, `related_question_ids`
  - `related_question_ids`, `top_n` kırpması sonrası raporda gerçekten yer alan sorulara filtrelenir; `count` ve `percentage` ise temanın gerçek büyüklüğünü yansıtmaya devam eder (ADR-0002 #5)
  - soru/tema id'leri benzersiz, `count <= analyzed_count`; `percentage`, bir ondalığa exact half-up yuvarlanmış gerçek orandır
- `executive_summary` ve `warnings[]`
  - `warnings[].code` tel üstünde serbest `string`'tir; backend yalnızca sürümlenmiş bir sözlükten (`ROW_LIMIT_TRUNCATED`, `CHUNK_PARTIAL_FAILURE`, `LOW_CONFIDENCE_THEMES`, `PII_REDACTION_INCOMPLETE`, `COST_LIMIT_APPROACHED`) üye yayar
  - `warnings[].message` kullanıcıya hazır Türkçe metindir — "ham backend metni kullanıcıya basılmaz" kuralının belgelenmiş tek istisnası (ADR-0002 #2)
- `model`, `prompt_version`, `prompt_hash` (`model` tarihsel raporlarda aktif whitelist'ten bağımsız, boş olmayan kimliktir)
- `token_usage` ve `estimated_cost_usd`

LLM çıktısı JSON Schema/Pydantic ile doğrulanır. MVP yalnızca structured-output desteği doğrulanmış modelleri kabul eder. Geçersiz yanıt en fazla iki kontrollü repair/retry denemesinden sonra `PROVIDER_BAD_RESPONSE` ile sonlandırılır.

## 9. Güvenlik ve çalışma sınırları

- MVP yalnızca `.xlsx` destekler; `.xls`, `.xlsm`, makrolu, şifreli veya bozuk dosya reddedilir
- Sözleşmede donmuş sıkıştırılmış upload sınırı: 150 MB
- OOXML açılmış toplam boyut sınırı: 1 GB
- Boyut ve süre sınırları environment config'tir (`AUZEF_` öneki); satır sınırı DEĞİLDİR
- Satır sınırı: 100.000, sözleşmede donmuştur (ADR-0002 #13) — hem Pydantic hem Zod cevap invariant'larında kullanıldığı için env ile oynatmak backend'in doğru ürettiği cevapları frontend'e reddettirirdi. Sınır aşımı upload'ı REDDETMEZ: dosya tam profillenir, `profile.exceeds_row_limit` işaretlenir, analiz ilk 100.000 satırı işler ve rapora `ROW_LIMIT_TRUNCATED` uyarısı eklenir (ADR-0002 #2)
- OpenRouter key PostgreSQL'e veya loglara yazılmaz
- BYOK anahtarı AES-GCM ile şifreli Redis kaydı olarak tutulur; TTL her zaman job hard timeout + 5 dakikadır (varsayılan 50 dakika) ve işlem bitince başarı/hata fark etmeksizin silinir
- Sunucu master encryption key yalnızca secret manager/environment içinde bulunur
- Sistem promptları backend'de sürümlenir; chatbot satırları açık delimiter içinde güvenilmeyen veri olarak gönderilir, tool/function çağrıları kapatılır ve modelden yalnızca kayıt ID eşlemesi alınır
- Ham upload ve Parquet ara dosya işlem sonunda silinir; kaçak dosyalar için azami 24 saat lifecycle uygulanır
- Toplu ve redakte rapor varsayılan 24 saat tutulur; kullanıcı elle silebilir ve gerçek kurum politikası onaylanınca config değiştirilir
- PII redaksiyonu bilinen T.C./öğrenci no, telefon, e-posta ve benzeri desenleri kapsar; serbest metindeki kişi adlarının eksiksiz maskeleneceği garanti edilmez
- Frontend ve API üretimde aynı origin altında çalışır; geliştirme CORS allowlist'i yalnızca açıkça tanımlı local origin'leri kabul eder
- Public deployment yapılmaz; yalnızca anonim örnek veri kullanılır. Gerçek kurum verisi öncesi SSO/erişim kontrolü ve AUZEF veri işleme onayı zorunludur
- Token ve tahmini maliyet üst sınırı aşılırsa LLM çağrısı başlamadan iş güvenli biçimde durur. Ön tahmin `POST /analyses` içinde senkron yapılır ve sınır aşılıyorsa istek `422 COST_LIMIT_EXCEEDED` ile reddedilir; çalışma sırasında aşılırsa aynı kod terminal job hatası olarak döner (ADR-0002 #10)

## 10. Ana riskler ve önlemler

1. **130 MB Excel bellek/CPU riski:** streaming upload, ZIP bomb kontrolü, `openpyxl` `read_only`, worker memory limiti/process recycling ve gerçek 130 MB fixture ile yük testi. Parser bir adapter arkasındadır; load test başarısızsa API sözleşmesini değiştirmeden alternatif streaming parser kullanılabilir.
2. **LLM halüsinasyonu/sayım hatası:** LLM yalnızca ID sınıflandırır; sayımlar backend'de deterministik yapılır.
3. **Yüksek token maliyeti:** exact dedupe, frekans taşıma, chunk limiti ve job başına maliyet tavanı.
4. **Rate limit/timeout:** exponential backoff + jitter, sınırlı retry ve kısmi hata takibi.
5. **Prompt injection:** sabit sürümlü system prompt, açık delimiter, tool çağrılarını kapatma, model çıktısını ID + schema ile sınırlandırma. Delimiter tek başına güvenlik garantisi değildir.
6. **PII sızıntısı:** kolon minimizasyonu, LLM öncesi desen tabanlı redaksiyon, güvenli log filtreleri ve kısa retention. Kişi adı gibi bağlamsal PII için kalan risk kurum tarafından kabul edilmeden gerçek veri kullanılmaz.
7. **Kuyrukta API key:** yalnızca şifreli TTL secret reference; task payload içinde düz anahtar yok.
8. **Altyapı karmaşıklığı:** tek monorepo, ortak backend kodu ve Docker Compose ile standart geliştirme ortamı.

## 11. Reddedilen alternatifler

- **Tek senkron HTTP isteği:** 130 MB dosya ve çoklu LLM çağrılarında timeout/yeniden deneme sorunu.
- **Excel'i tarayıcıda parse etmek:** yüksek RAM kullanımı, UI donması ve veri güvenliği riski.
- **OpenRouter'ı doğrudan browser'dan çağırmak:** API key, prompt ve veri kontrolünün istemciye taşınması.
- **Yalnız Next.js/serverless backend:** uzun job ve büyük upload limitleri için uygun değil.
- **Yalnız Redis/SQLite:** kalıcı job durumu ve rapor güvenilirliği yetersiz.
- **LLM'e "en sık soruları ve adetleri bul" demek:** sayım ve izlenebilirlik güvenilir değil.
- **Kafka/Kubernetes/çok sayıda mikroservis:** dört kişilik MVP için gereksiz operasyon yükü.
- **İlk sürümde vector DB/embedding:** SSS çıkarma MVP'si için zorunlu değil; kalite ölçümü gerektirirse Faz 1.1'de değerlendirilir.
- **İlk sürümde WebSocket/SSE:** polling daha basit ve yeterli.

## 12. Dört geliştirici için teknik sınırlar

- **Architecture owner:** ADR, ortak contract, entegrasyon kararları ve ilk repo iskeleti koordinasyonu
- **Frontend:** Next.js upload, column selection, progress ve dashboard
- **Backend:** FastAPI, PostgreSQL, object storage, upload ve job API
- **Data/LLM:** parser, preprocessing, prompt, chunk/map/reduce ve result schema
- **QA/DevOps:** Celery/Redis, Docker Compose, CI, güvenlik ve E2E test

Bir kişi birden çok alan üstlenebilir; her PR'ın sahibi dışında en az bir reviewer'ı olmalıdır.

## 13. Sonuç ve tamamlanma koşulu

Bu karar uygulamaya hazırdır. Trello kartı ancak aşağıdakiler tamamlanınca **Code Review ve Test** listesine taşınır:

1. Bu içerik `docs/mimari.md` olarak repoya eklenir.
2. Backend ve Data/LLM reviewer kararı onaylar.
3. Repo iskeleti ADR'deki klasör yapısıyla çelişmez.

BE-01 (API sözleşmesinin dondurulması) için ek tamamlanma koşulu: CI'daki
`web`, `backend` ve `contract` job'larının üçü de yeşil olmalı ve sözleşme
artefaktları tam yeniden üretimden sonra diff üretmemeli.
