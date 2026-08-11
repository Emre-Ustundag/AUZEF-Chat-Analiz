import { NextResponse } from "next/server";

import { deleteUploadRecord, getUploadRecord, problem } from "@/mocks/store";

const notFound = () =>
  NextResponse.json(
    problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Upload kaydı yok."),
    { status: 404 },
  );

export async function GET(
  _request: Request,
  context: RouteContext<"/api/mock/v1/uploads/[uploadId]">,
) {
  const { uploadId } = await context.params;
  const upload = getUploadRecord(uploadId);
  return upload ? NextResponse.json(upload) : notFound();
}

export async function DELETE(
  _request: Request,
  context: RouteContext<"/api/mock/v1/uploads/[uploadId]">,
) {
  const { uploadId } = await context.params;
  return deleteUploadRecord(uploadId)
    ? new NextResponse(null, { status: 204 })
    : notFound();
}
