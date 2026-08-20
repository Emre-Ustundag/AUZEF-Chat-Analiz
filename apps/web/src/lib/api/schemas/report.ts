import * as z from "zod";

import {
  analysisModeSchema,
  conversationConfigSchema,
  pricingSnapshotSchema,
  promptVersionSchema,
  rowFilterSchema,
} from "./analysis";

/** Yüzdeyi bir ondalığa exact half-up yuvarlar; backend ile aynı kural. */
export function percentageHalfUp(count: number, total: number): number {
  if (total === 0) return 0;
  const tenths = Math.floor((2 * count * 1000 + total) / (2 * total));
  return tenths / 10;
}

/**
 * AnalysisReport — ADR §8 "Sonuç modeli".
 *
 * GET /api/v1/analyses/{analysis_id}/result yalnızca status "completed"
 * olduğunda bu gövdeyi döner.
 *
 * Sayısal alanların tamamı backend'de mesajların gerçek frekanslarından
 * deterministik olarak hesaplanır (ADR §4 "Önemli analiz kararı"). LLM
 * yalnızca kayıt kimliklerini kategorilere eşler, sayı üretmez. Arayüz de
 * bu sayıları yeniden hesaplamaz, olduğu gibi gösterir.
 */

export const sourceSummarySchema = z
  .object({
    filename: z.string(),
    sheet_name: z.string(),
    text_column: z.string(),
    row_filters: z.array(rowFilterSchema).default([]),
    analysis_mode: analysisModeSchema.default("message"),
    conversation_config: conversationConfigSchema.nullable().default(null),
    total_rows: z.int().nonnegative(),
  })
  .superRefine((source, ctx) => {
    if (
      (source.analysis_mode === "contextual_user_turns") !==
      (source.conversation_config !== null)
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["conversation_config"],
        message: "Rapor analiz modu ile konuşma ayarı uyumsuz.",
      });
    }
  });

export type SourceSummary = z.infer<typeof sourceSummarySchema>;

/**
 * Ön işleme istatistikleri. README'nin "toplam, işlenen, elenen ve geçersiz
 * kayıt sayıları" çıktısını besler.
 */
export const preprocessingSummarySchema = z.object({
  /** Analize giren kayıt sayısı (boş/sistem kayıtları elendikten sonra). */
  analyzed_count: z.int().nonnegative(),
  /** Bağlama uygun olan, fakat hedef/sonuç sayımlarına girmeyen kayıtlar. */
  context_only_count: z.int().nonnegative().default(0),
  /** Boş veya analiz dışı olduğu için elenen kayıtlar. */
  discarded_count: z.int().nonnegative(),
  /** Exact hash ile tekilleştirilen kayıt sayısı; frekansları korunur. */
  duplicate_count: z.int().nonnegative(),
  /** İçinde PII maskelenmiş kayıt sayısı. */
  redacted_count: z.int().nonnegative(),
  /** Tekilleştirme sonrası LLM'e giden benzersiz kayıt sayısı. */
  unique_count: z.int().nonnegative(),
});

export type PreprocessingSummary = z.infer<typeof preprocessingSummarySchema>;

export const topQuestionSchema = z.object({
  id: z.string().min(1),
  canonical_question: z.string(),
  count: z.int().nonnegative(),
  /** 0-100 aralığında yüzde. Biçimlendirme için lib/format formatPercentage kullanılır. */
  percentage: z.number().min(0).max(100),
  /** PII redakte edilmiş, kırpılmış gerçek kullanıcı mesajları. */
  redacted_examples: z.array(z.string()).default([]),
});

export type TopQuestion = z.infer<typeof topQuestionSchema>;

export const themeSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  /** Temaya düşen TÜM mesajlar — top_n kırpmasından etkilenmez. */
  count: z.int().nonnegative(),
  percentage: z.number().min(0).max(100),
  /**
   * Bu temaya bağlı top_questions[].id değerleri.
   *
   * ADR-0002 #5: yalnızca raporda GERÇEKTEN yer alan sorulara filtrelenir;
   * aksi hâlde arayüz çözemeyeceği bir kimliğe bağlantı verirdi. `count` ve
   * `percentage` ise temanın gerçek büyüklüğünü yansıtmaya devam eder, yani
   * top_n küçükken bir temanın adedi listelenen sorularının toplamından
   * büyük olabilir. Bu beklenen davranıştır.
   */
  related_question_ids: z.array(z.string()).default([]),
});

export type Theme = z.infer<typeof themeSchema>;

export const tokenUsageSchema = z.object({
  prompt_tokens: z.int().nonnegative(),
  completion_tokens: z.int().nonnegative(),
  total_tokens: z.int().nonnegative(),
  cached_tokens: z.int().nonnegative().default(0),
  cache_write_tokens: z.int().nonnegative().default(0),
});

export type TokenUsage = z.infer<typeof tokenUsageSchema>;

/**
 * Backend'in bugün yayabileceği uyarı kodları (ADR-0002 #2).
 *
 * Sözlük üretici tarafında KAPALI, tüketici tarafında AÇIK: `code` alanı tel
 * üstünde serbest `string` kalır. Enum yapılsaydı backend'e eklenen her yeni
 * uyarı `apiRequest` içinde şema hatası, yani 500 muadili bir kırılma
 * üretirdi — oysa uyarı işi durdurmayan bir sinyaldir.
 */
export const KNOWN_WARNING_CODES = [
  "ROW_LIMIT_TRUNCATED",
  "CHUNK_PARTIAL_FAILURE",
  "LOW_CONFIDENCE_THEMES",
  "PII_REDACTION_INCOMPLETE",
  "COST_LIMIT_APPROACHED",
] as const;

export type KnownWarningCode = (typeof KNOWN_WARNING_CODES)[number];

/** Bilinen kodlarda otomatik tamamlama, bilinmeyende serbest string. */
export type WarningCode = KnownWarningCode | (string & {});

/**
 * Analiz sırasında oluşan ama işi durdurmayan uyarılar; örneğin satır
 * sınırının aşılması veya bir chunk'ın kısmen başarısız olması.
 */
export const analysisWarningSchema = z.object({
  code: z.string(),
  /**
   * KULLANICIYA HAZIR Türkçe metin.
   *
   * `common.ts` "ham backend metni kullanıcıya doğrudan basılmaz" diyor;
   * uyarılar bunun belgelenmiş tek istisnasıdır (ADR-0002 #2). `code` serbest
   * string olduğu için arayüz bilinmeyen bir koda mesaj uyduramaz ve
   * gizlenen bir uyarı, kusurlu bir uyarıdan kötüdür.
   */
  message: z.string(),
});

export type AnalysisWarning = z.infer<typeof analysisWarningSchema>;

export const analysisReportSchema = z
  .object({
    schema_version: z.literal("1.0"),
    analysis_id: z.uuid(),
    status: z.literal("completed"),
    generated_at: z.iso.datetime(),

    source_summary: sourceSummarySchema,
    preprocessing_summary: preprocessingSummarySchema,

    top_questions: z.array(topQuestionSchema),
    themes: z.array(themeSchema),

    executive_summary: z.string(),
    warnings: z.array(analysisWarningSchema).default([]),

    /** Tarihsel raporlar aktif whitelist değişse de okunabilsin diye serbest kimlik. */
    model: z.string().min(1),
    prompt_version: promptVersionSchema,
    prompt_hash: z.string(),

    token_usage: tokenUsageSchema,
    estimated_cost_usd: z.number().nonnegative(),
    cost_source: z.enum(["provider", "calculated"]).default("calculated"),
    pricing_snapshot: pricingSnapshotSchema.nullable().default(null),
  })
  .superRefine((report, ctx) => {
    const prep = report.preprocessing_summary;
    // ANALİZ KIRPMAZ — backend aynası `schemas/report.py`.
    //
    // Burada eskiden `Math.min(total_rows, LIMITS.MAX_ROWS)` yazıyordu ve
    // backend'deki aynı varsayımla birlikte 100.000 satırı aşan her raporu
    // reddediyordu. Backend tek başına düzeltilseydi arayüz raporu yine
    // reddederdi; iki tarafın birlikte değişmesi gerekiyordu.
    if (
      prep.analyzed_count + prep.context_only_count + prep.discarded_count !==
      report.source_summary.total_rows
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["preprocessing_summary"],
        message: "Analiz, bağlam ve elenen satır toplamı işlenen satır sayısıyla aynı olmalı.",
      });
    }
    if (prep.unique_count + prep.duplicate_count !== prep.analyzed_count) {
      ctx.addIssue({
        code: "custom",
        path: ["preprocessing_summary"],
        message: "Benzersiz ve tekrar kayıt toplamı analiz edilen kayıt sayısıyla aynı olmalı.",
      });
    }
    if (prep.redacted_count > prep.analyzed_count) {
      ctx.addIssue({
        code: "custom",
        path: ["preprocessing_summary", "redacted_count"],
        message: "Redakte kayıt sayısı analiz edilen kayıt sayısını aşamaz.",
      });
    }
    const usage = report.token_usage;
    if (usage.total_tokens !== usage.prompt_tokens + usage.completion_tokens) {
      ctx.addIssue({ code: "custom", path: ["token_usage"], message: "Token toplamı tutarsız." });
    }
    if (usage.cached_tokens + usage.cache_write_tokens > usage.prompt_tokens) {
      ctx.addIssue({
        code: "custom",
        path: ["token_usage"],
        message: "Cache tokenları prompt_tokens değerini aşamaz.",
      });
    }

    const questionIdList = report.top_questions.map((question) => question.id);
    const questionIds = new Set(questionIdList);
    if (questionIds.size !== questionIdList.length) {
      ctx.addIssue({
        code: "custom",
        path: ["top_questions"],
        message: "Soru id'leri benzersiz olmalı.",
      });
    }

    const themeIdList = report.themes.map((theme) => theme.id);
    if (new Set(themeIdList).size !== themeIdList.length) {
      ctx.addIssue({
        code: "custom",
        path: ["themes"],
        message: "Tema id'leri benzersiz olmalı.",
      });
    }

    const validateCount = (
      item: { count: number; percentage: number },
      path: ["top_questions" | "themes", number],
    ) => {
      if (item.count > prep.analyzed_count) {
        ctx.addIssue({
          code: "custom",
          path: [...path, "count"],
          message: "Adet analiz edilen kayıt sayısını aşamaz.",
        });
      }
      if (item.percentage !== percentageHalfUp(item.count, prep.analyzed_count)) {
        ctx.addIssue({
          code: "custom",
          path: [...path, "percentage"],
          message: "Yüzde adetten exact half-up kuralıyla türetilmeli.",
        });
      }
    };
    report.top_questions.forEach((question, index) =>
      validateCount(question, ["top_questions", index]),
    );
    report.themes.forEach((theme, index) => validateCount(theme, ["themes", index]));

    report.themes.forEach((theme, themeIndex) => {
      if (new Set(theme.related_question_ids).size !== theme.related_question_ids.length) {
        ctx.addIssue({
          code: "custom",
          path: ["themes", themeIndex, "related_question_ids"],
          message: "Tema aynı soru id'sini tekrarlayamaz.",
        });
      }
      theme.related_question_ids.forEach((id, idIndex) => {
        if (!questionIds.has(id)) {
          ctx.addIssue({
            code: "custom",
            path: ["themes", themeIndex, "related_question_ids", idIndex],
            message: "Tema yalnızca raporda bulunan sorulara bağlanabilir.",
          });
        }
      });
    });

    // `ROW_LIMIT_TRUNCATED` ↔ satır sınırı kontrolü KALDIRILDI: kesme
    // olmadığı için backend uyarıyı üretmiyor. Kod `KNOWN_WARNING_CODES`
    // içinde kalıyor — tüketici-açık taraf, eski raporlarda hâlâ bu kodu
    // görebilir ve onları reddetmemeli.
  });

export type AnalysisReport = z.infer<typeof analysisReportSchema>;
