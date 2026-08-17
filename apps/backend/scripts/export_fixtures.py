"""`tests/fixtures/contract/` üretir — iki dilli drift kontrolünün çekirdeği.

    uv run python scripts/export_fixtures.py            # yaz
    uv run python scripts/export_fixtures.py --check    # CI: fark varsa exit 1

`--check` PAZARLIKSIZ: olmazsa biri Pydantic modelini düzenler, yeniden
üretmeyi unutur ve hem pytest hem vitest bayat fixture'lara karşı yeşil
kalır. Çoğu fixture tabanlı drift kontrolünü dekoratif yapan şey tam olarak
budur.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from app.core.config import MAX_ROWS, MAX_UPLOAD_BYTES, get_settings
from app.schemas.analysis import AnalysisRequest
from app.schemas.examples import CONSTRAINT_CASES, build_cases
from app.services import idempotency
from scripts._io import dumps, frozen_artifact_env, repo_root, write_or_check

OUTPUT_DIR = repo_root() / "tests" / "fixtures" / "contract"

_HEADER = {
    "$comment": (
        "ÜRETİLMİŞ DOSYA — elle düzenlemeyin. Kaynak: apps/backend/scripts/export_fixtures.py"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Yazma, yalnızca farkı raporla.")
    args = parser.parse_args()

    with frozen_artifact_env():
        return _export(check=args.check)


def _export(*, check: bool) -> int:
    settings = get_settings()
    cases = build_cases()
    ok = True
    manifest_cases = []

    for case in cases:
        file_name: str | None = None
        if case.payload is not None:
            assert isinstance(case.payload, BaseModel)
            file_name = f"{case.id}.json"
            # mode="json" gerçek serializer'ları çalıştırır: Z datetime,
            # retry_after bastırma, UUID casing. Fixture'ın değerli olmasının
            # tek sebebi bu.
            payload = case.payload.model_dump(mode="json")
            ok &= write_or_check(OUTPUT_DIR / file_name, dumps(payload), check=check)

        manifest_cases.append(
            {
                "id": case.id,
                "method": case.method,
                "path": case.path,
                "status": case.status,
                "model": case.model,
                "file": file_name,
            }
        )

    manifest = _HEADER | {
        # `settings`'ten okunuyor, elle yazılmıyor: sürüm bump'ında
        # manifest.json ile openapi.json'ın sessizce ayrışmasını engeller.
        "contract_version": settings.contract_version,
        # Sözleşmede donmuş sınırlar. Frontend `LIMITS` sabitleri buraya karşı
        # doğrulanır (contract-fixtures.test.ts); iki dilin aynı sayıyı
        # gördüğünü kanıtlayan tek yer burası.
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_rows": MAX_ROWS,
        },
        "cases": manifest_cases,
    }
    ok &= write_or_check(OUTPUT_DIR / "manifest.json", dumps(manifest), check=check)

    constraints = _HEADER | {"cases": CONSTRAINT_CASES}
    ok &= write_or_check(OUTPUT_DIR / "constraints.json", dumps(constraints), check=check)

    fingerprints = _HEADER | {"cases": _fingerprint_cases()}
    ok &= write_or_check(
        OUTPUT_DIR / "idempotency.fingerprints.json", dumps(fingerprints), check=check
    )

    if ok and not check:
        written = sum(1 for c in cases if c.payload is not None) + 3
        print(f"yazıldı: {written} dosya -> {OUTPUT_DIR.relative_to(repo_root())}")
    return 0 if ok else 1


def _fingerprint_cases() -> list[dict[str, object]]:
    """`Idempotency-Key` fingerprint'lerinin iki dilli kanıtı (ADR-0002 #3).

    Fingerprint kuralı iki dilde AYRI yazıldı: Python
    `app/services/idempotency.py`, TypeScript `apps/web/src/mocks/idempotency.ts`.
    Fingerprint'ler tel üstünde karşılaşmadığı için ayrışmaları hiçbir çalışma
    zamanı hatası üretmez — mock'a karşı geliştirilen bir istemci gerçek
    backend'de sessizce başka davranırdı. Bu dosya farkı CI'da yakalar.

    `max_cost_usd` vakaları BİLEREK hem tam sayı değerli (5.0) hem kesirli
    (2.5): `JSON.stringify(5.0)` → `5`, Python'un varsayılanı `5.0` olurdu ve
    sözleşmenin EN TİPİK gövdesinde ayrışırlardı.
    """
    analysis_inputs: list[dict[str, object]] = [
        {
            "upload_id": "8f14e45f-ceea-467a-9f6b-2c1d3e4a5b6c",
            "sheet_name": "Mesajlar",
            "text_column": "mesaj",
            "model": "anthropic/claude-sonnet-4.6",
            "prompt_version": "faq_analysis/v1",
            "top_n": 20,
            "max_cost_usd": 5.0,
        },
        {
            "upload_id": "8f14e45f-ceea-467a-9f6b-2c1d3e4a5b6c",
            "sheet_name": "Sayfa 1",
            "text_column": "soru metni",
            "model": "google/gemini-2.5-flash",
            "prompt_version": "faq_analysis/v1",
            "top_n": 1,
            "max_cost_usd": 2.5,
        },
    ]

    upload_inputs: list[dict[str, object]] = [
        {
            "file_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "filename": "auzef-mesajlar.xlsx",
            "mime_type": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "size": 7377,
        },
        {
            "file_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "filename": "ğüşiöç.xlsx",
            "mime_type": "application/octet-stream",
            "size": 0,
        },
    ]

    cases: list[dict[str, object]] = []
    for index, payload in enumerate(analysis_inputs):
        cases.append(
            {
                "id": f"analyses.fingerprint.{index}",
                "kind": "analysis",
                "input": payload,
                "canonical_json": idempotency.canonical_json(payload),
                "fingerprint": idempotency.analysis_fingerprint(
                    AnalysisRequest.model_validate(payload)
                ),
            }
        )

    for index, metadata in enumerate(upload_inputs):
        cases.append(
            {
                "id": f"uploads.fingerprint.{index}",
                "kind": "upload",
                "input": metadata,
                "canonical_json": idempotency.canonical_json(metadata),
                "fingerprint": idempotency.upload_fingerprint(
                    file_sha256=str(metadata["file_sha256"]),
                    filename=str(metadata["filename"]),
                    mime_type=str(metadata["mime_type"]),
                    size=int(str(metadata["size"])),
                ),
            }
        )

    return cases


if __name__ == "__main__":
    raise SystemExit(main())
