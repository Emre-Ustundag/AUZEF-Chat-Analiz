"""Taksonomiye oturmayan («hicbiri») session'lari ayri bir ornek dosyasina yazar.

Ikinci tur bosluk analizi tum orneklemi degil yalnizca bu kovayi isler:
6.361 kayit icin 64 chunk, ~0,8 USD — 30.000'i bastan kosturmanin dortte biri.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs" / "analiz" / "atama-ornek-30000.json"
DST = ROOT / "outputs" / "analiz" / "ornek-hicbiri.jsonl"


def main() -> int:
    asg = json.loads(SRC.read_text(encoding="utf-8"))
    none = [a for a in asg if a["question_id"] == "none"]
    with DST.open("w", encoding="utf-8") as fh:
        for a in none:
            row = {k: v for k, v in a.items() if k != "question_id"}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    months: collections.Counter[str] = collections.Counter(a["month"] for a in none)
    fail = sum(1 for a in none if a["rej"] or a["fallback"])
    print(f"«hicbiri» session: {len(none)} / {len(asg)} (%{len(none)/len(asg)*100:.1f})")
    print(f"  sorunlu: {fail} (%{fail/len(none)*100:.1f})")
    print(f"  ortalama turn: {sum(a['turns'] for a in none)/len(none):.2f}")
    print(f"\naylik dagilim:")
    for m, c in sorted(months.items()):
        print(f"   {m}: {c:>5}")
    print(f"\n-> {DST}  ({DST.stat().st_size/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
