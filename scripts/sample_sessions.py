"""Aylara ORANTILI katmanli ornek cikarir. Deterministik.

Her aydan, o ayin yazili session hacmiyle orantili sayida kayit alinir ve
secim SISTEMATIK yapilir (rastgele degil): ayni girdi ayni ornegi verir,
kosudan kosuya oynamaz.

Kullanim:  python3 scripts/sample_sessions.py <adet> [cikti_yolu]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs" / "analiz" / "sessions-yil.jsonl"


def systematic(items: list[dict], n: int) -> list[dict]:
    if n >= len(items):
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "outputs" / "analiz" / f"ornek-{n}.jsonl"

    by_month: dict[str, list[dict]] = collections.defaultdict(list)
    total = 0
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            by_month[r["month"]].append(r)
            total += 1
    print(f"kaynak: {total} yazili session, {len(by_month)} ay")

    # Orantili paylar; yuvarlama artigi en buyuk aylara dagitilir.
    months = sorted(by_month)
    exact = {m: n * len(by_month[m]) / total for m in months}
    take = {m: int(exact[m]) for m in months}
    rem = n - sum(take.values())
    for m in sorted(months, key=lambda m: -(exact[m] - take[m]))[:rem]:
        take[m] += 1

    out: list[dict] = []
    print(f"\n{'ay':<9} {'havuz':>7} {'pay':>6} {'ornek':>7}")
    for m in months:
        picked = systematic(by_month[m], take[m])
        out.extend(picked)
        print(f"{m:<9} {len(by_month[m]):>7} {len(by_month[m])/total*100:>5.1f}% {len(picked):>7}")

    with dst.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    fail = sum(1 for r in out if r["rej"] or r["fallback"])
    turns = sum(r["turns"] for r in out)
    print(f"\ntoplam ornek: {len(out)}  ->  {dst}")
    print(f"  ortalama turn/session : {turns/len(out):.2f}")
    print(f"  ret veya fallback tasiyan: {fail} (%{fail/len(out)*100:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
