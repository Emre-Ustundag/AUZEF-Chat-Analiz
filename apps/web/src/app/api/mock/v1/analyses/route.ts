import type { NextRequest } from "next/server";

import { analysisRequestSchema } from "@/lib/api/schemas";
import { estimateCostUsd, findModel, KNOWN_PROMPT_VERSIONS } from "@/mocks/catalog";
import { analysisFingerprint } from "@/mocks/idempotency";
import { jsonResponse, problemResponse } from "@/mocks/responses";
import {
  createAnalysisRecord,
  getUploadRecord,
  lookupIdempotency,
  problem,
  rememberIdempotency,
} from "@/mocks/store";
import { validateIdempotencyKey } from "@/mocks/validation";

const PUBLIC_PATH = "/api/v1/analyses";

function invalidModel() {
  return problemResponse(
    problem("INVALID_MODEL", 422, "Geçersiz model", "Model backend whitelist'inde bulunmuyor.", {
      errors: [{ field: "model", message: "Bu model desteklenmiyor." }],
    }),
  );
}

function invalidPrompt() {
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

function idempotencyConflict() {
  return problemResponse(
    problem(
      "JOB_CONFLICT",
      409,
      "Idempotency çakışması",
      "Aynı Idempotency-Key daha önce farklı bir analiz gövdesiyle kullanıldı.",
    ),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

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
  const idempotencyKey = validateIdempotencyKey(request.headers.get("Idempotency-Key"));
  if (idempotencyKey.error) return problemResponse(idempotencyKey.error);

  const apiKey = request.headers.get("X-OpenRouter-Key");

  if (!apiKey || apiKey.trim() === "") {
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
    // Exact model/prompt enum'ları Pydantic ve Zod'da donduruludur. Diğer
    // alanlar geçerliyse enum hatasını jenerik REQUEST_VALIDATION'a
    // çevirmeyip sözleşmedeki özel kodla yayınla.
    const nonCatalogIssues = parsed.error.issues.filter(
      (issue) => issue.path[0] !== "model" && issue.path[0] !== "prompt_version",
    );
    if (nonCatalogIssues.length === 0 && isRecord(body)) {
      const model = body.model;
      if (typeof model === "string" && model.trim().length > 0 && !findModel(model)) {
        return invalidModel();
      }

      const promptVersion = body.prompt_version;
      if (
        typeof promptVersion === "string" &&
        promptVersion.trim().length > 0 &&
        !(KNOWN_PROMPT_VERSIONS as readonly string[]).includes(promptVersion)
      ) {
        return invalidPrompt();
      }
    }

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
    return invalidModel();
  }

  if (!(KNOWN_PROMPT_VERSIONS as readonly string[]).includes(prompt_version)) {
    return invalidPrompt();
  }

  const fingerprint = idempotencyKey.key ? analysisFingerprint(parsed.data) : null;
  if (idempotencyKey.key && fingerprint) {
    const lookup = lookupIdempotency("POST", PUBLIC_PATH, idempotencyKey.key, fingerprint);
    if (lookup.kind === "conflict") return idempotencyConflict();
    if (lookup.kind === "replay") {
      return jsonResponse(lookup.responseBody, {
        status: 202,
        headers: { "X-Trace-Id": lookup.traceId },
      });
    }
  }

  const upload = getUploadRecord(parsed.data.upload_id);
  if (!upload) {
    return problemResponse(
      problem("JOB_NOT_FOUND", 404, "Upload bulunamadı", "Analiz için verilen upload kaydı yok."),
    );
  }

  if (upload.status !== "ready" || !upload.profile) {
    return problemResponse(
      problem(
        "JOB_CONFLICT",
        409,
        "Upload henüz hazır değil",
        "Analiz yalnızca ready durumundaki bir upload için başlatılabilir.",
      ),
    );
  }

  const sheet = upload.profile.sheets.find((item) => item.name === parsed.data.sheet_name);
  if (!sheet) {
    return problemResponse(
      problem(
        "SHEET_OR_COLUMN_NOT_FOUND",
        422,
        "Sayfa bulunamadı",
        "Seçilen sayfa upload profilinde bulunmuyor.",
        { errors: [{ field: "sheet_name", message: "Bu sayfa dosyada bulunmuyor." }] },
      ),
    );
  }

  if (!sheet.columns.some((column) => column.name === parsed.data.text_column)) {
    return problemResponse(
      problem(
        "SHEET_OR_COLUMN_NOT_FOUND",
        422,
        "Kolon bulunamadı",
        "Seçilen metin kolonu upload profilinde bulunmuyor.",
        { errors: [{ field: "text_column", message: "Bu kolon seçilen sayfada bulunmuyor." }] },
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
  const responseBody = { analysis_id: record.analysisId, status: "queued" as const };

  if (idempotencyKey.key && fingerprint) {
    const stored = rememberIdempotency(
      "POST",
      PUBLIC_PATH,
      idempotencyKey.key,
      fingerprint,
      responseBody,
    );
    return jsonResponse(stored.responseBody, {
      status: 202,
      headers: { "X-Trace-Id": stored.traceId },
    });
  }

  return jsonResponse(responseBody, { status: 202 });
}
