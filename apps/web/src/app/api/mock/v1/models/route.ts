import { NextResponse } from "next/server";

/**
 * GET /api/mock/v1/models
 *
 * ADR §6 modelin backend whitelist'inden seçilmesini şart koşuyor ama listeyi
 * döndüren endpoint tanımlamıyor. Bu, arayüzün ihtiyaç duyduğu için önerilen
 * sözleşme; backend sahibiyle mutabakat gerekiyor.
 *
 * Listedekilerin tamamı structured output (JSON Schema) destekli modeller.
 */
export function GET() {
  return NextResponse.json({
    models: [
      {
        id: "anthropic/claude-sonnet-4",
        label: "Claude Sonnet 4",
        input_cost_per_million: 3,
        output_cost_per_million: 15,
        context_window: 200_000,
      },
      {
        id: "openai/gpt-4.1-mini",
        label: "GPT-4.1 mini",
        input_cost_per_million: 0.4,
        output_cost_per_million: 1.6,
        context_window: 128_000,
      },
      {
        id: "google/gemini-2.5-flash",
        label: "Gemini 2.5 Flash",
        input_cost_per_million: 0.3,
        output_cost_per_million: 2.5,
        context_window: 1_000_000,
      },
    ],
    default_model: "anthropic/claude-sonnet-4",
    default_prompt_version: "faq_analysis/v1",
  });
}
