import { describe, expect, it } from "vitest";

import { analysisRequestSchema } from "@/lib/api/schemas";

import { configureFormSchema, type ConfigureFormValues, toAnalysisRequest } from "./form-schema";

const UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";

const conversationConfig = {
  session_id_column: "session_id",
  message_order_column: "message_order",
  role_column: "direction",
  message_type_column: "message_type",
  user_role_values: ["Kullanıcı"],
  assistant_role_values: ["Bot"],
  include_assistant_context: false,
  target_message_types: ["text", "quick_reply"],
  context_message_types: ["text", "quick_reply", "single-choice"],
  max_context_turns: 4,
  max_context_tokens: 1000,
};

const validValues = {
  sheet_name: "Mesajlar",
  text_column: "mesaj",
  row_filters: [],
  analysis_mode: "message",
  conversation_config: null,
  model: "anthropic/claude-sonnet-4.6",
  prompt_version: "faq_analysis/v1",
  top_n: 20,
  max_cost_usd: 10,
  openrouter_api_key: "sk-or-gizli-anahtar",
} satisfies ConfigureFormValues;

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

  it("satır filtrelerini doğrular", () => {
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        row_filters: [{ column: "direction", allowed_values: ["Kullanıcı"] }],
      }).success,
    ).toBe(true);
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        row_filters: [{ column: "direction", allowed_values: [] }],
      }).success,
    ).toBe(false);
  });

  it("bağlamsal modu v4 prompt ve konuşma eşlemesiyle kabul eder", () => {
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        analysis_mode: "contextual_user_turns",
        conversation_config: conversationConfig,
        prompt_version: "faq_analysis/v4",
      }).success,
    ).toBe(true);
  });

  it("bağlamsal modun config ve v4 prompt kurallarını uygular", () => {
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        analysis_mode: "contextual_user_turns",
        conversation_config: null,
        prompt_version: "faq_analysis/v4",
      }).success,
    ).toBe(false);
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        analysis_mode: "contextual_user_turns",
        conversation_config: conversationConfig,
        prompt_version: "faq_analysis/v3",
      }).success,
    ).toBe(false);
  });

  it("konuşma eşlemesi sınırlarını ve rol ayrımını doğrular", () => {
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        analysis_mode: "contextual_user_turns",
        prompt_version: "faq_analysis/v4",
        conversation_config: { ...conversationConfig, max_context_turns: 9 },
      }).success,
    ).toBe(false);
    expect(
      configureFormSchema.safeParse({
        ...validValues,
        analysis_mode: "contextual_user_turns",
        prompt_version: "faq_analysis/v4",
        conversation_config: {
          ...conversationConfig,
          assistant_role_values: ["Kullanıcı"],
        },
      }).success,
    ).toBe(false);
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
    expect(request.row_filters).toEqual([]);
    expect(request.analysis_mode).toBe("message");
    expect(request.conversation_config).toBeNull();
  });

  it("satır filtrelerini istek gövdesine taşır", () => {
    const rowFilters = [{ column: "direction", allowed_values: ["Kullanıcı", "Temsilci"] }];
    const request = toAnalysisRequest(UPLOAD_ID, { ...validValues, row_filters: rowFilters });

    expect(request.row_filters).toEqual(rowFilters);
  });

  it("bağlamsal konuşma eşlemesini ve v4 prompt'u istek gövdesine taşır", () => {
    const request = toAnalysisRequest(UPLOAD_ID, {
      ...validValues,
      analysis_mode: "contextual_user_turns",
      conversation_config: conversationConfig,
      prompt_version: "faq_analysis/v4",
    });

    expect(request.analysis_mode).toBe("contextual_user_turns");
    expect(request.conversation_config).toEqual(conversationConfig);
    expect(request.prompt_version).toBe("faq_analysis/v4");
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
