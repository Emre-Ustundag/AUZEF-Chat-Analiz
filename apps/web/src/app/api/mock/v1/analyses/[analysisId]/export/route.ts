import type { NextRequest } from "next/server";

import { exportFormatSchema } from "@/lib/api/schemas";
import { problemResponse, tracedResponse } from "@/mocks/responses";
import { getAnalysisReportRecord, problem } from "@/mocks/store";

/**
 * GET /api/mock/v1/analyses/{analysisId}/export?format=xlsx|json
 *
 * Mock yalnızca JSON üretir. xlsx isteği bilinçli olarak 409 döner:
 * sahte bir xlsx üretmek arayüze gerçek bir şey doğrulatmaz, ama isteğin
 * doğru kurulduğunu ve hata yolunun çalıştığını gösterir.
 */
export async function GET(
  request: NextRequest,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]/export">,
) {
  const { analysisId } = await context.params;

  // `.catch("json")` bilinmeyen formatı sessizce yutuyordu; backend
  // REQUEST_VALIDATION ile 422 verecek (ADR-0002 #1).
  const parsedFormat = exportFormatSchema.safeParse(request.nextUrl.searchParams.get("format"));
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
    return problemResponse(
      problem(
        "JOB_CONFLICT",
        409,
        "xlsx export mock'ta desteklenmiyor",
        "Gerçek backend devreye girdiğinde bu format çalışacaktır.",
      ),
    );
  }

  return tracedResponse(JSON.stringify(report, null, 2), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Content-Disposition": `attachment; filename="analiz-${analysisId}.json"`,
    },
  });
}
