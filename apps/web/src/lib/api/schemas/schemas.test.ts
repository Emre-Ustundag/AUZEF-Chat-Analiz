import { describe, expect, it } from "vitest";

import {
  analysisJobSchema,
  analysisReportSchema,
  analysisRequestSchema,
  isAnalysisSettled,
  isUploadSettled,
  isRetryableError,
  percentageHalfUp,
  problemDetailsSchema,
  uploadSchema,
} from "./index";

const ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const NOW = "2026-08-11T10:00:00Z";

describe("uploadSchema", () => {
  it("profil ve hata alanları yokken varsayılan null uygular", () => {
    const result = uploadSchema.parse({
      upload_id: ID,
      status: "queued",
      filename: "chat.xlsx",
      size_bytes: 133_169_152,
      created_at: NOW,
    });

    expect(result.profile).toBeNull();
    expect(result.error).toBeNull();
  });

  it("geçersiz uuid'yi reddeder", () => {
    const result = uploadSchema.safeParse({
      upload_id: "gecersiz",
      status: "queued",
      filename: "chat.xlsx",
      size_bytes: 1,
      created_at: NOW,
    });

    expect(result.success).toBe(false);
  });
});

describe("problemDetailsSchema", () => {
  it("ADR §7'deki hata gövdesini ayrıştırır", () => {
    const result = problemDetailsSchema.parse({
      type: "/errors/upload-too-large",
      title: "Dosya boyutu sınırı aşıldı",
      status: 413,
      code: "UPLOAD_TOO_LARGE",
      detail: "En fazla 150 MB .xlsx yüklenebilir.",
      trace_id: ID,
    });

    expect(result.errors).toEqual([]);
    expect(result.retry_after).toBeUndefined();
  });

  // ADR-0002 #6: retry_after 429 dışında YOK; `null` değil. Zod `.optional()`
  // null'ı reddeder, yani backend null yollarsa bu şema düşer ve toApiError
  // her hatayı INTERNAL_ERROR'a çevirir.
  it("retry_after: null reddedilir, alanın yokluğu kabul edilir", () => {
    const base = {
      type: "/errors/job-not-found",
      title: "İşlem bulunamadı",
      status: 404,
      code: "JOB_NOT_FOUND",
      detail: "Analiz kaydı yok.",
      trace_id: ID,
    };

    expect(problemDetailsSchema.safeParse(base).success).toBe(true);
    expect(problemDetailsSchema.safeParse({ ...base, retry_after: null }).success).toBe(false);
  });

  it("status hata kodunun kayıtlı HTTP statüsüyle aynı olmalıdır", () => {
    const problem = {
      type: "/errors/internal-error",
      title: "Beklenmeyen hata",
      status: 400,
      code: "INTERNAL_ERROR",
      detail: "x",
      trace_id: ID,
    };

    expect(problemDetailsSchema.safeParse(problem).success).toBe(false);
  });

  it("retry_after yalnızca rate limit kodunda zorunludur", () => {
    const rateLimited = {
      type: "/errors/provider-rate-limited",
      title: "Sağlayıcı istek sınırına ulaşıldı",
      status: 429,
      code: "PROVIDER_RATE_LIMITED",
      detail: "x",
      trace_id: ID,
    };
    const internal = {
      type: "/errors/internal-error",
      title: "Beklenmeyen hata",
      status: 500,
      code: "INTERNAL_ERROR",
      detail: "x",
      trace_id: ID,
    };

    expect(problemDetailsSchema.safeParse(rateLimited).success).toBe(false);
    expect(problemDetailsSchema.safeParse({ ...rateLimited, retry_after: 60 }).success).toBe(true);
    expect(problemDetailsSchema.safeParse({ ...internal, retry_after: 60 }).success).toBe(false);
  });

  it("trace_id eksikse reddeder", () => {
    const result = problemDetailsSchema.safeParse({
      type: "/errors/job-not-found",
      title: "İşlem bulunamadı",
      status: 404,
      code: "JOB_NOT_FOUND",
      detail: "Analiz kaydı yok.",
    });

    expect(result.success).toBe(false);
  });

  it("bilinmeyen hata kodunu reddeder", () => {
    const result = problemDetailsSchema.safeParse({
      type: "/errors/x",
      title: "x",
      status: 500,
      code: "UYDURMA_KOD",
      detail: "x",
      trace_id: ID,
    });

    expect(result.success).toBe(false);
  });
});

describe("analysisRequestSchema", () => {
  const valid = {
    upload_id: ID,
    sheet_name: "Sayfa1",
    text_column: "mesaj",
    row_filters: [],
    model: "anthropic/claude-sonnet-4.6",
    prompt_version: "faq_analysis/v1",
    top_n: 20,
    max_cost_usd: 5,
  };

  it("geçerli isteği kabul eder", () => {
    expect(analysisRequestSchema.safeParse(valid).success).toBe(true);
  });

  it("top_n sıfır olamaz", () => {
    expect(analysisRequestSchema.safeParse({ ...valid, top_n: 0 }).success).toBe(false);
  });

  it("maliyet sınırı pozitif olmalıdır", () => {
    expect(analysisRequestSchema.safeParse({ ...valid, max_cost_usd: 0 }).success).toBe(false);
  });

  it("boş kolon adını reddeder", () => {
    expect(analysisRequestSchema.safeParse({ ...valid, text_column: "" }).success).toBe(false);
  });

  it("whitelist dışındaki model ve prompt sürümünü reddeder", () => {
    expect(analysisRequestSchema.safeParse({ ...valid, model: "unknown/model" }).success).toBe(
      false,
    );
    expect(
      analysisRequestSchema.safeParse({ ...valid, prompt_version: "faq_analysis/v2" }).success,
    ).toBe(false);
  });

  it("OpenRouter anahtarını gövdede taşımaz", () => {
    // ADR §6/§9: anahtar yalnızca X-OpenRouter-Key header'ında gider.
    // Şemada böyle bir alan bulunmamalı.
    const parsed = analysisRequestSchema.parse(valid);
    expect(Object.keys(parsed)).not.toContain("api_key");
    expect(Object.keys(parsed)).not.toContain("openrouter_key");
  });
});

describe("analysisJobSchema", () => {
  it("ilerleme durumunu ayrıştırır", () => {
    const result = analysisJobSchema.parse({
      analysis_id: ID,
      status: "analyzing",
      progress: 42.5,
      created_at: NOW,
      updated_at: NOW,
    });

    expect(result.estimated_seconds_remaining).toBeNull();
  });

  it("tanımsız durumu reddeder", () => {
    const result = analysisJobSchema.safeParse({
      analysis_id: ID,
      status: "uydurma_durum",
      progress: 1,
      created_at: NOW,
      updated_at: NOW,
    });

    expect(result.success).toBe(false);
  });

  it("100'den büyük ilerlemeyi reddeder", () => {
    const result = analysisJobSchema.safeParse({
      analysis_id: ID,
      status: "analyzing",
      progress: 101,
      created_at: NOW,
      updated_at: NOW,
    });

    expect(result.success).toBe(false);
  });
});

describe("analysisReportSchema", () => {
  const report = {
    schema_version: "1.0",
    analysis_id: ID,
    status: "completed",
    generated_at: NOW,
    source_summary: {
      filename: "chat.xlsx",
      sheet_name: "Sayfa1",
      text_column: "mesaj",
      total_rows: 5000,
    },
    preprocessing_summary: {
      analyzed_count: 4800,
      discarded_count: 200,
      duplicate_count: 1200,
      redacted_count: 340,
      unique_count: 3600,
    },
    top_questions: [
      {
        id: "q1",
        canonical_question: "Sınav tarihleri ne zaman açıklanacak?",
        count: 1240,
        percentage: 25.8,
        confidence: 0.92,
        redacted_examples: ["sınav ne zaman"],
      },
    ],
    themes: [
      {
        id: "t1",
        name: "Sınav takvimi",
        count: 1240,
        percentage: 25.8,
        related_question_ids: ["q1"],
      },
    ],
    executive_summary: "Mesajların dörtte biri sınav takvimiyle ilgili.",
    warnings: [],
    model: "anthropic/claude-sonnet-4.6",
    prompt_version: "faq_analysis/v1",
    prompt_hash: "abc123",
    token_usage: {
      prompt_tokens: 100_000,
      completion_tokens: 5_000,
      total_tokens: 105_000,
    },
    estimated_cost_usd: 1.2345,
  };

  it("ADR §8'deki tam raporu ayrıştırır", () => {
    expect(analysisReportSchema.safeParse(report).success).toBe(true);
  });

  it("yalnızca completed durumunu kabul eder", () => {
    // /result endpoint'i tamamlanmamış job için rapor dönmemeli.
    const result = analysisReportSchema.safeParse({
      ...report,
      status: "analyzing",
    });

    expect(result.success).toBe(false);
  });

  it("güven skorunu 0-1 aralığıyla sınırlar", () => {
    const result = analysisReportSchema.safeParse({
      ...report,
      top_questions: [{ ...report.top_questions[0], confidence: 92 }],
    });

    expect(result.success).toBe(false);
  });

  it("yüzdeyi 0-100 aralığıyla sınırlar", () => {
    const result = analysisReportSchema.safeParse({
      ...report,
      themes: [{ ...report.themes[0], percentage: 148 }],
    });

    expect(result.success).toBe(false);
  });

  it("tarihsel model kimliğini kabul eder, prompt sürümünü dondurur", () => {
    expect(analysisReportSchema.safeParse({ ...report, model: "retired/model-v1" }).success).toBe(
      true,
    );
    expect(
      analysisReportSchema.safeParse({ ...report, prompt_version: "faq_analysis/v2" }).success,
    ).toBe(false);
  });

  it("tüketici gelecekteki warning kodlarına açıktır", () => {
    expect(
      analysisReportSchema.safeParse({
        ...report,
        warnings: [{ code: "FUTURE_NON_FATAL_WARNING", message: "Kullanıcıya hazır uyarı." }],
      }).success,
    ).toBe(true);
  });

  it("rapor sürümünü 1.0'a sabitler", () => {
    expect(analysisReportSchema.safeParse({ ...report, schema_version: "garbage" }).success).toBe(
      false,
    );
  });

  it("count, yüzde ve kimlik invariant'larını uygular", () => {
    expect(
      analysisReportSchema.safeParse({
        ...report,
        preprocessing_summary: { ...report.preprocessing_summary, redacted_count: 4801 },
      }).success,
    ).toBe(false);
    expect(
      analysisReportSchema.safeParse({
        ...report,
        top_questions: [{ ...report.top_questions[0], count: 4801, percentage: 100 }],
      }).success,
    ).toBe(false);
    expect(
      analysisReportSchema.safeParse({
        ...report,
        themes: [{ ...report.themes[0], percentage: 25.7 }],
      }).success,
    ).toBe(false);
    expect(
      analysisReportSchema.safeParse({
        ...report,
        top_questions: [report.top_questions[0], report.top_questions[0]],
      }).success,
    ).toBe(false);
  });

  it("yüzdeleri bir ondalığa half-up yuvarlar", () => {
    expect(percentageHalfUp(1, 16)).toBe(6.3);
  });
});

describe("durum yardımcıları", () => {
  it("terminal analiz durumlarında polling'i durdurur", () => {
    expect(isAnalysisSettled("completed")).toBe(true);
    expect(isAnalysisSettled("failed")).toBe(true);
    expect(isAnalysisSettled("cancelled")).toBe(true);
  });

  it("devam eden durumlarda polling'i sürdürür", () => {
    expect(isAnalysisSettled("queued")).toBe(false);
    expect(isAnalysisSettled("analyzing")).toBe(false);
    expect(isAnalysisSettled("aggregating")).toBe(false);
  });

  it("upload terminal durumlarını ayırt eder", () => {
    expect(isUploadSettled("ready")).toBe(true);
    expect(isUploadSettled("failed")).toBe(true);
    expect(isUploadSettled("validating")).toBe(false);
  });

  it("yalnızca sağlayıcı kaynaklı hatalarda tekrar denemeye izin verir", () => {
    expect(isRetryableError("PROVIDER_RATE_LIMITED")).toBe(true);
    expect(isRetryableError("PROVIDER_TIMEOUT")).toBe(true);
    // Kullanıcının dosyası geçersizse tekrar denemek anlamsız.
    expect(isRetryableError("UPLOAD_INVALID_TYPE")).toBe(false);
    expect(isRetryableError("UPLOAD_TOO_LARGE")).toBe(false);
  });

  // ADR-0002 #1: dördü de kullanıcı girdisi hatası; aynı isteği tekrarlamak
  // aynı hatayı üretir.
  it.each([
    "REQUEST_VALIDATION",
    "INVALID_MODEL",
    "INVALID_PROMPT",
    "COST_LIMIT_EXCEEDED",
  ] as const)("%s tekrar denenebilir değildir", (code) => {
    expect(isRetryableError(code)).toBe(false);
  });
});
