# Ürünleştirme Kararları

## Verilmiş kararlar

**Ayrı ürün.** Analiz, chatbot panelinin içine alınmayacak. AUZEF-Chat-Analiz
son kullanıcı seviyesine getirilecek; chatbot tarafı zaten olgun ve oraya
analiz eklemek kimseye fayda sağlamıyor.

**Hedef kullanıcı teknik olmayan biri.** Ekranda model, prompt sürümü,
sıcaklık, chunk boyutu, kolon eşlemesi görünmeyecek. Hepsi backend config'de;
gerektiğinde geliştirici müdahale eder.

**Taksonomi bizim tarafta kalır.** Arayüzde düzenleme ekranı olmayacak.
Bayatlama durumunda ekranda uyarı çıkar / bildirim gider, gözden geçirme
geliştirici tarafında yapılır.

**Koşu sıklığı haftalık/aylık.** Bir yıllık devasa dökümler istisna; aylık
veri ~50 MB, mevcut 150 MB yükleme sınırının altında. Boyut ve çok-sayfa
sorunları rutin akışta ortaya çıkmıyor.

**Bayatlama göstergesi `«hiçbiri»` oranı.** Her koşuda zaten hesaplanıyor.
Şu an %14,2. Belirgin yükselirse taksonomi gerçeklikten kopmuş demektir.

## Cevaplandı (yönetim görüşmesi sonrası)

1. **OpenRouter anahtarı** → `.env`'de duracak, kullanıcı erişemeyecek,
   hesap limiti 50 USD. NOT: bu limit HESAP GENELİ; kullanıcı ya da koşu
   başına değil. Bir kişi aylık bütçeyi bir öğleden sonra bitirebilir.
   Uygulama içi tavan (aylık toplam sayaç) anahtarla birlikte gelmeli.

2. **Geçmiş raporlar** → saklanacak, hukuki engel yok.
   `upload_retention_hours` kısa kalabilir (ham konuşmayı tutmaya gerek yok),
   `report_retention_hours` uzun olacak. Somut süre kod aşamasında.

3. **Raporlar ekranda** → görünecek. Saklama + gösterim + dönemsel
   karşılaştırma tek pakettir. Mevcut ekran yalnızca `top_questions` ve
   `themes` gösteriyor; ürettiğimiz altı rapor için arayüz yok.

4. **Kullanıcı profili** → KARMA. Teknik olmayan kişiler koşacak, bir kısmı
   ekip içinden bir kısmı değil.

   >>> BU CEVAP KAPSAMI BÜYÜTÜYOR. Kimlik artık zorunlu: kullanıcı tablosu,
   >>> giriş, en az iki rol (koşabilen / görebilen), kim ne koştu kaydı,
   >>> aylık bütçe sayacı. Sistemde şu an bunların HİÇBİRİ yok.
   >>> İyi haber: desen chatbot'ta zaten var (editor/admin/super_admin).
   >>> Ürünleştirmenin en büyük tek kalemi bu olacak.

5. **Tam veri koşusu** → YAPILMAYACAK. Yönetim zorunlu tutmadı, karar bize
   bırakıldı. Gerekçe: 2.597 chunk ≈ 39-48 USD ve ~4 saat; sayılar anlamlı
   ölçüde değişmiyor (±%10 hata payı, sıralama sağlam). Daha önemlisi 50 USD
   tek seferlik harcama değil işletme bütçesi — aylık koşu ~3 USD, yani
   ~16 aylık operasyon. Değişmeyecek bir ölçüm için 13 ayı yakmak mantıksız.

## Açık teknik konu

**%22,8 kapsam boşluğu bir İÇERİK tahminidir, ölçülmüş cevaplama
başarısızlığı değil.** KB soru listesi ile talep listesi eşleştirilerek
bulundu. Yeni bot hibrit arama kullanıyor (MeiliSearch + Qdrant semantik +
LLM RAG yedeği); tam karşılığı olmayan soruya makul cevap üretiyor olabilir.

Doğrulaması ucuz: 35 boşluk sorusunu gerçek bota sor, hangilerinde
gerçekten düştüğünü gör. Liste ya doğrulanır ya kısalır.

BETİK HAZIR: `scripts/validate_gaps_against_bot.py`
Chatbot lokalde ayağa kalkınca:
    export OPENROUTER_API_KEY=...
    python3 scripts/validate_gaps_against_bot.py http://localhost
Uç nokta `POST /widget-chat`, gövde `{"message": "..."}`.
Önce deterministik "bilmiyorum" kalıbı aranır, sonra tek LLM çağrısıyla
cevabın soruyu karşılayıp karşılamadığı değerlendirilir.

## Taşınabilirlik notu

Kurulan boru hattı kaynak-bağımsız: betikler girdi olarak session listesi
alıyor, dosya formatına bağlı değil. Ayrı ürün kararı hiçbir şeyi
kaybettirmiyor.
