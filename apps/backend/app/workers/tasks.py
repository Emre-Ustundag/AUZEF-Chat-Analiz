"""Celery task'ları — Faz 1: upload doğrulama ve profilleme.

ADR §5 "Aşama A". İş mantığı asenkron bir fonksiyonda (`run_upload_profiling`)
tutulur; Celery task'ı yalnızca ince bir sarmalayıcıdır. Bunun iki sebebi var:

* Test edilebilirlik — testler task'ı doğrudan `await` edebilir, Celery
  broker'ı veya `task_always_eager` gerekmez.
* Hata yönetimi — profil işi başarısız olduğunda kullanıcıya HTTP hatası
  dönemeyiz (istek çoktan 202 ile kapandı). Hata, sözleşmeye uygun bir
  ProblemDetails olarak `uploads.error` sütununa YAZILIR ve frontend'in
  polling'i onu oradan okur.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import build_problem
from app.core.logging import get_logger
from app.models.upload import Upload
from app.schemas.upload import UploadProfile, UploadStatus
from app.services import storage
from app.services.xlsx import XlsxRejectedError, validate_and_profile
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def run_upload_profiling(upload_id: uuid.UUID) -> str:
    """Bir upload'ı doğrular, profilini çıkarır ve sonucu veritabanına yazar.

    Dönen değer son durumdur ("ready" veya "failed"). İstisna FIRLATMAZ:
    kullanıcıya görünen hata, kayıttaki `error` sütunudur.
    """
    settings = get_settings()

    async with session_scope() as session:
        upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
        if upload is None:
            # Kullanıcı iş kuyrukta beklerken DELETE çağırmış olabilir.
            logger.info("profile_upload_missing", extra={"upload_id": str(upload_id)})
            return "missing"

        if upload.status in {UploadStatus.READY, UploadStatus.FAILED}:
            # acks_late + yeniden dağıtım yüzünden aynı iş iki kez gelebilir.
            logger.info("profile_upload_already_settled", extra={"upload_id": str(upload_id)})
            return str(upload.status.value)

        upload.status = UploadStatus.VALIDATING
        storage_key = upload.storage_key
        await session.commit()

    problem_payload: dict[str, object] | None = None
    profile_payload: dict[str, object] | None = None

    # Geçici dizin `with` bloğuyla yönetiliyor: hata yolunda da 130 MB'lık
    # dosya diskte kalmasın (ADR §9 retention).
    with tempfile.TemporaryDirectory(prefix="auzef-upload-") as tmpdir:
        local_path = Path(tmpdir) / "source.xlsx"
        try:
            storage.download_to_path(storage_key, local_path, settings)
            raw_profile = validate_and_profile(local_path, settings)
            # Şemadan geçiriyoruz: veritabanına sözleşmeye uymayan bir gövde
            # yazmaktansa burada patlamak yeğdir.
            profile_payload = UploadProfile.model_validate(raw_profile).model_dump(mode="json")
        except XlsxRejectedError as exc:
            logger.warning(
                "upload_rejected",
                extra={"upload_id": str(upload_id), "reason": exc.reason},
            )
            problem_payload = build_problem(
                "UPLOAD_CORRUPT_OR_ENCRYPTED",
                "Dosya okunamadı. Bozuk, şifrelenmiş veya makro içeren dosyalar desteklenmez.",
            ).to_payload()
        except Exception:
            # ADR §9: hata cevabına dosya içeriği veya iz sızmaz. İstisna
            # yalnızca loglanır, kullanıcıya genel bir mesaj gider.
            logger.exception("upload_profiling_failed", extra={"upload_id": str(upload_id)})
            problem_payload = build_problem(
                "INTERNAL_ERROR",
                "Dosya işlenirken beklenmeyen bir hata oluştu.",
            ).to_payload()

    async with session_scope() as session:
        upload = await session.scalar(select(Upload).where(Upload.id == upload_id))
        if upload is None:
            return "missing"

        if problem_payload is not None:
            upload.status = UploadStatus.FAILED
            upload.error = problem_payload
            upload.profile = None
        else:
            upload.status = UploadStatus.READY
            upload.profile = profile_payload
            upload.error = None

        await session.commit()
        final_status = str(upload.status.value)

    logger.info(
        "upload_profiling_finished",
        extra={"upload_id": str(upload_id), "status": final_status},
    )
    return final_status


@celery_app.task(name="app.workers.tasks.profile_upload")  # type: ignore[untyped-decorator]
def profile_upload(upload_id: str) -> str:
    """Celery giriş noktası.

    `asyncio.run` gerekçesi `app/core/db.py` modül başlığında açıklanmıştır:
    plan bağımlılık listesinde senkron bir Postgres sürücüsü yok.
    """
    return asyncio.run(run_upload_profiling(uuid.UUID(upload_id)))
