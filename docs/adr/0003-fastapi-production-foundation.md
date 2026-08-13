# ADR-0003: FastAPI üretim temeli

- Durum: Kabul edildi
- Tarih: 2026-08-12
- Kapsam: BE-02

## Bağlam

BE-01 API sözleşmesini ve route iskeletini dondurdu. Backend'in veri tabanı ve
worker entegrasyonlarından önce ortam doğrulaması, güvenli gözlemlenebilirlik ve
orkestratör health sinyalleri için kararlı bir çalışma temeline ihtiyacı vardır.

## Karar

- Uygulama `create_app()` factory'siyle kurulur; ayarlar cache'li Pydantic
  `get_settings()` fonksiyonundan gelir.
- `development`, `test` ve `production` ortamları desteklenir. Production,
  Base64 kodlu 32 byte master key olmadan başlamaz; docs ve OpenAPI production'da
  sunulmaz.
- CORS origin listesi açıkça tanımlanır; wildcard kabul edilmez. `X-Trace-Id`
  `expose_headers` ile açılır — aksi hâlde cross-origin istemci header'ı okuyamaz
  ve `ApiError.traceId` sessizce boşalır.
- HTTP erişim logu JSON'dur ve yalnızca method, query string içermeyen path,
  status, süre, environment ve trace ID taşır. Header ve body loglanmaz; hassas
  isimler iç içe yapılarda redakte edilir. structlog kullanmayan kayıtlar
  (uvicorn, `warnings`) `ProcessorFormatter.foreign_pre_chain` ile aynı JSON
  boru hattından geçer; tek bir akışta iki format olmaz.
- `/api/v1/health/live` yalnızca process liveness bildirir. `/api/v1/health/ready`
  framework bağımsız async `ReadinessCheck` adapter'larını PARALEL ve kontrol
  başına süre bütçeli (`CHECK_TIMEOUT_SECONDS`) çalıştırır; hiç kontrol kayıtlı
  değilse, biri başarısızsa veya biri süreyi aşarsa `503 SERVICE_NOT_READY`
  Problem Details döndürür. Yalnızca en az bir kayıtlı kontrolün tamamı
  başarılıysa `200 ready` döner.
- Readiness cevabı bağımlılık ADLARINI (`postgres`, `redis`) kimlik doğrulaması
  olmadan verir. Bu bilinçlidir: orkestratör probe'u kimlik doğrulayamaz ve
  isimler sır değildir. Durum/hata ayrıntısı asla dışarı verilmez.
- Bağımlılık yönü `api/workers -> services -> domain` olarak sabitlenir;
  AST tabanlı test framework importlarının iç katmanlara sızmasını engeller.

## Bilinen sınır: ServerErrorMiddleware ve CORS

Starlette `ServerErrorMiddleware`'i TÜM user middleware'in — `CORSMiddleware`
dâhil — dışında çalışır. Yakalanmamış bir exception'dan doğan 500 bu yüzden CORS
katmanına hiç uğramaz. `unhandled_exception_handler` allow-list'e karşı
doğruladığı `Origin` için gerekli header'ları elle ekler; başka hiçbir handler'ın
buna ihtiyacı yoktur, çünkü onlar `ExceptionMiddleware` üzerinden (CORS'un
içinden) döner.

## Sonuçlar

PostgreSQL ve Redis readiness adapter'ları sonraki kartlarda aynı protokole
kaydolabilir. Celery, veri tabanı, migration, Docker ve analiz route iş mantığı bu
kararla eklenmez. Health sözleşmesi Pydantic, OpenAPI, fixture ve frontend Zod
şemalarında birlikte tutulur.

Backend Docker Compose'a eklendiğinde container healthcheck'i
`/api/v1/health/live`'a bağlanmalıdır. `/ready` kayıtlı kontrol olmadığı sürece
tasarım gereği 503 döner; healthcheck oraya bağlanırsa container hiçbir zaman
healthy olmaz.
