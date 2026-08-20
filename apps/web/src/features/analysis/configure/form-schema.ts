import * as z from "zod";

import { analysisRequestSchema, datasetTypeSchema } from "@/lib/api/schemas";
import type { AnalysisRequest, ChatbotLogConfig } from "@/lib/api/schemas";

/**
 * Analiz yapılandırma formu.
 *
 * İstek şemasından türetiliyor ki alan doğrulamaları (top_n aralığı, maliyet
 * sınırı, boş kolon reddi) tek yerde tanımlı kalsın. `upload_id` formda yok;
 * URL'den geliyor.
 *
 * Chatbot alanları bilinçli olarak DÜZ tutuluyor (istek gövdesindeki iç içe
 * `chatbot_config` yerine): virgülle ayrılan değer listeleri form üzerinde
 * serbest metin, istek gövdesinde dizi. Dönüşüm `toAnalysisRequest` içinde
 * tek yerde yapılır.
 */

/** "Kullanıcı, user , kullanici" → ["Kullanıcı", "user", "kullanici"] */
export function splitFilterValues(raw: string): string[] {
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

export const configureFormSchema = analysisRequestSchema
  .omit({ upload_id: true, dataset_type: true, chatbot_config: true })
  .extend({
    openrouter_api_key: z.string().min(1, "OpenRouter API anahtarı gereklidir."),
    dataset_type: datasetTypeSchema,
    /** CHATBOT_LOG alanları; GENERIC'te boş kalır ve isteğe girmez. */
    role_column: z.string(),
    role_user_values_raw: z.string(),
    session_id_column: z.string(),
    timestamp_column: z.string(),
    message_type_column: z.string(),
    allowed_message_types_raw: z.string(),
  })
  .superRefine((values, ctx) => {
    if (values.dataset_type !== "CHATBOT_LOG") return;

    if (values.role_column.trim().length === 0) {
      ctx.addIssue({
        code: "custom",
        path: ["role_column"],
        message: "Chatbot dökümü için gönderen/rol kolonu seçilmelidir.",
      });
    }
    if (splitFilterValues(values.role_user_values_raw).length === 0) {
      ctx.addIssue({
        code: "custom",
        path: ["role_user_values_raw"],
        message: "En az bir kullanıcı değeri girilmelidir (virgülle ayırın).",
      });
    }
    if (
      values.message_type_column.trim().length > 0 &&
      splitFilterValues(values.allowed_message_types_raw).length === 0
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["allowed_message_types_raw"],
        message: "Mesaj tipi kolonu seçiliyken izin verilen tipler girilmelidir.",
      });
    }
  });

export type ConfigureFormValues = z.infer<typeof configureFormSchema>;

function toChatbotConfig(values: ConfigureFormValues): ChatbotLogConfig {
  const messageTypeColumn = values.message_type_column.trim();
  return {
    role_column: values.role_column,
    role_user_values: splitFilterValues(values.role_user_values_raw),
    session_id_column: values.session_id_column.trim() || null,
    timestamp_column: values.timestamp_column.trim() || null,
    message_type_column: messageTypeColumn || null,
    allowed_message_types: messageTypeColumn
      ? splitFilterValues(values.allowed_message_types_raw)
      : null,
  };
}

/**
 * Form değerlerinden istek gövdesini kurar.
 *
 * Alanlar BİLEREK tek tek yazılıyor, spread kullanılmıyor. Forma ileride
 * eklenecek herhangi bir alan (özellikle API anahtarı) spread yüzünden
 * kazara istek gövdesine sızamasın diye. ADR §6/§9: anahtar yalnızca
 * X-OpenRouter-Key header'ında taşınır.
 *
 * `dataset_type` ve `chatbot_config` her zaman AÇIKÇA gönderilir: idempotency
 * fingerprint'i doğrulanmış gövdenin tamamı üzerinden hesaplanıyor
 * (ADR-0002 #3) ve alanları backend varsayılanına bırakmak ile açıkça
 * göndermek aynı fingerprint'i üretmeli.
 */
export function toAnalysisRequest(uploadId: string, values: ConfigureFormValues): AnalysisRequest {
  const chatbot = values.dataset_type === "CHATBOT_LOG";
  return {
    upload_id: uploadId,
    sheet_name: values.sheet_name,
    text_column: values.text_column,
    model: values.model,
    prompt_version: values.prompt_version,
    top_n: values.top_n,
    max_cost_usd: values.max_cost_usd,
    dataset_type: values.dataset_type,
    chatbot_config: chatbot ? toChatbotConfig(values) : null,
  };
}
