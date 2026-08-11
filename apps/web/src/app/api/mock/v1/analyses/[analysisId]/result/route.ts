import { NextResponse } from "next/server";

import {
  analysisExists,
  getAnalysisReportRecord,
  problem,
} from "@/mocks/store";

/**
 * GET /api/mock/v1/analyses/{analysisId}/result
 *
 * Yalnızca tamamlanmış analiz için rapor döner. Devam eden bir iş için
 * 409 dönmek bilinçli: arayüz raporu erken istemeye kalkarsa bu sessizce
 * boş veri değil, açık bir hata olarak görünmeli.
 */
export async function GET(
  _request: Request,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]/result">,
) {
  const { analysisId } = await context.params;

  if (!analysisExists(analysisId)) {
    return NextResponse.json(
      problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Analiz kaydı yok."),
      { status: 404 },
    );
  }

  const report = getAnalysisReportRecord(analysisId);
  if (!report) {
    return NextResponse.json(
      problem(
        "JOB_CONFLICT",
        409,
        "Analiz henüz tamamlanmadı",
        "Rapor yalnızca completed durumunda alınabilir.",
      ),
      { status: 409 },
    );
  }

  return NextResponse.json(report);
}
