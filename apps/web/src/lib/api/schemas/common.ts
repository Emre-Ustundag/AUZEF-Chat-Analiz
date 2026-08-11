import * as z from "zod";

/**
 * Backend ile paylaşılan ortak tipler.
 *
 * Bu şemalar ADR (docs/mimari.md) §6 ve §7'den elle türetilmiştir. Backend
 * hazır olduğunda ADR'nin öngördüğü gibi OpenAPI'den üretilen client ile
 * değiştirilmesi beklenir; o zamana kadar hem runtime doğrulama hem de
 * backend'e verilecek sözleşme görevi görürler.
 *
 * Alan adları backend'in Pydantic modelleriyle birebir aynı olsun diye
 * snake_case tutulmuştur; camelCase'e çeviren bir eşleme katmanı yoktur.
 */

/** ADR §7'de tanımlı hata kodları. */
export const errorCodeSchema = z.enum([
  "UPLOAD_TOO_LARGE",
  "UPLOAD_INVALID_TYPE",
  "UPLOAD_CORRUPT_OR_ENCRYPTED",
  "SHEET_OR_COLUMN_NOT_FOUND",
  "PROVIDER_AUTH_FAILED",
  "PROVIDER_RATE_LIMITED",
  "PROVIDER_BAD_RESPONSE",
  "PROVIDER_TIMEOUT",
  "JOB_NOT_FOUND",
  "JOB_CONFLICT",
  "INTERNAL_ERROR",
]);

export type ErrorCode = z.infer<typeof errorCodeSchema>;

/**
 * RFC 9457 Problem Details. Backend tüm hata cevaplarını bu biçimde döner.
 *
 * `type`, `title` ve `status` RFC'nin zorunlu olmayan ama ADR'de her zaman
 * gönderilen alanlarıdır. `errors` alan bazlı doğrulama hatalarını taşır.
 */
export const problemDetailsSchema = z.object({
  type: z.string(),
  title: z.string(),
  status: z.int(),
  code: errorCodeSchema,
  detail: z.string(),
  trace_id: z.string(),
  errors: z
    .array(
      z.object({
        field: z.string().optional(),
        message: z.string(),
      }),
    )
    .default([]),
  /** Yalnızca PROVIDER_RATE_LIMITED (429) cevaplarında gelir; saniye cinsinden. */
  retry_after: z.number().nonnegative().optional(),
});

export type ProblemDetails = z.infer<typeof problemDetailsSchema>;

/**
 * Hata kodlarının kullanıcıya gösterilecek Türkçe karşılıkları.
 *
 * Backend'in `detail` alanı teknik ve İngilizce olabilir; arayüzde bu tablo
 * kullanılır. Ham backend metni kullanıcıya doğrudan basılmaz.
 */
export const ERROR_MESSAGES_TR: Record<ErrorCode, string> = {
  UPLOAD_TOO_LARGE:
    "Dosya boyutu sınırı aşıldı. En fazla 150 MB büyüklüğünde bir .xlsx dosyası yükleyebilirsiniz.",
  UPLOAD_INVALID_TYPE:
    "Bu dosya türü desteklenmiyor. Yalnızca .xlsx dosyaları analiz edilebilir.",
  UPLOAD_CORRUPT_OR_ENCRYPTED:
    "Dosya okunamadı. Bozuk, şifrelenmiş veya makro içeren dosyalar desteklenmez.",
  SHEET_OR_COLUMN_NOT_FOUND:
    "Seçilen sayfa veya kolon dosyada bulunamadı. Lütfen seçiminizi güncelleyin.",
  PROVIDER_AUTH_FAILED:
    "OpenRouter API anahtarı doğrulanamadı. Anahtarı kontrol edip tekrar deneyin.",
  PROVIDER_RATE_LIMITED:
    "OpenRouter istek sınırına ulaşıldı. Bir süre bekleyip tekrar deneyin.",
  PROVIDER_BAD_RESPONSE:
    "Dil modelinden geçerli bir yanıt alınamadı. Analizi tekrar başlatmayı deneyin.",
  PROVIDER_TIMEOUT:
    "Dil modeli zaman aşımına uğradı. Analizi tekrar başlatmayı deneyin.",
  JOB_NOT_FOUND: "İşlem bulunamadı. Bağlantı geçersiz veya süresi dolmuş olabilir.",
  JOB_CONFLICT: "Bu işlem için zaten devam eden bir analiz var.",
  INTERNAL_ERROR: "Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.",
};

/**
 * Kullanıcının yeniden denemesinin anlamlı olduğu hatalar.
 * Arayüzde "Tekrar dene" düğmesi yalnızca bunlarda gösterilir.
 */
export const RETRYABLE_ERROR_CODES: readonly ErrorCode[] = [
  "PROVIDER_RATE_LIMITED",
  "PROVIDER_BAD_RESPONSE",
  "PROVIDER_TIMEOUT",
  "INTERNAL_ERROR",
];

export function isRetryableError(code: ErrorCode): boolean {
  return RETRYABLE_ERROR_CODES.includes(code);
}

/** ADR §9: varsayılan sınırlar. Backend bunları environment'tan okur. */
export const LIMITS = {
  /** Sıkıştırılmış upload sınırı: 150 MB */
  MAX_UPLOAD_BYTES: 150 * 1024 * 1024,
  MAX_ROWS: 100_000,
  ACCEPTED_MIME_TYPE:
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ACCEPTED_EXTENSION: ".xlsx",
  /** ADR §2: durum sorgulama aralığı 2-3 saniye. */
  POLL_INTERVAL_MS: 2500,
  /** ADR §2: analiz hard timeout'u 45 dakika. */
  ANALYSIS_TIMEOUT_MS: 45 * 60 * 1000,
} as const;
