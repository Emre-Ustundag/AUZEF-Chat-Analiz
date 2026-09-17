# AUZEF KB v3.1-final migration — dry-run

Sonuç: **PASS** · canlı DB değişmedi: **True**

Plan digest: `0a900d1a8f9e5d938fdcdf2114e45bb2746dd4e69c00073c635d0316159a75ac`
Snapshot digest: `11bab1e7e40e9a299f0be55e896e6f94dc91db17897d8289437c702fc2532c23`

## Önkoşullar

- Hata yok

## Birimler (her biri ayrı transaction)

| Tür | Adet |
|---|---|
| guard_only | 1 |
| update | 26 |
| alias_move | 1 |
| create | 14 |
| promotion | 1 |

Toplam alias taşıması: 47
Guard yazılan birim: 11

| Birim | QnA id | Guard | Alias | Durum |
|---|---|---|---|---|
| guard_only:EX-319 | 319 | GUARD-EX-319 | 0 | OK |
| update:EX-14 | 14 |  | 1 | OK |
| update:EX-21 | 21 |  | 1 | OK |
| update:EX-39 | 39 |  | 1 | OK |
| update:EX-40 | 40 |  | 1 | OK |
| update:EX-88 | 88 |  | 1 | OK |
| update:EX-91 | 91 |  | 3 | OK |
| update:EX-94 | 94 |  | 3 | OK |
| update:EX-103 | 103 |  | 1 | OK |
| update:EX-150 | 150 |  | 1 | OK |
| update:EX-151 | 151 |  | 1 | OK |
| update:EX-166 | 166 |  | 1 | OK |
| update:EX-171 | 171 |  | 2 | OK |
| update:EX-186 | 186 |  | 1 | OK |
| update:EX-256 | 256 |  | 1 | OK |
| update:EX-261 | 261 |  | 1 | OK |
| update:EX-266 | 266 |  | 2 | OK |
| update:EX-306 | 306 |  | 0 | OK |
| update:EX-307 | 307 |  | 0 | OK |
| update:EX-312 | 312 |  | 0 | OK |
| update:EX-314 | 314 |  | 2 | OK |
| update:EX-320 | 320 |  | 1 | OK |
| update:EX-322 | 322 |  | 2 | OK |
| update:EX-325 | 325 |  | 0 | OK |
| update:EX-336 | 336 | GUARD-EX-336 | 0 | OK |
| update:EX-337 | 337 |  | 0 | OK |
| update:EX-346 | 346 |  | 0 | OK |
| alias_move:215 | 215 |  | 3 | OK |
| create:NEW-01 | 366 (yeni) |  | 1 | OK |
| create:NEW-02 | 367 (yeni) | GUARD-NEW-02 | 1 | OK |
| create:NEW-03 | 368 (yeni) |  | 1 | OK |
| create:NEW-04 | 369 (yeni) |  | 1 | OK |
| create:NEW-05 | 370 (yeni) | GUARD-NEW-05 | 2 | OK |
| create:NEW-06 | 371 (yeni) |  | 1 | OK |
| create:NEW-07 | 372 (yeni) | GUARD-NEW-07 | 1 | OK |
| create:NEW-08 | 373 (yeni) | GUARD-NEW-08 | 1 | OK |
| create:NEW-09 | 374 (yeni) |  | 1 | OK |
| create:NEW-10 | 375 (yeni) | GUARD-NEW-10 | 1 | OK |
| create:NEW-12 | 376 (yeni) | GUARD-NEW-12 | 1 | OK |
| create:NEW-13 | 377 (yeni) | GUARD-NEW-13 | 1 | OK |
| create:NEW-14 | 378 (yeni) | GUARD-NEW-14 | 2 | OK |
| create:NEW-15 | 379 (yeni) |  | 2 | OK |
| promotion:NEW-11 | 380 (yeni) | GUARD-NEW-11 | 0 | OK |

Not: Yeni kayıt id'leri rollback edilen transaction'dan gelir; gerçek apply'da farklı olacaktır.

## Bütünlük kontrolleri

- Durum: **PASS** (136 kontrol geçti)

## Rollback rehearsal

- Durum: **PASS**
- Geri yükleme sonrası digest: `11bab1e7e40e9a299f0be55e896e6f94dc91db17897d8289437c702fc2532c23`
- İşlem sayıları: `{"foreign_changes": 0, "delete_qna": 15, "restore_qna": 26, "restore_alias_owner": 47, "reinsert_alias": 1, "guard_restore": 0, "guard_delete": 11, "reindex_ids": 57}`

## İndeks planı

- Yeniden indekslenecek QnA: 57
- Strateji: Qdrant delete_point + batch upsert; Meili add_documents + wait_for_task; sonra doğrulama
