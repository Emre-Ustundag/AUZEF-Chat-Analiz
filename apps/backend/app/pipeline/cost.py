"""Maliyet tavanı kontrolü — plan §4, ADR §9 ve §10 risk 3.

ADR §9: "Token ve tahmini maliyet üst sınırı aşılırsa LLM çağrısı başlamadan
iş güvenli biçimde durur."

Tavan ÜÇ ayrı noktada uygulanıyor ve üçü farklı soruya cevap veriyor:

1. `POST /analyses` — profildeki kolon istatistiklerinden yapılan senkron ön
   tahmin. Pahalı olduğu baştan belli olan istek job ve Redis secret
   oluşturmadan `422 COST_LIMIT_EXCEEDED` alır (ADR-0002 #10).
2. Worker, gerçek hücreleri okuyup tekilleştirdikten SONRA — dedupe'in
   etkisini gören daha doğru tahmin. LLM çağrıları henüz başlamadı.
3. Her map chunk'ından sonra, sağlayıcının `usage` bloğundan okunan GERÇEK
   tutara göre. Kontrolün chunk'tan SONRA olması bilinçli: harcanmış para
   geri alınamaz, yapılabilecek tek şey kalan chunk'ları göndermemek.

Üçü de aynı `COST_LIMIT_EXCEEDED` kodunu kullanır (ADR-0002 #10); 1 senkron
HTTP hatası, 2 ve 3 terminal job hatasıdır.

TAHMİN, HARCANAN DEĞİLDİR: rapordaki `token_usage` ve `estimated_cost_usd`
sağlayıcının bildirdiği gerçek tüketimden gelir, buradaki tahminden değil.
İkisini karıştırmak raporu yalancı yapardı.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import Settings
from app.pipeline.preprocess import RecordGroup
from app.prompts.faq_analysis import PromptBundle
from app.schemas.analysis import PricingSnapshot
from app.services.pricing import fallback_pricing_snapshot

#: Karakter başına kabaca token. Türkçe metinde tokenizer'lar İngilizce'ye
#: göre daha çok token üretiyor; 3 karakter/token temkinli bir tahmin.
CHARS_PER_TOKEN = 3.0

#: Her kayıt için prompt'ta yer kaplayan sabit yük (kimlik, ayraç, numara).
OVERHEAD_TOKENS_PER_RECORD = 12

#: Bir map yanıtının kayıtlar dışında kalan JSON/şema yükü. Gerçek OpenRouter
#: smoke testinde 5 benzersiz kayıt, tek map çağrısında 469 completion token
#: üretti. Bunu doğrudan ``469 / 5`` diye kayıt başına bölmek küçük çağrılardaki
#: sabit ``categories`` / ``assignments`` zarfını tüm veri boyutlarına taşırdı.
MAP_COMPLETION_BASE_TOKENS = 200

#: Her kayıt map çıktısında en az bir assignment üretir; kategorilerin kimlik,
#: kanonik soru ve tema alanları da kayıt sayısıyla birlikte büyür. 60 + sabit
#: yük formülü gerçek 5 kayıtlık ölçüm için 500 token verir: gerçekleşen 469'un
#: %6,6 üzerinde, yani tavan kontrolü için küçük bir güvenlik payıyla yakın.
MAP_COMPLETION_TOKENS_PER_RECORD = 60

#: Birden çok chunk varsa reduce yanıtının sabit JSON yükü ve girdi kategori
#: başına büyüyen grup/member listesi ayrıca fiyatlanır.
REDUCE_COMPLETION_BASE_TOKENS = 200
REDUCE_COMPLETION_TOKENS_PER_CATEGORY = 50

#: Prompt kompakt kategori sözlüğü istiyor. Kategori sayısını çağrıdan önce
#: bilemeyiz; her chunk için en fazla 20 kategori beklemek, tipik SSS verisini
#: her kaydı ayrı kategori varsayan aşırı pahalı üst sınırdan korurken reduce
#: maliyetini sıfır saymıyor. Koşu içi gerçek maliyet koruması sapmayı her map
#: çağrısından sonra ayrıca durdurur.
ESTIMATED_CATEGORIES_PER_CHUNK = 20


def cost_for_tokens(
    prompt_tokens: int,
    completion_tokens: int,
    model_id: str,
    *,
    pricing_snapshot: PricingSnapshot | None = None,
) -> float:
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
    try:
        pricing = pricing_snapshot or fallback_pricing_snapshot(model_id)
    except KeyError:
        return 0.0
    cost = (
        prompt_tokens / 1_000_000 * pricing.input_cost_per_million
        + completion_tokens / 1_000_000 * pricing.output_cost_per_million
    )
    return round(cost, 6)


def cost_for_usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    cache_write_tokens: int,
    model_id: str,
    pricing_snapshot: PricingSnapshot | None = None,
) -> float:
    """Provider ``usage.cost`` yoksa cache ayrıntılı token fallback hesabı."""
    try:
        pricing = pricing_snapshot or fallback_pricing_snapshot(model_id)
    except KeyError:
        return 0.0

    cached = min(max(0, cached_tokens), max(0, prompt_tokens))
    written = min(max(0, cache_write_tokens), max(0, prompt_tokens - cached))
    regular = max(0, prompt_tokens - cached - written)
    cache_read_rate = pricing.cache_read_cost_per_million
    cache_write_rate = pricing.cache_write_cost_per_million

    cost = (
        regular / 1_000_000 * pricing.input_cost_per_million
        + cached
        / 1_000_000
        * (cache_read_rate if cache_read_rate is not None else pricing.input_cost_per_million)
        + written
        / 1_000_000
        * (cache_write_rate if cache_write_rate is not None else pricing.input_cost_per_million)
        + completion_tokens / 1_000_000 * pricing.output_cost_per_million
    )
    return round(cost, 6)


def estimate_profile_cost(
    record_count: int,
    average_length: float,
    model_id: str,
    *,
    settings: Settings,
    prompt: PromptBundle,
    pricing_snapshot: PricingSnapshot | None = None,
) -> float:
    """Upload profilinden senkron analiz ön tahmini üretir.

    ADR-0002 #10 gereği ``POST /analyses`` pahalı olduğu baştan belli bir
    işi job oluşturmadan reddeder. ``record_count`` seçilen kolonun profil
    aşamasında hesaplanan ham benzersiz değer sayısıdır; normalize/PII sonrası
    dedupe ile birebir aynı değildir ama tüm dolu satırları benzersiz saymaktan
    daha adildir. Worker, gerçek ön işleme sonrasında kesin chunk listesini
    kullanarak kontrolü ayrıca uygular.
    """
    if record_count <= 0:
        return 0.0

    tokens_per_record = int(max(0.0, average_length) / CHARS_PER_TOKEN) + OVERHEAD_TOKENS_PER_RECORD
    chunk_sizes = _profile_chunk_sizes(record_count, tokens_per_record, settings)
    prompt_tokens = _estimated_prompt_tokens(
        record_tokens_total=record_count * tokens_per_record,
        chunk_count=len(chunk_sizes),
        prompt=prompt,
    )
    completion_tokens = _estimated_completion_tokens(chunk_sizes)
    return cost_for_tokens(
        prompt_tokens,
        completion_tokens,
        model_id,
        pricing_snapshot=pricing_snapshot,
    )


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


def _profile_chunk_sizes(
    record_count: int,
    tokens_per_record: int,
    settings: Settings,
) -> list[int]:
    """Profil istatistiklerinden worker chunk boyutlarını yaklaşıklar."""
    max_by_tokens = max(
        1,
        settings.llm_chunk_max_prompt_tokens // max(1, tokens_per_record),
    )
    chunk_size = max(1, min(settings.llm_chunk_max_records, max_by_tokens))
    full_chunks, remainder = divmod(record_count, chunk_size)
    sizes = [chunk_size] * full_chunks
    if remainder:
        sizes.append(remainder)
    return sizes


def _estimated_prompt_tokens(
    *,
    record_tokens_total: int,
    chunk_count: int,
    prompt: PromptBundle,
) -> int:
    if chunk_count == 0:
        return 0
    map_overhead = _template_tokens(prompt.map_system) + _template_tokens(prompt.map_user_template)
    reduce_overhead = (
        _template_tokens(prompt.reduce_system) + _template_tokens(prompt.reduce_user_template)
        if chunk_count > 1
        else 0
    )
    return record_tokens_total + map_overhead * chunk_count + reduce_overhead


def _estimated_completion_tokens(chunk_sizes: Sequence[int]) -> int:
    """Map ve olası reduce JSON çıktılarının completion-token tahmini."""
    if not chunk_sizes:
        return 0

    map_tokens = sum(
        MAP_COMPLETION_BASE_TOKENS + MAP_COMPLETION_TOKENS_PER_RECORD * size for size in chunk_sizes
    )
    if len(chunk_sizes) == 1:
        return map_tokens

    estimated_categories = sum(min(size, ESTIMATED_CATEGORIES_PER_CHUNK) for size in chunk_sizes)
    reduce_tokens = (
        REDUCE_COMPLETION_BASE_TOKENS + REDUCE_COMPLETION_TOKENS_PER_CATEGORY * estimated_categories
    )
    return map_tokens + reduce_tokens


def estimate_cost(
    groups: list[RecordGroup],
    model_id: str,
    max_cost_usd: float,
    *,
    settings: Settings,
    prompt: PromptBundle,
    pricing_snapshot: PricingSnapshot | None = None,
) -> CostDecision:
    """Benzersiz kayıtlardan tahmini maliyeti çıkarır.

    Girdi TEKİLLEŞTİRİLMİŞ kayıtlardır: dedupe'un maliyet üzerindeki etkisi
    (ADR §10 risk 3) burada doğrudan görünür — 100.000 satırlık bir dosyada
    30.000 benzersiz kayıt varsa tahmin de o oranda düşer.

    DÖRT BİLEŞEN sayılıyor. Önce yalnızca birincisi sayılıyordu ve tahmin
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
    4. **Completion JSON'u** — her map çağrısının sabit zarfı ve kayıt başına
       assignment/kategori yükü; çoklu chunk'ta reduce JSON'u da eklenir.
       Reduce kategori sayısı çağrıdan önce bilinmediği için, prompt'un
       kompakt kategori talimatıyla uyumlu biçimde chunk başına en fazla 20
       kategori varsayılır. Bu sayı gerçek tüketim loglarıyla yeniden
       kalibre edilebilir; koşu içi tavan sapmayı ayrıca sınırlar.

    ONARIM PAYI DA YOK: onarımlar sınırlı (`openrouter_max_repair_attempts`,
    varsayılan 2) ve istisnai. Her tahmine onarım payı eklemek, hiç onarım
    gerekmeyen işlerin çoğunu tavana takardı.

    Tahmin uçuş öncesi TEK KEZ kontrol ediliyor; koşu ortasında gerçek
    tüketime karşı yeniden kontrol ayrı bir iştir.
    """
    chunks = build_chunks(groups, settings)

    record_total = sum(record_tokens(group) for group in groups)
    prompt_tokens = _estimated_prompt_tokens(
        record_tokens_total=record_total,
        chunk_count=len(chunks),
        prompt=prompt,
    )
    completion_tokens = _estimated_completion_tokens([len(chunk) for chunk in chunks])

    return CostDecision(
        estimated_prompt_tokens=prompt_tokens,
        estimated_completion_tokens=completion_tokens,
        estimated_cost_usd=cost_for_tokens(
            prompt_tokens,
            completion_tokens,
            model_id,
            pricing_snapshot=pricing_snapshot,
        ),
        max_cost_usd=max_cost_usd,
    )
