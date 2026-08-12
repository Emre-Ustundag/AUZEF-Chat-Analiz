# ADR-0002 — API sözleşmesinin dondurulması ve OpenAPI parity

- **Durum:** Karar verildi — uygulandı (BE-01)
- **Tarih:** 12 Ağustos 2026
- **Bağımlılık:** [ADR-0001](../mimari.md) (MVP mimarisi)
- **Sonraki:** BE-02 FastAPI iskeleti → PostgreSQL/Alembic → yerel stack

## 1. Bağlam

ADR-0001 API sözleşmesini prosa olarak tanımladı. Frontend bu metinden Zod
şemalarını **elle** türetti (`apps/web/src/lib/api/schemas/`) ve upload, model
listesi, analiz oluşturma, polling, iptal, sonuç ve export akışlarını bu
şemalarla doğrulamaya başladı. Backend kodu yazılmaya başlamadan önce iki
tarafın tek bir sözleşmeyi okuduğunu garanti altına almak ve ADR-0001'in açık
bıraktığı noktaları kapatmak gerekiyordu.

İki taraf elle senkronize tutulamaz. Bu yüzden sözleşme artık **üretilmiş bir
artefakt** (`docs/api/openapi.json`) ve ayrışma CI'da kırmızıya dönüyor.

## 2. Uygulama öncesi doğrulanan iki tuzak

Kararların bir kısmı doğrudan bu iki ölçümden çıktı. Zod 4.4.3 ile
çalıştırılarak doğrulandı:

| Kontrol                                            | Sonuç               | Etkisi                                                                                                                                                                  |
| -------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `z.iso.datetime()` ← `"2026-08-11T10:00:00+00:00"` | **REJECT**          | Pydantic'in **varsayılan** datetime çıktısı frontend'i kırar                                                                                                            |
| `z.iso.datetime()` ← `"...Z"`                      | ACCEPT              | Yalnızca `Z` biçimi geçerli                                                                                                                                             |
| `retry_after: null` / alan yok                     | **REJECT** / ACCEPT | 429 dışında `null` yollamak `problemDetailsSchema`'yı düşürür → `client.ts` **her** hatayı sentetik `INTERNAL_ERROR`'a çevirir ve tüm Türkçe hata tablosu sessizce ölür |
| `z.object().parse({fazladan: 1})`                  | sessizce **strip**  | Düz `parse()` tabanlı bir drift testi, backend alan eklediğinde yeşil kalır                                                                                             |

Üçüncüsü drift kontrolünün tasarımını belirledi: fixture testleri
`expect(parsed).toEqual(raw)` kullanır, yalnızca `parse()` başarısına bakmaz.

## 3. Kararlar

### #1 — Hata kodu enum'u dört kodla genişletildi

`REQUEST_VALIDATION`, `INVALID_MODEL`, `INVALID_PROMPT`,
`COST_LIMIT_EXCEEDED`. Hepsi **422**, hiçbiri **retryable değil** (dördü de
kullanıcı girdisi hatasıdır; aynı isteği tekrarlamak aynı hatayı üretir).

Alternatif — hepsini mevcut `SHEET_OR_COLUMN_NOT_FOUND` altında toplamak —
reddedildi: kullanıcı "Seçilen sayfa veya kolon dosyada bulunamadı" mesajını
tamamen ilgisiz bir bağlamda görürdü.

`GET /api/v1/models` de bu kararla sözleşmeye girdi. ADR-0001 §6 modelin
whitelist'ten seçilmesini şart koşuyordu ama listeyi döndüren ucu
tanımlamıyordu; frontend o uç olmadan configure ekranını hiç render edemiyor.

Whitelist ve varsayılanlar `app/core/catalog.py` içinde tek kaynaktır:

| Model                         | Girdi / 1M token | Çıktı / 1M token | Context window |
| ----------------------------- | ---------------: | ---------------: | -------------: |
| `anthropic/claude-sonnet-4.6` |            3 USD |           15 USD |      1.000.000 |
| `openai/gpt-4.1-mini`         |          0,4 USD |          1,6 USD |      1.047.576 |
| `google/gemini-2.5-flash`     |          0,3 USD |          2,5 USD |      1.048.576 |

Bu değerler 12 Ağustos 2026 tarihinde
[OpenRouter'ın resmi model kataloğuyla](https://openrouter.ai/api/v1/models)
doğrulandı. Üç whitelist üyesinin de `structured_outputs` desteği zorunludur;
bu nedenle bu kabiliyeti yayımlamayan eski `anthropic/claude-sonnet-4`
yerine `anthropic/claude-sonnet-4.6` seçildi.

Varsayılan model `anthropic/claude-sonnet-4.6`, varsayılan ve bilinen prompt
sürümü `faq_analysis/v1`'dir. Fixture üreticisi bu kataloğu doğrudan okur;
TypeScript mock kataloğu üretilmiş `models.list.200.json` ile CI'da birebir
karşılaştırılır.

Model ve prompt kimlikleri tel şemasında serbest `string` değildir:
`ModelId` ve `PromptVersion` enum'ları `AnalysisRequest`, model listesi ve
raporun tamamında aynı exact whitelist'i zorlar. Geçersiz, boş olmayan model
ve prompt değerleri sırasıyla `INVALID_MODEL` ve `INVALID_PROMPT` olur.

### #2 — Satır sınırı: uyar + kırp, reddetme

Upload **her zaman** tam profillenir ve `profile.exceeds_row_limit` set
edilir. Analiz ilk `MAX_ROWS` (varsayılan 100.000) satırı işler ve rapora
`ROW_LIMIT_TRUNCATED` uyarısı ekler. Yeni bir hata kodu gerekmez.

Uyarı sözlüğü (`WarningCode`) **üretici-kapalı, tüketici-açık**: backend
yalnızca sözlükteki üyeleri yayabilir ama tel üstündeki alan `str` kalır. Zod
tarafını enum yapmak, backend'e eklenen her yeni uyarıyı `apiRequest` içinde
şema hatasına — yani 500 muadili bir kırılmaya — çevirirdi; oysa uyarı işi
durdurmayan bir sinyaldir.

**`warnings[].message` kullanıcıya hazır Türkçe olmak zorundadır.** Bu,
`common.ts`'teki "ham backend metni kullanıcıya doğrudan basılmaz" kuralının
belgelenmiş tek istisnasıdır: `code` serbest string olduğu için arayüz
bilinmeyen bir koda mesaj uyduramaz ve gizlenen bir uyarı, kusurlu bir
uyarıdan kötüdür.

### #3 — `Idempotency-Key` opsiyonel ve server destekli

Aynı anahtar + **aynı** gövde → ilk isteğin 202'si aynen döner (replay).
Aynı anahtar + **farklı** gövde → 409 `JOB_CONFLICT`. Kayıt 24 saat saklanır.

"Aynı gövde" ve saklama anahtarı byte seviyesinde şöyle tanımlanır:

- Saklama anahtarı: HTTP method + normalize edilmiş API path +
  `Idempotency-Key`. Query string bu iki POST uçta bulunmadığı için dâhil
  edilmez.
- `POST /analyses` fingerprint'i: Pydantic ile doğrulanmış
  `AnalysisRequest`'in UTF-8 canonical JSON'u (anahtarlar sıralı, gereksiz
  boşluk yok) üzerinden SHA-256. Header'lar ve özellikle
  `X-OpenRouter-Key` dâhil edilmez.
- `POST /uploads` fingerprint'i: dosyanın SHA-256 değeri, istemciden alınan
  orijinal filename, MIME type ve byte size alanlarının canonical JSON'u
  üzerinden ikinci bir SHA-256.
- Saklanan değer ilk `202` status/body/header metadata'sı ve fingerprint'tir;
  TTL oluşturulduğu andan itibaren 24 saattir.

Replay orijinal 202'yi döndürür ve istekle gelen **yeni `X-OpenRouter-Key`
header'ını yok sayar**: anahtar orijinal job'a bağlıdır. Aksi hâlde anahtar
rotasyonundan sonraki bir retry, TTL'i dolmuş ölü bir job'ı sessizce
diriltirdi.

Frontend şu an bu header'ı göndermiyor; OpenAPI belgeliyor ve backend
destekliyor, dolayısıyla sonradan eklemek kırılma yaratmaz.

### #4 — Tarihler: çıkışta yalnızca `…Z`

Çıktı biçimi **her zaman** `YYYY-MM-DDTHH:MM:SS.sssZ`. Girişte her RFC 3339
instant kabul edilip UTC'ye normalize edilir (girişte Postel, çıkışta katı).
Naive datetime bir sunucu hatasıdır ve validation hatası üretir.

Sabit üç haneli kesir bilinçli: çıplak `isoformat()` mikrosaniye sıfırken
kesirli kısmı atlar, değilse altı hane yazar; bu deterministik olmayan şekil
fixture yeniden üretiminde sahte diff üretirdi. Üç hane ayrıca JS
`Date.prototype.toISOString()` ile birebir aynıdır.

Bu asimetri paylaşılan kısıt tablosunda temsil **edilemez** (aynı satır bir
tarafta "geçerli", diğerinde "geçersiz" derdi); bu yüzden bir çıktı kuralı
olarak test edilir.

### #5 — `themes[].related_question_ids` kırpmadan sonra filtrelenir

`top_n` kırpması sonrası bu liste, raporda **gerçekten yer alan** soru
kimliklerine filtrelenir; aksi hâlde arayüz çözemeyeceği bir kimliğe bağlantı
verirdi.

Tema `count` ve `percentage` ise temanın gerçek büyüklüğünü yansıtmaya devam
eder. Yani `top_n` küçükken bir temanın adedi, listelenen sorularının
toplamından büyük olabilir — bu beklenen davranıştır, hata değil.

`tests/fixtures/contract/analyses.result.200.truncated.json` bu kuralın
çalıştırılabilir spesifikasyonudur.

### #6 — `ProblemDetails` alan garantileri

`type`, `title`, `status`, `code`, `detail`, `trace_id` **her** hata
cevabında bulunur. `errors` her zaman vardır (boş olabilir).

`retry_after` **yalnızca 429**'da bulunur ve başka hiçbir cevapta —
`null` olarak dahi — yer almaz. Gerekçesi §2'deki ölçümde.

`trace_id` hem gövdede hem `X-Trace-Id` header'ında UUID'dir ve iki değer
aynıdır. `errors[].field`, alan yolu üretilemeyen validation hatalarında
`null` olabilir; frontend bunu nullable + optional kabul eder.

`type` URI'si koddan türetilir: `/errors/` + küçük harf + `_` → `-`. Aynı
kural mock'ta da uygulanır ve iki taraftan da test edilir.

### #7 — FastAPI'nin varsayılan 422 gövdesi ezilir

`RequestValidationError` merkezi handler'dan RFC 9457 gövdesine çevrilir.
`errors[].field` noktalı yol taşır (`top_n`, `query.format`,
`path.upload_id`).

**`errors[].input` asla yankılanmaz.** FastAPI'nin varsayılan gövdesi onu
içerir ve `POST /analyses` için bu canlı bir sır sızıntısı yoludur: istemci
OpenRouter anahtarını yanlışlıkla gövdeye koyarsa varsayılan handler onu aynen
geri yansıtır ve hem frontend hata state'ine hem de gövde yakalayan her proxy
log'una düşer. RFC 9457 biçimi bu değişikliğin ikinci gerekçesidir, birincisi
değil.

Aynı sebeple `openapi.json`'dan `HTTPValidationError` ve `ValidationError`
şemaları silinir: sunucunun asla üretmediği bir gövdeyi belgeleyen bir kayıt
artefaktı, daha ilk gün yalan söyler.

### #8 — Sözleşme üretilmiş bir artefakt

`docs/api/openapi.json` kayıt artefaktıdır ve Pydantic modellerinden
üretilir; elle yazılmaz. Böylece Pydantic ↔ OpenAPI parity'si tanım gereği
sağlanır.

Public OpenAPI hedef v1 sözleşmesini gösterir; contract-only stub'ların geçici
`501` cevabı belgelenmez. Her uç yalnızca gerçekten üretebildiği status'ları,
hatalarda yalnızca `application/problem+json` media type'ını ve Pydantic
fixture'larından türetilmiş adlandırılmış request/response örneklerini taşır.

Drift, üretilmiş bir TypeScript client ile değil **iki dilli fixture
doğrulamasıyla** zorlanır (§4).

### #9 — `DELETE /analyses/{id}` üçlü durumu

Aktif job → **204** (durum `cancelled`). Terminal job → **409**
`JOB_CONFLICT`. Bilinmeyen id → **404** `JOB_NOT_FOUND`.

Terminal job'ı sessizce 204'le geçmek, kullanıcıya iptal ettiğini
düşündürürken aslında hiçbir şey olmaması demekti.

### #10 — `COST_LIMIT_EXCEEDED` iki noktada üretilir

`POST /analyses` içinde **senkron ön tahmin** (upload profilinden satır sayısı
× ortalama uzunluk × model fiyatı) sınırı aşarsa istek **422** ile reddedilir —
LLM çağrısı hiç başlamaz. Ayrıca çalışma sırasında sınır aşılırsa aynı kod
**terminal job hatası** olarak döner.

ADR-0001 §9 "iş güvenli biçimde durur" diyordu ama `POST /analyses` hemen 202
döndüğü için, yalnız o cümleyle kod hiçbir zaman 422 olarak üretilemezdi ve
configure ekranı ile progress ekranı hangisinin hatayı göstereceği konusunda
çelişirdi.

### #11 — Export dosya adı sabit

`Content-Disposition` her zaman
`attachment; filename="analiz-{analysis_id}.{xlsx|json}"`. Kullanıcının
yüklediği dosya adı **asla** kullanılmaz.

Kaynak dosyalar `öğrenci mesajları.xlsx` gibi adlandırılacak; "ASCII kullan"
demek türetme kuralını backend'in uydurmasına bırakırdı. Sabit ad tanım gereği
ASCII'dir, RFC 5987 `filename*` gerektirmez ve `endpoints.ts`'teki mevcut
fallback ile birebir aynıdır.

JSON export gövdesi telde gerçek `AnalysisReport` nesnesidir ve OpenAPI'de
aynı bileşene referans verir. Yalnızca XLSX media type'ı binary şema taşır;
JSON'u `string/binary` olarak belgelemek üretilmiş istemciyi yanlış tipe
yönlendirir.

### #12 — Sürümleme

`schema_version` **rapor gövdesini** sürümler (`"1.0"`). API `/api/v1` öneki
ve `openapi.info.version` ile sürümlenir. Opsiyonel bir alan eklemek ikisini de
artırmaz; bir alanı kaldırmak veya yeniden adlandırmak ikisini de artırır.

`openapi.info.version` paket sürümünden bilerek ayrıdır: bir bağımlılık
yükseltmesi kayıt artefaktını değiştirmemelidir.

## 4. Drift kontrolü

Dört katman, ucuzdan pahalıya:

| Katman            | Ne yapar                                                                                                                                                | Nerede                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 0 — Envanter      | `manifest.json` vakaları listeler; iki taraf da model→şema eşlemesinin **çift yönlü** tam olduğunu doğrular                                             | `contract-fixtures.test.ts`, `test_fixtures_contract.py` |
| 1 — Fixture       | Python fixture'ları Pydantic **instance**'larından üretir (gerçek serializer'lardan geçerler); Zod aynı dosyaları `parse` **ve `toEqual`** ile doğrular | aynı                                                     |
| 2 — Enum parity   | `openapi.json`'daki enum üyeleri Zod enum'larıyla küme olarak karşılaştırılır                                                                           | `contract-openapi.test.ts`, `test_openapi_contract.py`   |
| 4 — Yol envanteri | Belgelenen dokuz uç iki yönlü karşılaştırılır                                                                                                           | `contract-openapi.test.ts`                               |

**Katman 3 (üretilmiş TS tiplerini Zod ile diff'lemek) bilinçli olarak
yapılmadı.** Gürültü baskın: `z.input`/`z.output` `.default()` taşıyan her
alanda ayrışır ve `z.uuid()`, `z.iso.datetime()`, `z.int()` tip düzeyinde
`string`/`number`'a silindiği için drift'e en yatkın kısıtları zaten göremez.
Katman 1'in üstüne kattığı şey neredeyse yok (fixture'lar tüm alanlar dolu
üretiliyor), maliyeti ise CI'ı kırmızıya döndüren anlaşılmaz tip hataları.

Katman 1+2'nin göremediği tek şey **sınır değerlerin ayrışmasıdır** (backend
`le=100` vs frontend `.max(50)`). Bu doğrudan paylaşılan bir tabloyla
karşılanır: `tests/fixtures/contract/constraints.json`, iki suite de aynı
satırları çalıştırır.

**Bayatlama guard'ı pazarlıksızdır.** CI `export_*.py --check` çalıştırır;
olmasaydı biri Pydantic modelini değiştirip yeniden üretmeyi unutur ve her iki
suite de bayat dosyalara karşı yeşil kalırdı — çoğu fixture tabanlı drift
kontrolünü dekoratif yapan şey tam olarak budur.

### Halef karar

ADR-0001 §6 TypeScript client'ın OpenAPI'den üretileceğini söylüyor. O
yapıldığında drift yapısal olarak imkânsızlaşır ve buradaki dört katman
silinebilir. Bu kartta kapsam dışı: mevcut Zod şemaları Türkçe
`react-hook-form` doğrulama mesajları ve `.default()` semantiği taşıyor,
formları yeniden bağlamak bir sözleşme dondurma işi değil.

## 5. Kabul edilen ödünçler

Bunlar bilinen kusurlar; incident triage'da yeniden keşfedilmesinler diye
kayıtlılar.

1. **`JOB_CONFLICT` artık üç ilgisiz anlam taşıyor** — sonuç hazır değil,
   terminal job iptali, idempotency gövde uyuşmazlığı. Frontend üçüne de tek
   mesaj gösteriyor ("Bu işlem için zaten devam eden bir analiz var"), ki bu
   ikisi için yanlış. Enum'u yeniden açmak yerine çözüm çağrı-yeri bazlı bir
   mesaj override map'i (`endpoints.ts` hangi çağrıyı yaptığını biliyor);
   ayrı bir kart olarak not edildi.

2. **Yönlendirilmemiş 404 → `JOB_NOT_FOUND`** semantik olarak yanlış: yazım
   hatası olan bir URL kullanıcıya "İşlem bulunamadı. Bağlantı geçersiz veya
   süresi dolmuş olabilir." gösterir. API'yi yalnızca kendi frontend'imiz
   çağırdığı için kabul edildi; ayrı bir `NOT_FOUND` kodu enum'u büyütmeye
   değmedi.

3. **Eksik `X-OpenRouter-Key` → `PROVIDER_AUTH_FAILED`**, teknik olarak bir
   istek doğrulama hatası olmasına rağmen. `REQUEST_VALIDATION` var artık,
   ama boş anahtar alanı için mevcut Türkçe mesaj ("OpenRouter API anahtarı
   doğrulanamadı. Anahtarı kontrol edip tekrar deneyin.") jenerik doğrulama
   metninden belirgin şekilde daha yardımcı.

4. **İstek gövdelerinde `extra="forbid"`, Zod'da ise strip.** Frontend
   backend'in reddedeceği bir alanı memnuniyetle gönderebilir. Yön doğru
   (sunucu otoritedir) ama simetrik değil.

## 6. Sonuç

Karar uygulandı. Tamamlanma koşulu: `web`, `backend` ve `contract` CI
job'larının üçü de yeşil, ve tam yeniden üretimden sonra
`git diff --exit-code -- docs/api tests/fixtures/contract` temiz.
