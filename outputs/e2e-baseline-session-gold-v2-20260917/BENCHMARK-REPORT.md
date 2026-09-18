# Session-gold v2 E2E baseline — TAMAM

Chatbot `e2985358` · analiz `9f490d82`

| Metrik | 4o-mini | Luna-high |
|---|---|---|
| E2E doğru (puanlanan) | 392/490 (80.0%) | 373/490 (76.1%) |
| E2E birebir | 359/490 (73.3%) | 362/490 (73.9%) |
| FIRST_TURN | 276/336 (82.1%) | 253/336 (75.3%) |
| FOLLOW_UP_CONTEXT_AVAILABLE_BUT_NOT_REQUIRED | 116/154 (75.3%) | 120/154 (77.9%) |
| FOLLOW_UP_CONTEXT_REQUIRED | — | — |
| Retrieval ilk havuzda | 486/488 (99.6%) | 486/488 (99.6%) |
| Recall@1 / @5 (havuz sırası) | 462/488 (94.7%) / 481/488 (98.6%) | 462/488 (94.7%) / 481/488 (98.6%) |
| Seçici koşullu doğruluk | 361/453 (79.7%) | 352/464 (75.9%) |
| Gereksiz bölme (tek niyet) | 94 | 16 |
| Takvimden final cevap | 14 | 5 |
| Fallback kullanımı | 36 | 24 |
| Çözülemeyen API hatası | 0 | 0 |
| Gecikme p50 / p95 (s) | 2.0468 / 7.1229 | 2.6304 / 8.5518 |
| Maliyet (OpenRouter, $) | 0.317958 | 0.470449 |

## Eşli karşılaştırma

`{"both_correct": 339, "both_wrong": 64, "only_4o_mini": 53, "only_luna_high": 34}`
McNemar: `{"n_discordant": 87, "p_value": 0.053003, "method": "exact binomial McNemar"}`
Bootstrap (4o-mini − Luna): `{"mean_diff": 0.0388, "ci95": [0.002, 0.0755], "iterations": 5000, "seed": 20260917, "method": "paired bootstrap"}`
Birebir metrik: `{"counts": {"both_correct": 306, "both_wrong": 75, "only_4o_mini": 53, "only_luna_high": 56}, "mcnemar": {"n_discordant": 109, "p_value": 0.848195, "method": "exact binomial McNemar"}, "bootstrap_4o_minus_luna": {"mean_diff": -0.0061, "ci95": [-0.0469, 0.0347], "iterations": 5000, "seed": 20260917, "method": "paired bootstrap"}}`

Not: 'doğru' metriği, gereksiz bölmeyle birden çok cevap birleştiren koşuyu kayırır; 'birebir' metriği fazladan cevabı hata sayar.

Not: Benchmark mesajları KB alias'larından türetildiği için exact-alias ölçümleri iyimser yanlıdır.

## Hata sınıfları

- 4o-mini: `{"CALENDAR_ROUTING_INTERFERENCE": 10, "FALLBACK_ERROR": 4, "RETRIEVAL_MISS": 2, "SELECTOR_WRONG_CHOICE": 82}`
- Luna-high: `{"CALENDAR_ROUTING_INTERFERENCE": 5, "FALLBACK_ERROR": 2, "RETRIEVAL_MISS": 2, "SELECTOR_WRONG_CHOICE": 107, "SPLITTER_MISSED_SPLIT": 1}`

## Beyan edilen sapmalar

- Model kimliği üretimde sabit olduğu için süreç içinde aynı sınıfla (BenchProvider) değiştirildi.
- Luna-high için min max_tokens 1280 ve reasoning={effort: high, exclude: true}; 4o-mini üretim değerlerinde.
- Her iki modelde OpenRouter usage accounting (extra_body.usage.include) ve 120 s istek zaman aşımı eklendi.
- Hedef düzeyinde API hatası retry'ı benchmark politikasıdır (üretim sessizce fallback'e düşer).
- Bağlam yalnız FOLLOW_UP_CONTEXT_REQUIRED hedeflerine router penceresiyle verildi; üretimde bağlam varsayılan kapalı.
- 17 bağlam hedefinin dondurulmuş gold'da beklenen QnA'sı yok; bu hedefler koşturuldu ama puanlanmadı.
