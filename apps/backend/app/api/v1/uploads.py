"""`/api/v1/uploads` — plan §3.2 (e) ve (h).

Boyut savunması İKİ katmanlıdır ve bu bilinçlidir:

1. `Content-Length` başlığı sınırı aşıyorsa istek gövde OKUNMADAN reddedilir.
   Plan §3.2 (e): "sınırı aşan istek gövdeyi tamamen okumadan reddedilmeli".
2. Akış sırasında koşan bir bayt sayacı tutulur. Başlık eksik olabilir
   (`Transfer-Encoding: chunked`) veya YALAN söyleyebilir; kaydedilen
   `size_bytes` her zaman gerçekten sayılan bayttır, başlıktan okunan değer
   değil.
"""

from __future__ import annotations

import tempfile
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import ApiError, ProblemDetails
from app.core.logging import get_logger
from app.models.upload import Upload
from app.schemas.upload import UploadCreated, UploadProfile, UploadRead, UploadStatus
from app.services import storage
from app.workers.tasks import profile_upload

logger = get_logger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])

#: Akıştan tek seferde okunacak blok.
_CHUNK_SIZE = 1024 * 1024

#: `Content-Length` kontrolü için multipart zarfına tanınan pay. Zarf
#: (sınır dizeleri, başlıklar) dosyanın kendisine birkaç yüz bayt ekler;
#: bu pay olmadan tam sınır boyutundaki bir dosya haksız yere reddedilirdi.
_MULTIPART_ENVELOPE_ALLOWANCE = 1024 * 1024

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _reject_too_large(settings: Settings) -> ApiError:
    limit_mb = settings.max_upload_bytes // (1024 * 1024)
    return ApiError(
        "UPLOAD_TOO_LARGE",
        f"En fazla {limit_mb} MB .xlsx yüklenebilir.",
    )


def _validate_filename(filename: str | None) -> str:
    """Uzantı kontrolü (ADR §9: yalnızca `.xlsx`).

    Bu YALNIZCA ucuz bir ön eleme. Asıl doğrulama worker'daki magic bytes ve
    OOXML yapı kontrolüdür — uzantı kullanıcı tarafından yazılır ve hiçbir şey
    kanıtlamaz.
    """
    if not filename:
        raise ApiError("UPLOAD_INVALID_TYPE", "İstek gövdesinde dosya adı yok.")

    if not filename.lower().endswith(".xlsx"):
        raise ApiError(
            "UPLOAD_INVALID_TYPE",
            "Yalnızca .xlsx dosyaları analiz edilebilir.",
        )
    return filename


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadCreated,
    summary="Dosya yükle ve profil işini kuyruğa al",
)
async def create_upload(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> UploadCreated:
    filename = _validate_filename(file.filename)

    # ---- Katman 1: gövdeyi okumadan önce Content-Length ----
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > settings.max_upload_bytes + _MULTIPART_ENVELOPE_ALLOWANCE
    ):
        logger.info("upload_rejected_by_content_length", extra={"declared": declared})
        raise _reject_too_large(settings)

    upload_id = uuid.uuid4()
    storage_key = storage.build_storage_key(upload_id, filename)

    # ---- Katman 2: akış sırasında koşan bayt sayacı ----
    # Geçici dosya diskte tutulur; 130 MB'lık gövde belleğe ALINMAZ (ADR §2).
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as spool:
        size_bytes = 0
        while chunk := await file.read(_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > settings.max_upload_bytes:
                logger.info("upload_rejected_by_stream_counter")
                raise _reject_too_large(settings)
            spool.write(chunk)

        if size_bytes == 0:
            # Boş dosya OOXML olamaz; worker'a göndermeden eliyoruz.
            raise ApiError("UPLOAD_INVALID_TYPE", "Dosya boş.")

        spool.flush()
        spool.seek(0)

        # boto3 senkron çalışıyor; event loop'u bloklamamak için thread'e
        # taşınıyor. Aksi hâlde 130 MB'lık yükleme boyunca API hiçbir isteğe
        # cevap veremezdi.
        await run_in_threadpool(storage.upload_stream, spool, storage_key, settings)

    upload = Upload(
        id=upload_id,
        status=UploadStatus.QUEUED,
        filename=filename,
        size_bytes=size_bytes,
        storage_key=storage_key,
    )
    session.add(upload)
    await session.commit()

    # Kuyruğa alma DB commit'inden SONRA: worker'ın kaydı görebilmesi
    # garanti olmalı, yoksa task "upload bulunamadı" ile boşa düşer.
    profile_upload.delay(str(upload_id))

    logger.info(
        "upload_created",
        extra={"upload_id": str(upload_id), "size_bytes": size_bytes},
    )
    return UploadCreated(upload_id=upload_id, status=UploadStatus.QUEUED)


async def _get_upload_or_404(session: AsyncSession, upload_id: uuid.UUID) -> Upload:
    upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
    if upload is None:
        raise ApiError("JOB_NOT_FOUND", "Bu kimlikle bir yükleme bulunamadı.")
    return upload


@router.get(
    "/{upload_id}",
    response_model=UploadRead,
    summary="Yükleme durumu ve hazırsa profil",
)
async def get_upload(upload_id: uuid.UUID, session: SessionDep) -> UploadRead:
    upload = await _get_upload_or_404(session, upload_id)

    return UploadRead(
        upload_id=upload.id,
        status=upload.status,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        created_at=upload.created_at,
        # JSONB sütunları şemadan geçiriliyor: veritabanındaki eski biçimli
        # bir kayıt sözleşmeyi sessizce bozmasın.
        profile=UploadProfile.model_validate(upload.profile) if upload.profile else None,
        error=ProblemDetails.model_validate(upload.error) if upload.error else None,
    )


@router.delete(
    "/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Yüklemeyi iptal et ve depolamayı temizle",
)
async def delete_upload(
    upload_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    upload = await _get_upload_or_404(session, upload_id)
    storage_key = upload.storage_key

    await session.delete(upload)
    await session.commit()

    # Depolama temizliği DB silmesinden sonra: buradaki bir hata kullanıcıyı
    # ilgilendirmez, kayıt zaten gitti. Kaçak nesneler için ADR §9'un 24
    # saatlik lifecycle kuralı var.
    try:
        await run_in_threadpool(storage.delete_object, storage_key, settings)
    except Exception:
        logger.exception("upload_storage_cleanup_failed", extra={"upload_id": str(upload_id)})

    logger.info("upload_deleted", extra={"upload_id": str(upload_id)})

    # 204 gövdesi GERÇEKTEN boş olmalı: frontend client.ts status===204'te
    # kısa devre yapıyor ve gövdeyi okumuyor.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
