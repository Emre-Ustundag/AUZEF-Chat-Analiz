import { jsonResponse, problemResponse } from "@/mocks/responses";
import { analysisExists, getAnalysisReportRecord, problem } from "@/mocks/store";
import { invalidUuidProblem } from "@/mocks/validation";

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
  const invalidUuid = invalidUuidProblem(analysisId, "path.analysis_id");
  if (invalidUuid) return problemResponse(invalidUuid);

  if (!analysisExists(analysisId)) {
    return problemResponse(problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Analiz kaydı yok."));
  }

  const report = getAnalysisReportRecord(analysisId);
  if (!report) {
    return problemResponse(
      problem(
        "JOB_CONFLICT",
        409,
        "Analiz henüz tamamlanmadı",
        "Rapor yalnızca completed durumunda alınabilir.",
      ),
    );
  }

  return jsonResponse(report);
}
