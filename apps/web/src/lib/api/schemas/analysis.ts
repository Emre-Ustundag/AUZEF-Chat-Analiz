import * as z from "zod";

import { problemDetailsSchema } from "./common";

/**
 * Analiz job'ı şemaları — ADR §6 "Analysis" ve §5 "Aşama B".
 */

/**
 * ADR §6 job durumları:
 *   queued -> validating/preprocessing -> analyzing -> aggregating -> completed
 * Terminal durumlar: failed, cancelled
 */
export const analysisStatusSchema = z.enum([
  "queued",
  "validating",
  "preprocessing",
  "analyzing",
  "aggregating",
  "completed",
  "failed",
  "cancelled",
]);

export type AnalysisStatus = z.infer<typeof analysisStatusSchema>;

/** İlerleme ekranında gösterilecek Türkçe aşama adları. */
export const ANALYSIS_STAGE_LABELS_TR: Record<AnalysisStatus, string> = {
  queued: "Sırada bekliyor",
  validating: "Dosya doğrulanıyor",
  preprocessing: "Veri temizleniyor",
  analyzing: "Mesajlar analiz ediliyor",
  aggregating: "Sonuçlar birleştiriliyor",
  completed: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

/** Sırası ilerleme göstergesindeki adım sırasını belirler. */
export const ANALYSIS_ACTIVE_STAGES = [
  "queued",
  "validating",
  "preprocessing",
  "analyzing",
  "aggregating",
] as const satisfies readonly AnalysisStatus[];

const TERMINAL_STATUSES: readonly AnalysisStatus[] = ["completed", "failed", "cancelled"];

/**
 * Terminal durumlar polling'i durdurur. TanStack Query'nin refetchInterval'ı
 * bu fonksiyona bakar; yanlış olursa 45 dakikalık bir job bittikten sonra da
 * sonsuza kadar istek atmaya devam eder.
 */
export function isAnalysisSettled(status: AnalysisStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

/**
 * BE-01'de dondurulan OpenRouter model whitelist'i.
 *
 * Bu değerler request, model kataloğu ve rapor şemalarında aynı anda
 * kullanılır. Böylece backend'in desteklemediği bir model tel üstünde
 * "herhangi bir string" gibi görünemez.
 */
export const modelIdSchema = z.enum([
  "anthropic/claude-sonnet-4.6",
  "openai/gpt-4.1-mini",
  "google/gemini-2.5-flash",
]);

export type ModelId = z.infer<typeof modelIdSchema>;

/** BE-01'de dondurulan, sürümlenmiş prompt whitelist'i. */
export const promptVersionSchema = z.enum([
  "faq_analysis/v1",
  "faq_analysis/v2",
  "faq_analysis/v3",
  "faq_analysis/v4",
]);

export type PromptVersion = z.infer<typeof promptVersionSchema>;

export const analysisModeSchema = z.enum(["message", "contextual_user_turns"]);

export type AnalysisMode = z.infer<typeof analysisModeSchema>;

const conversationMappingValuesSchema = z
  .array(z.string().trim().min(1, "Eşleme değeri boş olamaz.").max(512))
  .min(1, "En az bir eşleme değeri girilmelidir.")
  .max(20, "En fazla 20 eşleme değeri girilebilir.")
  .superRefine((values, ctx) => {
    if (new Set(values).size !== values.length) {
      ctx.addIssue({ code: "custom", message: "Aynı eşleme değeri tekrarlanamaz." });
    }
  });

export const conversationConfigSchema = z
  .object({
    session_id_column: z.string().trim().min(1, "Oturum kimliği kolonu seçilmelidir.").max(512),
    message_order_column: z.string().trim().min(1, "Mesaj sırası kolonu seçilmelidir.").max(512),
    role_column: z.string().trim().min(1, "Gönderen rolü kolonu seçilmelidir.").max(512),
    message_type_column: z.string().trim().min(1, "Mesaj türü kolonu seçilmelidir.").max(512),
    user_role_values: conversationMappingValuesSchema.default(["Kullanıcı"]),
    assistant_role_values: conversationMappingValuesSchema.default(["Bot"]),
    include_assistant_context: z.boolean().default(false),
    // Quick reply varsayılan olarak yalnız bağlamdır; kullanıcı isterse
    // analiz hedeflerine açıkça ekleyebilir.
    target_message_types: conversationMappingValuesSchema.default(["text"]),
    context_message_types: conversationMappingValuesSchema.default([
      "text",
      "quick_reply",
      "single-choice",
    ]),
    max_context_turns: z
      .int("Bağlam turu sayısı tam sayı olmalıdır.")
      .min(1, "En az 1 önceki tur kullanılmalıdır.")
      .max(8, "En fazla 8 önceki tur kullanılabilir.")
      .default(4),
    max_context_tokens: z
      .int("Bağlam token sınırı tam sayı olmalıdır.")
      .min(128, "Bağlam token sınırı en az 128 olmalıdır.")
      .max(4000, "Bağlam token sınırı en fazla 4000 olabilir.")
      .default(1000),
  })
  .superRefine((config, ctx) => {
    const columns = [
      config.session_id_column,
      config.message_order_column,
      config.role_column,
      config.message_type_column,
    ];
    if (new Set(columns).size !== columns.length) {
      ctx.addIssue({
        code: "custom",
        path: ["message_type_column"],
        message: "Oturum, sıra, rol ve mesaj türü kolonları farklı olmalıdır.",
      });
    }

    const userRoles = new Set(config.user_role_values);
    if (config.assistant_role_values.some((value) => userRoles.has(value))) {
      ctx.addIssue({
        code: "custom",
        path: ["assistant_role_values"],
        message: "Aynı rol değeri hem kullanıcı hem bot olamaz.",
      });
    }

    const contextTypes = new Set(config.context_message_types);
    if (config.target_message_types.some((value) => !contextTypes.has(value))) {
      ctx.addIssue({
        code: "custom",
        path: ["target_message_types"],
        message: "Analiz edilecek mesaj türleri bağlam mesaj türlerinin alt kümesi olmalıdır.",
      });
    }
  });

export type ConversationConfig = z.infer<typeof conversationConfigSchema>;

export const rowFilterSchema = z.object({
  column: z.string().trim().min(1, "Filtre kolonu boş olamaz.").max(512),
  allowed_values: z
    .array(z.string().trim().min(1, "Filtre değeri boş olamaz.").max(512))
    .min(1, "En az bir filtre değeri girilmelidir.")
    .max(20, "Bir filtrede en fazla 20 değer olabilir.")
    .superRefine((values, ctx) => {
      if (new Set(values).size !== values.length) {
        ctx.addIssue({ code: "custom", message: "Aynı filtre değeri tekrarlanamaz." });
      }
    }),
});

export type RowFilter = z.infer<typeof rowFilterSchema>;

/**
 * POST /api/v1/analyses gövdesi.
 *
 * OpenRouter anahtarı BU GÖVDEDE YER ALMAZ. ADR §6/§9 gereği yalnızca
 * X-OpenRouter-Key header'ında taşınır, loglarda redakte edilir ve
 * PostgreSQL'e hiçbir şekilde yazılmaz.
 */
const analysisRequestFieldsSchema = z.object({
  upload_id: z.uuid(),
  sheet_name: z.string().min(1, "Sayfa seçilmelidir."),
  text_column: z.string().min(1, "Analiz edilecek metin kolonu seçilmelidir."),
  row_filters: z
    .array(rowFilterSchema)
    .max(5, "En fazla 5 satır filtresi tanımlanabilir.")
    .superRefine((filters, ctx) => {
      const columns = filters.map((rowFilter) => rowFilter.column);
      if (new Set(columns).size !== columns.length) {
        ctx.addIssue({ code: "custom", message: "Aynı kolon birden fazla filtrelenemez." });
      }
    }),
  analysis_mode: analysisModeSchema.default("message"),
  conversation_config: conversationConfigSchema.nullable().default(null),
  model: modelIdSchema,
  prompt_version: promptVersionSchema,
  top_n: z.int().min(1, "En az 1 sonuç istenmelidir.").max(100, "En fazla 100 sonuç istenebilir."),
  max_cost_usd: z
    .number()
    .positive("Maliyet sınırı sıfırdan büyük olmalıdır.")
    .max(100, "Maliyet sınırı en fazla 100 USD olabilir."),
});

type AnalysisConfigurationCandidate = Omit<
  z.infer<typeof analysisRequestFieldsSchema>,
  "upload_id"
>;

function validateAnalysisConfiguration(
  request: AnalysisConfigurationCandidate,
  ctx: z.RefinementCtx,
) {
  if (request.analysis_mode === "message") {
    if (request.conversation_config !== null) {
      ctx.addIssue({
        code: "custom",
        path: ["conversation_config"],
        message: "Konuşma ayarları yalnızca bağlamsal kullanıcı turları modunda kullanılabilir.",
      });
    }
    if (request.prompt_version === "faq_analysis/v4") {
      ctx.addIssue({
        code: "custom",
        path: ["prompt_version"],
        message: "faq_analysis/v4 yalnızca bağlamsal kullanıcı turları içindir.",
      });
    }
    return;
  }

  const config = request.conversation_config;
  if (config === null) {
    ctx.addIssue({
      code: "custom",
      path: ["conversation_config"],
      message: "Bağlamsal kullanıcı turları modu konuşma ayarlarını gerektirir.",
    });
    return;
  }

  if (request.prompt_version !== "faq_analysis/v4") {
    ctx.addIssue({
      code: "custom",
      path: ["prompt_version"],
      message: "Bağlamsal kullanıcı turları modu faq_analysis/v4 kullanmalıdır.",
    });
  }

  const mappedColumns = [
    ["session_id_column", config.session_id_column],
    ["message_order_column", config.message_order_column],
    ["role_column", config.role_column],
    ["message_type_column", config.message_type_column],
  ] as const;
  for (const [field, column] of mappedColumns) {
    if (column === request.text_column) {
      ctx.addIssue({
        code: "custom",
        path: ["conversation_config", field],
        message: "Metin kolonu konuşma eşleme kolonlarından farklı olmalıdır.",
      });
    }
  }

  const filterColumns = new Set(request.row_filters.map((rowFilter) => rowFilter.column));
  if (mappedColumns.some(([, column]) => filterColumns.has(column))) {
    ctx.addIssue({
      code: "custom",
      path: ["row_filters"],
      message: "Konuşma eşleme kolonları genel satır filtresi olarak kullanılamaz.",
    });
  }
}

export const analysisRequestSchema = analysisRequestFieldsSchema.superRefine(
  validateAnalysisConfiguration,
);

/** Formun URL'den aldığı upload_id dışındaki analiz ayarları. */
export const analysisConfigurationSchema = analysisRequestFieldsSchema
  .omit({ upload_id: true })
  .superRefine(validateAnalysisConfiguration);

/** İstemciler backend defaultlarını kullanmak için yeni alanları atlayabilir. */
export type AnalysisRequest = z.input<typeof analysisRequestSchema>;

/** Doğrulama sonrası tüm defaultları uygulanmış, fingerprint'e hazır istek. */
export type ValidatedAnalysisRequest = z.output<typeof analysisRequestSchema>;

/** POST /api/v1/analyses — 202 Accepted cevabı. */
export const analysisCreatedSchema = z.object({
  analysis_id: z.uuid(),
  status: z.literal("queued"),
});

export type AnalysisCreated = z.infer<typeof analysisCreatedSchema>;

/**
 * GET /api/v1/analyses/{analysis_id} cevabı — durum sorgulaması.
 *
 * ADR §2: ilerleme her satırda değil, yalnızca aşama veya anlamlı yüzde
 * değişiminde yazılır. Yani `progress` düzenli artmayabilir; arayüz
 * bunu düz bir sayaç gibi göstermemelidir.
 */
export const analysisJobSchema = z
  .object({
    analysis_id: z.uuid(),
    status: analysisStatusSchema,
    /** 0-100 arası tamamlanma yüzdesi. */
    progress: z.number().min(0).max(100),
    created_at: z.iso.datetime(),
    updated_at: z.iso.datetime(),
    /** Terminal olmayan durumlarda backend'in kalan süre tahmini, saniye. */
    estimated_seconds_remaining: z.number().nonnegative().nullable().default(null),
    /** Yalnızca status "failed" iken dolu. */
    error: problemDetailsSchema.nullable().default(null),
  })
  .superRefine((job, ctx) => {
    if ((job.status === "failed") !== (job.error !== null)) {
      ctx.addIssue({
        code: "custom",
        path: ["error"],
        message: "Job error alanı status ile uyumsuz.",
      });
    }
    if (TERMINAL_STATUSES.includes(job.status) && job.estimated_seconds_remaining !== null) {
      ctx.addIssue({
        code: "custom",
        path: ["estimated_seconds_remaining"],
        message: "Terminal job kalan süre tahmini taşıyamaz.",
      });
    }
  });

export type AnalysisJob = z.infer<typeof analysisJobSchema>;

/**
 * Model seçim listesi öğesi — `GET /api/v1/models`.
 *
 * ADR-0001 §6 "model yalnızca backend whitelist'inden seçilebilir" diyordu
 * ama bu listeyi döndüren bir endpoint tanımlamıyordu. ADR-0002 #1 ile uç
 * sözleşmeye dâhil edildi; artık kesinleşmiştir ve `docs/api/openapi.json`
 * üzerinden doğrulanır.
 */
export const modelOptionSchema = z.object({
  id: modelIdSchema,
  label: z.string(),
  /** 1M girdi tokenı başına USD; maliyet tahmini için. */
  input_cost_per_million: z.number().nonnegative(),
  output_cost_per_million: z.number().nonnegative(),
  cache_read_cost_per_million: z.number().nonnegative().nullable().default(null),
  cache_write_cost_per_million: z.number().nonnegative().nullable().default(null),
  context_window: z.int().positive(),
  pricing_source: z.enum(["openrouter", "fallback"]).default("fallback"),
  pricing_updated_at: z.iso.datetime().nullable().default(null),
});

export type ModelOption = z.infer<typeof modelOptionSchema>;

export const pricingSnapshotSchema = z.object({
  input_cost_per_million: z.number().nonnegative(),
  output_cost_per_million: z.number().nonnegative(),
  cache_read_cost_per_million: z.number().nonnegative().nullable().default(null),
  cache_write_cost_per_million: z.number().nonnegative().nullable().default(null),
  source: z.enum(["openrouter", "fallback"]),
  fetched_at: z.iso.datetime().nullable().default(null),
});

export type PricingSnapshot = z.infer<typeof pricingSnapshotSchema>;

export const modelListSchema = z
  .object({
    models: z.array(modelOptionSchema),
    default_model: modelIdSchema,
    default_prompt_version: promptVersionSchema,
  })
  .superRefine((catalog, ctx) => {
    const ids = catalog.models.map((model) => model.id);
    if (new Set(ids).size !== ids.length) {
      ctx.addIssue({
        code: "custom",
        path: ["models"],
        message: "Model id'leri benzersiz olmalı.",
      });
    }
    const actualIds = new Set(ids);
    if (
      modelIdSchema.options.some((id) => !actualIds.has(id)) ||
      actualIds.size !== modelIdSchema.options.length
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["models"],
        message: "Model listesi dondurulmuş whitelist ile birebir aynı olmalı.",
      });
    }
    if (!ids.includes(catalog.default_model)) {
      ctx.addIssue({
        code: "custom",
        path: ["default_model"],
        message: "Varsayılan model whitelist içinde olmalı.",
      });
    }
  });

export type ModelList = z.infer<typeof modelListSchema>;

/** GET /api/v1/analyses/{analysis_id}/export?format=xlsx|json */
export const exportFormatSchema = z.enum(["xlsx", "json"]);

export type ExportFormat = z.infer<typeof exportFormatSchema>;
