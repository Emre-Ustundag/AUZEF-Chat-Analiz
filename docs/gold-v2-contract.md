# Gold v2 veri sözleşmesi

Gold v2, 516 alias içeren session eşleştirme tabanı ile `yanit-gold-inceleme-174.xlsx` dosyasındaki insan kararlarının birleştirilmesiyle üretilir. İnceleme dışında kalan 342 alias mevcut QnA eşleşmesini korur; 174 inceleme satırı bu tabanı vaka numarasıyla override eder. Kaynak workbook değiştirilmez. Üretici her çalışmada doğrulama raporu yazar; bloklayıcı hata varken `gold-v2.json` oluşturmaz.

## İnsan alanları

`İnsan kararı` aşağıdaki değerlerden biri olmalıdır:

- `4o daha iyi`
- `Luna daha iyi`
- `İkisi de kabul edilebilir`
- `İkisi de sorunlu`
- `Bağlam olmadan değerlendirilemez`
- `Testten çıkar`

İlk üç karar koşulabilir vaka üretir ve en az bir kabul edilen QnA kimliği gerektirir. `İkisi de sorunlu` kararında kimlik girilmişse vaka koşulabilir; girilmemişse `needs_kb` olur. Son üç kararın açıklaması `İnceleme notu` alanına yazılmalıdır.

## Kabul edilen QnA kimlikleri

Tek niyette alternatif kabul edilen QnA kimlikleri virgülle ayrılır:

```text
306, 118
```

`Split kararı` değeri `Gerekli` olduğunda niyet grupları `|` ile ayrılır. Grup sırası `Split vakaları` sekmesindeki `Luna ayrımı` sırasıdır:

```text
306, 118 | 256
```

Bu örnekte ilk niyet için 306 veya 118, ikinci niyet için 256 kabul edilir. Niyet grubu sayısı `Split sayısı` ile aynı olmalıdır. Kimlikler aktif yerel QnA kataloğunda bulunmalıdır.

## Durumlar

- `ready`: Oturum testinde puanlanabilir.
- `needs_context`: Geçmiş konuşma tamamlanmadan puanlanmaz.
- `needs_kb`: Uygun QnA içeriği oluşturulmadan puanlanmaz.
- `excluded`: Test kapsamına alınmaz.

## Çıktı

Her hazır niyet kabul edilen QnA kimliklerini ve katalogdan dondurulmuş cevap metinlerini taşır. Böylece değerlendirme tek cevap varsayımına bağlı kalmaz ve sonraki QnA değişiklikleri eski benchmark sonucunu sessizce değiştirmez.

## Çalıştırma

Önce yerel Chatbot veritabanından aktif QnA kataloğu dışa aktarılır. Ardından doğrulayıcı workbook, katalog, Gold v2 çıktı yolu ve doğrulama raporu yoluyla çalıştırılır:

```bash
python3 scripts/build_gold_v2.py \
  --workbook outputs/local-kb-20260914-1443/yanit-gold-inceleme-174.xlsx \
  --qna-catalog outputs/session-eval-20260914/qna-catalog-20260915.json \
  --baseline-aliases outputs/session-eval-20260914/alias-session-matches.jsonl \
  --output outputs/session-eval-20260914/gold-v2.json \
  --report outputs/session-eval-20260914/gold-v2-validation.json
```

Doğrulama hatayla sonuçlanırsa rapor güncellenir fakat Gold v2 dosyası yazılmaz.

## Teknik olmayan inceleme ekranı

`auzef-kolay-inceleme.html` dosyası tarayıcıda doğrudan açılır. İnceleyici her
vakayı kartlar üzerinden değerlendirir; QnA kimlikleri yerine soru metinlerini
işaretler ve `Sonucu indir` düğmesiyle bir JSON dosyası üretir. Kararlar aynı
tarayıcıda otomatik saklanır.

İndirilen dosya workbook'a geri yazılmadan doğrulayıcıya verilebilir:

```bash
python3 scripts/build_gold_v2.py \
  --workbook outputs/local-kb-20260914-1443/yanit-gold-inceleme-174.xlsx \
  --review-json auzef-inceleme-174-174.json \
  --qna-catalog outputs/session-eval-20260914/qna-catalog-20260915.json \
  --baseline-aliases outputs/session-eval-20260914/alias-session-matches.jsonl \
  --output outputs/session-eval-20260914/gold-v2.json \
  --report outputs/session-eval-20260914/gold-v2-validation.json
```
