import * as z from "zod";

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
  count: z.int().nonnegative(),
  percentage: z.number().min(0).max(100),
  /** Bu temaya bağlı top_questions[].id değerleri. */
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
 * Analiz sırasında oluşan ama işi durdurmayan uyarılar; örneğin satır
 * sınırının aşılması veya bir chunk'ın kısmen başarısız olması.
 */
export const analysisWarningSchema = z.object({
  code: z.string(),
  message: z.string(),
});

export type AnalysisWarning = z.infer<typeof analysisWarningSchema>;

export const analysisReportSchema = z.object({
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
  model: z.string(),
  prompt_version: z.string(),
  prompt_hash: z.string(),

  token_usage: tokenUsageSchema,
  estimated_cost_usd: z.number().nonnegative(),
});

export type AnalysisReport = z.infer<typeof analysisReportSchema>;
