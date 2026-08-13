import type { ModelId, ModelList, ModelOption, PromptVersion } from "@/lib/api/schemas";

/**
 * Mock model whitelist'i.
 *
 * `models/route.ts` içinden çıkarıldı: `analyses/route.ts` de aynı listeye
 * karşı doğrulama yapıyor (ADR-0002 #1 INVALID_MODEL). Tek kopya kalması
 * ikisinin ayrışmasını engelliyor.
 *
 * Listedekilerin tamamı structured output (JSON Schema) destekli modeller.
 */
export const MOCK_MODELS: readonly ModelOption[] = [
  {
    id: "anthropic/claude-sonnet-4.6",
    label: "Claude Sonnet 4.6",
    input_cost_per_million: 3,
    output_cost_per_million: 15,
    context_window: 1_000_000,
  },
  {
    id: "openai/gpt-4.1-mini",
    label: "GPT-4.1 mini",
    input_cost_per_million: 0.4,
    output_cost_per_million: 1.6,
    context_window: 1_047_576,
  },
  {
    id: "google/gemini-2.5-flash",
    label: "Gemini 2.5 Flash",
    input_cost_per_million: 0.3,
    output_cost_per_million: 2.5,
    context_window: 1_048_576,
  },
];

export const DEFAULT_MODEL: ModelId = "google/gemini-2.5-flash";
export const DEFAULT_PROMPT_VERSION: PromptVersion = "faq_analysis/v1";

/** Backend'de sürümlenmiş prompt'lar; mock tek sürüm tanıyor. */
export const KNOWN_PROMPT_VERSIONS: readonly PromptVersion[] = [DEFAULT_PROMPT_VERSION];

export const MOCK_MODEL_LIST: ModelList = {
  models: [...MOCK_MODELS],
  default_model: DEFAULT_MODEL,
  default_prompt_version: DEFAULT_PROMPT_VERSION,
};

export function findModel(id: string): ModelOption | undefined {
  return MOCK_MODELS.find((model) => model.id === id);
}

/**
 * Kaba maliyet ön tahmini — ADR-0002 #10.
 *
 * Gerçek backend upload profilinden (satır sayısı × ortalama uzunluk × model
 * fiyatı) hesaplar. Mock sabit bir korpus varsaydığı için yalnızca modele
 * göre değişen bir tahmin üretir; amaç COST_LIMIT_EXCEEDED yolunun arayüzde
 * erişilebilir olması.
 */
export function estimateCostUsd(modelId: string): number {
  const model = findModel(modelId);
  if (!model) return 0;

  // ~1,28M girdi + ~96K çıktı tokenı (mocks/store.ts'teki token_usage ile aynı).
  const inputCost = (1_284_000 / 1_000_000) * model.input_cost_per_million;
  const outputCost = (96_400 / 1_000_000) * model.output_cost_per_million;
  return Number((inputCost + outputCost).toFixed(4));
}
