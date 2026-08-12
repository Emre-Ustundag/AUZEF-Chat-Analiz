"""Retention süpürücüsü — ADR §9.

ADR §9'un üç cümlesini uygular:

* "Ham upload ve Parquet ara dosya işlem sonunda silinir" — bunu
  `workers/tasks.py::run_analysis` kendi `finally` bloğunda yapar; bu
  modül **kaçanları** toplar.
* "kaçak dosyalar için azami 24 saat lifecycle uygulanır"
* "Toplu ve redakte rapor varsayılan 24 saat tutulur"

## Neden ayrı bir süpürücü gerekiyordu

Faz 1'den beri temizlik YALNIZCA DELETE uçlarında yapılıyordu, yani
kullanıcı düğmeye basarsa. Basmazsa hiçbir şey silinmiyordu: rapor da,
ham dosya da süresiz duruyordu. "Varsayılan 24 saat tutulur" bir
saklama süresi değil, bir ÜST SINIR; kimsenin tetiklemediği bir yol
onu uygulayamaz. Bu yüzden periyodik iş (Celery beat) zorunlu.

## Saat neye göre işliyor

Terminal kayıtlarda `updated_at` etkin olarak `settled_at`'tir: iş
sonlandıktan sonra o satıra bir daha yazılmaz. Ayrı bir sütun eklemek
yerine bunu kullanmak migration'dan kaçınmak için değil, DOĞRU olduğu
için: retention'ın saydığı şey "kayıt ne zamandan beri hareketsiz",
raporun içindeki `generated_at` değil (o, rapor gövdesinden gelir ve
şema değişirse kaybolabilir).

## Silme sırası

1. **Süresi dolan analizler** — rapor retention'ı. Satır silinir,
   `report` sütunu `NULL`'lanmaz (gerekçe `core/config.py`'de).
2. **Süresi dolan upload'lar** — önce storage nesnesi, sonra satır.
   Analiz kayıtları FK CASCADE ile gider.
3. **Kaçak nesneler** — hiçbir upload satırının işaret etmediği ve
   yeterince eskimiş nesneler.

Sıra önemli: upload silmek analizleri de götürdüğü için (2) tek başına
(1)'i büyük ölçüde kapsar, ama tersi doğru değil — analizi olmayan bir
upload yalnızca (2)'de yakalanır.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.analysis import Analysis
from app.models.upload import Upload
from app.schemas.analysis import TERMINAL_STATUSES
from app.services import storage

logger = get_logger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """Süpürmenin sayaçları. Loglanır ve testlerde doğrulanır."""

    expired_analyses: int = 0
    expired_uploads: int = 0
    orphan_objects: int = 0

    @property
    def total(self) -> int:
        return self.expired_analyses + self.expired_uploads + self.orphan_objects


def _cutoff(hours: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(hours=hours)


async def sweep_expired_analyses(
    session: AsyncSession,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Retention süresi dolmuş TERMİNAL analizleri (raporlarıyla) siler.

    Devam eden iş ASLA silinmez: `status` filtresi olmadan 24 saatten uzun
    süren bir analiz (hard timeout 45 dk ama kuyrukta bekleyebilir) süpürücü
    tarafından ortadan kaldırılır ve kullanıcı sonsuza kadar 404 poll ederdi.
    """
    cutoff = _cutoff(settings.report_retention_hours, now)
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(Analysis).where(
                Analysis.status.in_(TERMINAL_STATUSES),
                Analysis.updated_at < cutoff,
            )
        ),
    )
    await session.commit()
    return int(result.rowcount)


async def sweep_expired_uploads(
    session: AsyncSession,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Süresi dolmuş upload kayıtlarını ve ham dosyalarını siler.

    Nesne ÖNCE, satır SONRA silinir. Ters sırada bir çökme, storage
    anahtarını bilen tek kaydı yok eder ve dosya kaçak nesneye dönüşürdü —
    o zaman ancak (3). adım veya lifecycle yakalayabilirdi.
    """
    cutoff = _cutoff(settings.upload_retention_hours, now)
    uploads = list((await session.scalars(select(Upload).where(Upload.updated_at < cutoff))).all())

    removed = 0
    for upload in uploads:
        try:
            # boto3 senkron; kodun geri kalanıyla aynı gerekçeyle thread'e
            # taşınıyor (bkz. api/v1/uploads.py).
            await asyncio.to_thread(storage.delete_object, upload.storage_key, settings)
        except Exception:
            # Storage hatası satırın silinmesini ENGELLEMEMELİ: kalan nesne
            # kaçak olarak (3). adımda veya lifecycle ile toplanır, ama
            # satırı bırakırsak retention hiç ilerlemez.
            logger.exception("retention_object_delete_failed", extra={"upload_id": str(upload.id)})
        await session.delete(upload)
        removed += 1

    await session.commit()
    return removed


async def sweep_orphan_objects(
    session: AsyncSession,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Veritabanında karşılığı olmayan eskimiş nesneleri siler (ADR §9 "kaçak").

    YAŞ KONTROLÜ ŞART: yaş bakmadan silmek, upload isteği MinIO'ya yazdıktan
    sonra ama DB commit'inden önce çalışan bir süpürücünün kullanıcının
    dosyasını ayağının altından çekmesi demekti. Retention penceresi bu yarışa
    fazlasıyla yer bırakıyor.
    """
    cutoff = _cutoff(settings.orphan_object_retention_hours, now)
    known = set((await session.scalars(select(Upload.storage_key))).all())

    # Listeleme tek seferde thread'de yapılıyor: sayfalayıcı senkron ve her
    # sayfa bir HTTP çağrısı.
    objects = await asyncio.to_thread(lambda: list(storage.iter_object_ages(settings)))

    removed = 0
    for key, last_modified in objects:
        if key in known or last_modified >= cutoff:
            continue
        try:
            await asyncio.to_thread(storage.delete_object, key, settings)
            removed += 1
        except Exception:
            logger.exception("retention_orphan_delete_failed", extra={"key": key})

    return removed


async def sweep(
    session: AsyncSession,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> SweepResult:
    """Üç adımı sırayla çalıştırır ve sayaçları döndürür."""
    settings = settings or get_settings()

    result = SweepResult(
        expired_analyses=await sweep_expired_analyses(session, settings, now),
        expired_uploads=await sweep_expired_uploads(session, settings, now),
        orphan_objects=await sweep_orphan_objects(session, settings, now),
    )

    if result.total:
        logger.info(
            "retention_swept",
            extra={
                "expired_analyses": result.expired_analyses,
                "expired_uploads": result.expired_uploads,
                "orphan_objects": result.orphan_objects,
            },
        )
    return result
