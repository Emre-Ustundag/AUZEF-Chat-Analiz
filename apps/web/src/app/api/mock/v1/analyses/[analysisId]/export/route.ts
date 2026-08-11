import { NextResponse, type NextRequest } from "next/server";

import { exportFormatSchema } from "@/lib/api/schemas";
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
  const format = exportFormatSchema.catch("json").parse(request.nextUrl.searchParams.get("format"));

  const report = getAnalysisReportRecord(analysisId);
  if (!report) {
    return NextResponse.json(
      problem(
        "JOB_CONFLICT",
        409,
        "Rapor hazır değil",
        "Export yalnızca tamamlanmış analiz için alınabilir.",
      ),
      { status: 409 },
    );
  }

  if (format === "xlsx") {
    return NextResponse.json(
      problem(
        "JOB_CONFLICT",
        409,
        "xlsx export mock'ta desteklenmiyor",
        "Gerçek backend devreye girdiğinde bu format çalışacaktır.",
      ),
      { status: 409 },
    );
  }

  return new NextResponse(JSON.stringify(report, null, 2), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Content-Disposition": `attachment; filename="analiz-${analysisId}.json"`,
    },
  });
}
