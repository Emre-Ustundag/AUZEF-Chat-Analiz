import { randomUUID } from "node:crypto";

import type { ProblemDetails } from "@/lib/api/schemas";

export const TRACE_ID_HEADER = "X-Trace-Id";
export const PROBLEM_MEDIA_TYPE = "application/problem+json";

function headersWithTrace(headers?: HeadersInit, traceId: string = randomUUID()): Headers {
  const result = new Headers(headers);
  result.set(TRACE_ID_HEADER, traceId);
  return result;
}

/** Başarılı JSON cevabı; gerçek FastAPI gibi her zaman trace header'ı taşır. */
export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = headersWithTrace(init.headers);
  headers.set("Content-Type", "application/json");
  return new Response(JSON.stringify(body), { ...init, headers });
}

/** RFC 9457 cevabı; header ve gövdedeki trace id karakter karakter aynıdır. */
export function problemResponse(problem: ProblemDetails): Response {
  const headers = headersWithTrace({ "Content-Type": PROBLEM_MEDIA_TYPE }, problem.trace_id);
  return new Response(JSON.stringify(problem), { status: problem.status, headers });
}

export function noContentResponse(): Response {
  return new Response(null, { status: 204, headers: headersWithTrace() });
}

/** Binary/attachment gibi JSON olmayan cevaplara trace header'ı ekler. */
export function tracedResponse(body: BodyInit | null, init: ResponseInit = {}): Response {
  return new Response(body, { ...init, headers: headersWithTrace(init.headers) });
}
