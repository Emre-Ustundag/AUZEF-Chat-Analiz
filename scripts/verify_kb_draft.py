"""Uretilen KB taslagini dogrular: format module_479_Qna.csv ile uyumlu mu?"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs" / "analiz" / "KB-taslak-eksik-sorular.csv"
REF = ROOT / "module_479_Qna.csv"


def read(path: Path) -> list[dict]:
    csv.field_size_limit(10 * 1024 * 1024)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def main() -> int:
    draft = read(SRC)
    ref = read(REF)
    dcols = [c for c in draft[0] if c and not c.startswith("_")]
    rcols = [c for c in ref[0] if c and c != "qna_id"]
    print(f"taslak satir: {len(draft)}")
    print(f"taslak kolon (yardimci haric): {dcols[:3]} ... {dcols[-1]}")
    print(f"referans kolon (qna_id haric): {rcols[:3]} ... {rcols[-1]}")
    print(f"KOLON UYUMU: {'EVET' if dcols == rcols else 'HAYIR'}")
    if dcols != rcols:
        print(f"  fark: {set(dcols) ^ set(rcols)}")

    bos_answer = sum(1 for r in draft if not (r.get("answer") or "").strip())
    print(f"\nanswer alani bos olan: {bos_answer}/{len(draft)}  (hepsi bos OLMALI)")
    noq = [r["question"] for r in draft if not (r.get("query_1") or "").strip()]
    print(f"hic gercek ifadesi olmayan: {len(noq)}")

    print("\nORNEK SATIR:")
    r = draft[0]
    print(f"  question : {r['question']}")
    print(f"  tags     : {r['tags'] or '(bos - elle doldurun)'}")
    print(f"  answer   : {r['answer'] or '(BOS - sizin dolduracaginiz alan)'}")
    qs = [r[f"query_{i}"] for i in range(1, 21) if (r.get(f"query_{i}") or "").strip()]
    print(f"  gercek ogrenci ifadeleri ({len(qs)}):")
    for q in qs[:6]:
        print(f"     · {q}")
    print(f"  hacim {r['_hacim']} | sorunlu %{r['_sorunlu_yuzde']}")
    print(f"  eski bot cevabi (taslak icin):\n     {r['_eski_bot_cevabi'][:260]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
