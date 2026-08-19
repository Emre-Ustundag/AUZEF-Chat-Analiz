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

Whitelist, context window ve kesinti durumunda kullanılan yedek fiyatlar
`app/core/catalog.py` içinde tek kaynaktır:

| Model                         | Girdi / 1M token | Çıktı / 1M token | Context window |
| ----------------------------- | ---------------: | ---------------: | -------------: |
| `anthropic/claude-sonnet-4.6` |            3 USD |           15 USD |      1.000.000 |
| `openai/gpt-4.1-mini`         |          0,4 USD |          1,6 USD |      1.047.576 |
| `google/gemini-2.5-flash`     |          0,3 USD |          2,5 USD |      1.048.576 |

Normal çalışmada fiyatlar public
[OpenRouter model kataloğundan](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
bir saatlik Redis cache ile yenilenir. Yenileme başarısızsa yedi güne kadar
son bilinen snapshot, o da yoksa tablodaki yedekler kullanılır. Dış katalog
yalnızca fiyatları günceller; whitelist'e kendiliğinden model ekleyemez.
Üç whitelist üyesinin de `structured_outputs` desteği zorunludur;
bu nedenle bu kabiliyeti yayımlamayan eski `anthropic/claude-sonnet-4`
yerine `anthropic/claude-sonnet-4.6` seçildi.

Daha düşük katalog fiyatı nedeniyle varsayılan model
`google/gemini-2.5-flash`, varsayılan prompt sürümü
`faq_analysis/v2`'dir. `v1` tarihsel sonuçlar için kayıtlı kalır. Fixture
üreticisi yedek kataloğu doğrudan okur;
TypeScript mock kataloğu üretilmiş `models.list.200.json` ile CI'da birebir
karşılaştırılır.

Model ve prompt kimlikleri tel şemasında serbest `string` değildir:
`ModelId` ve `PromptVersion` enum'ları `AnalysisRequest`, model listesi ve
raporun tamamında aynı exact whitelist'i zorlar. Geçersiz, boş olmayan model
ve prompt değerleri sırasıyla `INVALID_MODEL` ve `INVALID_PROMPT` olur.

### #2 — Büyük veri eşiği: uyar, kıpma

Upload **her zaman** tam profillenir ve `profile.exceeds_row_limit` set
edilir. `MAX_ROWS` (100.000) artık kesme veya ret sınırı değil, kullanıcıya
dosyanın uzun sürebileceğini ve pahalı olabileceğini bildiren bir eşiktir.
Worker filtrelerden sonra kalan tüm satırları işler; raporda kesme uyarısı
üretmez. Maliyet güvenliğini satır kesmek değil, analiz öncesi ve koşu
sırasındaki `max_cost_usd` kapıları sağlar.

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

Uygulama: backend `apps/backend/app/services/idempotency.py` (kayıt Redis'te,
TTL `Settings.idempotency_ttl_seconds`), mock `apps/web/src/mocks/idempotency.ts`.
Fingerprint kuralı iki dilde ayrı yazıldığı ve fingerprint'ler tel üstünde
karşılaşmadığı için `tests/fixtures/contract/idempotency.fingerprints.json`
üretiliyor: Python'un hesapladığı hash'ler TypeScript tarafında da doğrulanıyor
(`contract-idempotency.test.ts`).

Talep İKİ FAZLI tutulur (`SET NX` ile "pending", 202'de gövde): tek fazlı bir
kayıt, aynı anahtarla EŞZAMANLI gelen iki isteğin ikisini birden geçirirdi.
Yalnızca 202 saklanır; hata ile biten istek talebi bırakır — aksi hâlde
`COST_LIMIT_EXCEEDED` alan kullanıcı, hatanın önerdiği düzeltmeyi
(`max_cost_usd`'yi yükseltmek) uygulayınca gövdesi değiştiği için 409 alırdı.

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

Soru ve tema id'leri kendi listelerinde benzersizdir; bir tema aynı soru
bağlantısını tekrarlayamaz. Her soru/tema `count` değeri `analyzed_count`'u
aşamaz ve `percentage`, `count / analyzed_count * 100` değerinin bir ondalığa
**half-up** yuvarlanmış halidir. Python `round()` half-even kullandığı için iki
dil de aynı tam sayı formülünü uygular. `redacted_count` da `analyzed_count`'u
aşamaz.

`tests/fixtures/contract/analyses.result.200.truncated.json` bu kuralın
çalıştırılabilir spesifikasyonudur.

### #6 — `ProblemDetails` alan garantileri

`type`, `title`, `status`, `code`, `detail`, `trace_id` **her** hata
cevabında bulunur. `errors` her zaman vardır (boş olabilir).

`retry_after` **yalnızca 429**'da bulunur ve başka hiçbir cevapta —
`null` olarak dahi — yer almaz. Gerekçesi §2'deki ölçümde.

`status`, `code` kaydındaki HTTP statüsüyle birebir eşleşir; handler veya
istemci bu eşleşmeyi override edemez. Contract-only 501 bu nedenle ayrı
`NOT_IMPLEMENTED` kodudur, `INTERNAL_ERROR` değildir.

`trace_id` hem gövdede hem `X-Trace-Id` header'ında UUID'dir ve iki değer
aynıdır. `errors[].field`, alan yolu üretilemeyen validation hatalarında
`null` olabilir; frontend bunu nullable + optional kabul eder.
Request sonunda ContextVar token'ı her durumda reset edilir; unhandled-error
yolu aynı kimliği `request.state.trace_id` üzerinden korur.

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

Starlette/python-multipart'ın route validation'dan önce ürettiği bozuk veya
boundary'siz multipart 400'leri güvenli, sabit detaylı
`REQUEST_VALIDATION`/422'ye normalize edilir; parser detayı dışarı yansıtılmaz.

### #8 — Sözleşme üretilmiş bir artefakt

`docs/api/openapi.json` kayıt artefaktıdır ve Pydantic modellerinden
üretilir; elle yazılmaz. Böylece Pydantic ↔ OpenAPI parity'si tanım gereği
sağlanır.

**"Tanım gereği" bir muafiyet değil, bir yapılandırma sonucudur.** FastAPI
cevap modelleri için Pydantic'in _serialization_ şemasını yayımlar ve default'u
olan alanlar orada varsayılan olarak `required` DIŞINDA kalır — oysa
`model_dump()` onları her cevapta yazar. Bu yüzden `ApiModel`
`json_schema_serialization_defaults_required=True` taşır. Bayrak yalnızca
`ProblemDetails`'te olduğu sürece artefakt `status`, `warnings`, `error`,
`profile` ve `estimated_seconds_remaining`'i "opsiyonel" diye belgeliyordu;
üretilmiş bir client'ta arayüzün dayandığı discriminator'lar buharlaşırdı.
`contract-openapi.test.ts` beş cevap modelinin `required` listesini kilitler.

Public OpenAPI mevcut contract-only davranışı da doğru gösterir: dokuz stub
`501 NOT_IMPLEMENTED` cevabını belgeler. BE-02 bir route'u uyguladığında o
route'un 501 kaydı kaldırılır. Her uç yalnızca gerçekten üretebildiği status'ları,
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
`AnalysisReport` bu değeri serbest string değil literal `"1.0"` olarak doğrular.

`openapi.info.version` paket sürümünden bilerek ayrıdır: bir bağımlılık
yükseltmesi kayıt artefaktını değiştirmemelidir.

### #13 — Upload sınırı ve büyük veri eşiği sözleşme sabitidir

`MAX_UPLOAD_BYTES` ve `MAX_ROWS`, `app/core/config.py` içinde modül seviyesinde
`Final` sabitlerdir ve `.env.example`'da yer almaz. Diğer çalışma sınırları
(`max_uncompressed_bytes`, `analysis_timeout_seconds`,
`idempotency_ttl_seconds`) `Settings` içinde ve `AUZEF_` önekiyle
environment'tan değiştirilebilir.

`MAX_ROWS`, backend `profile.exceeds_row_limit` işareti ile frontend'in büyük
veri uyarısını aynı noktada üretmesi için iki tarafta aynı kalır; rapor sayım
değişmezlerine girmez ve analizi kırpmaz. `MAX_UPLOAD_BYTES` ise
frontend'in yükleme öncesi rejection ve kullanıcıya gösterilen metnini
belirler: backend daha yüksekse browser geçerli dosyayı engeller, daha düşükse
UI geçerli gösterdiği dosyayı ancak upload bittikten sonra 413 ile kaybeder.

İki sınır `manifest.json`'da `limits.max_upload_bytes` ve `limits.max_rows`
olarak yayımlanır; `contract-fixtures.test.ts` frontend sabitlerini bunlara
karşı doğrular. Değeri değiştirmek bir sözleşme değişikliğidir:
`config.py` sabiti + `contract_version` bump + `make generate` + ilgili
frontend `LIMITS` sabiti birlikte güncellenir.

### #14 — Maliyet job başında sabitlenir, sağlayıcı tutarı önceliklidir

Analiz oluşturulurken seçilen canlı/yedek fiyatlar `analyses.pricing_snapshot`
alanına yazılır. Kuyrukta beklerken katalog yenilense bile ön tahmin, worker
tavanı ve fallback hesabı aynı fiyatlarla çalışır. OpenRouter yanıtı
`usage.cost` taşıyorsa rapordaki tutar doğrudan bu gerçek borçlandırma
değeridir (`cost_source=provider`). Alan yoksa prompt, completion, cache-read
ve cache-write tokenları snapshot oranlarıyla hesaplanır
(`cost_source=calculated`).

`AnalysisReport.estimated_cost_usd` tarihsel adı geriye uyumluluk için korunur
ve cevap şemasında yalnızca `ge=0` kısıtı taşır. `pricing_snapshot` rapora da
eklenir; doğruluk **yazma yolunda** garanti edilir.

Bunu bir `model_validator` içinde `catalog.estimate_cost_usd` ile yeniden
hesaplayıp karşılaştırmak cazipti ama okuma yolunu değişken kataloğa bağlardı:
OpenRouter fiyatı değiştiği an ve daha önce üretilmiş
**tüm** raporlar cevap doğrulamasında düşerek kalıcı 500 verirdi
(`GET /analyses/{id}/result`). Aynı gerekçeyle raporun `model` alanı aktif
`ModelId` whitelist'inden ayrı, boş olmayan bir tarihsel kimliktir — bir modeli
kullanımdan kaldırmak, onunla üretilmiş raporları silmek anlamına gelmemeli.
Request ve model listesi aktif enum'u kullanmaya devam eder.

Kural: cevap şemaları **tel biçimini** ve gövde içi tutarlılığı doğrular;
zamanla değişen dış tablolara bakan iş mantığı doğrulaması yazma yolunda kalır.

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
