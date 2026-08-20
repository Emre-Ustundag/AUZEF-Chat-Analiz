import * as z from "zod";

import { datasetTypeSchema, promptVersionSchema } from "./analysis";
import { LIMITS } from "./common";

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
  /**
   * Analize giren kayıtlardaki benzersiz oturum sayısı. Yalnızca
   * `CHATBOT_LOG` ön ayarında ve oturum kolonu seçiliyken dolu; backend her
   * cevapta yazar (aksi hâlde `null`).
   */
  session_count: z.int().nonnegative().nullable().optional(),
});

export type PreprocessingSummary = z.infer<typeof preprocessingSummarySchema>;

export const topQuestionSchema = z.object({
  id: z.string().min(1),
  canonical_question: z.string(),
  count: z.int().nonnegative(),
  /** 0-100 aralığında yüzde. Biçimlendirme için lib/format formatPercentage kullanılır. */
  percentage: z.number().min(0).max(100),
  /** 0-1 aralığında model güven skoru. */
  confidence: z.number().min(0).max(1),
  /** PII redakte edilmiş, kırpılmış gerçek kullanıcı mesajları. */
  redacted_examples: z.array(z.string()).default([]),
  /** Bu soruyu soran benzersiz oturum sayısı (`CHATBOT_LOG` + oturum kolonu). */
  session_count: z.int().nonnegative().nullable().optional(),
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
  /** Bu temaya düşen benzersiz oturum sayısı (`CHATBOT_LOG` + oturum kolonu). */
  session_count: z.int().nonnegative().nullable().optional(),
});

export type Theme = z.infer<typeof themeSchema>;

/** Tek bir günün adedi; tarih her zaman UTC `YYYY-MM-DD`. */
export const trendPointSchema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Tarih YYYY-MM-DD biçiminde olmalı."),
  count: z.int().nonnegative(),
});

export type TrendPoint = z.infer<typeof trendPointSchema>;

/** Bir soru veya temanın günlük kırılımı; `id` ilgili listeye bağlanır. */
export const trendSeriesSchema = z.object({
  id: z.string().min(1),
  daily: z.array(trendPointSchema),
});

export type TrendSeries = z.infer<typeof trendSeriesSchema>;

/**
 * Günlük zaman serisi (`CHATBOT_LOG` + zaman kolonu seçiliyken).
 *
 * Tarihler her seride artan sıralı ve benzersizdir. Zaman damgası
 * çözümlenemeyen kayıtlar seriye girmez; bu yüzden serilerin toplamı ilgili
 * `count` değerinden küçük olabilir, asla büyük olamaz. Bu kurallar raporun
 * `superRefine` bloğunda doğrulanır (backend `_time_series_invariants` aynası).
 */
export const analysisTimeSeriesSchema = z.object({
  /** Analize giren tüm mesajların günlük toplamı. */
  daily_totals: z.array(trendPointSchema),
  /** Yalnızca raporda yer alan (`top_questions`) soruların kırılımı. */
  question_trends: z.array(trendSeriesSchema),
  /** Tüm temaların kırılımı. */
  theme_trends: z.array(trendSeriesSchema),
});

export type AnalysisTimeSeries = z.infer<typeof analysisTimeSeriesSchema>;

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
    schema_version: z.literal("1.0"),
    analysis_id: z.uuid(),
    status: z.literal("completed"),
    generated_at: z.iso.datetime(),

    /** Analizin çalıştığı veri kümesi ön ayarı; eski raporlarda bulunmayabilir. */
    dataset_type: datasetTypeSchema.optional(),

    source_summary: sourceSummarySchema,
    preprocessing_summary: preprocessingSummarySchema,

    top_questions: z.array(topQuestionSchema),
    themes: z.array(themeSchema),

    /** Günlük zaman serisi; yalnızca `CHATBOT_LOG` + zaman kolonu seçiliyken. */
    time_series: analysisTimeSeriesSchema.nullable().optional(),

    executive_summary: z.string(),
    warnings: z.array(analysisWarningSchema).default([]),

    /** Tarihsel raporlar aktif whitelist değişse de okunabilsin diye serbest kimlik. */
    model: z.string().min(1),
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

    const truncated = report.source_summary.total_rows > LIMITS.MAX_ROWS;
    const hasWarning = report.warnings.some((warning) => warning.code === "ROW_LIMIT_TRUNCATED");
    if (truncated !== hasWarning) {
      ctx.addIssue({
        code: "custom",
        path: ["warnings"],
        message: "ROW_LIMIT_TRUNCATED uyarısı satır sınırıyla uyumlu olmalı.",
      });
    }

    // ---- Oturum sayıları (backend `_session_invariants` aynası) ----
    const totalSessions = prep.session_count ?? null;
    const sessionItems: [
      "top_questions" | "themes",
      { count: number; session_count?: number | null }[],
    ][] = [
      ["top_questions", report.top_questions],
      ["themes", report.themes],
    ];
    for (const [listName, items] of sessionItems) {
      items.forEach((item, index) => {
        const sessions = item.session_count ?? null;
        if (sessions === null) return;
        if (totalSessions === null || sessions > item.count || sessions > totalSessions) {
          ctx.addIssue({
            code: "custom",
            path: [listName, index, "session_count"],
            message: "session_count, count ve toplam oturum sayısını aşamaz.",
          });
        }
      });
    }

    // ---- Zaman serisi (backend `_time_series_invariants` aynası) ----
    const series = report.time_series ?? null;
    if (series !== null) {
      if ((report.dataset_type ?? "GENERIC") === "GENERIC") {
        ctx.addIssue({
          code: "custom",
          path: ["time_series"],
          message: "time_series yalnızca CHATBOT_LOG raporlarında bulunabilir.",
        });
      }

      const checkDates = (points: { date: string; count: number }[], path: (string | number)[]) => {
        const dates = points.map((point) => point.date);
        const sortedUnique = [...new Set(dates)].sort();
        if (dates.length !== sortedUnique.length || dates.some((d, i) => d !== sortedUnique[i])) {
          ctx.addIssue({
            code: "custom",
            path,
            message: "Tarihler artan sıralı ve benzersiz olmalı.",
          });
        }
        return points.reduce((sum, point) => sum + point.count, 0);
      };

      if (checkDates(series.daily_totals, ["time_series", "daily_totals"]) > prep.analyzed_count) {
        ctx.addIssue({
          code: "custom",
          path: ["time_series", "daily_totals"],
          message: "daily_totals toplamı analyzed_count'u aşamaz.",
        });
      }

      const questionCounts = new Map(report.top_questions.map((q) => [q.id, q.count]));
      const themeCounts = new Map(report.themes.map((t) => [t.id, t.count]));
      const trendLists: ["question_trends" | "theme_trends", Map<string, number>][] = [
        ["question_trends", questionCounts],
        ["theme_trends", themeCounts],
      ];
      for (const [listName, counts] of trendLists) {
        const seen = new Set<string>();
        series[listName].forEach((trend, index) => {
          if (!counts.has(trend.id) || seen.has(trend.id)) {
            ctx.addIssue({
              code: "custom",
              path: ["time_series", listName, index, "id"],
              message: "Seri id'si raporda bulunmalı ve tekrarlanamaz.",
            });
            return;
          }
          seen.add(trend.id);
          const total = checkDates(trend.daily, ["time_series", listName, index, "daily"]);
          if (total > counts.get(trend.id)!) {
            ctx.addIssue({
              code: "custom",
              path: ["time_series", listName, index, "daily"],
              message: "Günlük toplam ilgili count değerini aşamaz.",
            });
          }
        });
      }
    }
  });

export type AnalysisReport = z.infer<typeof analysisReportSchema>;
