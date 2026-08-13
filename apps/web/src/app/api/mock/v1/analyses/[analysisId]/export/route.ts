import type { NextRequest } from "next/server";

import { exportFormatSchema } from "@/lib/api/schemas";
import { minimalXlsxBytes } from "@/mocks/minimal-xlsx";
import { problemResponse, tracedResponse } from "@/mocks/responses";
import { analysisExists, getAnalysisReportRecord, problem } from "@/mocks/store";
import { invalidUuidProblem } from "@/mocks/validation";

/**
 * GET /api/mock/v1/analyses/{analysisId}/export?format=xlsx|json
 *
 * JSON raporun yanında gerçekten açılabilen deterministic bir minimal XLSX
 * üretir. Böylece frontend her iki başarı media type'ını da uçtan uca dener.
 */
export async function GET(
  request: NextRequest,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]/export">,
) {
  const { analysisId } = await context.params;
  const invalidUuid = invalidUuidProblem(analysisId, "path.analysis_id");
  if (invalidUuid) return problemResponse(invalidUuid);

  // `.catch("json")` bilinmeyen formatı sessizce yutuyordu; backend
  // REQUEST_VALIDATION ile 422 verecek (ADR-0002 #1).
  const parsedFormat = exportFormatSchema.safeParse(
    request.nextUrl.searchParams.get("format") ?? "xlsx",
  );
  if (!parsedFormat.success) {
    return problemResponse(
      problem(
        "REQUEST_VALIDATION",
        422,
        "Geçersiz export biçimi",
        "format yalnızca xlsx veya json olabilir.",
        { errors: [{ field: "query.format", message: "Desteklenmeyen biçim." }] },
      ),
    );
  }
  const format = parsedFormat.data;

  if (!analysisExists(analysisId)) {
    return problemResponse(problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Analiz kaydı yok."));
  }

  const report = getAnalysisReportRecord(analysisId);
  if (!report) {
    return problemResponse(
      problem(
        "JOB_CONFLICT",
        409,
        "Rapor hazır değil",
        "Export yalnızca tamamlanmış analiz için alınabilir.",
      ),
    );
  }

  if (format === "xlsx") {
    return tracedResponse(minimalXlsxBytes(), {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename="analiz-${analysisId}.xlsx"`,
      },
    });
  }

  return tracedResponse(JSON.stringify(report, null, 2), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Content-Disposition": `attachment; filename="analiz-${analysisId}.json"`,
    },
  });
}
