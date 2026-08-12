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
- CORS origin listesi açıkça tanımlanır; wildcard kabul edilmez.
- HTTP erişim logu JSON'dur ve yalnızca method, query string içermeyen path,
  status, süre, environment ve trace ID taşır. Header ve body loglanmaz; hassas
  isimler iç içe yapılarda redakte edilir.
- `/api/v1/health/live` yalnızca process liveness bildirir. `/api/v1/health/ready`
  framework bağımsız async `ReadinessCheck` adapter'larını çalıştırır ve
  hiç kontrol kayıtlı değilse veya herhangi bir kontrol başarısızsa
  `503 SERVICE_NOT_READY` Problem Details döndürür. Yalnızca en az bir kayıtlı
  kontrolün tamamı başarılıysa `200 ready` döner.
- Bağımlılık yönü `api/workers -> services -> domain` olarak sabitlenir;
  AST tabanlı test framework importlarının iç katmanlara sızmasını engeller.

## Sonuçlar

PostgreSQL ve Redis readiness adapter'ları sonraki kartlarda aynı protokole
kaydolabilir. Celery, veri tabanı, migration, Docker ve analiz route iş mantığı bu
kararla eklenmez. Health sözleşmesi Pydantic, OpenAPI, fixture ve frontend Zod
şemalarında birlikte tutulur.
