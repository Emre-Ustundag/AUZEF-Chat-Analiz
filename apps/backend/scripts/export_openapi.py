"""`docs/api/openapi.json` üretir.

    uv run python scripts/export_openapi.py            # yaz
    uv run python scripts/export_openapi.py --check    # CI: fark varsa exit 1

OpenAPI, Pydantic modellerinden TÜRETİLİR; elle yazılmaz. Parity bu yüzden
tanım gereği sağlanır (ADR-0002 #8).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.openapi import build_openapi
from scripts._io import dumps, repo_root, write_or_check

OUTPUT = repo_root() / "docs" / "api" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Yazma, yalnızca farkı raporla.")
    args = parser.parse_args()

    previous_environment = os.environ.get("AUZEF_ENVIRONMENT")
    os.environ["AUZEF_ENVIRONMENT"] = "test"
    get_settings.cache_clear()
    try:
        # Geç import: script production env'i miras alsa bile şema daima
        # dokümanları açık, secretsiz test settings ile üretilir.
        from app.main import create_app

        schema_app = create_app()
        ok = write_or_check(OUTPUT, dumps(build_openapi(schema_app)), check=args.check)
    finally:
        if previous_environment is None:
            os.environ.pop("AUZEF_ENVIRONMENT", None)
        else:
            os.environ["AUZEF_ENVIRONMENT"] = previous_environment
        get_settings.cache_clear()
    if ok and not args.check:
        print(f"yazıldı: {OUTPUT.relative_to(repo_root())}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
