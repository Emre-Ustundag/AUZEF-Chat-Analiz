# API sözleşmesi

`openapi.json` **üretilmiş bir dosyadır — elle düzenlemeyin.**

Kaynak, `apps/backend/app/schemas/` altındaki Pydantic modelleri ve
`apps/backend/app/api/v1/` altındaki route imzalarıdır. Şema bunlardan
türetildiği için Pydantic ↔ OpenAPI uyumu tanım gereği sağlanır.

## Yeniden üretme

```bash
make generate            # openapi.json + tests/fixtures/contract/
# veya
cd apps/backend && uv run --locked python scripts/export_openapi.py
```

## Drift kontrolü

```bash
make contract
```

CI aynı adımları çalıştırır ve dosya bayatsa düşer. Bir Pydantic modelini
değiştirdiyseniz artefaktları yeniden üretip commit'lemeniz gerekir.

OpenAPI'deki request/response örnekleri ayrı elle yazılmış kopyalar değildir;
fixture'larla aynı Pydantic instance'larından üretilir. Hata cevapları yalnızca
`application/problem+json`, tüm cevaplar örnek bir `X-Trace-Id` header'ı taşır.

## İlgili

- Kararlar: [`docs/adr/0002-api-contract-freeze.md`](../adr/0002-api-contract-freeze.md)
- Mimari: [`docs/mimari.md`](../mimari.md)
- Frontend şemaları: `apps/web/src/lib/api/schemas/`
- Paylaşılan fixture'lar: `tests/fixtures/contract/`
