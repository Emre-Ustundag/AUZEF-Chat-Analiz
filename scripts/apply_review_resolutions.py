"""İnsan inceleme sonucuna izlenebilir teknik düzeltmeler uygular."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

RESULT_SCHEMA = "simple-gold-review-result-v1"
RESOLUTION_SCHEMA = "simple-gold-review-resolution-v1"
ALLOWED_CHANGES = {
    "accepted_qna_ids",
    "review_note",
    "complete",
    "evaluation_status_override",
}
ALLOWED_STATUS_OVERRIDES = {"needs_context", "needs_kb", "excluded"}


def file_digest(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=32)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON okunamadı: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"JSON nesnesi bekleniyordu: {path}")
    return payload


def apply_resolutions(
    result: dict[str, Any],
    resolution: dict[str, Any],
    *,
    expected_source_digest: str | None = None,
) -> dict[str, Any]:
    if result.get("schema_version") != RESULT_SCHEMA:
        raise ValueError("İnceleme sonucu şeması geçersiz")
    if resolution.get("schema_version") != RESOLUTION_SCHEMA:
        raise ValueError("Resolution şeması geçersiz")
    if (
        expected_source_digest is not None
        and resolution.get("source_result_blake2b") != expected_source_digest
    ):
        raise ValueError("Resolution farklı bir inceleme sonucuna ait")

    reviews = result.get("reviews")
    patches = resolution.get("resolutions")
    if not isinstance(reviews, list) or not isinstance(patches, list):
        raise TypeError("reviews ve resolutions liste olmalı")

    by_case = {int(row["case_no"]): {**row} for row in reviews}
    if len(by_case) != len(reviews):
        raise ValueError("İnceleme sonucunda tekrarlanan vaka var")

    applied = []
    seen = set()
    for patch in patches:
        case_no = int(patch.get("case_no") or 0)
        if case_no in seen:
            raise ValueError(f"Tekrarlanan resolution vakası: {case_no}")
        seen.add(case_no)
        if case_no not in by_case:
            raise ValueError(f"Resolution vakası sonuçta bulunamadı: {case_no}")
        reason = str(patch.get("reason") or "").strip()
        source = str(patch.get("source") or "").strip()
        changes = patch.get("changes")
        if not reason or not source:
            raise ValueError(f"Vaka {case_no}: reason ve source zorunlu")
        if not isinstance(changes, dict) or not changes:
            raise ValueError(f"Vaka {case_no}: changes boş veya geçersiz")
        unknown = set(changes) - ALLOWED_CHANGES
        if unknown:
            raise ValueError(
                f"Vaka {case_no}: değiştirilemeyen alanlar: {sorted(unknown)}"
            )
        if "complete" in changes and not isinstance(changes["complete"], bool):
            raise TypeError(f"Vaka {case_no}: complete boolean olmalı")
        if (
            "evaluation_status_override" in changes
            and changes["evaluation_status_override"] not in ALLOWED_STATUS_OVERRIDES
        ):
            raise ValueError(
                f"Vaka {case_no}: geçersiz evaluation_status_override"
            )
        for field in {"accepted_qna_ids", "review_note"} & set(changes):
            if not isinstance(changes[field], str):
                raise TypeError(f"Vaka {case_no}: {field} metin olmalı")

        before = {field: by_case[case_no].get(field) for field in changes}
        by_case[case_no].update(changes)
        applied.append(
            {
                "case_no": case_no,
                "reason": reason,
                "source": source,
                "before": before,
                "after": {field: by_case[case_no][field] for field in changes},
            }
        )

    resolved_reviews = [by_case[int(row["case_no"])] for row in reviews]
    completed_count = sum(bool(row.get("complete")) for row in resolved_reviews)
    return {
        **result,
        "completed_count": completed_count,
        "reviews": resolved_reviews,
        "resolution_audit": {
            "schema_version": RESOLUTION_SCHEMA,
            "resolution_count": len(applied),
            "applied": applied,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--resolutions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = _load_json(args.input)
    resolution = _load_json(args.resolutions)
    resolved = apply_resolutions(
        result,
        resolution,
        expected_source_digest=file_digest(args.input),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "completed_count": resolved["completed_count"],
                "resolution_count": resolved["resolution_audit"][
                    "resolution_count"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
