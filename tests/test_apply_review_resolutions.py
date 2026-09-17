from __future__ import annotations

import pytest

from scripts.apply_review_resolutions import apply_resolutions


def result() -> dict:
    return {
        "schema_version": "simple-gold-review-result-v1",
        "completed_count": 0,
        "reviews": [
            {
                "case_no": 7,
                "human_decision": "Bağlam olmadan değerlendirilemez",
                "accepted_qna_ids": "",
                "review_note": "",
                "complete": False,
            }
        ],
    }


def resolution(**changes) -> dict:
    return {
        "schema_version": "simple-gold-review-resolution-v1",
        "source_result_blake2b": "abc",
        "resolutions": [
            {
                "case_no": 7,
                "reason": "Önceki mesaj gerekli",
                "source": "session-1",
                "changes": changes,
            }
        ],
    }


def test_applies_allowed_fields_and_keeps_human_decision() -> None:
    resolved = apply_resolutions(
        result(),
        resolution(review_note="Giriş yapılan ekran bilinmiyor", complete=True),
        expected_source_digest="abc",
    )

    assert resolved["completed_count"] == 1
    assert resolved["reviews"][0]["review_note"] == "Giriş yapılan ekran bilinmiyor"
    assert (
        resolved["reviews"][0]["human_decision"]
        == "Bağlam olmadan değerlendirilemez"
    )
    assert resolved["resolution_audit"]["resolution_count"] == 1


def test_rejects_human_decision_change() -> None:
    with pytest.raises(ValueError, match="değiştirilemeyen"):
        apply_resolutions(
            result(),
            resolution(human_decision="Luna daha iyi"),
            expected_source_digest="abc",
        )


def test_rejects_resolution_for_another_source() -> None:
    with pytest.raises(ValueError, match="farklı"):
        apply_resolutions(
            result(),
            resolution(review_note="not"),
            expected_source_digest="different",
        )


def test_allows_a_bounded_evaluation_status_override() -> None:
    resolved = apply_resolutions(
        result(),
        resolution(evaluation_status_override="needs_context"),
        expected_source_digest="abc",
    )

    assert resolved["reviews"][0]["evaluation_status_override"] == "needs_context"

    with pytest.raises(ValueError, match="geçersiz"):
        apply_resolutions(
            result(),
            resolution(evaluation_status_override="ready"),
            expected_source_digest="abc",
        )
