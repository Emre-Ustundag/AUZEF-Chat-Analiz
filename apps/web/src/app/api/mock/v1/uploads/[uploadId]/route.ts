import { jsonResponse, noContentResponse, problemResponse } from "@/mocks/responses";
import { deleteUploadRecord, getUploadRecord, problem } from "@/mocks/store";

const notFound = () =>
  problemResponse(problem("JOB_NOT_FOUND", 404, "İşlem bulunamadı", "Upload kaydı yok."));

export async function GET(
  _request: Request,
  context: RouteContext<"/api/mock/v1/uploads/[uploadId]">,
) {
  const { uploadId } = await context.params;
  const upload = getUploadRecord(uploadId);
  return upload ? jsonResponse(upload) : notFound();
}

export async function DELETE(
  _request: Request,
  context: RouteContext<"/api/mock/v1/uploads/[uploadId]">,
) {
  const { uploadId } = await context.params;
  return deleteUploadRecord(uploadId) ? noContentResponse() : notFound();
}
