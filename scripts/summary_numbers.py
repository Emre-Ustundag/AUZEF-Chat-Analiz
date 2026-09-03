"""Yonetici ozeti icin rakamlari tek yerden hesaplar (elle yazip yanilmamak icin)."""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "outputs" / "analiz" / "atama-birlesik.json"
G = ROOT / "outputs" / "analiz" / "kb-bosluk-analizi.json"

WRITTEN = 289_567          # yillik yazili session (olculdu)
SESSIONS = 592_964
ROWS = 6_405_948
OK, REJ = 388_586, 133_112  # cevap duzeyinde onay/ret (yillik, QuickReplyLabel)


def main() -> int:
    asg = json.loads(A.read_text(encoding="utf-8"))
    gaps = json.loads(G.read_text(encoding="utf-8"))
    n = len(asg)
    scale = WRITTEN / n

    vol = collections.Counter(a["question_id"] for a in asg)
    prob = sum(1 for a in asg if a["rej"] or a["fallback"])
    fb = sum(1 for a in asg if a["fallback"])
    rej = sum(1 for a in asg if a["rej"])

    cov = {g["soru"]: g["kb_id"] != "yok" for g in gaps}
    by_q = {g["soru"]: g for g in gaps}
    top20 = [g for g in sorted(gaps, key=lambda g: -g["adet"])[:20]]
    top20_missing = [g for g in top20 if not cov[g["soru"]]]

    vol_cov = sum(g["adet"] for g in gaps if cov[g["soru"]])
    vol_mis = sum(g["adet"] for g in gaps if not cov[g["soru"]])
    none = vol["none"]

    print(f"VERI       : {ROWS:,} satir | {SESSIONS:,} session | yazili {WRITTEN:,}")
    print(f"ORNEKLEM   : {n:,} (%{n/WRITTEN*100:.1f}), aylara orantili")
    print(f"olcek carpani: {scale:.2f}\n")

    print(f"CEVAP DUZEYI onay: {OK/(OK+REJ)*100:.1f}%  ({OK:,} onay / {REJ:,} ret)")
    print(f"SESSION DUZEYI sorunlu: %{prob/n*100:.1f}  "
          f"(ret {rej/n*100:.1f}% | anlamadi {fb/n*100:.1f}%)")
    print(f"  -> populasyonda ~{int(prob*scale):,} sorunlu konusma\n")

    print(f"KB KAPSAMI")
    print(f"  kapsanan   : %{vol_cov/n*100:.1f}  (~{int(vol_cov*scale):,} konusma)")
    print(f"  kapsanmayan: %{vol_mis/n*100:.1f}  (~{int(vol_mis*scale):,})")
    print(f"  oturmayan  : %{none/n*100:.1f}  (~{int(none*scale):,})")
    print(f"  TOPLAM BOSLUK: %{(vol_mis+none)/n*100:.1f}  "
          f"(~{int((vol_mis+none)*scale):,} konusma/yil)\n")

    print(f"EN COK SORULAN 20'NIN {len(top20_missing)}'si KB'DE YOK:")
    for g in top20_missing:
        print(f"   {g['adet']:>4} (~{int(g['adet']*scale):,}/yil)  {g['soru'][:58]}")

    print(f"\nYAZILACAK MAKALE: {sum(1 for g in gaps if not cov[g['soru']] and g['adet']>0)} madde")
    top8 = [g for g in sorted(gaps, key=lambda g: -g["adet"]) if not cov[g["soru"]]][:8]
    print(f"  ilk 8'i tek basina ~{int(sum(g['adet'] for g in top8)*scale):,} konusma/yil kapsiyor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
