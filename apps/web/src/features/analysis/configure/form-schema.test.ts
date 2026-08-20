import { describe, expect, it } from "vitest";

import { analysisRequestSchema } from "@/lib/api/schemas";

import { configureFormSchema, type ConfigureFormValues, toAnalysisRequest } from "./form-schema";

const UPLOAD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";

const validValues = {
  sheet_name: "Mesajlar",
  text_column: "mesaj",
  model: "anthropic/claude-sonnet-4.6",
  prompt_version: "faq_analysis/v1",
  top_n: 20,
  max_cost_usd: 10,
  openrouter_api_key: "sk-or-gizli-anahtar",
  dataset_type: "GENERIC",
  role_column: "",
  role_user_values_raw: "",
  session_id_column: "",
  timestamp_column: "",
  message_type_column: "",
  allowed_message_types_raw: "",
} satisfies ConfigureFormValues;

const chatbotValues = {
  ...validValues,
  dataset_type: "CHATBOT_LOG",
  sheet_name: "CSV",
  text_column: "message_text_clean",
  role_column: "direction",
  role_user_values_raw: "Kullanıcı, user",
  session_id_column: "session_id",
  timestamp_column: "message_time_tr",
  message_type_column: "message_type",
  allowed_message_types_raw: "text",
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

  it("upload_id istemez", () => {
    // URL'den geliyor; formda sorulması kullanıcıya anlamsız bir alan olurdu.
    expect(configureFormSchema.safeParse(validValues).success).toBe(true);
  });

  it("CHATBOT_LOG için rol kolonu ve kullanıcı değerleri zorunlu", () => {
    expect(configureFormSchema.safeParse(chatbotValues).success).toBe(true);
    expect(configureFormSchema.safeParse({ ...chatbotValues, role_column: "" }).success).toBe(
      false,
    );
    expect(
      configureFormSchema.safeParse({ ...chatbotValues, role_user_values_raw: " , " }).success,
    ).toBe(false);
    // Mesaj tipi kolonu seçiliyken izinli tipler boş bırakılamaz.
    expect(
      configureFormSchema.safeParse({ ...chatbotValues, allowed_message_types_raw: "" }).success,
    ).toBe(false);
  });

  it("GENERIC'te chatbot alanları boş kalabilir", () => {
    expect(
      configureFormSchema.safeParse({ ...validValues, role_column: "", role_user_values_raw: "" })
        .success,
    ).toBe(true);
  });
});

describe("toAnalysisRequest", () => {
  it("geçerli bir istek gövdesi üretir", () => {
    const request = toAnalysisRequest(UPLOAD_ID, validValues);
    expect(analysisRequestSchema.safeParse(request).success).toBe(true);
    expect(request.upload_id).toBe(UPLOAD_ID);
    // Fingerprint kararlılığı için her zaman açıkça gönderilir (ADR-0002 #3).
    expect(request.dataset_type).toBe("GENERIC");
    expect(request.chatbot_config).toBeNull();
  });

  it("CHATBOT_LOG'da kolon eşlemesini gövdeye kurar", () => {
    const request = toAnalysisRequest(UPLOAD_ID, chatbotValues);

    expect(analysisRequestSchema.safeParse(request).success).toBe(true);
    expect(request.dataset_type).toBe("CHATBOT_LOG");
    expect(request.chatbot_config).toEqual({
      role_column: "direction",
      role_user_values: ["Kullanıcı", "user"],
      session_id_column: "session_id",
      timestamp_column: "message_time_tr",
      message_type_column: "message_type",
      allowed_message_types: ["text"],
    });
  });

  it("opsiyonel kolonlar seçilmediyse null gönderir", () => {
    const request = toAnalysisRequest(UPLOAD_ID, {
      ...chatbotValues,
      session_id_column: "",
      timestamp_column: "",
      message_type_column: "",
    });

    expect(request.chatbot_config).toEqual({
      role_column: "direction",
      role_user_values: ["Kullanıcı", "user"],
      session_id_column: null,
      timestamp_column: null,
      message_type_column: null,
      allowed_message_types: null,
    });
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
