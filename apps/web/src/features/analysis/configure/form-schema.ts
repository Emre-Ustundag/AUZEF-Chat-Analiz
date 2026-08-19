import * as z from "zod";

import { analysisRequestSchema } from "@/lib/api/schemas";
import type { AnalysisRequest } from "@/lib/api/schemas";

/**
 * Analiz yapılandırma formu.
 *
 * İstek şemasından türetiliyor ki alan doğrulamaları (top_n aralığı, maliyet
 * sınırı, boş kolon reddi) tek yerde tanımlı kalsın. `upload_id` formda yok;
 * URL'den geliyor.
 */
export const configureFormSchema = analysisRequestSchema.omit({ upload_id: true }).extend({
  openrouter_api_key: z.string().min(1, "OpenRouter API anahtarı gereklidir."),
});

export type ConfigureFormValues = z.infer<typeof configureFormSchema>;

/**
 * Form değerlerinden istek gövdesini kurar.
 *
 * Alanlar BİLEREK tek tek yazılıyor, spread kullanılmıyor. Forma ileride
 * eklenecek herhangi bir alan (özellikle API anahtarı) spread yüzünden
 * kazara istek gövdesine sızamasın diye. ADR §6/§9: anahtar yalnızca
 * X-OpenRouter-Key header'ında taşınır.
 */
export function toAnalysisRequest(uploadId: string, values: ConfigureFormValues): AnalysisRequest {
  return {
    upload_id: uploadId,
    sheet_name: values.sheet_name,
    text_column: values.text_column,
    row_filters: values.row_filters,
    model: values.model,
    prompt_version: values.prompt_version,
    top_n: values.top_n,
    max_cost_usd: values.max_cost_usd,
  };
}
