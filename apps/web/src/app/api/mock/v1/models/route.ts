import { NextResponse } from "next/server";

/**
 * GET /api/mock/v1/models
 *
 * ADR §6 modelin backend whitelist'inden seçilmesini şart koşuyor ama listeyi
 * döndüren endpoint tanımlamıyor. Bu, arayüzün ihtiyaç duyduğu için önerilen
 * sözleşme; backend sahibiyle mutabakat gerekiyor.
 *
 * Listedekilerin tamamı structured output (JSON Schema) destekli modeller.
 *
 * ⚠️ Bu liste `apps/backend/app/domain/model_catalog.py` ile BİREBİR aynı
 * olmak zorundadır. İkisi ayrışırsa arayüz mock'ta çalışır, gerçek backend'de
 * "model listede yok" hatası verir. Kimlikler OpenRouter'ın canlı
 * katalogundan (GET https://openrouter.ai/api/v1/models) doğrulanmıştır;
 * gerekçe ve doğrulama tarihi backend dosyasının başlığındadır.
 */
export function GET() {
  return NextResponse.json({
    models: [
      {
        id: "google/gemini-2.5-flash",
        label: "Gemini 2.5 Flash",
        input_cost_per_million: 0.3,
        output_cost_per_million: 2.5,
        context_window: 1_048_576,
      },
      {
        id: "openai/gpt-4.1-mini",
        label: "GPT-4.1 mini",
        input_cost_per_million: 0.4,
        output_cost_per_million: 1.6,
        context_window: 1_047_576,
      },
      {
        id: "anthropic/claude-sonnet-5",
        label: "Claude Sonnet 5",
        input_cost_per_million: 2,
        output_cost_per_million: 10,
        context_window: 1_000_000,
      },
    ],
    default_model: "google/gemini-2.5-flash",
    default_prompt_version: "faq_analysis/v1",
  });
}
