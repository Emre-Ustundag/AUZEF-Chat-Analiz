# AUZEF Chat Analiz — Backend

Bu paket **BE-01 kapsamında contract-only**dir: Pydantic modelleri, route imzaları,
merkezi RFC 9457 hata yönetimi ve üretilmiş OpenAPI şeması içerir. Route gövdeleri
`NotImplementedError` fırlatır ve **501** döner; gerçek uygulama BE-02'de gelir.

Kararların tamamı için: [`docs/adr/0002-api-contract-freeze.md`](../../docs/adr/0002-api-contract-freeze.md)

## Kurulum

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv yoksa
cd apps/backend
uv sync --locked --dev
```

## Kalite kapıları

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

## Sözleşme artefaktlarını yeniden üretme

`docs/api/openapi.json` ve `tests/fixtures/contract/` **üretilmiş** dosyalardır;
elle düzenlenmezler. Pydantic modellerini değiştirdiyseniz yeniden üretin:

```bash
uv run --locked python scripts/export_openapi.py
uv run --locked python scripts/export_fixtures.py
```

CI aynı script'leri `--check` ile çalıştırır ve fark bulursa düşer:

```bash
uv run --locked python scripts/export_openapi.py --check
uv run --locked python scripts/export_fixtures.py --check
```

## Çalıştırma

```bash
uv run --locked uvicorn app.main:app --reload --port 8000
open http://localhost:8000/docs
```

## BE-02'nin iş listesi

```bash
grep -rn "raise NotImplementedError" app/api    # tam 9 hit
```
