import { expect, it } from "vitest";

import { modelIdSchema, modelListSchema, promptVersionSchema } from "@/lib/api/schemas";
import { readFixture } from "@/lib/api/schemas/contract-paths";

import { KNOWN_PROMPT_VERSIONS, MOCK_MODEL_LIST } from "./catalog";

it("mock model kataloğu backend'in ürettiği whitelist fixture'ıyla birebir aynı", () => {
  const generated = modelListSchema.parse(readFixture("models.list.200.json"));
  expect(MOCK_MODEL_LIST).toEqual(generated);
  expect(KNOWN_PROMPT_VERSIONS).toEqual(promptVersionSchema.options);
  expect(generated.models.map((model) => model.id)).toEqual(modelIdSchema.options);
  expect(generated.default_prompt_version).toBe("faq_analysis/v3");
});

it("dondurulmuş model kimlikleri, fiyatlar ve context window değerleri exact", () => {
  expect(MOCK_MODEL_LIST).toEqual({
    models: [
      {
        id: "anthropic/claude-sonnet-4.6",
        label: "Claude Sonnet 4.6",
        input_cost_per_million: 3,
        output_cost_per_million: 15,
        cache_read_cost_per_million: 0.3,
        cache_write_cost_per_million: 3.75,
        context_window: 1_000_000,
        pricing_source: "fallback",
        pricing_updated_at: null,
      },
      {
        id: "openai/gpt-4.1-mini",
        label: "GPT-4.1 mini",
        input_cost_per_million: 0.4,
        output_cost_per_million: 1.6,
        cache_read_cost_per_million: 0.1,
        cache_write_cost_per_million: null,
        context_window: 1_047_576,
        pricing_source: "fallback",
        pricing_updated_at: null,
      },
      {
        id: "google/gemini-2.5-flash",
        label: "Gemini 2.5 Flash",
        input_cost_per_million: 0.3,
        output_cost_per_million: 2.5,
        cache_read_cost_per_million: 0.03,
        cache_write_cost_per_million: 0.0833333333333,
        context_window: 1_048_576,
        pricing_source: "fallback",
        pricing_updated_at: null,
      },
    ],
    default_model: "google/gemini-2.5-flash",
    default_prompt_version: "faq_analysis/v3",
  });
});
