"""KB taslagini teslime hazir hale getirir.

- `_eski_bot_cevabi` kolonu atilir: cevaplari dolduracak kisiler ayri.
- `tags` bosaltilir: sozluksel tahmin bazi satirlarda yanlisti ("Kayit
  tarihleri" -> UC_DERS_SINAVI). Gozden gecirecek kisi icin yanlis tag
  bos tag'den kotudur.
- `_hacim` ve `_sorunlu_yuzde` KALIR: cevap yazacak kisi onceligi bilsin.
  Ice aktarmadan once alt cizgili kolonlar silinmeli.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs" / "analiz" / "KB-taslak-eksik-sorular.csv"
DST = ROOT / "outputs" / "analiz" / "KB-taslak-eksik-sorular.csv"
DROP = {"_eski_bot_cevabi"}


def main() -> int:
    csv.field_size_limit(10 * 1024 * 1024)
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    if not rows:
        print("HATA: kaynak bos", file=sys.stderr)
        return 1

    header = [c for c in rows[0] if c and c not in DROP]
    out = []
    for r in rows:
        rec = {c: (r.get(c) or "") for c in header}
        rec["tags"] = ""
        out.append(rec)

    with DST.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out)

    qcols = [c for c in header if c.startswith("query_")]
    avg = sum(sum(1 for c in qcols if r[c]) for r in out) / len(out)
    print(f"{len(out)} satir | kolonlar: {header[:3]} ... {header[-1]}")
    print(f"  answer bos      : {sum(1 for r in out if not r['answer'])}/{len(out)}")
    print(f"  tags bos        : {sum(1 for r in out if not r['tags'])}/{len(out)}")
    print(f"  ort. gercek ifade: {avg:.1f}")
    print(f"\nen yuksek hacimli 8:")
    for r in sorted(out, key=lambda r: -int(r["_hacim"]))[:8]:
        print(f"  {r['_hacim']:>5}  %{r['_sorunlu_yuzde']:>3} sorunlu  {r['question'][:58]}")
    print(f"\n-> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
