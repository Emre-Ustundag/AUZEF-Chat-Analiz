import type { NextRequest } from "next/server";

import { analysisRequestSchema } from "@/lib/api/schemas";
import { estimateCostUsd, findModel, KNOWN_PROMPT_VERSIONS } from "@/mocks/catalog";
import { jsonResponse, problemResponse } from "@/mocks/responses";
import { createAnalysisRecord, problem } from "@/mocks/store";

/**
 * POST /api/mock/v1/analyses
 *
 * ADR §6/§9: OpenRouter anahtarı yalnızca X-OpenRouter-Key header'ında
 * taşınır. Mock anahtarın varlığını doğrular ama DEĞERİNİ hiçbir yere
 * yazmaz ve loglamaz — gerçek backend'de de aynı kural geçerli.
 *
 * Hata kodları ADR-0002 #1 ile hizalandı; bu yollar mock'ta da üretilmezse
 * arayüzde erişilemez ve test edilemez kalırlar.
 */
export async function POST(request: NextRequest) {
  const apiKey = request.headers.get("X-OpenRouter-Key");

  if (!apiKey) {
    // REQUEST_VALIDATION varken bu bir miktar zorlama; yine de korunuyor,
    // çünkü boş anahtar alanı için Türkçe mesajı daha isabetli (ADR-0002).
    return problemResponse(
      problem(
        "PROVIDER_AUTH_FAILED",
        422,
        "API anahtarı eksik",
        "X-OpenRouter-Key header'ı zorunludur.",
      ),
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    // FastAPI çözümlenemeyen gövdede 400 değil 422/REQUEST_VALIDATION üretir.
    return problemResponse(
      problem("REQUEST_VALIDATION", 422, "Geçersiz gövde", "JSON çözümlenemedi."),
    );
  }

  const parsed = analysisRequestSchema.safeParse(body);
  if (!parsed.success) {
    return problemResponse(
      problem("REQUEST_VALIDATION", 422, "İstek doğrulanamadı", "İstek alanları doğrulanamadı.", {
        errors: parsed.error.issues.map((issue) => ({
          field: issue.path.join("."),
          message: issue.message,
        })),
      }),
    );
  }

  const { model, prompt_version, max_cost_usd } = parsed.data;

  if (!findModel(model)) {
    return problemResponse(
      problem("INVALID_MODEL", 422, "Geçersiz model", "Model backend whitelist'inde bulunmuyor.", {
        errors: [{ field: "model", message: "Bu model desteklenmiyor." }],
      }),
    );
  }

  if (!KNOWN_PROMPT_VERSIONS.includes(prompt_version)) {
    return problemResponse(
      problem(
        "INVALID_PROMPT",
        422,
        "Geçersiz prompt sürümü",
        "Prompt sürümü backend'de tanımlı değil.",
        { errors: [{ field: "prompt_version", message: "Bu prompt sürümü tanınmıyor." }] },
      ),
    );
  }

  // ADR-0002 #10: maliyet tavanı iki noktada kontrol edilir; bu, LLM çağrısı
  // hiç başlamadan submit anındaki senkron ön tahmin.
  const estimated = estimateCostUsd(model);
  if (estimated > max_cost_usd) {
    return problemResponse(
      problem(
        "COST_LIMIT_EXCEEDED",
        422,
        "Maliyet sınırı aşıldı",
        `Tahmini maliyet ${estimated} USD, sınır ${max_cost_usd} USD.`,
        { errors: [{ field: "max_cost_usd", message: "Tahmini maliyet bu sınırın üzerinde." }] },
      ),
    );
  }

  const record = createAnalysisRecord(parsed.data);

  return jsonResponse({ analysis_id: record.analysisId, status: "queued" }, { status: 202 });
}
