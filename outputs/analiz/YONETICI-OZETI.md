# AUZEF Chatbot — Bir Yıllık Konuşma Analizi

**Kapsam:** 10 Temmuz 2025 – 10 Temmuz 2026 · 6,4 milyon mesaj · 592.964 konuşma
**Yöntem:** Yazılı soru içeren 289.567 konuşmanın aylara orantılı %10'luk örneklemi (29.911 konuşma) sınıflandırıldı.

---

## 1. Kullanıcıların yarısı hiç yazmıyor

592.964 konuşmanın **%50,2'sinde** kullanıcı tek kelime yazmadan çıkıyor; yalnızca tıklıyor, geziniyor. Bu, chatbot içeriğinden bağımsız bir kullanım sorunu ve ayrıca incelenmeye değer.

Aşağıdaki tüm sayılar, en az bir soru yazılmış 289.567 konuşmaya aittir.

## 2. Eski botun performansı

İki farklı ölçüt var ve karıştırılmamalı:

| Ölçüt | Sonuç | Anlamı |
|---|---|---|
| **Cevap düzeyinde** | %74,5 onay | Bot bir cevap verdiğinde kullanıcı dörtte üç oranında "yeterli" dedi |
| **Konuşma düzeyinde** | %52,8 sorunlu | Konuşmaların yarısından fazlasında **en az bir** ret veya "anlayamadım" yaşandı |

Yılda yaklaşık **153.000 konuşmada** en az bir başarısızlık anı var. Tekil cevaplar makul, ancak bir konuşma boyunca en az bir kez yanlış anlaşılma çok yaygın.

## 3. Asıl bulgu: bilgi tabanı boşluğu

Yeni chatbot'un bilgi tabanı (266 soru), eski Etiya botunun kataloğunun **neredeyse birebir kopyası** — 265 sorudan 263'ü aynı, yalnızca 1 soru eklenmiş. Soru kümesi devralınmış, dolayısıyla eski botun boşlukları da devralınmış.

Öğrencilerin gerçekte sorduğu konular bilgi tabanıyla karşılaştırıldığında:

| | Pay | Yıllık konuşma |
|---|---|---|
| Bilgi tabanında karşılığı **var** | %77,2 | ~223.600 |
| Karşılığı **yok** (tanımlı 35 konu) | %8,6 | ~25.000 |
| Hiçbir tanıma oturmayan (uzun kuyruk) | %14,2 | ~41.000 |
| **Toplam boşluk** | **%22,8** | **~66.000** |

Yani her dört öğrenci sorusundan yaklaşık birinin sistemde karşılığı yok.

**Önemli nüans:** En çok sorulan 20 konunun 19'u bilgi tabanında mevcut. Boşluk tepe sorularda değil, orta ve uzun kuyrukta. Bilgi tabanı kötü değil — **eksik**.

## 4. Aksiyon: 35 makale

Bilgi tabanında karşılığı olmayan 35 konu tespit edildi ve içe aktarılabilir formatta hazırlandı. **En yüksek hacimli 8 tanesi tek başına yılda ~15.600 konuşmayı kapsıyor.**

| Konu | Yıllık | Sorunlu |
|---|---|---|
| Kayıt tarihleri ne zamandır? | ~3.700 | %49 |
| Kaçıncı sınıftayım / kaç dersim kaldı? | ~3.050 | %53 |
| Çıkmış sınav soruları ve kitapçıkları | ~2.220 | %54 |
| Kaydım aktif mi, nasıl kontrol ederim? | ~2.140 | %52 |
| Hangi lisans/ön lisans bölümleri var? | ~1.340 | %68 |
| Lisans tamamlama basamakları | ~1.180 | %66 |
| Kayıt ücretini ödedikten sonra ne yapmalıyım? | ~1.020 | %62 |
| Ödeme zamanı geçtikten sonra ödeme | ~930 | %61 |

Teslim edilen `KB-taslak-eksik-sorular.csv` dosyasında her konu için **gerçek öğrenci ifadeleri** (ortalama 9,8 adet, dökümden alınmış) hazır. Doldurulması gereken tek alan cevap metni.

## 5. Mevsimsellik

Soru dağılımı dönemlere göre sert biçimde değişiyor. Örnekler:

- *"Ders alma nasıl yapılır?"* — tıklamalarının **%57'si tek başına Eylül'de**
- *"Ödeyeceğim harç miktarı"* — **%55'i Şubat'ta**
- *"Diplomamı ne zaman alabilirim?"* — Haziran'da zirve yapıyor

Eylül ayı tek başına yıllık hacmin %23'ü. SSS sayfalarının dönem başlarında güncellenmesi, sabit bir liste tutmaktan belirgin daha etkili olur.

## 6. Ek bulgu: mobil

*"AUZEF Mobil uygulamasına neden giriş yapamıyorum?"* sorusu bilgi tabanında **mevcut** olmasına rağmen **%73 sorunlu** oranıyla listenin en üstünde. Soru biliniyor, cevap veriliyor, ancak sorun çözülmüyor. Bu bir içerik değil, muhtemelen bir sistem sorunu; ilgili ekibe iletilmesi önerilir.

---

### Yöntem notu

Örneklem rastgele değil, aylara orantılı ve sistematiktir; aynı veriden aynı sonuç üretilir. %10'luk örneklemde orta ölçekli bir konunun tahmin hatası ±%10 civarındadır, üst sıralarda daha düşüktür. Bu belirsizlik hiçbir önceliklendirme kararını değiştirmemektedir.

Sınıflandırma sabit bir soru listesine karşı yapılmıştır; sistem kendiliğinden kategori üretmez. Bu, sonuçların koşudan koşuya değişmemesini sağlar.
