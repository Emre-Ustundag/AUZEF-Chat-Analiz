"""Maliyet tavanı kontrolü — plan §4, ADR §9 ve §10 risk 3.

ADR §9: "Token ve tahmini maliyet üst sınırı aşılırsa LLM çağrısı başlamadan
iş güvenli biçimde durur."

FAZ 2'DE NE OLUYOR — bunu açıkça yazmak gerekiyor çünkü kolayca yanlış
anlaşılır:

* Tahmin GERÇEKTEN hesaplanıyor. Benzersiz kayıtların karakter sayısından
  token tahmini çıkarılıyor ve whitelist'teki model fiyatıyla çarpılıyor.
  Bu kod Faz 3'te olduğu gibi kullanılacak.
* Ama Faz 2'de OpenRouter'a HİÇBİR ÇAĞRI YAPILMIYOR, dolayısıyla harcanan
  gerçek para SIFIR. Tavanı aşan bir dosyada işi DURDURMAK yanlış olurdu:
  ortada durdurulacak bir çağrı yok ve kullanıcı hiç oluşmayan bir maliyet
  yüzünden analizden mahrum kalırdı. Bu yüzden Faz 2 aşımı rapora bir
  UYARI olarak yazar.
* Faz 3'te `CostDecision.exceeds` değeri, LLM çağrıları başlamadan işi
  `failed` ile durduracak. Değişecek tek şey karar noktasıdır; tahmin
  matematiği ve arayüz aynı kalır.

Raporlanan `token_usage` ve `estimated_cost_usd` Faz 2'de 0'dır (plan §4):
tahmin, harcanan değildir ve ikisini karıştırmak raporu yalancı yapardı.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.model_catalog import MODEL_WHITELIST
from app.pipeline.preprocess import RecordGroup

#: Karakter başına kabaca token. Türkçe metinde tokenizer'lar İngilizce'ye
#: göre daha çok token üretiyor; 3 karakter/token temkinli bir tahmin.
CHARS_PER_TOKEN = 3.0

#: Her kayıt için prompt'ta yer kaplayan sabit yük (kimlik, ayraç, numara).
OVERHEAD_TOKENS_PER_RECORD = 12

#: Kayıt başına modelden beklenen çıktı: yalnızca "kayıt kimliği → kategori"
#: eşlemesi (ADR §9). Serbest metin dönmediği için çok küçük.
OUTPUT_TOKENS_PER_RECORD = 8


@dataclass(frozen=True)
class CostDecision:
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_cost_usd: float
    max_cost_usd: float

    @property
    def exceeds(self) -> bool:
        return self.estimated_cost_usd > self.max_cost_usd


def estimate_cost(
    groups: list[RecordGroup],
    model_id: str,
    max_cost_usd: float,
) -> CostDecision:
    """Benzersiz kayıtlardan tahmini maliyeti çıkarır.

    Girdi TEKİLLEŞTİRİLMİŞ kayıtlardır: dedupe'un maliyet üzerindeki etkisi
    (ADR §10 risk 3) burada doğrudan görünür — 100.000 satırlık bir dosyada
    30.000 benzersiz kayıt varsa tahmin de o oranda düşer.
    """
    option = next((m for m in MODEL_WHITELIST if m.id == model_id), None)

    characters = sum(len(group.normalized) for group in groups)
    prompt_tokens = int(characters / CHARS_PER_TOKEN) + OVERHEAD_TOKENS_PER_RECORD * len(groups)
    completion_tokens = OUTPUT_TOKENS_PER_RECORD * len(groups)

    if option is None:
        # Whitelist dışı model API katmanında zaten reddediliyor; buraya
        # düşmek bir programlama hatasıdır, maliyeti 0 saymak yerine
        # tahmini sıfırlamıyoruz ama fiyat bilinmediği için 0 döndürüyoruz.
        cost = 0.0
    else:
        cost = (
            prompt_tokens / 1_000_000 * option.input_cost_per_million
            + completion_tokens / 1_000_000 * option.output_cost_per_million
        )

    return CostDecision(
        estimated_prompt_tokens=prompt_tokens,
        estimated_completion_tokens=completion_tokens,
        estimated_cost_usd=round(cost, 6),
        max_cost_usd=max_cost_usd,
    )
