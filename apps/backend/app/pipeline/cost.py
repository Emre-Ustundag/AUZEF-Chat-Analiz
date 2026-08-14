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

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.domain.model_catalog import MODEL_WHITELIST
from app.pipeline.preprocess import RecordGroup
from app.prompts.faq_analysis import PromptBundle

#: Karakter başına kabaca token. Türkçe metinde tokenizer'lar İngilizce'ye
#: göre daha çok token üretiyor; 3 karakter/token temkinli bir tahmin.
CHARS_PER_TOKEN = 3.0

#: Her kayıt için prompt'ta yer kaplayan sabit yük (kimlik, ayraç, numara).
OVERHEAD_TOKENS_PER_RECORD = 12

#: Kayıt başına modelden beklenen çıktı: yalnızca "kayıt kimliği → kategori"
#: eşlemesi (ADR §9). Serbest metin dönmediği için çok küçük.
OUTPUT_TOKENS_PER_RECORD = 8


def cost_for_tokens(prompt_tokens: int, completion_tokens: int, model_id: str) -> float:
    """Token sayılarını whitelist fiyatıyla USD tutara çevirir.

    FİYAT ARİTMETİĞİNİN TEK YERİ. Faz 3'te aynı çarpma iki yerde yapılıyordu
    (`estimate_cost` burada, `workers/tasks.py::_actual_cost` orada) ve iki
    kopyanın ayrışması sessiz bir hataydı: birinde milyon böleni ya da
    yuvarlama değişse tavan kontrolü ile raporlanan tutar birbirini tutmaz,
    kullanıcı "sınırın altındaydı ama fazla ödedim" derdi.

    Whitelist dışı model için 0.0 döner. Buraya düşmek bir programlama
    hatasıdır — API katmanı ve worker modeli zaten iki kez doğruluyor — ama
    fiyat bilinmediğinde uydurma bir sayı üretmek daha kötü olurdu.
    """
    option = next((m for m in MODEL_WHITELIST if m.id == model_id), None)
    if option is None:
        return 0.0
    cost = (
        prompt_tokens / 1_000_000 * option.input_cost_per_million
        + completion_tokens / 1_000_000 * option.output_cost_per_million
    )
    return round(cost, 6)


@dataclass(frozen=True)
class CostDecision:
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_cost_usd: float
    max_cost_usd: float

    @property
    def exceeds(self) -> bool:
        return self.estimated_cost_usd > self.max_cost_usd


def build_chunks(groups: Sequence[RecordGroup], settings: Settings) -> list[list[RecordGroup]]:
    """Kayıtları token bütçesine ve kayıt sayısına göre partilere böler.

    İKİ SINIR BİRDEN uygulanır, hangisi önce dolarsa parti orada kapanır:

    * `llm_chunk_max_records` — tek bir yanıtta çok fazla kayıt istemek,
      modelin kayıt atlama olasılığını artırır ve tek bir bozuk yanıt çok
      kaydı riske atar.
    * `llm_chunk_max_prompt_tokens` — bağlam penceresi bir ÜST sınırdır ama
      ona kadar doldurmak kaliteyi düşürür. Uzun bir mesaj tek başına
      bütçeyi aşsa bile kendi partisinde gönderilir; kayıt ATLANMAZ.

    BURADA YAŞIYOR çünkü maliyet tahmini de chunk sayısını bilmek zorunda:
    map prompt'u her chunk'ta yeniden gönderiliyor. İki modülde iki ayrı
    chunk hesabı tutmak, tavan kontrolü ile gerçek çağrı boyutunun sessizce
    ayrışması demekti.
    """
    max_records = max(1, settings.llm_chunk_max_records)
    max_tokens = max(1, settings.llm_chunk_max_prompt_tokens)

    chunks: list[list[RecordGroup]] = []
    current: list[RecordGroup] = []
    current_tokens = 0

    for group in groups:
        cost = record_tokens(group)
        would_overflow = current and (
            len(current) >= max_records or current_tokens + cost > max_tokens
        )
        if would_overflow:
            chunks.append(current)
            current = []
            current_tokens = 0

        current.append(group)
        current_tokens += cost

    if current:
        chunks.append(current)
    return chunks


def record_tokens(group: RecordGroup) -> int:
    """Bir kaydın prompt'ta kaplayacağı tahmini token.

    `redacted_text` ölçülüyor — prompt'a giden dize o (`llm_classifier`
    `<kayit>` gövdesine onu koyuyor). `normalized` ölçmek, tahmini gerçekten
    ayırırdı.
    """
    return int(len(group.redacted_text) / CHARS_PER_TOKEN) + OVERHEAD_TOKENS_PER_RECORD


def _template_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def estimate_cost(
    groups: list[RecordGroup],
    model_id: str,
    max_cost_usd: float,
    *,
    settings: Settings,
    prompt: PromptBundle,
) -> CostDecision:
    """Benzersiz kayıtlardan tahmini maliyeti çıkarır.

    Girdi TEKİLLEŞTİRİLMİŞ kayıtlardır: dedupe'un maliyet üzerindeki etkisi
    (ADR §10 risk 3) burada doğrudan görünür — 100.000 satırlık bir dosyada
    30.000 benzersiz kayıt varsa tahmin de o oranda düşer.

    ÜÇ BİLEŞEN sayılıyor. Önce yalnızca birincisi sayılıyordu ve tahmin
    ölçülen 600/3000/6000 kayıtta sistematik olarak 1.21-1.23x eksik
    kalıyordu:

    1. **Kayıtlar** — `redacted_text` üzerinden, chunk'lamayla aynı hesap.
    2. **Map yükü × chunk sayısı** — system prompt + kullanıcı şablonu HER
       chunk'ta yeniden gönderiliyor (v1'de ölçülen: ~661 token/chunk).
       Chunk sayısı tahmin edilmiyor, `build_chunks` ile GERÇEKTEN
       hesaplanıyor.
    3. **Reduce çağrısı** — system + kullanıcı şablonu, YALNIZCA birden çok
       chunk varsa. `llm_classifier` reduce'u `len(chunks) > 1` koşuluyla
       çalıştırıyor; tek chunk'ta koşulsuz reduce eklemek, küçük dosyaların
       tahminini şişirip haksız yere tavana takardı.

    REDUCE'UN KATEGORİ YÜKÜ SAYILMIYOR. Kova sayısı çağrı yapılmadan
    bilinemez ve uydurma bir sabit koymak tahmini şişirip işleri haksız yere
    reddederdi. Kalan sapma budur ve ölçümde küçüktür.

    ONARIM PAYI DA YOK: onarımlar sınırlı (`openrouter_max_repair_attempts`,
    varsayılan 2) ve istisnai. Her tahmine onarım payı eklemek, hiç onarım
    gerekmeyen işlerin çoğunu tavana takardı.

    Tahmin uçuş öncesi TEK KEZ kontrol ediliyor; koşu ortasında gerçek
    tüketime karşı yeniden kontrol ayrı bir iştir.
    """
    chunks = build_chunks(groups, settings)

    record_total = sum(record_tokens(group) for group in groups)
    map_overhead = _template_tokens(prompt.map_system) + _template_tokens(prompt.map_user_template)
    # `llm_classifier`: `needs_reduce = len(chunks) > 1 and len(buckets) > 1`.
    # Kova sayısı çağrı yapılmadan bilinemez, chunk sayısı bilinir — gerekli
    # koşulu kullanıyoruz. Tek chunk'ta reduce hiç çalışmıyor.
    reduce_overhead = (
        _template_tokens(prompt.reduce_system) + _template_tokens(prompt.reduce_user_template)
        if len(chunks) > 1
        else 0
    )

    prompt_tokens = record_total + map_overhead * len(chunks) + reduce_overhead
    completion_tokens = OUTPUT_TOKENS_PER_RECORD * len(groups)

    return CostDecision(
        estimated_prompt_tokens=prompt_tokens,
        estimated_completion_tokens=completion_tokens,
        estimated_cost_usd=cost_for_tokens(prompt_tokens, completion_tokens, model_id),
        max_cost_usd=max_cost_usd,
    )
