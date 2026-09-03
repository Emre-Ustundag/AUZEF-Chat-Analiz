"""Iki tur siniflandirmayi tek atama dosyasinda birlestirir.

1. tur 95 maddelik taksonomiyle tum orneklemi isledi; oturmayanlar «none»
kaldi. 2. tur yalnizca o kovayi 25 yeni maddeye karsi isledi. Ikinci turun
q1..q25'i birlesik listede q96..q120'ye kayar.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A1 = ROOT / "outputs" / "analiz" / "atama-ornek-30000.json"
A2 = ROOT / "outputs" / "analiz" / "atama-tur2.json"
T1 = ROOT / "outputs" / "analiz" / "taxonomy-final.json"
T2 = ROOT / "outputs" / "analiz" / "taxonomy-tur2-yeni.json"
OUT_A = ROOT / "outputs" / "analiz" / "atama-birlesik.json"
OUT_T = ROOT / "outputs" / "analiz" / "taxonomy-birlesik.json"


def main() -> int:
    a1 = json.loads(A1.read_text(encoding="utf-8"))
    a2 = {r["session"]: r["question_id"] for r in json.loads(A2.read_text(encoding="utf-8"))}
    t1 = json.loads(T1.read_text(encoding="utf-8"))
    t2 = json.loads(T2.read_text(encoding="utf-8"))
    offset = len(t1)

    moved = 0
    out = []
    for r in a1:
        qid = r["question_id"]
        if qid == "none":
            q2 = a2.get(r["session"], "none")
            if q2 != "none":
                qid = f"q{int(q2[1:]) + offset}"
                moved += 1
        out.append({**r, "question_id": qid})

    OUT_A.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    OUT_T.write_text(json.dumps(t1 + t2, ensure_ascii=False, indent=2), encoding="utf-8")

    vol = collections.Counter(r["question_id"] for r in out)
    total = len(out)
    print(f"taksonomi : {len(t1)} + {len(t2)} = {len(t1) + len(t2)} madde")
    print(f"orneklem  : {total} session")
    print(f"\n1. tur kapsama : {total - len([r for r in a1 if r['question_id'] == 'none'])} "
          f"(%{(total - len([r for r in a1 if r['question_id'] == 'none'])) / total * 100:.1f})")
    print(f"2. turda eklenen: {moved} (%{moved / total * 100:.1f})")
    print(f"BIRLESIK KAPSAMA: {total - vol['none']} (%{(total - vol['none']) / total * 100:.1f})")
    print(f"hala oturmayan  : {vol['none']} (%{vol['none'] / total * 100:.1f})")
    print(f"\n2. TURDA EKLENEN MADDELER — hacme gore:")
    for i, q in enumerate(t2, offset + 1):
        c = vol.get(f"q{i}", 0)
        if c:
            print(f"  {c:>4}  {q['canonical_question'][:66]}")
    print(f"\n-> {OUT_A}\n-> {OUT_T}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
