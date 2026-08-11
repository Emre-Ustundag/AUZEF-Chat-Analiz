import { NextResponse } from "next/server";

import {
  cancelAnalysisRecord,
  getAnalysisJobRecord,
  problem,
} from "@/mocks/store";

const notFound = () =>
  NextResponse.json(
    problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Analiz kaydı yok."),
    { status: 404 },
  );

export async function GET(
  _request: Request,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]">,
) {
  const { analysisId } = await context.params;
  const job = getAnalysisJobRecord(analysisId);
  return job ? NextResponse.json(job) : notFound();
}

export async function DELETE(
  _request: Request,
  context: RouteContext<"/api/mock/v1/analyses/[analysisId]">,
) {
  const { analysisId } = await context.params;
  return cancelAnalysisRecord(analysisId)
    ? new NextResponse(null, { status: 204 })
    : notFound();
}
