import * as z from "zod";

import { ERROR_MESSAGES_TR, isRetryableError, problemDetailsSchema } from "./schemas";
import type { ErrorCode, ProblemDetails } from "./schemas";

/**
 * HTTP client.
 *
 * Backend'in taban adresi NEXT_PUBLIC_API_BASE_URL ile belirlenir.
 * Geliştirmede bu değer mock route handler'larını (/api/mock/v1) gösterir.
 * Gerçek FastAPI devreye girince .env'de /api/v1 yapılır ve next.config'e
 * aynı origin altında reverse proxy rewrite'ı eklenir (ADR §2).
 *
 * Mock'lar bilerek /api/v1 altına konmadı: Next'te route handler'lar
 * rewrite'lardan önce eşleşir, yani aynı yolu paylaşsalardı mock dosyaları
 * repoda kaldığı sürece gerçek backend'i sessizce gölgelerdi.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/mock/v1";

/**
 * Backend'in RFC 9457 hata cevabını taşıyan hata sınıfı.
 *
 * `userMessage` her zaman bizim Türkçe tablomuzdan gelir; backend'in `detail`
 * alanı teknik olabilir ve kullanıcıya doğrudan gösterilmez.
 */
export class ApiError extends Error {
  readonly code: ErrorCode;
  readonly status: number;
  readonly traceId: string;
  readonly retryAfterSeconds?: number;
  readonly problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(`${problem.code}: ${problem.title}`);
    this.name = "ApiError";
    this.problem = problem;
    this.code = problem.code;
    this.status = problem.status;
    this.traceId = problem.trace_id;
    this.retryAfterSeconds = problem.retry_after;
  }

  get userMessage(): string {
    return ERROR_MESSAGES_TR[this.code];
  }

  get isRetryable(): boolean {
    return isRetryableError(this.code);
  }
}

/**
 * Beklenen biçimde bir hata gövdesi alınamadığında kullanılır; ağ kopması,
 * reverse proxy'nin ürettiği HTML hata sayfası veya bozuk JSON gibi durumlar.
 */
export function unknownApiError(status: number, detail: string): ApiError {
  return new ApiError({
    type: "/errors/internal",
    title: "Beklenmeyen hata",
    status,
    code: "INTERNAL_ERROR",
    detail,
    trace_id: "",
    errors: [],
  });
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return unknownApiError(
      response.status,
      `Sunucu yanıtı okunamadı (HTTP ${response.status}).`,
    );
  }

  const parsed = problemDetailsSchema.safeParse(body);
  if (!parsed.success) {
    return unknownApiError(
      response.status,
      `Sunucu beklenmeyen bir hata biçimi döndü (HTTP ${response.status}).`,
    );
  }

  return new ApiError(parsed.data);
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/**
 * Tek giriş noktası: yolu çağırır, hata cevabını ApiError'a çevirir ve
 * başarılı cevabı verilen şemaya göre doğrular.
 *
 * Şema doğrulaması bilinçli olarak zorunlu: backend'in gövdesi sözleşmeden
 * saparsa hata dashboard'da bozuk sayı olarak değil, burada ortaya çıkar.
 */
export async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, headers, signal } = options;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      body,
      headers,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw unknownApiError(0, "Sunucuya ulaşılamadı.");
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return schema.parse(undefined);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw unknownApiError(response.status, "Sunucu yanıtı çözümlenemedi.");
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw unknownApiError(
      response.status,
      `Sunucu yanıtı beklenen sözleşmeye uymuyor: ${z.prettifyError(parsed.error)}`,
    );
  }

  return parsed.data;
}

/**
 * OpenRouter anahtarını taşıyan header'ı üretir.
 *
 * ADR §6/§9: anahtar yalnızca bu header'da gider, istek gövdesine veya
 * sorgu parametresine ASLA konmaz, loglanmaz ve kalıcı olarak saklanmaz.
 */
export function openRouterKeyHeader(apiKey: string): Record<string, string> {
  return { "X-OpenRouter-Key": apiKey };
}
