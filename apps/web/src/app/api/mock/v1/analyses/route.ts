import { NextResponse, type NextRequest } from "next/server";

import { analysisRequestSchema } from "@/lib/api/schemas";
import { createAnalysisRecord, problem } from "@/mocks/store";

/**
 * POST /api/mock/v1/analyses
 *
 * ADR §6/§9: OpenRouter anahtarı yalnızca X-OpenRouter-Key header'ında
 * taşınır. Mock anahtarın varlığını doğrular ama DEĞERİNİ hiçbir yere
 * yazmaz ve loglamaz — gerçek backend'de de aynı kural geçerli.
 */
export async function POST(request: NextRequest) {
  const apiKey = request.headers.get("X-OpenRouter-Key");

  if (!apiKey) {
    return NextResponse.json(
      problem(
        "PROVIDER_AUTH_FAILED",
        422,
        "API anahtarı eksik",
        "X-OpenRouter-Key header'ı zorunludur.",
      ),
      { status: 422 },
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      problem("INTERNAL_ERROR", 400, "Geçersiz gövde", "JSON çözümlenemedi."),
      { status: 400 },
    );
  }

  const parsed = analysisRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      {
        ...problem(
          "SHEET_OR_COLUMN_NOT_FOUND",
          422,
          "Geçersiz analiz isteği",
          "İstek alanları doğrulanamadı.",
        ),
        errors: parsed.error.issues.map((issue) => ({
          field: issue.path.join("."),
          message: issue.message,
        })),
      },
      { status: 422 },
    );
  }

  const record = createAnalysisRecord(parsed.data);

  return NextResponse.json(
    { analysis_id: record.analysisId, status: "queued" },
    { status: 202 },
  );
}
