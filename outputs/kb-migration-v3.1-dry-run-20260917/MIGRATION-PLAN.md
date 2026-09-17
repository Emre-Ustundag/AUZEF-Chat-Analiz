# AUZEF KB v3.1-final — migration planı

Durum: **hazır, uygulanmadı.** Canlı DB'ye apply yapılmadı.

- Plan: `outputs/kb-consolidation-v3.1-final-dry-run-guard-20260917/kb-mutation-plan-v3.1-final.json`
- Plan digest (apply `--confirm`): `0a900d1a8f9e5d93`
- Kod: `scripts/kb_migration_v31.py` (CLI), `scripts/kb_migration_v31_runner.py` (backend içinde çalışır)
- Test: `tests/test_kb_migration_v31.py`

## Birimler (her biri ayrı transaction, sırayla)

| Sıra | Tür | Adet | İçerik |
|---|---|---|---|
| 1 | guard_only | 1 | GUARD-EX-319 yazılır; QnA 319 içeriği **uygulanmaz** (vaka 214) |
| 2 | update | 26 | [guard] → soru/cevap güncelle → bu QnA'ya gelen alias'lar. EX-336 guard'lı |
| 3 | alias_move | 1 | QnA 215'e 3 alias (hedef içeriği değişmiyor) |
| 4 | create | 14 | QnA oluştur → gerçek id → [guard] → alias bağla → commit. 8'i guard'lı |
| 5 | promotion | 1 | NEW-11: QnA 318'deki alias silinir + yeni kanonik QnA + guard, tek transaction |

Toplam: 43 birim, 47 alias taşıması, 11 guard. Beklenen son durum: 326 aktif QnA, 2695 alias.

Guard sözleşmesi olduğu gibi kalır: guard'lı QnA exact/fallback yolundan dönmez,
`semantic_selector_only` yalnız LLM seçiciye girer, tarih penceresi dışı hard-block.
`content_mode` / `on_expiry` metadata olarak yazılır. `valid_until` yalnız GUARD-EX-319'da (2026-12-09).

Her birim içinde: hedef satır `FOR UPDATE` ile kilitlenir ve beklenen mevcut içerik yeniden
karşılaştırılır; alias güncellemesi kaynak id + metin koşuluyla yapılır ve tam 1 satır etkilemezse
birim geri alınır. Guard yazıldıktan sonra `RoutingGuardPolicy` kararı (selector açık, fallback kapalı)
aynı transaction'da okunur; tutmazsa QnA da commit edilmez.

## Çalıştırma sırası (onaydan sonra)

```bash
C="$HOME/Masaüstü/AUZEF Chatbot"; P=outputs/kb-consolidation-v3.1-final-dry-run-guard-20260917/kb-mutation-plan-v3.1-final.json
# 0) Admin panelinden QnA düzenlemesini durdur. Dry-run da canlı satırları FOR UPDATE ile kilitler;
#    production'da dry-run dahil tüm adımlar bu dondurma penceresinde çalışır. Apply ile olası
#    rollback arasındaki dış yazmalar otomatik rollback'i durdurur.
python3 scripts/kb_migration_v31.py dry-run --plan $P --compose-root "$C" --out-dir outputs/kb-migration-v3.1-apply-<tarih>/dry-run
python3 scripts/kb_migration_v31.py backup  --compose-root "$C" --out-dir outputs/kb-migration-v3.1-apply-<tarih>/backup
python3 scripts/kb_migration_v31.py apply   --plan $P --compose-root "$C" --backup-dir .../backup --out-dir .../apply --confirm 0a900d1a8f9e5d93
python3 scripts/kb_migration_v31.py verify  --plan $P --compose-root "$C" --backup-dir .../backup --out-dir .../verify
```

`apply`: backup snapshot digest'i canlı DB ile eşleşmezse başlamaz; birim başına commit eder ve
`journal.ndjson`'a yazar; ilk hatada durur (kalan birimler uygulanmaz). DB bütünlüğü geçerse
57 QnA için indeks senkronu çalışır.

## İndeks senkronu

Qdrant `upsert_points` alias sayısı azaldığında eski alias noktalarını silmiyor; taşınan alias eski
QnA'ya yönlendirmeye devam ederdi. Bu yüzden etkilenen her QnA için önce `delete_point` (kanonik +
63 alias noktası), sonra toplu upsert yapılır; Meili `add_documents` görevi beklenir. Ardından her id
için: Qdrant nokta kümesi = kanonik + güncel alias'lar, payload cevabı = DB, Meili dokümanı = DB,
guard'lı id'ler Meili/Qdrant aramasında fallback'e sızmıyor.

## Rollback

`rollback` journal'a değil backup snapshot farkına dayanır (commit edilip journal'a yazılamamış birim de
geri alınır): yeni QnA'lar silinir, 26 içerik geri yüklenir, 47 alias eski sahibine, NEW-11 alias'ı
orijinal id'siyle geri eklenir, 11 guard silinir; ardından digest snapshot ile birebir eşleşmezse
transaction geri alınır. Migration dışı bir değişiklik (admin düzenlemesi, yeni alias vb.) varsa
otomatik rollback yapılmaz; `backup/admin-qna-tables.dump` (pg_dump) ile kontrollü restore gerekir.
DB geri alındıktan sonra aynı id'ler için indeks senkronu çalışır.

## Doğrulama sonuçları (2026-09-17)

- **Canlı DB dry-run: PASS.** 43/43 birim SAVEPOINT içinde çalıştı, 136 bütünlük kontrolü geçti,
  rollback rehearsal digest'i geri getirdi, dış transaction ROLLBACK; canlı digest değişmedi.
  Yan etki: `qna` id sequence'i dry-run'da ilerler (id boşluğu), veri yazılmaz.
- **Bozuk plan dry-run: FAIL (beklenen).** `QNA_CURRENT_MISMATCH`, `ALIAS_SOURCE_ROW_NOT_UNIQUE`; birim çalışmadı.
- **Kopya DB rehearsal (`auzef_migration_rehearsal`, indeks kapalı):** backup → apply 43 COMMITTED,
  APPLIED/PASS → verify PASS (326 aktif, 2695 alias, 11 guard, QnA 319 değişmedi) → aynı backup'la
  ikinci apply ABORTED → rollback ROLLED_BACK, digest birebir → ikinci rollback no-op. Kopya DB silindi.
  İlk rehearsal denemesi journal yazımındaki bir hatayı buldu (birim commit edildi, journal yazılamadı);
  snapshot tabanlı rollback onu da geri aldı; hata düzeltildi. Kanıtlar: `clone-rehearsal/`.
- **Yarıda kalan apply + rollback (kopya DB, trigger ile hata enjeksiyonu):** 27 birim commit, 28. birim
  (`alias_move:215`, 3. alias) hata → PARTIAL_FAILED, o birimin ilk 2 alias'ı da geri alındı; rollback
  26 içerik + 27 alias + 2 guard geri aldı, digest backup ile birebir. Kopya DB silindi.
- **Test edilmeyen:** `index-sync` modu gerçek Meili/Qdrant'a karşı çalıştırılmadı (paylaşılan indekse yazar).
