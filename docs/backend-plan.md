# Backend Uygulama Planı

- **Durum:** Uygulamaya hazır
- **Tarih:** 11 Ağustos 2026
- **Bağımlılık:** [ADR-0001](./mimari.md) — bu plan ADR'yi uygular, ondan sapmaz
- **Sözleşme kaynağı:** `apps/web/src/lib/api/schemas/` (Zod şemaları)

## 0. Bu plan neden var

Frontend tamamlandı ve çalışan bir mock backend'e karşı uçtan uca doğrulandı.
Mock, ADR §6'daki sözleşmeyi zamana bağlı bir durum makinesiyle taklit ediyor.
Gerçek backend'in işi bu mock'un yerine geçmek — sözleşmeyi **değiştirmek
değil**.

Sözleşmenin somut hâli `apps/web/src/lib/api/schemas/` altındaki Zod
şemalarıdır. Alan adları snake_case; Pydantic modelleri bunlarla **birebir**
eşleşmelidir. Bir alan adı bile ayrışırsa frontend'in runtime doğrulaması
hatayı yakalar ama kullanıcı boş ekran görür.

Referans mock: `apps/web/src/mocks/store.ts`. Aşama süreleri, hata senaryoları
ve rapor şekli oradan okunabilir.

## 1. Kesinleşen sözleşme kararları

ADR'de tanımsız bırakılmış, frontend yazılırken ortaya çıkan iki boşluk.
Frontend ve backend aynı kişide olduğu için bunlar karar olarak kapatılıyor:

### 1.1 `GET /api/v1/models` eklenecek

ADR §6 "model yalnızca backend whitelist'inden seçilebilir" diyor ama listeyi
döndüren endpoint tanımlamıyor. Arayüzün açılır listeyi doldurabilmesi için
gerekli.

```
GET /api/v1/models  →  200
{
  "models": [
    { "id": "...", "label": "...", "input_cost_per_million": 3.0,
      "output_cost_per_million": 15.0, "context_window": 200000 }
  ],
  "default_model": "...",
  "default_prompt_version": "..."
}
```

Liste backend'de sabit kodlu (config) tutulur; yalnızca structured-output
desteği **doğrulanmış** modeller girer. OpenRouter'dan dinamik çekilmez —
whitelist'in amacı tam olarak bunu engellemek.

### 1.2 `themes[].related_question_ids` top_n kırpmasından sonra filtrelenir

ADR §8 bu alanın `top_n` kırpması sonrası ne göstereceğini tanımlamıyor.

**Karar:** yalnızca raporda gerçekten yer alan soru kimlikleri listelenir.
Aksi hâlde arayüz çözemeyeceği bir kimliğe bağlantı verir.

**Ama tema `count` değeri kırpmadan etkilenmez** — tema büyüklüğü o temaya
düşen tüm mesajların sayısıdır, kaç sorunun gösterildiğine bağlı değildir.
Aksi hâlde dashboard'daki oranlar yanlış olur.

Referans uygulama: `apps/web/src/mocks/store.ts`, `getAnalysisReportRecord`.

## 2. Fazlar

Her faz **çalışan bir dikey dilim**dir: sonunda frontend'in bir bölümü
mock'tan gerçeğe geçer. Faz bitmeden sonrakine geçilmez.

| Faz   | Kapsam                            | Frontend'de karşılığı                           |
| ----- | --------------------------------- | ----------------------------------------------- |
| **1** | İskelet + upload + profilleme     | Yükleme ve kolon seçimi ekranları gerçeğe geçer |
| **2** | Analiz job'ı + durum + iptal      | İlerleme ekranı gerçeğe geçer                   |
| **3** | LLM pipeline (map/reduce) + rapor | Sonuç ekranı gerçeğe geçer                      |
| **4** | Export (xlsx/json) + retention    | Dışa aktarma gerçeğe geçer                      |

Faz 3 en riskli olan; ADR §10'daki tüm riskler (halüsinasyon, token maliyeti,
rate limit, prompt injection) orada yoğunlaşıyor. Faz 1-2 onun altyapısı.

---

## 3. FAZ 1 — İskelet, upload ve profilleme

Bu fazın tamamlanma ölçütü tek cümle: **`docker compose up` sonrası kullanıcı
tarayıcıdan bir `.xlsx` yükleyip kolon seçim ekranını gerçek verisiyle
görebiliyor.**

### 3.1 Klasör yapısı (ADR §3)

```text
apps/backend/
├── pyproject.toml          # uv ile yönetilir
├── alembic.ini
├── alembic/versions/
└── app/
    ├── main.py             # FastAPI uygulaması
    ├── api/v1/
    │   ├── uploads.py
    │   └── models.py       # §1.1
    ├── core/
    │   ├── config.py       # pydantic-settings
    │   ├── errors.py       # RFC 9457 + exception handler
    │   └── logging.py      # X-OpenRouter-Key redaksiyonu
    ├── domain/
    ├── models/             # SQLAlchemy
    ├── schemas/            # Pydantic v2
    ├── services/
    │   ├── storage.py      # S3/MinIO
    │   └── xlsx.py         # güvenli .xlsx doğrulama + profil
    ├── pipeline/
    └── workers/
        ├── celery_app.py
        └── tasks.py
```

### 3.2 Yapılacaklar

**a) Proje kurulumu**

- `uv` ile `pyproject.toml`; Python 3.12
- Bağımlılıklar: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`,
  `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `celery[redis]`, `redis`,
  `boto3`, `openpyxl`, `polars`, `pyarrow`, `python-multipart`
- Dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`

**b) Hata modeli (ADR §7) — İLK yazılacak şey**

RFC 9457 Problem Details. Alanlar: `type`, `title`, `status`, `code`,
`detail`, `trace_id`, `errors[]`, opsiyonel `retry_after`.

11 hata kodu ve HTTP durumları ADR §7'de. Frontend'in `ErrorCode` enum'u ile
**birebir** eşleşmeli — `apps/web/src/lib/api/schemas/common.ts`.

FastAPI exception handler tüm hataları bu biçime çevirmeli. Beklenmeyen
istisnalar `INTERNAL_ERROR` olarak sarılmalı; stack trace, dosya içeriği veya
API anahtarı cevaba **asla** sızmamalı.

**c) Veri modeli + migration**

`uploads` tablosu: `id` (uuid), `status`, `filename`, `size_bytes`,
`storage_key`, `profile` (JSONB, nullable), `error` (JSONB, nullable),
`created_at`, `updated_at`.

Alembic migration yazılacak. `status` bir enum: `queued/validating/ready/failed`.

**d) Object storage**

MinIO'ya **stream** ile yazma. Dosyanın tamamı belleğe alınmayacak — gerçek
dosyalar ~130 MB.

**e) `POST /api/v1/uploads`**

- multipart, `202 Accepted`, `{ upload_id, status }` döner
- Boyut sınırı 150 MB — sınırı aşan istek gövdeyi tamamen okumadan reddedilmeli
- Uzantı `.xlsx` değilse `UPLOAD_INVALID_TYPE` (415)
- Dosya storage'a yazılır, DB kaydı açılır, Celery profil job'ı kuyruğa alınır
- Opsiyonel `Idempotency-Key` desteği (ADR §6)

**f) Güvenli `.xlsx` doğrulama (ADR §5 Aşama A, §9)**

Worker içinde, LLM'e hiç gitmeden önce:

- Magic bytes (`PK\x03\x04`) ve OOXML ZIP yapısı
- **ZIP bomba kontrolü:** açılmış toplam boyut sınırı 1 GB, sıkıştırma oranı
  kontrolü
- Makrolu (`.xlsm` içeriği), şifreli veya bozuk dosya → `UPLOAD_CORRUPT_OR_ENCRYPTED` (422)

Bu maddeyi atlamak, kullanıcının yüklediği rastgele bir zip'i sunucuda açmak
demektir. İsteğe bağlı değil.

**g) Profilleme**

`openpyxl` `read_only=True, data_only=True` ile satır satır okuma.

Her sayfa için: ad, satır sayısı, kolon sayısı. Her kolon için: `name`,
`index`, `non_empty_count`, `empty_count`, `unique_count`, `avg_length`,
`is_likely_text`, `sample_values`.

- `sample_values` **redakte edilmiş ve kırpılmış** olmalı (ADR §9) — kolon
  seçim ekranında gerçek öğrenci mesajları ham gösterilmez
- `is_likely_text`: ortalama uzunluk + benzersizlik oranına dayalı basit bir
  sezgisel. Kesin olması gerekmiyor, kullanıcı zaten seçiyor
- 100.000 satır sınırı aşılırsa `exceeds_row_limit: true` — iş başarısız
  OLMAZ, yalnızca işaretlenir

**h) `GET /api/v1/uploads/{id}` ve `DELETE`**

GET: durum + hazırsa profil. Bulunamazsa `JOB_NOT_FOUND` (404).
DELETE: iptal + storage temizliği, `204`.

**i) `GET /api/v1/models`** — §1.1

**j) Docker Compose**

Mevcut dosyada yalnızca `web` var. Eklenecek: `api`, `worker`, `postgres`,
`redis`, `minio`.

`web` servisi `api`'ye bağlanmalı. `infra/docker/` altına `api.Dockerfile`
ve `worker.Dockerfile` (aynı imaj, farklı komut — ADR: API ve worker aynı
kodu paylaşır, ayrı kod tabanı yok).

**k) Frontend'i gerçeğe bağlama**

- `apps/web/.env.example`: `NEXT_PUBLIC_API_BASE_URL=/api/v1`
- `apps/web/next.config.ts`'e rewrite ekle (yeri yorumla işaretli):
  `/api/v1/:path*` → `${API_ORIGIN}/api/v1/:path*`
- ⚠️ `next.config.ts`'teki **proxy.ts uyarısını oku**. Projeye `proxy.ts`
  eklenirse Next istek gövdesini belleğe kopyalar, varsayılan sınır 10 MB ve
  aşıldığında hata vermez — gövdeyi sessizce kırpar. 130 MB'lık upload fark
  edilmeden bozulur.
- Mock'lar (`apps/web/src/mocks/`, `apps/web/src/app/api/mock/`) bu fazda
  **SİLİNMEZ**. Faz 4 bitene kadar dururlar; env değiştirilerek geri
  dönülebilir olmalı.

### 3.3 Testler

- `pytest` + `pytest-asyncio`
- **Gerçek fixture dosyaları** `tests/fixtures/` altında: geçerli çok sayfalı
  xlsx, bozuk zip, şifreli dosya, makrolu dosya, boş dosya
- Upload akışının uçtan uca testi (httpx ile API, worker'ı senkron çalıştırarak)
- Hata cevaplarının RFC 9457 biçimine ve frontend'in `ErrorCode` kümesine
  uyduğunun testi
- ZIP bomba reddinin testi

### 3.4 Tamamlanma ölçütü (hepsi zorunlu)

1. `docker compose up --build` ile altı servis de ayağa kalkıyor
2. `pytest` yeşil
3. `ruff check` ve `mypy` temiz
4. Tarayıcıdan `.xlsx` yüklenip kolon seçim ekranı gerçek profil verisiyle
   görülebiliyor
5. Frontend'in mevcut testleri (`npm test`) hâlâ yeşil — sözleşme
   değişmediğine dair kanıt
6. Bozuk/şifreli dosya yüklendiğinde arayüzde doğru Türkçe hata görünüyor

---

## 4. Sonraki fazların ana hatları

### Faz 2 — Analiz job'ı, ön işleme ve deterministik toplama

**Kapsam kararı (11 Ağustos 2026'da revize edildi).** İlk taslakta Faz 2
yalnızca durum makinesiydi ve LLM Faz 3'e bırakılmıştı. Bu yanlıştı: LLM
olmadan iş `analyzing` aşamasında sonsuza kadar takılırdı, yani ilerleme
ekranı "gerçeğe geçmiş" sayılmazdı ve faz kendi tanımını (çalışan dikey
dilim) karşılamazdı.

Doğru kesim şu: **LLM'in yaptığı iş yalnızca "kayıt kimliğini kategoriye
eşlemek"** (ADR §4). Bu, arkasında bir arayüz olan tek bir bileşendir. Faz 2
o bileşenin yerine **deterministik bir vekil sınıflandırıcı** koyar
(normalize edilmiş metne göre gruplama) ve pipeline'ın geri kalanını —
ön işleme, PII redaksiyonu, tekilleştirme, **adet/oran toplama**, rapor
üretimi — GERÇEK yazar.

Kazancı iki katlı:

- Frontend'in tamamı (ilerleme + sonuç ekranı) gerçeğe geçer
- ADR'nin en kritik kararı — **sayıları backend'in deterministik hesaplaması**
  — LLM'in belirsizliği devreye girmeden, tek başına test edilebilir olur

Faz 3 yalnızca sınıflandırıcıyı değiştirir; toplama matematiği o zaman
zaten kanıtlanmış olur.

**Yapılacaklar**

- `POST /api/v1/analyses` + `X-OpenRouter-Key`
- Anahtar AES-GCM ile şifrelenip Redis'e TTL ile yazılır (TTL = hard timeout +
  5 dk, varsayılan 50 dk). PostgreSQL'e **yazılmaz**, loglarda redakte edilir.
  Faz 2'de anahtar henüz KULLANILMIYOR ama saklama/silme yolu tam kurulur ve
  test edilir; Faz 3'te yalnızca okuyan taraf eklenir
- `analyses` tablosu + migration; job durum makinesi
  `queued → validating → preprocessing → analyzing → aggregating → completed`,
  terminal: `failed`, `cancelled`
- Progress her satırda değil, aşama veya anlamlı yüzde değişiminde yazılır
  (ADR §2) — her satırda yazmak PostgreSQL'i gereksiz yere döver
- İptal: `DELETE /api/v1/analyses/{id}`. Worker iptal bayrağını aşama
  sınırlarında kontrol eder; iş bitmişse `JOB_CONFLICT` (409)
- Ön işleme: seçilen kolon satır satır okunur, boş/sistem kayıtları elenir,
  **PII maskelenir** (Faz 1'deki `services/redaction.py` yeniden kullanılır),
  normalize edilip exact hash ile tekilleştirilir, **gerçek frekanslar
  korunur**
- Vekil sınıflandırıcı: `pipeline/classifier.py` arkasında bir arayüz.
  Faz 2 uygulaması deterministik (normalize metin → kanonik soru). Faz 3'te
  OpenRouter uygulaması aynı arayüzü sağlayacak
- **Deterministik toplama:** adet, oran, Top N, tema grupları. Sayılar
  yalnızca gerçek mesaj frekanslarından hesaplanır
- `related_question_ids` §1.2'deki karara göre filtrelenir; tema `count`'u
  kırpmadan etkilenmez
- `GET /api/v1/analyses/{id}` ve `GET /api/v1/analyses/{id}/result`
- Maliyet tavanı kontrolü iskeleti (Faz 2'de gerçek token yok, `0` raporlanır)

**Tamamlanma ölçütü**

1. Tarayıcıdan dosya yükleyip analiz başlatılabiliyor, ilerleme ekranı
   aşamaları GERÇEKTEN ilerletiyor, sonuç ekranı gerçek raporu gösteriyor
2. İptal çalışıyor; iptal edilen iş `cancelled` kalıyor
3. Rapor gövdesi frontend'in `analysisReportSchema`'sından geçiyor
4. Toplama testleri: oranların adetlerden türetildiği, tema toplamının
   analiz edilen kaydı aşmadığı, `top_n` kırpmasının tema `count`'unu
   değiştirmediği
5. Anahtarın Redis'te şifreli durduğu, TTL'inin doğru olduğu ve iş bitince
   silindiği test ediliyor; PostgreSQL'de ve loglarda anahtar YOK
6. `pytest`, `ruff`, `mypy`, `npm test` yeşil

### Faz 3 — LLM pipeline

- PII redaksiyonu (telefon, e-posta, T.C./öğrenci no) — LLM'den **önce**
- Normalize + exact hash dedupe, gerçek frekanslar korunur
- Token bütçesine göre chunk
- OpenRouter map çağrıları (`httpx`), structured output
- Reduce: chunk kategorilerini birleştirme
- **Adet ve oranlar backend'de deterministik hesaplanır** — LLM yalnızca kayıt
  kimliğini kategoriye eşler, sayı üretmez (ADR §4). Bu, mimarinin en kritik
  kararı
- Pydantic doğrulaması; geçersiz yanıt en fazla 2 onarım denemesinden sonra
  `PROVIDER_BAD_RESPONSE`
- Maliyet tavanı LLM çağrıları **başlamadan** kontrol edilir

### Faz 4 — Export ve retention

- `GET /api/v1/analyses/{id}/export?format=xlsx|json`
- Ham upload ve Parquet ara dosyaları iş sonunda silinir; kaçaklar için 24
  saat lifecycle
- Rapor varsayılan 24 saat tutulur

---

## 5. Değişmezler — her fazda geçerli

Bunlar müzakere edilebilir değil; ADR §9'dan geliyor:

1. OpenRouter anahtarı PostgreSQL'e veya loglara **asla** yazılmaz
2. Ham OpenRouter yanıtı, API anahtarı veya mesaj içeriği hata cevabına ve
   loglara girmez
3. PII, LLM çağrısından önce maskelenir
4. Model yalnızca backend whitelist'inden seçilebilir
5. LLM çıktısı Pydantic ile doğrulanır; sayısal sonuçlar modelden doğrudan
   kabul edilmez
6. Yalnızca `.xlsx`; `.xls`, `.xlsm`, makrolu, şifreli, bozuk reddedilir
7. Sınırlar environment config'tir, sabit kodlanmaz
8. Public deployment yok; yalnızca anonim örnek veri
