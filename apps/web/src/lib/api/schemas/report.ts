import * as z from "zod";

import { modelIdSchema, promptVersionSchema } from "./analysis";
import { LIMITS } from "./common";

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

export const sourceSummarySchema = z.object({
  filename: z.string(),
  sheet_name: z.string(),
  text_column: z.string(),
  total_rows: z.int().nonnegative(),
});

export type SourceSummary = z.infer<typeof sourceSummarySchema>;

/**
 * Ön işleme istatistikleri. README'nin "toplam, işlenen, elenen ve geçersiz
 * kayıt sayıları" çıktısını besler.
 */
export const preprocessingSummarySchema = z.object({
  /** Analize giren kayıt sayısı (boş/sistem kayıtları elendikten sonra). */
  analyzed_count: z.int().nonnegative(),
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
  id: z.string(),
  canonical_question: z.string(),
  count: z.int().nonnegative(),
  /** 0-100 aralığında yüzde. Biçimlendirme için lib/format formatPercentage kullanılır. */
  percentage: z.number().min(0).max(100),
  /** 0-1 aralığında model güven skoru. */
  confidence: z.number().min(0).max(1),
  /** PII redakte edilmiş, kırpılmış gerçek kullanıcı mesajları. */
  redacted_examples: z.array(z.string()).default([]),
});

export type TopQuestion = z.infer<typeof topQuestionSchema>;

export const themeSchema = z.object({
  id: z.string(),
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
    schema_version: z.string(),
    analysis_id: z.uuid(),
    status: z.literal("completed"),
    generated_at: z.iso.datetime(),

    source_summary: sourceSummarySchema,
    preprocessing_summary: preprocessingSummarySchema,

    top_questions: z.array(topQuestionSchema),
    themes: z.array(themeSchema),

    executive_summary: z.string(),
    warnings: z.array(analysisWarningSchema).default([]),

    /** İzlenebilirlik: hangi model ve hangi prompt sürümü bu sonucu üretti. */
    model: modelIdSchema,
    prompt_version: promptVersionSchema,
    prompt_hash: z.string(),

    token_usage: tokenUsageSchema,
    estimated_cost_usd: z.number().nonnegative(),
  })
  .superRefine((report, ctx) => {
    const prep = report.preprocessing_summary;
    const considered = Math.min(report.source_summary.total_rows, LIMITS.MAX_ROWS);
    if (prep.analyzed_count + prep.discarded_count !== considered) {
      ctx.addIssue({
        code: "custom",
        path: ["preprocessing_summary"],
        message: "Analiz edilen ve elenen satır toplamı işlenen satır sayısıyla aynı olmalı.",
      });
    }
    if (prep.unique_count + prep.duplicate_count !== prep.analyzed_count) {
      ctx.addIssue({
        code: "custom",
        path: ["preprocessing_summary"],
        message: "Benzersiz ve tekrar kayıt toplamı analiz edilen kayıt sayısıyla aynı olmalı.",
      });
    }
    const usage = report.token_usage;
    if (usage.total_tokens !== usage.prompt_tokens + usage.completion_tokens) {
      ctx.addIssue({ code: "custom", path: ["token_usage"], message: "Token toplamı tutarsız." });
    }

    const questionIds = new Set(report.top_questions.map((question) => question.id));
    report.themes.forEach((theme, themeIndex) => {
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

    const truncated = report.source_summary.total_rows > LIMITS.MAX_ROWS;
    const hasWarning = report.warnings.some((warning) => warning.code === "ROW_LIMIT_TRUNCATED");
    if (truncated !== hasWarning) {
      ctx.addIssue({
        code: "custom",
        path: ["warnings"],
        message: "ROW_LIMIT_TRUNCATED uyarısı satır sınırıyla uyumlu olmalı.",
      });
    }
  });

export type AnalysisReport = z.infer<typeof analysisReportSchema>;
