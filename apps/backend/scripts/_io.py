"""Üretilmiş artefaktların yazılması ve `--check` karşılaştırması."""

import difflib
import json
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    """apps/backend/scripts/_io.py -> repo kökü."""
    return Path(__file__).resolve().parents[3]


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
