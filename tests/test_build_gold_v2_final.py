from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_gold_v2_final import (
    EXPECTED_COUNTS,
    EXPECTED_PENDING,
    audit,
    build,
    intent_groups,
    temporal_kind,
)

OUTPUT = Path("outputs/gold-v2-final-20260917")


def case(**overrides):
    base = {
        "case_id": 1, "alias_id": "A1", "user_message": "soru", "status": "READY",
        "expected_qna_ids": [10], "expected_qna_refs": ["QNA-10"], "multi_intent": False,
        "expected_intents": [{"intent_index": 1, "intent_text": "soru", "accepted_qna_ids": [10]}],
        "split_audit": {}, "source_decision": {"origin": "baseline_alias_mapping"},
    }
    return {**base, **overrides}


def baseline(active=(10,), owners=None):
    return {
        "active": {qid: {"question_text": f"q{qid}", "answer_text": f"a{qid}"} for qid in active},
        "alias_owners": owners if owners is not None else {"soru": {10}},
        "guards": {},
    }


EMPTY_PLAN = {"alias_mutations": [], "atomic_promotions": []}


def test_temporal_kind_classes():
    assert temporal_kind("historical_term_snapshot") == "historical"
    assert temporal_kind("blocked_shared_qna_update") == "dated_content_with_expiry"
    assert temporal_kind("policy_review") == "policy"
    assert temporal_kind("dynamic_announcement") == "dynamic_current_status"


def test_identical_intent_groups_collapse_to_single_intent():
    source = {"student_message": "m", "split_decision": "Gerekli", "intents": [
        {"intent_index": 1, "intent_text": "a", "accepted_qna_ids": [5, 7]},
        {"intent_index": 2, "intent_text": "b", "accepted_qna_ids": [7, 5]},
    ]}
    intents, info = intent_groups(source)
    assert len(intents) == 1 and intents[0]["accepted_qna_ids"] == [5, 7]
    assert info["cleanup"] == "identical_accepted_sets_collapsed_to_single_intent"


def test_distinct_intent_groups_are_kept():
    source = {"student_message": "m", "split_decision": "Gerekli", "intents": [
        {"intent_index": 1, "intent_text": "a", "accepted_qna_ids": [5]},
        {"intent_index": 2, "intent_text": "b", "accepted_qna_ids": [7]},
    ]}
    intents, info = intent_groups(source)
    assert [i["accepted_qna_ids"] for i in intents] == [[5], [7]]
    assert "cleanup" not in info


@pytest.mark.parametrize(
    ("records", "decisions", "base", "check"),
    [
        ([case(), case()], {}, baseline(), "duplicate_case_id"),
        ([case(case_id=214, status="READY")], {}, baseline(), "pending_set"),
        ([case(expected_qna_ids=[99], expected_intents=[{"intent_index": 1, "intent_text": "s", "accepted_qna_ids": [99]}])],
         {}, baseline(), "expected_qna_not_active_in_baseline"),
        ([case(status="CONTEXT_REQUIRED")], {}, baseline(), "non_ready_has_expected_qna"),
        ([case(expected_qna_ids=[], expected_intents=[{"intent_index": 1, "intent_text": "s", "accepted_qna_ids": []}])],
         {}, baseline(), "ready_without_expected_qna"),
        ([case()], {7: {"errors": ["target_question_differs_from_baseline"]}}, baseline(), "v31_decision_inconsistent"),
        ([case(), case(case_id=2, alias_id="A2", expected_qna_ids=[11])], {}, baseline(active=(10, 11)),
         "same_message_conflicting_gold"),
        ([case()], {}, baseline(owners={"soru": {11}}), "unreviewed_case_alias_owner_differs"),
        ([case()], {}, baseline(owners={}), "ready_message_not_alias_unexpectedly"),
    ],
)
def test_audit_detects_blockers(records, decisions, base, check):
    result = audit(records, decisions, base, EMPTY_PLAN, set())
    assert check in {b["check"] for b in result["blockers"]}


def test_audit_accepts_human_override_without_blocking():
    records = [case(source_decision={"origin": "human_review_174"}, expected_qna_ids=[10])]
    result = audit(records, {}, baseline(owners={"soru": {11}, "x": {10}}), EMPTY_PLAN, set())
    # İnsan kararı alias sahibinden farklı olabilir (exact alias ≠ gold); bu blokaj değildir.
    assert "unreviewed_case_alias_owner_differs" not in {b["check"] for b in result["blockers"]}
    assert result["alias_mapping"]["human_override_count"] == 1


@pytest.mark.skipif(not OUTPUT.exists(), reason="Gold v2 çıktısı üretilmemiş")
def test_frozen_dataset_matches_current_sources(tmp_path):
    """Depodaki dondurulmuş çıktı, kaynaklardan yeniden üretimle birebir aynı olmalı."""
    build(tmp_path)
    for name in ("gold-v2-all.jsonl", "gold-v2-ready.jsonl", "gold-v2-context-required.jsonl",
                 "gold-v2-pending.jsonl", "gold-v2-report.json", "gold-v2-diff-from-previous.json"):
        assert (tmp_path / name).read_bytes() == (OUTPUT / name).read_bytes(), name


def test_build_produces_expected_distribution(tmp_path):
    result = build(tmp_path)
    report = result["report"]
    assert report["audit"]["counts"] == EXPECTED_COUNTS
    assert report["audit"]["blockers"] == []
    assert report["freeze"]["frozen"] is True

    records = {r["case_id"]: r for r in result["records"]}
    assert sorted(r["case_id"] for r in result["records"] if r["status"] == "PENDING_CONTENT") == EXPECTED_PENDING
    assert not records[214]["expected_qna_ids"]
    baseline_ids = {int(r["id"]) for r in json.loads(
        (Path(report["baseline"]["migration_report"]).parent / "qna-canonical.json").read_text(encoding="utf-8")
    ) if r["status"] == 1}
    for record in result["records"]:
        assert set(record["expected_qna_ids"]) <= baseline_ids
        assert (record["status"] == "CONTEXT_REQUIRED") == record["context_required"]
        assert record["multi_intent"] == (len(record["expected_intents"]) > 1)
    # v3.1 migration eşlemesi: NEW-* referansları gerçek yeni QnA kimliklerine bağlı
    created = report["baseline"]["created_qna_ids"]
    assert records[195]["expected_qna_ids"] == [created["NEW-11"]]
    assert records[13]["expected_qna_ids"] == [created["NEW-01"]]
    assert records[205]["routing_guarded"] and records[205]["temporal_meta"]["valid_until"] == "2026-12-09"
    assert records[459]["temporal_meta"]["temporal_kind"] == "historical"
