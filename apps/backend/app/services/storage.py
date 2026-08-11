"""S3/MinIO object storage erişimi.

ADR §2: dosyalar ~130 MB olabilir, bu yüzden hiçbir noktada dosyanın tamamı
belleğe alınmaz. Yükleme yolu şöyledir:

    HTTP multipart akışı
      → bloklar hâlinde geçici DOSYAYA yazılır (koşan bayt sayacı ile)
      → boyut sınırı aşılırsa akış anında kesilir
      → boto3 `upload_fileobj` ile MinIO'ya multipart olarak gönderilir

Geçici dosya adımı bilinçlidir: boto3 senkron bir `read()` bekler, FastAPI'nin
`UploadFile`'ı ise asenkron okunur. Araya diski koymak, tüm gövdeyi belleğe
almanın tek alternatifidir.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
else:
    S3Client = Any

logger = get_logger(__name__)


def build_s3_client(settings: Settings | None = None) -> S3Client:
    settings = settings or get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            # MinIO yol tabanlı adresleme kullanır; virtual-host stilinde
            # bucket adı DNS'e taşınır ve compose ağında çözülemez.
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def ensure_bucket(settings: Settings | None = None) -> None:
    """Bucket'ı idempotent biçimde oluşturur.

    MinIO ilk açılışta boş gelir; bucket olmadan ilk upload 500 ile düşerdi.
    API ve worker başlangıcında çağrılır.
    """
    settings = settings or get_settings()
    client = build_s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    try:
        client.create_bucket(Bucket=settings.s3_bucket)
        logger.info("bucket_created", extra={"bucket": settings.s3_bucket})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # İki process (api + worker) aynı anda kurabilir; yarış zararsızdır.
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise


def build_storage_key(upload_id: uuid.UUID, filename: str) -> str:
    """Depolama anahtarı üretir.

    GÜVENLİK: anahtar kullanıcının dosya adından TÜRETİLMEZ. `../../etc/passwd`
    gibi bir ad yol geçişine, çok uzun bir ad ise anahtar sınırı hatasına yol
    açardı. Yalnızca uzantı korunur, o da sabit listeden.
    """
    suffix = Path(filename).suffix.lower()
    safe_suffix = suffix if suffix == ".xlsx" else ".bin"
    return f"uploads/{upload_id}/source{safe_suffix}"


def upload_stream(
    fileobj: IO[bytes],
    key: str,
    settings: Settings | None = None,
) -> None:
    """Dosya nesnesini MinIO'ya multipart olarak yükler."""
    settings = settings or get_settings()
    client = build_s3_client(settings)
    client.upload_fileobj(fileobj, settings.s3_bucket, key)


def download_to_path(key: str, destination: Path, settings: Settings | None = None) -> Path:
    """Nesneyi yerel bir yola indirir (worker profil çıkarmadan önce çağırır)."""
    settings = settings or get_settings()
    client = build_s3_client(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(settings.s3_bucket, key, str(destination))
    return destination


def delete_object(key: str, settings: Settings | None = None) -> None:
    """Nesneyi siler. Zaten yoksa sessizce geçer (DELETE idempotent olmalı)."""
    settings = settings or get_settings()
    client = build_s3_client(settings)
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"NoSuchKey", "404"}:
            raise
        logger.info("delete_object_missing", extra={"key": key})
