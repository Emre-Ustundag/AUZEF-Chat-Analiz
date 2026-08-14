# Backend sözleşme fixture'ları

Buradaki JSON dosyaları **elle yazılmadı**. Gerçek backend'in uçtan uca
koşusundan çıkan gövdelerin aynısıdır ve `backend-contract.test.ts`
tarafından arayüzün Zod şemalarına karşı doğrulanır.

Amaç, `schemas.test.ts`'in kanıtlayamadığı şeyi kanıtlamak: orada elle
kurulmuş nesneler doğrulanıyor, yani şema yalnızca kendisiyle tutarlı
olduğunu gösteriyor. Buradaki dosyalar ise backend'in **gerçekten
ürettiği** gövdenin arayüzün beklediği biçimde olduğunu gösterir. Bir alan
adı ayrışırsa hata burada yakalanır — kullanıcı boş ekran görmeden önce.

## `faz3-llm-report.json`

Faz 3 LLM pipeline'ının çıktısı: upload → ön işleme → chunk → OpenRouter
map/reduce → deterministik toplama → rapor.

OpenRouter tarafı `tests/fake_openrouter.py` üzerinden sahte transport ile
karşılandı (elimizde gerçek anahtar yok); backend'in geri kalanı gerçek
koddur. Koşu bilinçli olarak modelin **kayıt atladığı** bir senaryoyla
yapıldı, böylece fixture `warnings[]` alanını da kapsıyor.

Yeniden üretmek için (`docker compose up -d` gerekir):

```bash
cd apps/backend
cat > tests/test_zz_dump.py <<'PY'
from __future__ import annotations
import json, os, uuid
from pathlib import Path
import pytest
from tests.test_analysis_integration import (  # noqa: F401
    _bucket, _create, _no_broker, _ready_upload, client, install_fake_provider,
)
from app.workers import tasks

pytestmark = pytest.mark.integration
OUT = Path(os.environ["DUMP_PATH"])

async def test_dump(client, monkeypatch) -> None:
    install_fake_provider(monkeypatch, drop_records=1)
    upload_id, _ = await _ready_upload(client)
    created = await _create(client, upload_id)
    analysis_id = uuid.UUID(created.json()["analysis_id"])
    assert await tasks.run_analysis(analysis_id) == "completed"
    report = (await client.get(f"/api/v1/analyses/{analysis_id}/result")).json()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    await client.delete(f"/api/v1/uploads/{upload_id}")
PY
DUMP_PATH=../web/src/lib/api/schemas/__fixtures__/faz3-llm-report.json \
  .venv/bin/python -m pytest tests/test_zz_dump.py -q
rm tests/test_zz_dump.py
```

`analysis_id` ve `generated_at` her koşuda değişir; testler bu iki alanın
değerine bakmaz.
