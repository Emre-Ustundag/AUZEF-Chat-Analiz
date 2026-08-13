"""Üretilmiş artefaktların yazılması ve `--check` karşılaştırması."""

import difflib
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    """apps/backend/scripts/_io.py -> repo kökü."""
    return Path(__file__).resolve().parents[3]


@contextmanager
def frozen_artifact_env() -> Iterator[None]:
    """Artefakt üretimini daima secretsiz test ortamına sabitler.

    Script'ler hem geliştiricinin kabuğundan hem kök `.env`'den `AUZEF_*`
    miras alır. Sabitlemezsek `contract_version` gibi alanlar üretilmiş
    artefakta sızar; üstelik openapi.json ile manifest.json farklı ortamlarda
    üretilirse hiçbir drift kontrolü kırmızıya dönmeden sessizce ayrışırlar.
    Bu yüzden İKİ export script'i de aynı guard'ı kullanır.
    """
    from app.core.config import get_settings

    previous = os.environ.get("AUZEF_ENVIRONMENT")
    os.environ["AUZEF_ENVIRONMENT"] = "test"
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AUZEF_ENVIRONMENT", None)
        else:
            os.environ["AUZEF_ENVIRONMENT"] = previous
        get_settings.cache_clear()


#: `json.dumps` girdisi tanım gereği şemasız; daraltmak yanlış kesinlik verirdi.
JsonValue = Any


def dumps(payload: JsonValue) -> str:
    """Deterministik JSON metni.

    `ensure_ascii=False`: Türkçe metinler okunabilir kalsın. Sondaki newline
    POSIX metin dosyası kuralı; git diff'i temiz tutar.
    """
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_or_check(path: Path, content: str, *, check: bool) -> bool:
    """`check` ise farkı raporlar, değilse yazar. True = her şey yolunda."""
    relative = path.relative_to(repo_root())

    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    if not path.exists():
        print(f"EKSİK: {relative} — `scripts/export_*.py` çalıştırın.", file=sys.stderr)
        return False

    current = path.read_text(encoding="utf-8")
    if current == content:
        return True

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
    )
    print(f"BAYAT: {relative}", file=sys.stderr)
    sys.stderr.writelines(diff)
    return False
