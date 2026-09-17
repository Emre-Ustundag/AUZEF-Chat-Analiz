# v3.1 migration — izole indeks provası

Sonuç: **ISOLATED INDEX MIGRATION REHEARSAL: PASS** (2026-09-17 17:21–17:44 UTC, 18/18 adım)

- Disposable DB: `auzef_migration_v31_test_20260917172157` (canlı `auzef_bot` pg_dump kopyası)
- Meili index / Qdrant collection: `qna_migration_v31_test_20260917172157` (gerçek index ayarları kopyalandı)
- Plan digest: `0a900d1a8f9e5d93`; çalıştıran: `scripts/rehearse_kb_migration_v31_indexes.py`

| Aşama | DB (aktif QnA / alias / guard) | Meili doküman | Qdrant nokta |
|---|---|---|---|
| Seed (pre-migration) | 311 / 2696 / 0 | 311 | 3006 |
| Apply sonrası | 326 / 2695 / 11 | 326 | 3020 |
| Rollback sonrası | 311 / 2696 / 0 | 311 | 3006 |

Kontroller: DB 136/136; DB↔Meili↔Qdrant global eşitlik (eksik/fazla/stale/orphan/duplicate nokta yok);
etkilenen 57 QnA'nın 536 vektörü metinden yeniden encode ile min cosine 0.99999988; 47/47 alias eski QnA'dan
çıkıp hedefte (Meili + Qdrant); NEW-11 yeni kanonik (id 395, Meili/Qdrant top-1); 15/15 yeni QnA aranabilir;
guard smoke 21 guard'lı sorgu (sızıntı yok, seçici havuzda, guard'sız karşı-olguda 21/21 sızardı);
EX-319 içerik ve vaka 205/206/214 alias'ları üç katmanda değişmedi; rollback sonrası indeks seed ile birebir
(min cosine 0.99999982); gerçek DB/Meili/Qdrant parmak izleri öncesi = sonrası.

`guard-failure/`: kopya DB'de GUARD-NEW-02 yazımı trigger ile düşürüldü → create:NEW-02 birimi geri alındı
(QnA yok, guard yok, vaka 62 alias'ı kaynakta), apply PARTIAL_FAILED'de durdu, rollback digest'i geri getirdi.

Bulunan ve düzeltilen: index-sync, guard'lı QnA'larda detached ORM nesnesi okuyup çöküyordu (gerçek apply'da
DB commit edilip indeksler eski kalırdı).
