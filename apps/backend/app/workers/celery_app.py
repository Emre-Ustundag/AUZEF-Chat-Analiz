"""Celery uygulaması.

ADR §2: Celery + Redis broker, ayrı worker container. API ve worker AYNI kod
tabanını paylaşır (ADR §3) — bu modül yalnızca kuyruk yapılandırmasıdır,
iş mantığı `app/workers/tasks.py` ve `app/services/` altındadır.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()

settings = get_settings()

celery_app = Celery(
    "auzef",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # İş kaybını önlemek için: worker task'ı bitirmeden çökerse mesaj kuyrukta
    # kalır ve yeniden dağıtılır. Profilleme idempotent olduğu için güvenli.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # ADR §10 risk 1: 130 MB Excel bellek riski. Her N task'tan sonra worker
    # process'i yenilenir; openpyxl'in bıraktığı parçalanmış bellek birikmez.
    worker_max_tasks_per_child=8,
    task_soft_time_limit=settings.upload_profile_soft_time_limit_seconds,
    task_time_limit=settings.upload_profile_time_limit_seconds,
    # Sonuçlar PostgreSQL'de tutuluyor (ADR §2: "PostgreSQL job durumunun
    # kalıcı gerçek kaynağıdır"); Redis'teki result backend yalnızca hata
    # ayıklama içindir, uzun süre saklanmasına gerek yok.
    result_expires=3600,
)
