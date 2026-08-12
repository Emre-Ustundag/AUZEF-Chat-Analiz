"""`tests/fixtures/contract/` üretir — iki dilli drift kontrolünün çekirdeği.

    uv run python scripts/export_fixtures.py            # yaz
    uv run python scripts/export_fixtures.py --check    # CI: fark varsa exit 1

`--check` PAZARLIKSIZ: olmazsa biri Pydantic modelini düzenler, yeniden
üretmeyi unutur ve hem pytest hem vitest bayat fixture'lara karşı yeşil
kalır. Çoğu fixture tabanlı drift kontrolünü dekoratif yapan şey tam olarak
budur.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from app.core.config import get_settings
from app.schemas.examples import CONSTRAINT_CASES, build_cases
from scripts._io import dumps, repo_root, write_or_check

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

    previous_environment = os.environ.get("AUZEF_ENVIRONMENT")
    os.environ["AUZEF_ENVIRONMENT"] = "test"
    get_settings.cache_clear()
    try:
        cases = build_cases()
        contract_version = get_settings().contract_version
    finally:
        if previous_environment is None:
            os.environ.pop("AUZEF_ENVIRONMENT", None)
        else:
            os.environ["AUZEF_ENVIRONMENT"] = previous_environment
        get_settings.cache_clear()
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
            ok &= write_or_check(OUTPUT_DIR / file_name, dumps(payload), check=args.check)

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
        "contract_version": contract_version,
        "cases": manifest_cases,
    }
    ok &= write_or_check(OUTPUT_DIR / "manifest.json", dumps(manifest), check=args.check)

    constraints = _HEADER | {"cases": CONSTRAINT_CASES}
    ok &= write_or_check(OUTPUT_DIR / "constraints.json", dumps(constraints), check=args.check)

    if ok and not args.check:
        written = sum(1 for c in cases if c.payload is not None) + 2
        print(f"yazıldı: {written} dosya -> {OUTPUT_DIR.relative_to(repo_root())}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
