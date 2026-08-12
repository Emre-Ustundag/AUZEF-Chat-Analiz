import { jsonResponse, noContentResponse, problemResponse } from "@/mocks/responses";
import { cancelAnalysisRecord, getAnalysisJobRecord, problem } from "@/mocks/store";

const notFound = () =>
  problemResponse(problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Analiz kaydı yok."));

export async function GET(
  _request: Request,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]">,
) {
  const { analysisId } = await context.params;
  const job = getAnalysisJobRecord(analysisId);
  return job ? jsonResponse(job) : notFound();
}

/**
 * ADR-0002 #9: aktif → 204, terminal → 409 JOB_CONFLICT, bilinmeyen → 404.
 *
 * Terminal job'ı sessizce 204'le geçmek, kullanıcıya iptal ettiğini
 * düşündürürken aslında hiçbir şey olmaması demekti.
 */
export async function DELETE(
  _request: Request,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]">,
) {
  const { analysisId } = await context.params;

  switch (cancelAnalysisRecord(analysisId)) {
    case "cancelled":
      return noContentResponse();
    case "terminal":
      return problemResponse(
        problem(
          "JOB_CONFLICT",
          409,
          "İşlem zaten sonlanmış",
          "Tamamlanmış, başarısız veya iptal edilmiş bir analiz iptal edilemez.",
        ),
      );
    case "not-found":
      return notFound();
  }
}
