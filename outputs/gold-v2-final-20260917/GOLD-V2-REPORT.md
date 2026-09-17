# Gold v2 final — FREEZE PASS

Toplam 516 vaka · CONTEXT_REQUIRED: 25 · PENDING_CONTENT: 1 · READY: 490

- Beklenen dağılımla uyum (490/25/1): **True**
- Blokaj: **0** · uyarı: 0
- Post-migration baseline: `outputs/kb-migration-v3.1-local-apply-20260917/baseline/migration-report.json`
- Migration sonrası taşınan alias vakaları doğrulandı: 47
- Multi-intent (READY): 0 · split etiketi temizlenen: 9
- Temporal/guard'lı READY vaka: 32
- İnsan kararıyla alias sahibinden farklı hedef (bilgi): 26

## Blokajlar

- Yok

## Açık noktalar

- Vaka 480: iki ayrı konu (İstanbulkart / YÖK kaydı) için insan kararı her iki gruba da '125, 344' girmiş; tek niyet + iki kabul edilen QnA olarak işlendi, teyit önerilir.

## Temporal / guard'lı READY vakalar

| Vaka | QnA | Ref | Guard | Tür | as_of |
|---|---|---|---|---|---|
| 62 | [397] | ['NEW-02'] | GUARD-NEW-02 | dynamic_current_status | 2026-09-16 |
| 86 | [405] | ['NEW-10'] | GUARD-NEW-10 | dynamic_current_status | 2026-09-16 |
| 195 | [410] | ['NEW-11'] | GUARD-NEW-11 | dynamic_current_status | 2026-09-16 |
| 200 | [319, 333] | ['EX-319', 'QNA-333'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 201 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 202 | [400] | ['NEW-05'] | GUARD-NEW-05 | policy | 2026-09-16 |
| 203 | [400] | ['NEW-05'] | GUARD-NEW-05 | policy | 2026-09-16 |
| 204 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 205 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 206 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 207 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 208 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 210 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 211 | [319, 333] | ['EX-319', 'QNA-333'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 215 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 217 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 312 | [402] | ['NEW-07'] | GUARD-NEW-07 | dynamic_current_status | 2026-09-16 |
| 333 | [406] | ['NEW-12'] | GUARD-NEW-12 | dynamic_current_status | 2026-09-16 |
| 340 | [403] | ['NEW-08'] | GUARD-NEW-08 | policy | 2026-09-16 |
| 379 | [319, 333] | ['EX-319', 'QNA-333'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 381 | [319] | ['EX-319'] | GUARD-EX-319 | dated_content_with_expiry | 2026-09-16 |
| 408 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 409 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 410 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 411 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 412 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 413 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 414 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 418 | [336] | ['EX-336'] | GUARD-EX-336 | policy | 2026-09-16 |
| 459 | [407] | ['NEW-13'] | GUARD-NEW-13 | historical | 2026-09-16 |
| 462 | [408] | ['NEW-14'] | GUARD-NEW-14 | dynamic_current_status | 2026-09-16 |
| 463 | [408] | ['NEW-14'] | GUARD-NEW-14 | dynamic_current_status | 2026-09-16 |
