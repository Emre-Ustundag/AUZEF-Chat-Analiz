# ============================================
# apps/backend — FastAPI API ve Celery worker imajı
# Build context: monorepo kökü.
#
# ADR §3: "Backend API ve Celery worker aynı Python domain/pipeline kodunu
# paylaşır; ayrı mikroservis kod tabanları oluşturulmaz."
#
# PLANDAN SAPMA (bilinçli): plan §3.2 (j) `api.Dockerfile` ve
# `worker.Dockerfile` diye İKİ dosya istiyordu. Tek dosyada iki `target`
# kullanıldı, çünkü iki ayrı dosyanın her ikisi de ya
#   (a) kurulum adımlarını KOPYALARDI — elle senkron tutulması gereken iki
#       bağımlılık ağacı, ayrışırsa worker'ın doğrulama kodu API'nin
#       şemalarından sessizce çatallanır; ya da
#   (b) `FROM auzef-backend:latest` ile zincirlenirdi — bu da compose'a
#       build sırası bağımlılığı sokar ve `docker compose build` paralel
#       çalıştığında yarışır.
# İki target aynı katmanları paylaşır ve tek kaynaktan gelir.
# ============================================

FROM python:3.13-slim-bookworm AS base

# uv resmi imajdan kopyalanıyor: kurulum betiği indirmekten hem daha hızlı
# hem de sürüm olarak sabit.
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Bağımlılıklar önce kuruluyor: kaynak kod değiştiğinde bu katman cache'ten
# gelir. `--no-install-project` olmadan her kod değişikliği tüm bağımlılıkları
# yeniden kurdururdu.
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY apps/backend/ ./

RUN uv sync --frozen --no-dev

# Root olmayan kullanıcı: worker güvenilmeyen kullanıcı dosyaları açıyor.
RUN useradd --create-home --uid 1001 auzef && chown -R auzef:auzef /app
USER auzef


# ============================================
# API — uvicorn
# ============================================
FROM base AS api

EXPOSE 8000

# `/api/v1/health/ready` — basit `/health` DEĞİL. Sözleşmedeki readiness ucu
# Postgres, Redis ve object storage'a gerçekten dokunuyor; container'ı ona
# bağlamak, "ayakta ama bağımlılıkları yok" durumundaki bir API'ye compose'un
# `service_healthy` demesini engeller. Süre bütçesi uçta zaten var
# (`services/health.py`: kontrol başına 2 sn), bu yüzden probe asılı kalmaz.
HEALTHCHECK --interval=10s --timeout=8s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=6).status==200 else 1)"

# Migration'lar burada DEĞİL, compose'daki ayrı `migrate` servisinde çalışır:
# birden fazla API replikası aynı anda `alembic upgrade` çalıştırırsa yarışır.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ============================================
# Worker — celery
# ============================================
FROM base AS worker

# `--concurrency=2`: her worker process'i openpyxl ile 130 MB'lık bir dosya
# açabilir; sınırsız eşzamanlılık container'ın belleğini tüketir (ADR §10).
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD celery -A app.workers.celery_app.celery_app inspect ping -d celery@$HOSTNAME || exit 1

CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", \
     "--loglevel=info", "--concurrency=2", "--max-tasks-per-child=8"]
