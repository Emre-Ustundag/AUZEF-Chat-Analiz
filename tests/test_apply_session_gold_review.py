from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.apply_session_gold_review import ReviewError, apply_decisions, build, parse_ids

ROOT = Path(__file__).resolve().parents[1]
#: Depoya alınan doldurulmuş inceleme dosyası (manifestteki kaynak da budur).
REVIEW_WORKBOOK = ROOT / "outputs" / "session-gold-v2-review-20260917" / "session-gold-human-review-filled.xlsx"
REVIEWED = ROOT / "outputs" / "gold-v2-reviewed-final-20260917"
FROZEN = ROOT / "outputs" / "session-gold-v2-freeze-20260917"
GOLD_V2 = ROOT / "outputs" / "gold-v2-final-20260917"
BASELINE = ROOT / "outputs" / "kb-migration-v3.1-local-apply-20260917" / "baseline"

EXPECTED_CONTEXT = {106: "EXCLUDE_FROM_EVAL", 199: "EXCLUDE_FROM_EVAL", 237: "EXCLUDE_FROM_EVAL",
                    248: "EXCLUDE_FROM_EVAL", 319: "SOURCE_MISSING_HOLD", 416: "EXCLUDE_FROM_EVAL",
                    457: "EXCLUDE_FROM_EVAL", 514: "EXCLUDE_FROM_EVAL"}
EXPECTED_INTENT = {62: "KEEP_SINGLE_INTENT", 71: "MARK_MULTI_INTENT", 212: "KEEP_SINGLE_INTENT",
                   318: "KEEP_SINGLE_INTENT", 436: "KEEP_SINGLE_INTENT", 456: "KEEP_SINGLE_INTENT",
                   480: "MARK_MULTI_INTENT"}
#: Bu digestler inceleme öncesi dondurulmuş katmanlardır; değişirse test kırılır.
IMMUTABLE = {
    GOLD_V2 / "gold-v2-all.jsonl": "32b05e290024b1a47326bb3ba9abe3c616dc04db6c632ca31de2d6d6c8d3cd88",
    GOLD_V2 / "gold-v2-manifest.json": "3afdb866c27c57bd484711aece60a1fd6907895818825c5fcc4a71662a727e17",
    BASELINE / "qna-canonical.json": "925dd5243b95763e4f1c2992b6a38548c0c59ae1448a17ac7843d91a8ca354ea",
    BASELINE / "qna-aliases.json": "4ad878add0c06d1badee7e08b7f5cf79ff269678eec88d5379062183acc58f46",
    BASELINE / "qna-routing-guards.json": "663bf2caaad679b7ce9e1d23cb0b4f18af535f88934475a2c327551115cbe673",
}


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path: Path) -> str:
    return hashlib.new("sha256", path.read_bytes()).hexdigest()


def test_id_parsing_rejects_garbage():
    assert parse_ids("398, 181") == [398, 181]
    assert parse_ids(None) == []
    with pytest.raises(ReviewError):
        parse_ids("398, abc")


def gold_stub(status: str = "READY", ids=(1,)):
    return {1: {"case_id": 1, "status": status, "expected_qna_ids": list(ids), "expected_qna_refs": [],
                "expected_intents": [{"intent_index": 1, "intent_text": "m", "accepted_qna_ids": list(ids)}],
                "multi_intent": False, "user_message": "m", "notes": ""}}


@pytest.mark.parametrize(
    ("decision", "status", "note", "ids"),
    [
        ({"case_id": 1, "context_decision": "EXCLUDE_FROM_EVAL", "intent_decision": None,
          "final_expected_qna_ids": [], "reviewer_note": "", "sheet": "x"}, "CONTEXT_REQUIRED", "gerekçe yok", []),
        ({"case_id": 1, "context_decision": "EXCLUDE_FROM_EVAL", "intent_decision": None,
          "final_expected_qna_ids": [], "reviewer_note": "n", "sheet": "x"}, "READY", "yanlış durum", []),
        ({"case_id": 1, "context_decision": None, "intent_decision": "KEEP_SINGLE_INTENT",
          "final_expected_qna_ids": [9], "reviewer_note": "n", "sheet": "x"}, "READY", "ID değişimi", [1]),
        ({"case_id": 1, "context_decision": None, "intent_decision": "KEEP_SINGLE_INTENT",
          "final_expected_qna_ids": [42], "reviewer_note": "n", "sheet": "x"}, "READY", "baseline dışı", [42]),
    ],
)
def test_precondition_violations_are_rejected(decision, status, note, ids):
    gold = gold_stub(status, ids or (1,))
    with pytest.raises(ReviewError):
        apply_decisions(gold, {"decisions": {1: decision}, "anomaly": []}, {}, {1, 9}, "test")


@pytest.mark.skipif(not REVIEWED.exists(), reason="Reviewed gold üretilmemiş")
class TestReviewedLayer:
    @pytest.fixture(scope="class")
    def records(self):
        return {int(r["case_id"]): r for r in read(REVIEWED / "gold-v2-reviewed-all.jsonl")}

    def test_decisions_match_reviewer_sheet(self):
        manifest = json.loads((REVIEWED / "gold-v2-reviewed-manifest.json").read_text(encoding="utf-8"))
        assert {int(k): v for k, v in manifest["decisions"]["context"].items()} == EXPECTED_CONTEXT
        assert {int(k): v for k, v in manifest["decisions"]["intent"].items()} == EXPECTED_INTENT

    def test_excluded_cases_keep_metadata_but_lose_targets(self, records):
        for case_id in (106, 199, 237, 248, 416, 457, 514):
            record = records[case_id]
            assert record["status"] == "EXCLUDED_FROM_EVAL"
            assert record["exclusion_reason"] and record["review"]["review_source"]
            assert record["expected_qna_ids"] == []

    def test_hold_and_pending_are_separate_kinds(self, records):
        assert records[319]["status"] == "SOURCE_MISSING_HOLD" and records[319]["hold_type"] == "SOURCE_MISSING"
        assert records[214]["status"] == "PENDING_CONTENT"
        assert "hold_type" not in records[214]

    def test_multi_intent_groups_follow_message_order(self, records):
        assert [i["accepted_qna_ids"] for i in records[71]["expected_intents"]] == [[398], [181]]
        assert [i["accepted_qna_ids"] for i in records[480]["expected_intents"]] == [[125], [344]]
        for case_id in (71, 480):
            assert records[case_id]["multi_intent"] is True
        for case_id in (62, 212, 318, 436, 456):
            assert records[case_id]["multi_intent"] is False

    def test_reviewed_layer_rebuild_is_deterministic(self, tmp_path):
        first = build(REVIEW_WORKBOOK, tmp_path / "a", "2026-09-17T00:00:00Z")
        second = build(REVIEW_WORKBOOK, tmp_path / "b", "2026-09-17T00:00:00Z")
        for name in ("gold-v2-reviewed-all.jsonl", "gold-v2-reviewed-ready.jsonl", "review-decisions.jsonl"):
            assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
            assert (tmp_path / "a" / name).read_bytes() == (REVIEWED / name).read_bytes()
        assert first["manifest"]["counts"] == second["manifest"]["counts"]

    def test_immutable_layers_untouched(self):
        for path, expected in IMMUTABLE.items():
            assert digest(path) == expected, path.name


@pytest.mark.skipif(not FROZEN.exists(), reason="Final session gold üretilmemiş")
class TestFrozenSessionGold:
    @pytest.fixture(scope="class")
    def targets(self):
        return {int(r["case_id"]): r for r in read(FROZEN / "session-targets.jsonl")}

    def test_excluded_hold_pending_are_not_targets(self, targets):
        for case_id in (106, 199, 237, 248, 416, 457, 514, 319, 214):
            assert case_id not in targets

    def test_case_345_matches_real_source_without_leakage(self, targets):
        target = targets[345]
        assert target["target_match_method"] == "exact_text"
        assert target["source_session_id"] and target["context_length"] > 0
        assert target["context_turn_ids"] == list(range(target["turn_index"]))
        assert target["expected_qna_ids"] == []

    def test_multi_intent_targets_expose_groups(self, targets):
        assert targets[71]["expected_intent_groups"] == [[398], [181]]
        assert targets[480]["expected_intent_groups"] == [[125], [344]]
        rows = {int(r["case_id"]) for r in read(FROZEN / "multi-intent-targets.jsonl")}
        assert rows == {71, 480}

    def test_manifest_is_frozen_with_provenance(self):
        manifest = json.loads((FROZEN / "session-gold-manifest.json").read_text(encoding="utf-8"))
        assert manifest["frozen"] is True
        assert manifest["normalization_version"] == "tr-normalize-v2"
        assert manifest["gold_layer"]["layer"] == "reviewed"
        assert manifest["counts"]["excluded_from_eval"] == 7
        assert manifest["counts"]["source_missing_hold"] == 1
        assert manifest["counts"]["pending_content"] == 1
        assert manifest["counts"]["multi_intent"] == 2

    def test_hold_and_excluded_files_carry_reasons(self):
        excluded = read(FROZEN / "excluded-from-eval.jsonl")
        hold = read(FROZEN / "source-missing-hold.jsonl")
        assert sorted(r["case_id"] for r in excluded) == [106, 199, 237, 248, 416, 457, 514]
        assert all(r["exclusion_reason"] for r in excluded)
        assert sorted(r["case_id"] for r in hold) == [214, 319]
        assert {r["case_id"]: r["hold_type"] for r in hold} == {214: "CONTENT_PENDING", 319: "SOURCE_MISSING"}
