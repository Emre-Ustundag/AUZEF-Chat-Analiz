# Eşli model hata denetimi v1

Evren (birebir metrik): yalnız 4o-mini doğru 53 · yalnız Luna doğru 56 · ikisi de yanlış 75 = **184**

> Kullanıcı özetindeki 64 'ikisi de yanlış' gevşek metriğe aittir; birebir metrikte 75. Evren tutarlılık için birebir metrikle kuruldu; gevşek sonuç her vakada ayrı alan.

## Karar tablosu

| Problem | 4o-mini | Luna | Ortak mimari mi? | Potansiyel müdahale |
|---|---:|---:|---|---|
| Gereksiz bölme (fazladan cevap / bağlam kaybı) | 48 | 9 | Bölücü davranışı modele bağlı; birleştirme mimarisi ortak | Bölme kararı/eşiği; spekülatif sonucu tercih |
| Seçici yakın-kopya karışıklığı | 16 | 27 | Ortak (KB'de yakın QnA'lar) | KB ayrıştırma / aday açıklaması |
| Seçici anlamsal yanlış eşleşme | 35 | 76 | Modele bağlı | Seçici prompt / model |
| KB örtüşmesi (mesaj seçilen QnA'nın alias'ı) | 13 | 3 | Ortak (KB/gold) | Gold/KB inceleme |
| None kalibrasyonu (gold varken 'hiçbiri') | 5 | 2 | Modele bağlı | Seçici eşik/prompt |
| Takvim dikkat dağıtıcı | 9 | 5 | Ortak mimari (takvim havuzun başında) | Takvim yönlendirmesi |
| Retrieval kaçırma | 2 | 2 | Ortak mimari | Retrieval |
| Çoklu niyet kaçırma | 0 | 1 | Modele bağlı | Bölücü |
| Pozisyon yanlılığı sinyali (yanlış seçim gold'un üstünde, gold sırası ≥4) | 1 | 1 | Aday sırası ortak | Aday sıralama/karıştırma |
| Bağlam (puanlanamadı) | — | — | Gold eksik (17 hedef) | Bağlam hedeflerine gold |

## Tam mesaj seçici (aynı girdi, bölücüden bağımsız)

- 4o-mini: 368/486
- Luna-high: 349/486
- Eşli: `{"cases": 488, "both_correct": 315, "only_4o_mini": 55, "only_luna_high": 36, "both_wrong": 82, "mcnemar": {"n_discordant": 91, "p_value": 0.058574, "method": "exact binomial McNemar"}}`

## Gereksiz bölme

`{"4o-mini": {"HARMLESS_FALSE_SPLIT": 46, "EXTRA_ANSWER_FALSE_SPLIT": 30, "CONTEXT_LOSS_FALSE_SPLIT": 7, "WRONG_ANSWER_FALSE_SPLIT": 11, "total": 94, "exact_broken": 48, "mean_answer_length_ratio": 1.6, "pieces_distribution": {"2": 72, "3": 18, "4": 3, "5": 1}}, "luna-high": {"HARMLESS_FALSE_SPLIT": 7, "EXTRA_ANSWER_FALSE_SPLIT": 8, "CONTEXT_LOSS_FALSE_SPLIT": 0, "WRONG_ANSWER_FALSE_SPLIT": 1, "total": 16, "exact_broken": 9, "mean_answer_length_ratio": 1.46, "pieces_distribution": {"2": 16}}}`

## Saf seçici model farkı

`{"cases": 69, "split": {"4o_mini_correct": 44, "luna_correct": 25}, "case_ids": [2, 9, 23, 42, 50, 52, 55, 84, 93, 94, 99, 103, 104, 108, 110, 118, 153, 158, 166, 172, 179, 182, 184, 189, 192, 194, 196, 201, 206, 207, 208, 216, 217, 225, 231, 250, 298, 320, 321, 330, 337, 338, 344, 366, 367, 377, 404, 408, 415, 418, 420, 421, 422, 425, 440, 448, 449, 451, 452, 454, 455, 470, 473, 474, 487, 503, 506, 510, 516]}`

## İnsan incelemesi

- 17 vaka
