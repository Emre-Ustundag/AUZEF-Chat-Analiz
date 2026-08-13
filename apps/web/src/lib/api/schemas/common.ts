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

/**
 * ADR-0001 §7 + ADR-0002 #1'de tanımlı hata kodları.
 *
 * Backend'in `ErrorCode` enum'uyla küme olarak birebir aynı olmak zorunda;
 * `contract-openapi.test.ts` bunu `docs/api/openapi.json` üzerinden kilitler.
 * Listede olmayan bir kod gelirse `toApiError` cevabı sentetik bir
 * INTERNAL_ERROR'a düşürür ve kullanıcı yanlış mesajı görür.
 */
export const errorCodeSchema = z.enum([
  "UPLOAD_TOO_LARGE",
  "UPLOAD_INVALID_TYPE",
  "UPLOAD_CORRUPT_OR_ENCRYPTED",
  "SHEET_OR_COLUMN_NOT_FOUND",
  // ADR-0002 #1 ile eklenen istek doğrulama kodları; hepsi 422.
  "REQUEST_VALIDATION",
  "INVALID_MODEL",
  "INVALID_PROMPT",
  "COST_LIMIT_EXCEEDED",
  "PROVIDER_AUTH_FAILED",
  "PROVIDER_RATE_LIMITED",
  "PROVIDER_BAD_RESPONSE",
  "PROVIDER_TIMEOUT",
  "JOB_NOT_FOUND",
  "JOB_CONFLICT",
  "NOT_IMPLEMENTED",
  "SERVICE_NOT_READY",
  "INTERNAL_ERROR",
]);

export type ErrorCode = z.infer<typeof errorCodeSchema>;

/** Backend `ERROR_STATUS` kaydının tüketici tarafındaki aynası. */
export const ERROR_STATUS_BY_CODE = {
  UPLOAD_TOO_LARGE: 413,
  UPLOAD_INVALID_TYPE: 415,
  UPLOAD_CORRUPT_OR_ENCRYPTED: 422,
  SHEET_OR_COLUMN_NOT_FOUND: 422,
  REQUEST_VALIDATION: 422,
  INVALID_MODEL: 422,
  INVALID_PROMPT: 422,
  COST_LIMIT_EXCEEDED: 422,
  PROVIDER_AUTH_FAILED: 422,
  PROVIDER_RATE_LIMITED: 429,
  PROVIDER_BAD_RESPONSE: 502,
  PROVIDER_TIMEOUT: 504,
  JOB_NOT_FOUND: 404,
  JOB_CONFLICT: 409,
  NOT_IMPLEMENTED: 501,
  SERVICE_NOT_READY: 503,
  INTERNAL_ERROR: 500,
} as const satisfies Record<ErrorCode, number>;

/**
 * RFC 9457 Problem Details. Backend tüm hata cevaplarını bu biçimde döner.
 *
 * `type`, `title` ve `status` RFC'nin zorunlu olmayan ama ADR'de her zaman
 * gönderilen alanlarıdır. `errors` alan bazlı doğrulama hatalarını taşır.
 */
export const problemDetailsSchema = z
  .object({
    type: z.string(),
    title: z.string(),
    status: z.int(),
    code: errorCodeSchema,
    detail: z.string(),
    trace_id: z.uuid(),
    errors: z
      .array(
        z.object({
          field: z.string().nullish(),
          message: z.string(),
        }),
      )
      .default([]),
    /** Yalnızca PROVIDER_RATE_LIMITED (429) cevaplarında gelir; saniye cinsinden. */
    retry_after: z.number().nonnegative().optional(),
  })
  .superRefine((problem, ctx) => {
    if (problem.status !== ERROR_STATUS_BY_CODE[problem.code]) {
      ctx.addIssue({
        code: "custom",
        path: ["status"],
        message: `${problem.code} status=${ERROR_STATUS_BY_CODE[problem.code]} taşımalı.`,
      });
    }

    const rateLimited = problem.code === "PROVIDER_RATE_LIMITED";
    if (rateLimited !== (problem.retry_after !== undefined)) {
      ctx.addIssue({
        code: "custom",
        path: ["retry_after"],
        message: "retry_after yalnızca PROVIDER_RATE_LIMITED cevabında zorunludur.",
      });
    }
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
  UPLOAD_INVALID_TYPE: "Bu dosya türü desteklenmiyor. Yalnızca .xlsx dosyaları analiz edilebilir.",
  UPLOAD_CORRUPT_OR_ENCRYPTED:
    "Dosya okunamadı. Bozuk, şifrelenmiş veya makro içeren dosyalar desteklenmez.",
  SHEET_OR_COLUMN_NOT_FOUND:
    "Seçilen sayfa veya kolon dosyada bulunamadı. Lütfen seçiminizi güncelleyin.",
  REQUEST_VALIDATION: "Gönderilen bilgiler geçersiz. Lütfen formu kontrol edip tekrar deneyin.",
  INVALID_MODEL: "Seçilen model desteklenmiyor. Listeden geçerli bir model seçin.",
  INVALID_PROMPT: "Seçilen analiz sürümü geçersiz. Sayfayı yenileyip tekrar deneyin.",
  COST_LIMIT_EXCEEDED:
    "Tahmini maliyet belirlediğiniz üst sınırı aşıyor. Sınırı yükseltin veya daha ucuz bir model seçin.",
  PROVIDER_AUTH_FAILED:
    "OpenRouter API anahtarı doğrulanamadı. Anahtarı kontrol edip tekrar deneyin.",
  PROVIDER_RATE_LIMITED: "OpenRouter istek sınırına ulaşıldı. Bir süre bekleyip tekrar deneyin.",
  PROVIDER_BAD_RESPONSE:
    "Dil modelinden geçerli bir yanıt alınamadı. Analizi tekrar başlatmayı deneyin.",
  PROVIDER_TIMEOUT: "Dil modeli zaman aşımına uğradı. Analizi tekrar başlatmayı deneyin.",
  JOB_NOT_FOUND: "İşlem bulunamadı. Bağlantı geçersiz veya süresi dolmuş olabilir.",
  JOB_CONFLICT: "Bu işlem için zaten devam eden bir analiz var.",
  NOT_IMPLEMENTED: "Bu özellik henüz kullanıma hazır değil.",
  SERVICE_NOT_READY: "Servis henüz trafiğe hazır değil. Lütfen kısa bir süre sonra tekrar deneyin.",
  INTERNAL_ERROR: "Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.",
};

/**
 * Kullanıcının yeniden denemesinin anlamlı olduğu hatalar.
 * Arayüzde "Tekrar dene" düğmesi yalnızca bunlarda gösterilir.
 *
 * ADR-0002 #1 ile eklenen dört kod (REQUEST_VALIDATION, INVALID_MODEL,
 * INVALID_PROMPT, COST_LIMIT_EXCEEDED) bilerek DIŞARIDA: hepsi kullanıcı
 * girdisi hatasıdır ve aynı isteği tekrarlamak aynı hatayı üretir. Bunu
 * `contract-openapi.test.ts` kilitler.
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

/** ADR §9: sözleşme ve çalışma sınırları. */
export const LIMITS = {
  /** Sıkıştırılmış upload sınırı: backend ile birlikte sözleşmede donmuştur. */
  MAX_UPLOAD_BYTES: 150 * 1024 * 1024,
  MAX_ROWS: 100_000,
  ACCEPTED_MIME_TYPE: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ACCEPTED_EXTENSION: ".xlsx",
  /** ADR §2: durum sorgulama aralığı 2-3 saniye. */
  POLL_INTERVAL_MS: 2500,
  /** ADR §2: analiz hard timeout'u 45 dakika. */
  ANALYSIS_TIMEOUT_MS: 45 * 60 * 1000,
} as const;
