# AUZEF KB konsolidasyon v3.1-final — dry-run

Sonuç: **PASS**

Bu koşu salt okunurdur. Veritabanı, Qdrant, Meilisearch ve Gold üzerinde yazma yapılmadı.

## Geçen kontroller

- Aktif QnA sayısı 311
- 27 mevcut QnA soru/cevap önkoşulu birebir eşleşti
- 47 normal alias taşıma önkoşulu eşleşti
- NEW-11 exact alias atomik promotion önkoşulu eşleşti
- 15 yeni kanonik soru için birebir canonical/alias çakışması yok
- 27 mevcut QnA için CSV sıra/soru/cevap önkoşulu eşleşti
- 11 routing guard Excel/plan arasında eşleşti
- QnA 319 / vaka 214 blokajı korunuyor
- Runtime routing guard kabiliyeti mevcut

## Blokajlar


## Operasyon sınıflandırması

- Guard olmadan uygulanabilir QnA işlemi: 31
- Guard eksikliği nedeniyle bekleyen QnA işlemi: 10
- Açıkça bloklu QnA işlemi: 1
- Guard olmadan uygulanabilir alias taşıması: 37
- Guard eksikliği nedeniyle bekleyen alias taşıması: 10

## Final Excel'den plana yansıyan operasyonlar

- EX-266
- EX-322
- NEW-12
- NEW-11

Makine planı: `kb-mutation-plan-v3.1-final.json`
