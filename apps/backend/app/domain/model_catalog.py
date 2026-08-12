"""Model whitelist'i — plan §1.1.

ADR §6/§9 değişmezi: "Model yalnızca JSON Schema structured output desteği
DOĞRULANMIŞ backend whitelist'inden seçilebilir."

Liste OpenRouter'dan DİNAMİK ÇEKİLMEZ. Whitelist'in varlık sebebi tam olarak
budur: sağlayıcının katalogunda structured output desteklemeyen yüzlerce model
var ve bunlardan biri seçilirse Faz 3'teki Pydantic doğrulaması her seferinde
başarısız olur, iş `PROVIDER_BAD_RESPONSE` ile biter. Listeye yeni model
eklemek bilinçli bir karar ve bir doğrulama adımı gerektirir.

Değerler `apps/web/src/app/api/mock/v1/models/route.ts` ile BİREBİR aynıdır:
kullanıcı `NEXT_PUBLIC_API_BASE_URL`'i mock'a geri çevirdiğinde arayüzün
aynı kalması gerekiyor.

------------------------------------------------------------------------
DOĞRULAMA — 12 Ağustos 2026 (Faz 3, plan §4'ün ilk maddesi)
------------------------------------------------------------------------

Liste OpenRouter'ın CANLI katalogundan doğrulandı (anahtar gerektirmeyen
public endpoint):

    GET https://openrouter.ai/api/v1/models      → 406 model

Kabul ölçütü, whitelist'in kendi tanımıdır: modelin `supported_parameters`
dizisi hem `structured_outputs` hem `response_format` içermeli.

Doğrulama sonucu — planın §4'teki iddiası KISMEN YANLIŞ çıktı:

* `anthropic/claude-sonnet-4` — katalogda GERÇEKTEN VAR (plan "geçerli bir
  Claude model kimliği değil" diyordu; bu doğru değil). Ama
  `structured_outputs=False`. Yani listeden çıkarılması gerekiyor, planın
  gerekçesiyle değil, whitelist'in kendi ölçütüyle: bu modelle JSON Schema
  zorlanamaz ve Faz 3 her çağrıda `PROVIDER_BAD_RESPONSE` alırdı.
* `openai/gpt-4.1-mini` — VAR, `structured_outputs=True`. Fiyatlar
  ($0.40 / $1.60) buradaki değerlerle birebir tutuyor. Yalnızca
  `context_window` düzeltildi (128.000 → 1.047.576).
* `google/gemini-2.5-flash` — VAR, `structured_outputs=True`. Fiyatlar
  ($0.30 / $2.50) tutuyor. `context_window` 1.000.000 → 1.048.576.

Yerine konan model `anthropic/claude-sonnet-5`: katalogda var,
`structured_outputs=True`, 1M bağlam, $2.00 / $10.00.

Listeye yeni model eklemek aynı kontrolü tekrar gerektirir; kimlik TAHMİN
EDİLMEZ.
"""

from __future__ import annotations

from app.schemas.catalog import ModelList, ModelOption

#: Faz 3'te kullanılacak varsayılan prompt sürümü. Backend'de sürümlenir
#: (ADR §9); frontend bunu analiz isteğinde geri gönderir.
DEFAULT_PROMPT_VERSION = "faq_analysis/v1"

#: Varsayılan model, whitelist'in EN UCUZ GİRDİ fiyatlı seçeneğidir.
#: Gerekçe: bu iş yükünde model yalnızca "kayıt kimliği → kategori" eşlemesi
#: üretir (ADR §4), yani çıktı kayıt başına birkaç token; maliyeti neredeyse
#: tamamen GİRDİ tarafı belirler. 100.000 satırlık bir dosyada girdi fiyatı
#: onda birlik farkla bile işin toplam maliyetini katlar. Varsayılanı
#: listenin en pahalı modeli yapmak, kullanıcıyı hiçbir şey değiştirmeden
#: maliyet tavanına çarptırırdı. Kullanıcı arayüzden diğerlerini seçebilir.
DEFAULT_MODEL = "google/gemini-2.5-flash"

MODEL_WHITELIST: tuple[ModelOption, ...] = (
    ModelOption(
        id="google/gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        input_cost_per_million=0.3,
        output_cost_per_million=2.5,
        context_window=1_048_576,
    ),
    ModelOption(
        id="openai/gpt-4.1-mini",
        label="GPT-4.1 mini",
        input_cost_per_million=0.4,
        output_cost_per_million=1.6,
        context_window=1_047_576,
    ),
    ModelOption(
        id="anthropic/claude-sonnet-5",
        label="Claude Sonnet 5",
        input_cost_per_million=2.0,
        output_cost_per_million=10.0,
        context_window=1_000_000,
    ),
)


def get_model_list() -> ModelList:
    return ModelList(
        models=list(MODEL_WHITELIST),
        default_model=DEFAULT_MODEL,
        default_prompt_version=DEFAULT_PROMPT_VERSION,
    )


def is_allowed_model(model_id: str) -> bool:
    """Faz 2'de `POST /analyses` bunu kullanacak."""
    return any(option.id == model_id for option in MODEL_WHITELIST)
