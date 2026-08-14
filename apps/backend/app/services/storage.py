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
from collections.abc import Iterator
from datetime import UTC, datetime
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


class StorageObjectMissingError(Exception):
    """İstenen nesne bucket'ta yok.

    Genel `ClientError`'dan AYRILMASI gerekiyor: retention işi ham dosyayı
    iş bitiminde siliyor (ADR §9), dolayısıyla "nesne yok" artık bir altyapı
    arızası değil BEKLENEN bir durum. Ayırmadan, aynı upload üzerinde ikinci
    kez analiz başlatan kullanıcı `INTERNAL_ERROR` görürdü — hem yanıltıcı
    hem de traceback üretir.
    """


#: `LIFECYCLE_RULE_ID` sabit: kural her `ensure_bucket` çağrısında aynı
#: kimlikle yazılır, yani tekrar tekrar kurmak yeni kural biriktirmez.
LIFECYCLE_RULE_ID = "auzef-kacak-nesne-temizligi"


def ensure_bucket(settings: Settings | None = None) -> None:
    """Bucket'ı ve lifecycle kuralını idempotent biçimde kurar.

    MinIO ilk açılışta boş gelir; bucket olmadan ilk upload 500 ile düşerdi.
    API ve worker başlangıcında çağrılır.
    """
    settings = settings or get_settings()
    client = build_s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        ensure_lifecycle_policy(settings, client)
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

    ensure_lifecycle_policy(settings, client)


def ensure_lifecycle_policy(
    settings: Settings | None = None, client: S3Client | None = None
) -> None:
    """ADR §9: kaçak dosyalar için azami 24 saat lifecycle.

    DÜRÜST SINIR — S3 lifecycle'ın en küçük birimi GÜN'dür ve `Days=1`
    "oluşturulmasından bir gün sonraki UTC gece yarısı" anlamına gelir;
    yani gerçek silinme 24 ile 48 saat arasında bir yere düşer. Tek başına
    "azami 24 saat" kuralını KARŞILAMAZ.

    Bu yüzden ikili savunma var:

    * `sweep_retention` (Celery beat) tam 24 saatte süpürür — ASIL uygulayıcı
      odur ve saat granülaritesinde çalışır.
    * Bu kural yalnızca AĞ: süpürücü hiç çalışmazsa veya bir nesneyi
      veritabanı kaydı olmadığı için hiç öğrenemezse, storage kendi başına
      da temizler.

    Kurulamazsa istisna FIRLATMAZ: lifecycle desteklemeyen bir S3 uyumlu
    servis yüzünden API'nin açılmaması kabul edilemez; süpürücü zaten işi
    görüyor.
    """
    settings = settings or get_settings()
    client = client or build_s3_client(settings)
    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=settings.s3_bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": LIFECYCLE_RULE_ID,
                        "Status": "Enabled",
                        # Boş `Prefix` = bucket'taki her nesne. Bucket
                        # yalnızca bu uygulamanın upload'larını taşıyor.
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": settings.storage_lifecycle_expiration_days},
                        "AbortIncompleteMultipartUpload": {
                            "DaysAfterInitiation": settings.storage_lifecycle_expiration_days
                        },
                    }
                ]
            },
        )
        logger.info(
            "bucket_lifecycle_configured",
            extra={
                "bucket": settings.s3_bucket,
                "days": settings.storage_lifecycle_expiration_days,
            },
        )
    except ClientError:
        logger.warning("bucket_lifecycle_failed", extra={"bucket": settings.s3_bucket})


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
    """Nesneyi yerel bir yola indirir (worker profil çıkarmadan önce çağırır).

    Nesne yoksa `StorageObjectMissingError` fırlatır — gerekçesi o sınıfın
    docstring'inde: retention ham dosyayı iş bitiminde siliyor, dolayısıyla
    "yok" beklenen bir durum ve altyapı hatasından ayrılmalı.
    """
    settings = settings or get_settings()
    client = build_s3_client(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file(settings.s3_bucket, key, str(destination))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise StorageObjectMissingError(key) from None
        raise
    return destination


def iter_object_ages(
    settings: Settings | None = None,
    prefix: str = "",
) -> Iterator[tuple[str, datetime]]:
    """Bucket'taki nesneleri `(key, last_modified)` olarak gezer.

    Süpürücü kaçak nesneleri bununla bulur. Sayfalayıcı kullanılıyor:
    `list_objects_v2` tek çağrıda en fazla 1000 anahtar döner ve elle
    döngü yazmak, binlerce kaçak nesnenin ilk bininden sonrasını sessizce
    görmezden gelirdi.
    """
    settings = settings or get_settings()
    client = build_s3_client(settings)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            last_modified = item.get("LastModified")
            key = item.get("Key")
            if key is None or last_modified is None:
                continue
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=UTC)
            yield key, last_modified


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
