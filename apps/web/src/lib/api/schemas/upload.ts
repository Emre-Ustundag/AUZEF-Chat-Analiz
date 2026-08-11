import * as z from "zod";

import { problemDetailsSchema } from "./common";

/**
 * Upload ve dosya profilleme şemaları — ADR §5 "Aşama A" ve §6 "Upload".
 *
 * Frontend Excel'i parse etmez (ADR §11'de açıkça reddedilen alternatif).
 * Sayfa, kolon ve satır bilgisi yalnızca backend'in profil çıktısından gelir.
 */

/** ADR §6: queued/validating/ready/failed */
export const uploadStatusSchema = z.enum(["queued", "validating", "ready", "failed"]);

export type UploadStatus = z.infer<typeof uploadStatusSchema>;

/**
 * Tek bir kolonun veri profili. Kolon seçim ekranı kullanıcıya hangi kolonun
 * mesaj metni olduğunu tahmin ettirmek için bu istatistikleri gösterir.
 */
export const columnProfileSchema = z.object({
  name: z.string(),
  /** Sayfadaki 0 tabanlı kolon sırası. */
  index: z.int().nonnegative(),
  non_empty_count: z.int().nonnegative(),
  empty_count: z.int().nonnegative(),
  /** Normalize edilmemiş ham benzersiz değer sayısı. */
  unique_count: z.int().nonnegative(),
  /** Ortalama karakter uzunluğu; metin kolonunu sayısal/ID kolonundan ayırmaya yarar. */
  avg_length: z.number().nonnegative(),
  /**
   * Backend'in bu kolonun serbest metin olduğuna dair tahmini.
   * Yalnızca bir öneridir; seçim her zaman kullanıcıya aittir.
   */
  is_likely_text: z.boolean(),
  /** Redakte edilmiş, kırpılmış örnek değerler. */
  sample_values: z.array(z.string()).default([]),
});

export type ColumnProfile = z.infer<typeof columnProfileSchema>;

export const sheetProfileSchema = z.object({
  name: z.string(),
  row_count: z.int().nonnegative(),
  column_count: z.int().nonnegative(),
  columns: z.array(columnProfileSchema),
});

export type SheetProfile = z.infer<typeof sheetProfileSchema>;

export const uploadProfileSchema = z.object({
  sheets: z.array(sheetProfileSchema),
  total_row_count: z.int().nonnegative(),
  /**
   * ADR §9'daki 100.000 satır sınırı aşıldıysa true. Analiz yine de
   * başlatılabilir ama kullanıcı uyarılır.
   */
  exceeds_row_limit: z.boolean().default(false),
});

export type UploadProfile = z.infer<typeof uploadProfileSchema>;

/**
 * GET /api/v1/uploads/{upload_id} cevabı.
 *
 * `profile` yalnızca status "ready" iken dolu; `error` yalnızca "failed" iken.
 * Bu koşulluluk discriminated union yerine nullable alanlarla modellendi,
 * çünkü polling sırasında aynı endpoint tüm durumları döndürüyor ve
 * TanStack Query cache'inde tek bir tip tutmak istiyoruz.
 */
export const uploadSchema = z.object({
  upload_id: z.uuid(),
  status: uploadStatusSchema,
  filename: z.string(),
  size_bytes: z.int().nonnegative(),
  created_at: z.iso.datetime(),
  profile: uploadProfileSchema.nullable().default(null),
  error: problemDetailsSchema.nullable().default(null),
});

export type Upload = z.infer<typeof uploadSchema>;

/** POST /api/v1/uploads — 202 Accepted cevabı. */
export const uploadCreatedSchema = z.object({
  upload_id: z.uuid(),
  status: uploadStatusSchema,
});

export type UploadCreated = z.infer<typeof uploadCreatedSchema>;

/** Upload'ın terminal (artık değişmeyecek) bir duruma ulaşıp ulaşmadığı. */
export function isUploadSettled(status: UploadStatus): boolean {
  return status === "ready" || status === "failed";
}
