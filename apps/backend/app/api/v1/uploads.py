"""Upload uçları — ADR-0001 §6.

BE-01 contract-only: gövdeler `NotImplementedError` fırlatır ve 501 döner.
"""

from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.api.v1.responses import UPLOAD_CREATE, UPLOAD_DELETE, UPLOAD_READ
from app.schemas.upload import Upload, UploadCreated

router = APIRouter(tags=["uploads"])


@router.post(
    "/uploads",
    response_model=UploadCreated,
    status_code=status.HTTP_202_ACCEPTED,
    responses=UPLOAD_CREATE,
    summary="Dosya yükle",
    description=(
        "Multipart `.xlsx` yükler ve doğrulama/profil job'ını kuyruğa alır. "
        "Opsiyonel `Idempotency-Key` header'ı desteklenir."
    ),
)
async def create_upload(file: UploadFile = File(...)) -> UploadCreated:
    raise NotImplementedError


@router.get(
    "/uploads/{upload_id}",
    response_model=Upload,
    responses=UPLOAD_READ,
    summary="Upload durumu ve profili",
)
async def get_upload(upload_id: UUID) -> Upload:
    raise NotImplementedError


@router.delete(
    "/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=UPLOAD_DELETE,
    summary="Upload'ı iptal et ve temizle",
)
async def delete_upload(upload_id: UUID) -> None:
    raise NotImplementedError
