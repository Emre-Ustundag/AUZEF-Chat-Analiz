import { describe, expect, it } from "vitest";

import { ApiError, toApiError, TRACE_ID_HEADER, unknownApiError } from "./client";
import { readFixture } from "./schemas/contract-paths";

/**
 * `toApiError` sözleşme drift'inin ÜRETİMDEKİ semptomunun geçtiği yer:
 * gövde beklenen biçimde değilse kullanıcı jenerik bir hata görür. O anda
 * elde kalan tek iz `X-Trace-Id` header'ıdır.
 */

function errorResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json", ...headers },
  });
}

const TRACE = "9d8c7b6a-5e4f-4321-8abc-0123456789ab";

describe("toApiError", () => {
  it("geçerli problem gövdesini ApiError'a çevirir", async () => {
    const body = readFixture("errors.job-not-found.404.json");
    const error = await toApiError(errorResponse(body, 404));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("JOB_NOT_FOUND");
    expect(error.status).toBe(404);
    expect(error.traceId).toBe(TRACE);
    expect(error.isRetryable).toBe(false);
  });

  it("429 gövdesinde retry_after okunur", async () => {
    const body = readFixture("errors.provider-rate-limited.429.json");
    const error = await toApiError(errorResponse(body, 429));

    expect(error.retryAfterSeconds).toBe(60);
    expect(error.isRetryable).toBe(true);
  });

  it.each([
    "REQUEST_VALIDATION",
    "INVALID_MODEL",
    "INVALID_PROMPT",
    "COST_LIMIT_EXCEEDED",
  ] as const)("ADR-0002 #1 kodu %s tanınır ve INTERNAL_ERROR'a düşmez", async (code) => {
    const file = `errors.${code.toLowerCase().replaceAll("_", "-")}.422.json`;
    const error = await toApiError(errorResponse(readFixture(file), 422));

    expect(error.code).toBe(code);
    expect(error.userMessage).not.toBe(
      "Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.",
    );
    expect(error.isRetryable).toBe(false);
  });

  /**
   * Drift'in ta kendisi: backend tanımadığımız bir kod yollarsa gövde
   * ayrıştırılamaz ve kullanıcı yanlış mesajı görür. Bu davranışı kilitliyoruz
   * ki sessizce değişmesin — ve trace id'nin header'dan kurtarıldığını
   * doğruluyoruz, yoksa sunucu tarafında arayacak hiçbir şey kalmaz.
   */
  it("bilinmeyen kod INTERNAL_ERROR'a düşer ama trace id header'dan kurtarılır", async () => {
    const body = { ...readFixture<object>("errors.job-not-found.404.json"), code: "YENI_KOD" };
    const error = await toApiError(errorResponse(body, 404, { [TRACE_ID_HEADER]: TRACE }));

    expect(error.code).toBe("INTERNAL_ERROR");
    expect(error.traceId).toBe(TRACE);
  });

  it("trace_id eksik gövde reddedilir", async () => {
    const body = readFixture<Record<string, unknown>>("errors.job-not-found.404.json");
    delete body.trace_id;
    const error = await toApiError(errorResponse(body, 404, { [TRACE_ID_HEADER]: TRACE }));

    expect(error.code).toBe("INTERNAL_ERROR");
    expect(error.traceId).toBe(TRACE);
  });

  it("JSON olmayan gövdede de trace id korunur", async () => {
    const response = new Response("<html>502 Bad Gateway</html>", {
      status: 502,
      headers: { "Content-Type": "text/html", [TRACE_ID_HEADER]: TRACE },
    });
    const error = await toApiError(response);

    expect(error.code).toBe("INTERNAL_ERROR");
    expect(error.traceId).toBe(TRACE);
  });

  it("header yoksa trace id boş kalır", async () => {
    const error = await toApiError(new Response("bozuk", { status: 500 }));
    expect(error.traceId).toBe("");
  });
});

describe("unknownApiError", () => {
  it("her zaman geçerli bir ApiError üretir", () => {
    const error = unknownApiError(0, "Sunucuya ulaşılamadı.");
    expect(error.code).toBe("INTERNAL_ERROR");
    expect(error.userMessage).toBeTruthy();
    expect(error.isRetryable).toBe(true);
  });
});
