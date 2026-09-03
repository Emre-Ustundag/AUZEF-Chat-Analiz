"""Yeni chatbot'un bilgi tabanini (module_479_Qna.csv) profiller.

Dosya noktali virgul ayracli, BOM'lu ve alanlarinda gomulu satir sonlari var;
duz okuma bozuk sonuc verir.
"""

from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "module_479_Qna.csv"


def load(path: Path = SRC) -> list[dict]:
    csv.field_size_limit(10 * 1024 * 1024)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def variants(row: dict) -> list[str]:
    return [
        v.strip()
        for k, v in row.items()
        if k and k.startswith("query_") and (v or "").strip()
    ]


def main() -> int:
    rows = load()
    print(f"QnA kaydi : {len(rows)}")
    print(f"kolonlar  : {[k for k in rows[0] if k][:4]} ... (+query_1..20)")

    counts = [len(variants(r)) for r in rows]
    print(
        f"\nalternatif ifade (query_N): toplam {sum(counts)}, "
        f"ortalama {sum(counts) / len(rows):.1f}, maks {max(counts)}"
    )
    print(f"  hic alternatifi olmayan kayit: {sum(1 for c in counts if c == 0)}")

    tags: collections.Counter[str] = collections.Counter()
    for r in rows:
        for t in (r.get("tags") or "").replace(",", ";").split(";"):
            t = t.strip()
            if t:
                tags[t] += 1
    print(f"\ntag: {len(tags)} farkli")
    for t, c in tags.most_common(10):
        print(f"   {c:>4}  {t[:60]}")

    alen = [len((r.get("answer") or "").strip()) for r in rows]
    empty = sum(1 for a in alen if a == 0)
    print(f"\ncevap uzunlugu: ortalama {sum(alen) / len(alen):.0f}, maks {max(alen)}")
    print(f"  cevabi BOS olan kayit: {empty}")

    print("\nILK 8 SORU:")
    for r in rows[:8]:
        v = variants(r)
        print(f"  [{r.get('qna_id')}] {(r.get('question') or '')[:76]}")
        if v:
            print(f"        {len(v)} alt ifade, orn: {v[0][:62]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
