import type { NextRequest } from "next/server";

import { LIMITS } from "@/lib/api/schemas";
import { uploadFingerprint } from "@/mocks/idempotency";
import { jsonResponse, problemResponse } from "@/mocks/responses";
import { createUploadRecord, lookupIdempotency, problem, rememberIdempotency } from "@/mocks/store";
import { validateIdempotencyKey } from "@/mocks/validation";

const PUBLIC_PATH = "/api/v1/uploads";

function idempotencyConflict() {
  return problemResponse(
    problem(
      "JOB_CONFLICT",
      409,
      "Idempotency çakışması",
      "Aynı Idempotency-Key daha önce farklı bir upload gövdesiyle kullanıldı.",
    ),
  );
}

/**
 * POST /api/mock/v1/uploads
 *
 * Gerçek backend dosyayı object storage'a stream eder ve OOXML yapısını
 * doğrular. Mock yalnızca boyut/uzantı reddini taklit eder; amaç arayüzün
 * hata yollarını gerçekten çalıştırabilmesi.
 */
export async function POST(request: NextRequest) {
  const idempotencyKey = validateIdempotencyKey(request.headers.get("Idempotency-Key"));
  if (idempotencyKey.error) return problemResponse(idempotencyKey.error);

  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return problemResponse(
      problem(
        "REQUEST_VALIDATION",
        422,
        "Geçersiz multipart gövdesi",
        "Multipart form verisi çözümlenemedi.",
      ),
    );
  }
  const file = formData.get("file");

  if (!(file instanceof File)) {
    return problemResponse(
      problem("REQUEST_VALIDATION", 422, "Dosya bulunamadı", "İstek gövdesinde 'file' alanı yok.", {
        errors: [{ field: "file", message: "file alanı zorunludur." }],
      }),
    );
  }

  const lowerName = file.name.toLocaleLowerCase("tr");
  if (!LIMITS.ACCEPTED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return problemResponse(
      problem(
        "UPLOAD_INVALID_TYPE",
        415,
        "Desteklenmeyen dosya türü",
        "Yalnızca .xlsx veya .csv dosyaları analiz edilebilir.",
      ),
    );
  }

  if (file.size > LIMITS.MAX_UPLOAD_BYTES) {
    return problemResponse(
      problem(
        "UPLOAD_TOO_LARGE",
        413,
        "Dosya boyutu sınırı aşıldı",
        "En fazla 150 MB .xlsx veya .csv yüklenebilir.",
      ),
    );
  }

  let fingerprint: string | null = null;
  if (idempotencyKey.key) {
    fingerprint = await uploadFingerprint(file);
    const lookup = lookupIdempotency("POST", PUBLIC_PATH, idempotencyKey.key, fingerprint);

    if (lookup.kind === "conflict") return idempotencyConflict();
    if (lookup.kind === "replay") {
      return jsonResponse(lookup.responseBody, {
        status: 202,
        headers: { "X-Trace-Id": lookup.traceId },
      });
    }
  }

  const record = createUploadRecord(file.name, file.size);
  const responseBody = { upload_id: record.uploadId, status: "queued" as const };

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

  // ADR §2: uzun iş kuyruğa alınır, API hemen 202 döner.
  return jsonResponse(responseBody, { status: 202 });
}
