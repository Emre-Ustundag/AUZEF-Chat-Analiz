# AUZEF Chatbot Analizi — Uygulama Planı
Kaynak: chatbot.xlsx (7 sayfa)
ÖLÇÜLDÜ: 6.405.948 satır | 592.964 session | 2025-07-10 → 2026-07-10

Aylık hacim (satır):
  2025-07  229.674     2026-01  329.237
  2025-08  320.847     2026-02  1.102.893
  2025-09  1.474.843   2026-03  719.732
  2025-10  788.096     2026-04  232.398
  2025-11  284.541     2026-05  134.089
  2025-12  162.138     2026-06  504.947
                       2026-07  122.513
Eylül, Haziran'ın 3 katı. Eylül-Ekim yılın %35'i ve önceki veri setinde
hiç yoktu — Haziran'a dayalı bir SSS listesi kayıt dönemini kaçırırdı.

Sinyaller: Onayladı 388.586 / Reddetti 133.112 (onay %74,5, Haziran'daki
%75,8 ile tutarlı) | QnA seçimi 174.475 tıklama | MessageFeedback BOŞ
Yaklaşım: katmanlı örneklem (seçenek b)

## İlke
LLM'e yalnızca insanın yapamayacağı iş verilir. Sayma, filtreleme, sinyal
çıkarma, örnekleme — hepsi deterministik ve ücretsiz. LLM sadece iki yerde:
taksonomi önerisi ve session→soru ataması. Böylece maliyet öngörülebilir,
sonuç tekrarlanabilir olur.

---

## FAZ 0 — Hazırlık   (deterministik, 0 USD)

0.1  Yedi sayfayı tek akışta birleştir, kolonları normalize et
     (PascalCase → snake_case). 869 MB xlsx tek geçişte okunur, belleğe
     alınmaz.

0.2  Session'a indirge. Her session için:
       - kullanıcının yazdığı text mesajlar (sırayla, birleştirilmiş)
       - ay etiketi
       - sonuç sinyali: onay sayısı, ret sayısı, "anlayamadım" sayısı
       - kanal (webchat/mobile)
     Yalnızca en az bir yazılı mesajı olan session'lar tutulur.

0.3  Çıktı: outputs/analiz/sessions-yil.jsonl
     Beklenen: ~295.000 yazılı session (Haziran'da yazılı oran %49,8 idi;
     592.964 session'ın yarısı). Dosya birkaç on MB.

0.4  Profil raporu: aylık session hacmi, yazılı oran, onay/ret oranları,
     "anlayamadım" oranı — ay ay.

ÇIKTI: çalışma dosyası + veri seti profili
KARAR NOKTASI YOK

---

## FAZ 1 — Taksonomi   (~0,5 USD)

1.1  Tam yıldan QnA kataloğunu çıkar (QuickReplyLabel → "QnA seçimi: ...").
     Tam yılda 174.475 QnA tıklaması var (Haziran'da 10.355). Katalog
     hem daha geniş hem mevsimsel soruları da içerecek.  [deterministik]

1.2  Tıklamaların %80'ini kapsayan kısmı taksonomi çekirdeği yap.
     [deterministik]

1.3  Aylara orantılı 3.000 session'lık ön örneklemi bu çekirdeğe karşı
     sınıflandır. Amaç kapsama değil, BOŞLUK BULMAK.  [~0,15 USD]

1.4  "hiçbiri" kovasından yeni madde öner. Mevsimsel sorular burada
     çıkacak (kayıt dönemi, ekle-bırak, bütünleme).  [~0,05 USD]

1.5  >>> ONAY: taksonomi listesi gözden geçirmeye sunulur <<<
     Gereksiz maddeler silinir, ifadeler düzeltilir, eksikler eklenir.

ÇIKTI: onaylanmış sabit taksonomi
KARAR NOKTASI: taksonomi içeriği

---

## FAZ 2 — Sınıflandırma   (~5-7 USD)

2.1  Katmanlı örneklem: her aydan, o ayın yazılı session hacmiyle
     ORANTILI olarak toplam ~30.000 session (yazılı session'ların ~%10'u). Sistematik seçim, rastgele
     değil — aynı veriyle aynı örneklem çıkar.

2.2  Sabit taksonomiye karşı sınıflandır. Reduce yok, kategori icadı yok.
     Model yalnızca atama yapar; kategori sayısı sabittir.

2.3  Denetim: en büyük kovaların içeriğine bak, çöp kovası oluşmuş mu
     kontrol et. Atamalar kaydedilir.

ÇIKTI: session → soru ataması + sonuç sinyali
KARAR NOKTASI: sonuçlar tutarlıysa devam, değilse taksonomi revizyonu

---

## FAZ 3 — Raporlar   (deterministik, 0 USD)

3.1  SSS RAPORU — hacme göre sıralı sorular.
     Doğrudan SSS sayfalarına konabilecek liste.

3.2  KB YENİ MAKALE — "hiçbiri" kovasındaki konular.
     Botun hiç cevabı olmayan sorular.

3.3  KB DÜZELTME — ret oranına göre sıralı.
     Botun cevabı VAR ama kullanıcı beğenmiyor. Soru da mevcut cevap da
     belli olduğu için en hızlı kazanç burada.

3.4  MEVSİMSELLİK — hangi soru hangi ayda soruluyor.
     SSS sayfasını dönem başında güncellemek için. Bir yıllık veri
     olmadan üretilemezdi.

3.5  KANAL KIRILIMI — webchat vs mobile fark var mı.

ÇIKTI: beş rapor (xlsx + markdown)

---

## FAZ 4 — Ürünleştirme   (KARAR VERİLDİ: backend'e alınacak)

AUZEF-Chat-Analiz son kullanıcı seviyesine getirilecek, AYRI ÜRÜN olarak
kalacak (chatbot paneline taşınmayacak). Hedef kullanıcı teknik olmayan
biri: model, prompt, sıcaklık, kolon eşlemesi ekranda görünmeyecek.

Rutin akış tek LLM cagri tipi kullanir (bkz. BORU-HATTI.md):
  hazirlik (deterministik) -> siniflandirma -> raporlama (deterministik)

Ayrintili karar kaydi ve yarin cevaplanacak dort soru: KARARLAR.md

---

## Ayrı duran iş: commit bekleyen düzeltmeler

Çalışma ağacında 22 dosya / ~1100 satır var. İçinde main'de duran bir çökme
hatasının düzeltmesi de var (WarningCode.LLM_REDUCE_PARTIAL_COVERAGE,
34af95d'den beri; reduce yakınsamazsa analiz tüm maliyeti harcayıp
INTERNAL_ERROR veriyor). Bu analiz işinden bağımsız, ayrıca commit'lenmeli.

---

## Toplam öngörü
FAZ 0: 0 USD, ~15 dk makine zamanı
FAZ 1: ~0,5 USD  + sizin gözden geçirmeniz
FAZ 2: ~3,5-5 USD (ölçüldü: 2.000 session ≈ 0,25 USD)
FAZ 3: 0 USD
Bakiye 16,63 USD — yeterli.
