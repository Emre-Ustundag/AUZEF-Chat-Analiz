import { describe, expect, it } from "vitest";

import { analysisRequestSchema } from "@/lib/api/schemas";

import { configureFormSchema, toAnalysisRequest } from "./form-schema";

const UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";

const validValues = {
  sheet_name: "Mesajlar",
  text_column: "mesaj",
  model: "anthropic/claude-sonnet-4",
  prompt_version: "faq_analysis/v1",
  top_n: 20,
  max_cost_usd: 10,
  openrouter_api_key: "sk-or-gizli-anahtar",
};

describe("configureFormSchema", () => {
  it("geçerli değerleri kabul eder", () => {
    expect(configureFormSchema.safeParse(validValues).success).toBe(true);
  });

  it("API anahtarını zorunlu tutar", () => {
    expect(configureFormSchema.safeParse({ ...validValues, openrouter_api_key: "" }).success).toBe(
      false,
    );
  });

  it("istek şemasının kısıtlarını devralır", () => {
    // top_n ve maliyet sınırı analysisRequestSchema'da tanımlı; formda
    // yeniden yazılmadıkları için burada da geçerli olmaları gerekir.
    expect(configureFormSchema.safeParse({ ...validValues, top_n: 0 }).success).toBe(false);
    expect(configureFormSchema.safeParse({ ...validValues, max_cost_usd: 0 }).success).toBe(false);
    expect(configureFormSchema.safeParse({ ...validValues, text_column: "" }).success).toBe(false);
  });

  it("upload_id istemez", () => {
    // URL'den geliyor; formda sorulması kullanıcıya anlamsız bir alan olurdu.
    expect(configureFormSchema.safeParse(validValues).success).toBe(true);
  });
});

describe("toAnalysisRequest", () => {
  it("geçerli bir istek gövdesi üretir", () => {
    const request = toAnalysisRequest(UPLOAD_ID, validValues);
    expect(analysisRequestSchema.safeParse(request).success).toBe(true);
    expect(request.upload_id).toBe(UPLOAD_ID);
  });

  it("API anahtarını gövdeye KOYMAZ", () => {
    // ADR §6/§9: anahtar yalnızca X-OpenRouter-Key header'ında gider,
    // gövdeye veya sorgu parametresine asla yazılmaz.
    const request = toAnalysisRequest(UPLOAD_ID, validValues);

    expect(Object.keys(request)).not.toContain("openrouter_api_key");
    expect(JSON.stringify(request)).not.toContain("sk-or-gizli-anahtar");
  });

  it("forma sonradan eklenen alanları gövdeye sızdırmaz", () => {
    // Alanlar spread yerine tek tek yazıldığı için bu davranış garanti;
    // biri spread'e dönerse bu test kırılır.
    const request = toAnalysisRequest(UPLOAD_ID, {
      ...validValues,
      // @ts-expect-error — şemada olmayan bir alanın sızmadığını doğruluyoruz.
      dahili_not: "gizli",
    });

    expect(JSON.stringify(request)).not.toContain("gizli");
  });
});
