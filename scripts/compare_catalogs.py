"""Eski bot katalogu ile yeni chatbot bilgi tabanini karsilastirir.

Iki liste de 266 kayit; ayni liste mi, farkli mi? Ayniysa yeni bot eski
botun bosluklarini da devralmis demektir.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from profile_new_kb import load  # noqa: E402

OLD = ROOT / "outputs" / "analiz" / "qna-katalog-yil.json"
TR = str.maketrans(
    {"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"}
)
STOP = {"nasil", "nedir", "ne", "mi", "mu", "icin", "bir", "bu", "ile", "var",
        "olarak", "nereden", "nasıl"}


def toks(s: str) -> set[str]:
    s = s.translate(TR).casefold()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]+", s) if len(w) > 2 and w not in STOP}


def norm(s: str) -> str:
    return " ".join(sorted(toks(s)))


def main() -> int:
    old = [c["question"] for c in json.loads(OLD.read_text(encoding="utf-8"))
           if c["question"].strip().casefold() not in {"yok", ""}]
    new_rows = load()
    new = [(r.get("question") or "").strip() for r in new_rows]

    print(f"eski bot katalogu : {len(old)} soru")
    print(f"yeni bot KB       : {len(new)} soru")

    old_n = {norm(q): q for q in old}
    new_n = {norm(q): q for q in new}
    exact = set(old_n) & set(new_n)
    print(f"\nBIREBIR ORTAK (kelime kumesi ayni): {len(exact)}")

    # yakin eslesme
    old_t = [(q, toks(q)) for q in old if norm(q) not in exact]
    new_t = [(q, toks(q)) for q in new if norm(q) not in exact]
    near = 0
    pairs = []
    for oq, ot in old_t:
        best, bs = None, 0.0
        for nq, nt in new_t:
            if not ot or not nt:
                continue
            s = len(ot & nt) / len(ot | nt)
            if s > bs:
                best, bs = nq, s
        if bs >= 0.5:
            near += 1
            pairs.append((bs, oq, best))
    print(f"YAKIN ESLESME (>=0.5): {near}")
    print(f"\nESKIDE VAR, YENIDE KARSILIGI YOK: {len(old_t) - near}")
    print(f"YENIDE VAR, ESKIDE YOK          : ~{len(new_t) - near}")

    only_old = {oq for _, oq, _ in pairs}
    print("\nESKIDE OLUP YENIDE BULUNAMAYANLARDAN 12 ORNEK:")
    shown = 0
    for oq, _ in old_t:
        if oq in only_old:
            continue
        print(f"   · {oq[:76]}")
        shown += 1
        if shown >= 12:
            break

    print("\nYENIDE OLUP ESKIDE BULUNAMAYANLARDAN 12 ORNEK:")
    matched_new = {b for _, _, b in pairs}
    shown = 0
    for nq, _ in new_t:
        if nq in matched_new:
            continue
        print(f"   · {nq[:76]}")
        shown += 1
        if shown >= 12:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
