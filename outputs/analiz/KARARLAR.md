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

## Yarın cevaplanacaklar

1. **OpenRouter anahtarı nerede duracak?**
   Görüş: `.env`'de, kullanıcı hiç erişmemeli — anahtarı elden vermek daha
   büyük bir zafiyet. UYARI: bugün maliyeti frenleyen tek şey anahtarın
   kullanıcıda olması. Kodda toplam bütçe sınırı, kullanıcı başına sınır ve
   koşu sayısı sınırı YOK; ayrıca sistemde hiç kimlik modeli yok
   (`analyses` ve `uploads` dışında tablo yok, giriş yok, rol yok).
   Anahtar taşınacaksa bütçe tavanı ve erişim kontrolü AYNI ANDA gelmeli.

2. **Geçmiş raporlar ne kadar saklanacak?**
   Görüş: kesinlikle saklanmalı — hem dönemler arası karşılaştırma için hem
   de kullanıcı raporu kaybederse yeniden analiz koşmasın diye. Şu an
   `report_retention_hours` ve `upload_retention_hours` ikisi de 24.
   KVKK için kullanışlı ayrım: yükleme (ham konuşma) kısa sürede silinir,
   rapor (redakte + toplulaştırılmış) uzun saklanır. İkisi ayrı ayar.

3. **Raporlar ekranda mı görünecek?**
   Görüş: görünmeli, excel'de okumak analiz işi değil dosya işi. NOT:
   saklama + ekranda gösterim + dönemsel karşılaştırma tek bir pakettir,
   üç ayrı talep değil. Mevcut ekran yalnızca `top_questions` ve `themes`
   gösteriyor; ürettiğimiz altı rapor için arayüz yok.

4. **Analizi kim koşacak?**
   Belirleyici soru. Kendi ekipse kimlik gerekmez, iş küçük. AUZEF'te başka
   bir birimse giriş + rol + bütçe kontrolü gerekir, iş belirgin büyür.

## Açık teknik konu

**%22,8 kapsam boşluğu bir İÇERİK tahminidir, ölçülmüş cevaplama
başarısızlığı değil.** KB soru listesi ile talep listesi eşleştirilerek
bulundu. Yeni bot hibrit arama kullanıyor (MeiliSearch + Qdrant semantik +
LLM RAG yedeği); tam karşılığı olmayan soruya makul cevap üretiyor olabilir.

Doğrulaması ucuz: 35 boşluk sorusunu gerçek bota sor, hangilerinde
gerçekten düştüğünü gör. Liste ya doğrulanır ya kısalır.

## Taşınabilirlik notu

Kurulan boru hattı kaynak-bağımsız: betikler girdi olarak session listesi
alıyor, dosya formatına bağlı değil. Ayrı ürün kararı hiçbir şeyi
kaybettirmiyor.
