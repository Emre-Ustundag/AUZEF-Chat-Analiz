from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.kb_migration_v31_runner import (
    ACTOR,
    PLAN_SCHEMA,
    PLAN_WORKBOOK_DIGEST,
    PlanError,
    build_units,
    index_targets,
    integrity_checks,
    resolve_plan,
    restore_actions,
    snapshot_digest,
)

REAL_PLAN = Path("outputs/kb-consolidation-v3.1-final-dry-run-guard-20260917/kb-mutation-plan-v3.1-final.json")
TS = "2026-09-01T10:00:00"


def guard(ref: str, target: dict, valid_until: str | None = None) -> dict:
    return {
        "action": "upsert_routing_guard",
        "guard_ref": f"GUARD-{ref}",
        "target": target,
        "cases": [],
        "exact_bypass": "disabled",
        "selector_mode": "semantic_selector_only",
        "content_mode": "dynamic",
        "valid_from": None,
        "valid_until": valid_until,
        "on_expiry": "block",
        "source_of_truth": "duyuru",
    }


def make_plan() -> dict:
    return {
        "schema_version": PLAN_SCHEMA,
        "source_workbook_blake2b": PLAN_WORKBOOK_DIGEST,
        "preconditions": {"expected_active_qna_count": 3},
        "stats": {"expected_active_qna_after_full_apply": 5},
        "qna_mutations": [
            {
                "action": "update_qna", "operation_ref": "EX-1", "qna_id": 1, "status": "READY",
                "expected_current": {"question": "Soru 1?", "answer": "eski 1"},
                "set": {"question": "Soru 1?", "answer": "yeni 1"}, "content_control_ref": None,
            },
            {
                "action": "update_qna", "operation_ref": "EX-3", "qna_id": 3, "status": "BLOCKED_CASE_214",
                "expected_current": {"question": "Soru 3?", "answer": "eski 3"},
                "set": {"question": "Soru 3?", "answer": "yasak"}, "content_control_ref": "EX-3",
            },
            {
                "action": "create_qna", "temp_ref": "NEW-01", "status": "READY_WITH_GUARD",
                "set": {"question": "Yeni soru?", "answer": "yeni cevap"}, "content_control_ref": "NEW-01",
            },
        ],
        "alias_mutations": [
            {
                "action": "move_alias", "case_no": 10, "alias": "taşınan",
                "expected_source": {"canonical_question": "Soru 2?"},
                "target": {"temp_ref": "NEW-01", "canonical_question": "Yeni soru?"},
                "exact_alias_policy": "selector_only_no_permanent_bypass", "status": "READY_WITH_GUARD",
            },
            {
                "action": "move_alias", "case_no": 11, "alias": "ikinci",
                "expected_source": {"canonical_question": "Soru 2?"},
                "target": {"qna_id": 1, "canonical_question": "Soru 1?"},
                "exact_alias_policy": "exact_alias_allowed_after_validation", "status": "READY",
            },
        ],
        "atomic_promotions": [
            {
                "action": "promote_alias_to_new_qna_canonical", "operation_ref": "NEW-11", "atomic": True,
                "case_no": 12, "status": "READY_WITH_GUARD", "content_control_ref": "NEW-11",
                "expected_source": {"canonical_question": "Soru 2?", "alias": "Terfi eden"},
                "target": {"question": "Terfi eden?", "answer": "terfi cevap"},
            }
        ],
        "routing_guard_mutations": [
            guard("EX-3", {"qna_id": 3, "operation_ref": "EX-3"}, valid_until="2026-12-09"),
            guard("NEW-01", {"temp_ref": "NEW-01"}),
            guard("NEW-11", {"temp_ref": "NEW-11"}),
        ],
    }


def qna(qid: int, question: str, answer: str, updated_by: str | None = None) -> dict:
    return {"id": qid, "question_text": question, "answer_text": answer, "status": 1,
            "updated_by": updated_by, "created_at": TS, "updated_at": TS}


def alias(aid: int, qid: int, text_value: str) -> dict:
    return {"id": aid, "qna_id": qid, "query_text": text_value, "query_type": 1, "created_at": TS}


def make_before() -> dict:
    snapshot = {
        "qna": [qna(1, "Soru 1?", "eski 1"), qna(2, "Soru 2?", "cevap 2"), qna(3, "Soru 3?", "eski 3")],
        "aliases": [alias(100, 2, "taşınan"), alias(101, 2, "ikinci"), alias(102, 2, "Terfi eden"), alias(103, 3, "kalıcı")],
        "guards": [],
    }
    snapshot["digest"] = snapshot_digest(snapshot)
    return snapshot


def guard_row(qid: int, plan_guard: dict) -> dict:
    return {
        "qna_id": qid, "guard_ref": plan_guard["guard_ref"], "exact_bypass_enabled": 0,
        "selector_mode": plan_guard["selector_mode"], "content_mode": plan_guard["content_mode"],
        "valid_from": plan_guard["valid_from"], "valid_until": plan_guard["valid_until"],
        "on_expiry": plan_guard["on_expiry"], "source_of_truth": plan_guard["source_of_truth"],
        "created_at": TS, "updated_at": TS,
    }


def make_after(plan: dict, before: dict) -> dict:
    after = deepcopy(before)
    after["qna"][0].update(answer_text="yeni 1", updated_by=ACTOR, updated_at="2026-09-17T12:00:00")
    after["qna"] += [qna(4, "Yeni soru?", "yeni cevap", ACTOR), qna(5, "Terfi eden?", "terfi cevap", ACTOR)]
    after["aliases"] = [alias(100, 4, "taşınan"), alias(101, 1, "ikinci"), alias(103, 3, "kalıcı")]
    guards = plan["routing_guard_mutations"]
    after["guards"] = [guard_row(3, guards[0]), guard_row(4, guards[1]), guard_row(5, guards[2])]
    after["digest"] = snapshot_digest(after)
    return after


def test_units_follow_transaction_contract():
    units = build_units(make_plan())
    assert [u["unit"] for u in units] == ["guard_only:EX-3", "update:EX-1", "create:NEW-01", "promotion:NEW-11"]
    create = units[2]
    assert create["guard"]["guard_ref"] == "GUARD-NEW-01"
    assert [a["case_no"] for a in create["aliases"]] == [10]
    assert units[0]["skipped_content_update"]["status"] == "BLOCKED_CASE_214"
    assert [a["case_no"] for a in units[1]["aliases"]] == [11]


def test_guard_required_target_without_guard_is_rejected():
    plan = make_plan()
    plan["routing_guard_mutations"] = plan["routing_guard_mutations"][:1] + plan["routing_guard_mutations"][2:]
    with pytest.raises(PlanError, match="NEW-01 guard gerektiriyor"):
        build_units(plan)


def test_selector_only_alias_to_unguarded_target_is_rejected():
    plan = make_plan()
    plan["alias_mutations"][1]["exact_alias_policy"] = "selector_only_no_permanent_bypass"
    with pytest.raises(PlanError, match="selector-only"):
        build_units(plan)


def test_exact_bypass_enabled_guard_is_rejected():
    plan = make_plan()
    plan["routing_guard_mutations"][1]["exact_bypass"] = "enabled"
    with pytest.raises(PlanError, match="exact bypass"):
        build_units(plan)


def test_alias_into_blocked_target_is_rejected():
    plan = make_plan()
    plan["alias_mutations"][1]["target"] = {"qna_id": 3, "canonical_question": "Soru 3?"}
    with pytest.raises(PlanError, match="Bloklu EX-3"):
        build_units(plan)


def test_foreign_plan_is_rejected():
    plan = make_plan()
    plan["source_workbook_blake2b"] = "0" * 64
    with pytest.raises(PlanError):
        build_units(plan)


def test_preflight_passes_and_resolves_sources_to_ids():
    resolved, _ = resolve_plan(make_plan(), make_before())
    assert resolved["errors"] == []
    assert resolved["alias_sources"]["10"] == {"source_qna_id": 2, "alias_id": 100}
    assert resolved["promotion_sources"]["NEW-11"]["alias_id"] == 102


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda s: s["qna"][0].update(answer_text="elle değişmiş"), "QNA_CURRENT_MISMATCH"),
        (lambda s: s["qna"][2].update(answer_text="elle değişmiş"), "BLOCKED_QNA_CURRENT_MISMATCH"),
        (lambda s: s["aliases"].append(alias(200, 2, "taşınan")), "ALIAS_SOURCE_ROW_NOT_UNIQUE"),
        (lambda s: s["aliases"].append(alias(201, 3, "Yeni soru?")), "NEW_CANONICAL_ALIAS_COLLISION"),
        (lambda s: s["aliases"].pop(2), "PROMOTION_SOURCE_MISMATCH"),
        (lambda s: s["aliases"].append(alias(202, 1, "ikinci")), "ALIAS_ALREADY_ON_TARGET"),
        (lambda s: s["qna"].append(qna(9, "Başka?", "x")), "ACTIVE_QNA_COUNT"),
    ],
)
def test_preflight_detects_live_drift(mutate, code):
    before = make_before()
    mutate(before)
    resolved, _ = resolve_plan(make_plan(), before)
    assert code in {error["code"] for error in resolved["errors"]}


def test_integrity_passes_for_exact_expected_state():
    plan, before = make_plan(), make_before()
    resolved, units = resolve_plan(plan, before)
    result = integrity_checks(plan, units, resolved, before, make_after(plan, before))
    assert result["status"] == "PASS", result["failures"]
    assert result["created_ids"] == {"NEW-01": 4, "NEW-11": 5}
    assert index_targets(units, resolved, result["created_ids"]) == [1, 2, 4, 5]


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (lambda a: a["guards"].pop(1), "guard:GUARD-NEW-01"),
        (lambda a: a["guards"][1].update(exact_bypass_enabled=1), "guard:GUARD-NEW-01"),
        (lambda a: a["guards"][0].update(valid_until=None), "guard:GUARD-EX-3"),
        (lambda a: a["qna"][2].update(answer_text="yasak"), "content_unchanged:EX-3"),
        (lambda a: a["qna"][1].update(answer_text="kapsam dışı"), "untouched_qna_rows_identical"),
        (lambda a: a["aliases"].append(alias(300, 5, "Terfi eden")), "promotion_no_duplicate_canonical_alias:NEW-11"),
        (lambda a: a["aliases"][0].update(qna_id=2), "alias_moved:10"),
        (lambda a: a["aliases"][2].update(query_text="değişti"), "untouched_alias_rows_identical"),
        (lambda a: a["qna"][3].update(updated_by="admin@iu.test"), "created_rows_are_migration_owned"),
    ],
)
def test_integrity_detects_bad_post_state(mutate, failed_check):
    plan, before = make_plan(), make_before()
    resolved, units = resolve_plan(plan, before)
    after = make_after(plan, before)
    mutate(after)
    result = integrity_checks(plan, units, resolved, before, after)
    assert failed_check in {failure["check"] for failure in result["failures"]}


def test_restore_actions_cover_every_migration_write():
    plan, before = make_plan(), make_before()
    resolved, units = resolve_plan(plan, before)
    actions = restore_actions(units, resolved, before, make_after(plan, before))
    assert actions["foreign_changes"] == []
    assert actions["delete_qna"] == [4, 5]
    assert [row["id"] for row in actions["restore_qna"]] == [1]
    assert sorted(row["id"] for row in actions["restore_alias_owner"]) == [100, 101]
    assert [row["id"] for row in actions["reinsert_alias"]] == [102]
    assert actions["guard_delete"] == [3, 4, 5]
    assert actions["reindex_ids"] == [1, 2, 3, 4, 5]


def test_restore_is_noop_before_apply_and_partial_apply_is_covered():
    plan, before = make_plan(), make_before()
    resolved, units = resolve_plan(plan, before)
    assert restore_actions(units, resolved, before, deepcopy(before))["noop"] is True

    partial = deepcopy(before)
    partial["qna"][0].update(answer_text="yeni 1", updated_by=ACTOR)
    partial["aliases"][1].update(qna_id=1)
    actions = restore_actions(units, resolved, before, partial)
    assert [row["id"] for row in actions["restore_qna"]] == [1]
    assert [row["id"] for row in actions["restore_alias_owner"]] == [101]
    assert actions["delete_qna"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a["qna"][1].update(answer_text="admin düzenledi"),
        lambda a: a["qna"].append(qna(6, "Admin ekledi?", "x", "admin@iu.test")),
        lambda a: a["aliases"].append(alias(400, 1, "admin alias")),
        lambda a: a["aliases"][2].update(query_text="admin değiştirdi"),
        lambda a: a["qna"][0].update(answer_text="admin apply sonrası düzeltti"),
        lambda a: a["guards"].append(guard_row(2, guard("X", {"qna_id": 2}))),
    ],
)
def test_restore_refuses_foreign_changes(mutate):
    plan, before = make_plan(), make_before()
    resolved, units = resolve_plan(plan, before)
    after = make_after(plan, before)
    mutate(after)
    assert restore_actions(units, resolved, before, after)["foreign_changes"]


@pytest.mark.skipif(not REAL_PLAN.is_file(), reason="v3.1-final plan çıktısı yok")
def test_real_plan_unit_breakdown():
    units = build_units(json.loads(REAL_PLAN.read_text(encoding="utf-8")))
    kinds = {kind: sum(1 for u in units if u["kind"] == kind) for kind in {u["kind"] for u in units}}
    assert kinds == {"guard_only": 1, "update": 26, "alias_move": 1, "create": 14, "promotion": 1}
    assert sum(len(u["aliases"]) for u in units) == 47
    assert sum(1 for u in units if u["guard"]) == 11
    assert all(u["kind"] != "update" or u["ref"] != "EX-319" for u in units)
    guarded = {u["ref"] for u in units if u["guard"]}
    assert "NEW-13" in guarded
    new13 = next(u for u in units if u["ref"] == "NEW-13")
    assert new13["guard"]["valid_until"] is None
