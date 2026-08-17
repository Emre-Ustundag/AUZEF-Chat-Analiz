# 130 MB yük testi — ADR §10 risk 1

Tarih: 17 Ağustos 2026 · Komut: `make loadtest` ·
Koşucu: `apps/backend/scripts/load_test.py`

ADR §10 risk 1 şunu istiyordu:

> "130 MB Excel bellek/CPU riski: streaming upload, ZIP bomb kontrolü,
> `openpyxl` `read_only`, worker memory limiti/process recycling ve **gerçek
> 130 MB fixture ile yük testi**. Parser bir adapter arkasındadır; load test
> başarısızsa API sözleşmesini değiştirmeden alternatif streaming parser
> kullanılabilir."

Bu bir regresyon testi değil, bir **mimari karar noktası**ydı: tüm asenkron
mimari bu dosya boyutu için var ve o yol bir kez bile ölçülmemişti.

**Sonuç: mimari ayakta, parser değiştirilmesine gerek yok — ama test ÜÇ GERÇEK
KUSUR buldu. İlk ikisi o boyuttaki her dosyayı işlenemez kılıyordu; üçüncüsü
ikincisinin teşhisini imkânsızlaştırıyordu.**

## Ortam

|            |                                                            |
| ---------- | ---------------------------------------------------------- |
| Makine     | Apple Silicon, Docker Desktop 28.5.1 (linuxkit aarch64)    |
| Yığın      | `docker compose up -d` — 8 uzun ömürlü servis, proxy dâhil |
| İstek yolu | `:3000` → Caddy → FastAPI (gerçek üretim yolu)             |
| Worker     | Celery prefork, `--concurrency=2 --max-tasks-per-child=8`  |

## Fixture

Gerçek dosya commit edilmiyor; `scripts/make_large_xlsx.py` deterministik
olarak üretiyor (sabit tohum). İçerik gerçek AUZEF trafiğine benzetildi:
sabit şablon havuzu + değişken alanlar (ders adı, tarih, öğrenci no, puan).
Tekrar eden tek bir dize kullanılsaydı ZIP onu neredeyse tamamen yok eder ve
test "büyük dosya"yı değil "çok satır"ı ölçerdi.

|                     |                                                                               |
| ------------------- | ----------------------------------------------------------------------------- |
| Sıkıştırılmış boyut | **130,9 MB**                                                                  |
| Satır               | **2.812.506** (satır sınırının 28 katı)                                       |
| Kolon               | 6 (`tarih`, `kullanici_id`, `mesaj`, `kanal`, `oturum_id`, `yanit_suresi_ms`) |
| Açılmış toplam      | **1,12 GB** (`xl/worksheets/sheet1.xml` tek başına 1145 MB)                   |
| Sıkıştırma oranı    | **8,7** — bomba eşiğinin (200) çok altında                                    |
| Üretim süresi       | 163 sn                                                                        |

## Bulgu 1 — açılmış boyut tavanı meşru dosyayı reddediyordu

İlk koşu upload'ı geçti, worker `422 UPLOAD_CORRUPT_OR_ENCRYPTED` ile
reddetti:

```
{'reason': 'declared_uncompressed_size_exceeds_limit'}
```

Kullanıcıya giden metin: "Dosya okunamadı. Bozuk, şifrelenmiş veya makro
içeren dosyalar desteklenmez." — **üçü de yanlış.** Dosya sağlam, şifresiz
ve makrosuz.

Kök neden sözleşmenin kendi içindeki bir çelişki: ADR §9 hem "sıkıştırılmış
upload sınırı 150 MB" hem "açılmış toplam boyut sınırı 1 GB" diyordu. OOXML
worksheet XML'i ~9x genişlediğine göre 150 MB'lık bir dosya ~1,3 GB eder;
**iki sayı aynı anda doğru olamazdı.** Tavan ZIP bombasına karşıydı ama bu
dosyanın oranı 8,7 — asıl bomba savunması olan üye başına oran kontrolünün
(200) çok altında.

**Düzeltme:** tavan 1 GiB → **4 GiB** (`AUZEF_MAX_UNCOMPRESSED_BYTES`). Donmuş
150 MB sınırında ~27x genişlemeye izin verir. Bomba savunması zayıflamıyor:
asıl katmanlar üye başına sıkıştırma oranı ve akış sırasında sayan gerçek
bayt kontrolü; bu sayı yalnızca ikincil bir mutlak tavan.

`test_config.py` artık iki sayının çelişemeyeceğini doğruluyor:
`max_uncompressed_bytes >= MAX_UPLOAD_BYTES × 10`.

## Bulgu 2 — tarama tavanı profili şemaya uymaz hâle getiriyordu

Tavan düzeltilince dosya doğrulamayı geçti (`uncompressed_bytes: 1200676573`),
worker 166 sn çalıştı ve **`500 INTERNAL_ERROR`** ile düştü.

Container logunda tek satır vardı:

```json
{ "event": "upload_profiling_failed", "exc_info": true }
```

Traceback YOK — kök neden yerelde yeniden üretilerek bulundu (bu da ayrı bir
bulgu, aşağıda).

Gerçek hata `profile_max_scan_rows` (200.000) tavanındaydı. Tavan `continue`
ile uygulanıyordu: kolon istatistikleri ilk 200.000 satırda duruyor, `row_count`
ise 2.812.506'ya kadar saymaya devam ediyordu. Sonuç `UploadProfile`'ın
değişmezini ihlal etti:

```
sheets.0  Value error, tarih: non_empty_count + empty_count, row_count'a eşit olmalı.
```

Yani **satır sınırının üstündeki HER dosya profillenemiyordu** — oysa
ADR-0002 #13 tam tersini şart koşuyor: "Sınır aşımı upload'ı REDDETMEZ: dosya
tam profillenir, `profile.exceeds_row_limit` işaretlenir." Frontend'in bu
senaryo için hazır bir ekranı ve mock'un `row-limit` senaryosu bile vardı;
gerçek backend o durumu hiçbir zaman üretemiyordu.

**Düzeltme:** tavan artık `break` — profil GERÇEKTEN taranmış satırları
anlatıyor ve değişmez yapı gereği doğru. Varsayılan tavan 200.000 → 10.000.000
yükseltildi; gerçek bound açılmış boyut tavanı (4 GiB ≈ 9,5 M satır ≈ 8,5 dk,
Celery soft time limit'inin altında). Config artık tavanın `MAX_ROWS`'un
altına indirilemeyeceğini fail-fast doğruluyor — aksi hâlde `exceeds_row_limit`
sessizce yalan söylerdi.

Regresyon testi tavanı 4'e çekip aynı kod yolunu saniyeler içinde zorluyor.

## Bulgu 3 — worker istisnaları teşhis edilemiyordu

Bulgu 2'nin kök nedeni loglardan **bulunamadı**: `logger.exception(...)`
çıktıya yalnızca `"exc_info": true` yazıyordu.

Bu bir eksiklik değil, bilinçli bir güvenlik kararının bedeli:
`core/logging.py` traceback render etmiyor çünkü istisna mesajı sağlayıcı
URL'i (`?api_key=…`), veritabanı DSN'i veya dosya içeriği taşıyabilir ve
ADR §7/§9 bunu yasaklıyor. `test_logging.py` bu kuralı koruyor.

**Düzeltme, kuralı bozmadan:** istisna yakalayan yer artık istisna TİPİNİ ve
`ValidationError` ise `loc` yollarını ayrı, güvenli alanlar olarak logluyor:

```json
{
  "event": "upload_profiling_failed",
  "exception_type": "ValidationError",
  "validation_locations": ["sheets.0:value_error"]
}
```

`msg` ve `input` bilerek dışarıda: ikisi de hücre içeriği taşıyabilir. Bu
kayıt kök nedeni tek bakışta verirdi.

## Ölçülen sonuç (düzeltmelerden sonra)

```
durum                : ready
dosya boyutu         : 130.9 MB
upload süresi        : 1.8 sn (72.7 MB/sn)
profilleme süresi    : 184.6 sn
satır (sheet)        : 2,812,506
kolon                : 6
satır sınırı aşıldı  : True
api bellek           : 103 -> 146 MB
worker bellek tepesi : 596 MB (başlangıç 155 MB)
```

### Yorum

**Upload tarafı iddia edildiği gibi çalışıyor.** 130,9 MB, 1,8 saniyede ve
API container'ının belleği yalnızca 43 MB artarak geçti. Gövde hiçbir katmanda
tamponlanmıyor: Caddy akıtıyor, FastAPI diske spool ediyor, oradan MinIO'ya
stream ediliyor. ADR §2'nin "130 MB'lık gövde belleğe ALINMAZ" iddiası
ölçümle doğrulandı.

**`openpyxl read_only` gerçekten sabit bellekte kalmıyor ama kabul edilebilir
kalıyor.** 1,12 GB'lık XML işlenirken worker tepesi 596 MB — dosya boyutuyla
doğrusal DEĞİL (1,12 GB girdi → ~440 MB artış) ama sabit de değil. Tek başına
bir worker için sorun yok; `--concurrency=2` ile iki eşzamanlı 130 MB'lık iş
~1,2 GB ister. Bu, container'a bellek limiti koyarken bilinmesi gereken sayı.

**Profilleme 184,6 saniye** — 45 dakikalık hard timeout'un ve 15 dakikalık
upload profil soft limit'inin rahatça altında. Hız ~15.200 satır/sn. Açılmış
boyut tavanının (4 GiB) izin verdiği en kötü dosya ~9,5 M satır ≈ 10,4 dakika
eder; yine soft limit'in altında.

**Parser değiştirilmesine gerek yok.** ADR §10'un "load test başarısızsa
alternatif streaming parser" maddesi devreye girmiyor: kusurların hiçbiri parser'ın
kendisinde değil, onu saran sınırlarda ve şema değişmezindeydi.

### Bilinen sınır: `unique_count` bir alt sınırdır

`mesaj` kolonu için `unique_count: 200000` raporlandı; gerçek benzersiz değer
sayısı bundan yüksek. `_UNIQUE_TRACKING_LIMIT` (200.000) bilinçli bir bellek
tavanı ve bu sayı **alt sınır** olarak raporlanıyor. Analiz tarafındaki
tekilleştirme bu tavandan etkilenmiyor; yalnızca profil ekranındaki bilgi
yaklaşık.

## Yeniden koşturma

```bash
docker compose up -d
make loadtest                     # fixture'ı üretir, ölçer, siler
make loadtest ARGS="--keep"       # fixture'ı sakla (~131 MB)
```

Fixture'ı ayrıca üretmek için:

```bash
cd apps/backend
uv run python scripts/make_large_xlsx.py --target-mb 130 --out /tmp/buyuk.xlsx
```
