import type { NextRequest } from "next/server";

import { LIMITS } from "@/lib/api/schemas";
import { jsonResponse, problemResponse } from "@/mocks/responses";
import { createUploadRecord, problem } from "@/mocks/store";

/**
 * POST /api/mock/v1/uploads
 *
 * Gerçek backend dosyayı object storage'a stream eder ve OOXML yapısını
 * doğrular. Mock yalnızca boyut/uzantı reddini taklit eder; amaç arayüzün
 * hata yollarını gerçekten çalıştırabilmesi.
 */
export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get("file");

  if (!(file instanceof File)) {
    return problemResponse(
      problem(
        "REQUEST_VALIDATION",
        422,
        "Dosya bulunamadı",
        "İstek gövdesinde 'file' alanı yok.",
        { errors: [{ field: "file", message: "file alanı zorunludur." }] },
      ),
    );
  }

  if (!file.name.toLocaleLowerCase("tr").endsWith(LIMITS.ACCEPTED_EXTENSION)) {
    return problemResponse(
      problem(
        "UPLOAD_INVALID_TYPE",
        415,
        "Desteklenmeyen dosya türü",
        "Yalnızca .xlsx dosyaları analiz edilebilir.",
      ),
    );
  }

  if (file.size > LIMITS.MAX_UPLOAD_BYTES) {
    return problemResponse(
      problem(
        "UPLOAD_TOO_LARGE",
        413,
        "Dosya boyutu sınırı aşıldı",
        "En fazla 150 MB .xlsx yüklenebilir.",
      ),
    );
  }

  const record = createUploadRecord(file.name, file.size);

  // ADR §2: uzun iş kuyruğa alınır, API hemen 202 döner.
  return jsonResponse({ upload_id: record.uploadId, status: "queued" }, { status: 202 });
}
