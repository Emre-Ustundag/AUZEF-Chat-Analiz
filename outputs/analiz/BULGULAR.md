# AUZEF chatbot analizi — bulgular (2026-09-03)

## VERİ SETİ HAKKINDA — ÖNCELİKLE BU

**Elimizdeki veri bir yıl değil, BİR AY.**
Aralık: 2026-05-31 → 2026-06-30 (31 gün, 505.442 satır, %99,9'u Haziran).
Firmadan bir senelik veri alındığı söylenmişti; gelen Haziran 2026.

Sonucu: Haziran bir sınav ve mezuniyet dönemi. Yalnız bu aya dayanan bir SSS
sayfası sınav/mezuniyet sorularını abartır, kayıt dönemi (Eylül-Ekim), ders
ekle-bırak, dönem başı sorularını HİÇ görmez. Firmadan kalan aylar istenmeli.

**Önceki tüm kategori ölçümleri yalnızca ÜÇ GÜNÜ kapsıyordu.**
"İlk 20.000 satır" = 31 Mayıs + 1-2 Haziran. "Sınav giriş belgesi her ölçümde
1. sıra" tespiti ayın tamamı için değil, o üç gün için geçerlidir.

## Session sonuç dağılımı — DÜZELTİLDİ (2026-09-03)

!! ÖNCEKİ SÜRÜMDE YANLIŞ RAKAM VARDI: "başarı oranı %13,2" ve "yazılı
session'ların %53,5'i başarısız" yazıyordu. İkisi de HATALIYDI. Sebep:
session etiketlemesi öncelik sıralıydı; bir session'da tek bir olumsuz
sinyal (bir "Reddetti" veya bir "anlayamadım") tüm olumluları eziyordu.
Oysa bot bir session içinde memnuniyet sorusunu defalarca soruyor.

DOĞRU TABLO (kaynak: EtiyaChatbot.csv, quick_reply_label kolonu):

  CEVAP DÜZEYİNDE — asıl ölçüt
    Onayladı 31.068  |  Reddetti 9.933  |  ONAY ORANI %75,8

  SESSION DÜZEYİNDE (23.708 yazılı session)
    14.704 (%62,0) tüm cevapları onaylanmış
     7.112 (%30,0) karışık
     1.886 (%8,0)  memnuniyet sorusu yok/cevapsız
         6 (%0,0)  tamamı reddedilmiş
     7.267 (%30,7) en az bir kez "anlayamadım"

Yani eski bot cevaplarının dörtte üçü kabul edilmiş. "Bot yarısında
düşüyor" iddiası geçersiz. Gerçek boşluk sinyali: 9.933 reddedilen cevap
ve 7.267 fallback'li session.

Not: Onayladı/Reddetti satırları message_type='quick_reply'; session
metinlerine sızmamışlar (kontrol edildi), taksonomi çalışması etkilenmedi.

## Veri kaynağı: EtiyaChatbot.csv kullanılmalı

İki dosya AYNI dönemi ve aynı 505.442 satırı içeriyor. Fark kolonlarda:
  - EtiyaChatbot.csv (ham): quick_reply_label + message_feedback var
  - analize-hazır-chatbot.csv: quick_reply_label yok, değerleri
    message_text_clean'e taşınmış; duzeltme_notu eklenmiş
message_feedback TAMAMEN BOŞ (505.442 satır), işe yaramıyor.
quick_reply_label ise hem memnuniyet sinyalini hem QnA tıklamalarını taşıyor.

## Eski botun QnA kataloğu — taksonomi çekirdeği

quick_reply_label içindeki "QnA seçimi: ..." kayıtları eski botun kendi
soru kataloğunu veriyor: 241 farklı soru, 10.355 tıklama, gerçek talep
sayılarıyla. outputs/analiz/etiya-qna-katalog.json
LLM'e ürettirdiğim 39 maddelik taksonomi bununla bağımsız olarak örtüşüyor.

## Analiz birimi ölçümü (aynı prompt/model/sıcaklık, tek değişken birim)

|                  | mesaj birimi | session birimi |
|------------------|--------------|----------------|
| kayıt            | 2.077        | 843            |
| MAP kategorisi   | 1.344        | 483/487/487    |
| son kategori     | 84           | 48/46/42       |
| top-20 kapsama   | %75,3        | %87,7/89,1/94,0|

Kategori patlaması ağırlıklı olarak PROMPT değil BİRİM hatasıydı.
Üç koşuda map 483/487/487 — gürültü bandı çok dar, yön kesin.

Ayrıca: kapsama ile başlık netliği aynı kadranın iki ucu. Az kategori =>
yüksek kapsama => bulanık başlık. Model her koşuda kadranın rastgele bir
noktasına düşüyor; bunu sabitleyen tek şey SABİT TAKSONOMİ.

## Sabit taksonomi + sınıflandırma (2.000 session örneklem, 39 madde)

atanan 1.988 | uydurma kimlik 10 | atlanan 12 | «hiçbiri» %13,2
Hiç eşleşmeyen madde: 0

AÇIK SORU: q1 "Mezuniyet durumumu nasıl öğrenebilirim?" %17,3 (344 kayıt) ile
tek başına devleşti. Haziran mezuniyet dönemi olduğu için makul olabilir, ama
çöp kovası gibi davranıp davranmadığı DENETLENMEDİ (atamalar kaydedilmemişti,
sonra /tmp temizlendi). Bir sonraki koşuda atamalar kaydedilecek ve en büyük
kovaların içine bakılacak.

## Kod tarafında bugün yapılanlar (hepsi test edildi, commit EDİLMEDİ)
- WarningCode.LLM_REDUCE_PARTIAL_COVERAGE — main'de duran çökme hatası (34af95d)
- llm_temperature ayarı, varsayılan 0
- catalog: Luna cache fiyat düzeltmesi + temperature beyaz listesi
- openrouter: sağlayıcı hata metni sızıntısının geri alınması
- v6.py, v7.py (deney, v4'ten iyi olduğu KANITLANMADI)
Kapılar: backend 604 passed, frontend 434 passed, ruff/mypy/typecheck temiz.

## Luna notu
gpt-5.6-luna json_schema ile ÇALIŞIYOR (Azure). Engel `provider:
{require_parameters: true}` bayrağı — onunla uygun endpoint kalmıyor.
Luna'ya geçiş bu bayrağı gevşetme kararı gerektirir. Luna `temperature`
kabul ediyor ama supported_parameters'da listelemiyor; `seed` destekliyor.
