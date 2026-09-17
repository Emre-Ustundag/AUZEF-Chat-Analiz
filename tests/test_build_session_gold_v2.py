from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_session_gold_v2 import (
    EXTRACT_DIR,
    GAP_CANDIDATES_MINUTES,
    PENDING_CASE,
    build,
    choose_occurrence,
    dedup_messages,
    gap_statistics,
    segment_session,
)

OUTPUT = Path("outputs/session-gold-v2-final-20260917")
READY_SOURCES = (EXTRACT_DIR / "sessions-extract.jsonl").exists()


def message(order: int, time: str, text: str, direction: str = "Kullanıcı", message_id: int | None = None):
    return {"message_id": message_id if message_id is not None else order, "message_order": order,
            "direction": direction, "message_type": "text", "quick_reply": "", "time": time, "text": text,
            "is_gold_user_turn": direction == "Kullanıcı", "is_bot_fallback": False, "user_turn_index": None}


def test_duplicate_message_id_dropped_but_repeated_text_kept():
    session = {"session_id": "s1", "messages": [
        message(1, "2025-07-01 10:00:00", "aynı soru", message_id=5),
        message(2, "2025-07-01 10:00:00", "aynı soru", message_id=5),
        message(3, "2025-07-01 10:05:00", "aynı soru", message_id=6),
    ]}
    cleaned, report = dedup_messages([session])
    assert [m["message_id"] for m in cleaned["s1"]] == [5, 6]
    assert report["dropped_duplicate_message_id"] == 1
    assert report["kept_repeated_user_texts"] == 0


def test_same_signature_with_other_message_id_is_kept_as_real_repeat():
    session = {"session_id": "s1", "messages": [
        message(1, "2025-07-01 10:00:00", "tekrar", message_id=1),
        message(2, "2025-07-01 10:00:00", "tekrar", message_id=2),
    ]}
    cleaned, report = dedup_messages([session])
    assert len(cleaned["s1"]) == 2
    assert report["kept_repeated_user_texts"] == 1


def test_segmentation_splits_only_on_long_gap():
    messages = [message(1, "2025-07-01 10:00:00", "a"), message(2, "2025-07-01 10:20:00", "b"),
                message(3, "2025-07-04 09:00:00", "c")]
    segments = segment_session(messages, 60)
    assert [len(s) for s in segments] == [2, 1]
    assert [len(s) for s in segment_session(messages, 1440 * 7)] == [3]


def test_gap_threshold_is_data_driven():
    short = [message(i, f"2025-07-01 {10 + i // 60:02d}:{i % 60:02d}:00", "x") for i in range(0, 300)]
    long_gap = [message(300, "2025-07-05 10:00:00", "y")]
    stats = gap_statistics({"s1": short + long_gap})
    assert stats["selected_threshold_minutes"] in GAP_CANDIDATES_MINUTES
    assert stats["tail_fractions"][str(stats["selected_threshold_minutes"])] <= 0.01


@pytest.mark.parametrize(
    ("occurrences", "expected_session", "policy"),
    [
        ([{"session_id": "s1", "turn_index": 3}], "s1", "unique_occurrence"),
        ([{"session_id": "s2", "turn_index": 4}, {"session_id": "s1", "turn_index": 1}], "s1", "ambiguous_first_turn_preferred"),
        ([{"session_id": "s2", "turn_index": 4}, {"session_id": "s1", "turn_index": 3}], "s1", "ambiguous_lowest_session_turn"),
        ([], None, "no_occurrence"),
    ],
)
def test_occurrence_choice_is_deterministic(occurrences, expected_session, policy):
    chosen, used = choose_occurrence(occurrences)
    assert used == policy
    assert (chosen or {}).get("session_id") == expected_session


def test_context_cases_prefer_an_occurrence_with_real_history():
    occurrences = [{"session_id": "s1", "turn_index": 1}, {"session_id": "s2", "turn_index": 4},
                   {"session_id": "s3", "turn_index": 2}]
    chosen, used = choose_occurrence(occurrences, prefer_context=True)
    assert used == "ambiguous_context_preferred"
    assert chosen["session_id"] == "s2"
    only_first = [{"session_id": "s1", "turn_index": 1}, {"session_id": "s2", "turn_index": 1}]
    chosen, used = choose_occurrence(only_first, prefer_context=True)
    assert used == "ambiguous_lowest_session_turn" and chosen["session_id"] == "s1"


@pytest.mark.skipif(not READY_SOURCES, reason="Oturum çıkarımı yok")
class TestBuiltDataset:
    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        return build(tmp_path_factory.mktemp("session-gold"))

    def test_no_future_turn_in_context(self, built):
        sessions = {s["evaluation_session_id"]: s for s in built["sessions"]}
        for target in built["targets"]:
            session = sessions[target["evaluation_session_id"]]
            assert target["context_turn_ids"] == list(range(target["turn_index"]))
            assert all(i < target["turn_index"] for i in target["context_turn_ids"])
            assert len(session["turns"]) >= target["turn_index"] + 1

    def test_pending_case_is_never_a_target(self, built):
        assert all(t["case_id"] != PENDING_CASE for t in built["targets"])
        for session in built["sessions"]:
            assert all(turn.get("case_id") != PENDING_CASE for turn in session["turns"])

    def test_every_ready_case_is_targeted_once(self, built):
        gold = {int(json.loads(l)["case_id"]): json.loads(l) for l in
                Path("outputs/gold-v2-final-20260917/gold-v2-all.jsonl").read_text(encoding="utf-8").splitlines()}
        ready = {cid for cid, r in gold.items() if r["status"] == "READY"}
        targeted = [t["case_id"] for t in built["targets"] if t["case_id"] in ready]
        assert sorted(targeted) == sorted(ready)
        assert len(targeted) == len(set(targeted))

    def test_expected_qna_comes_from_frozen_gold(self, built):
        gold = {int(json.loads(l)["case_id"]): json.loads(l) for l in
                Path("outputs/gold-v2-final-20260917/gold-v2-all.jsonl").read_text(encoding="utf-8").splitlines()}
        baseline = {int(r["id"]) for r in json.loads(Path(
            "outputs/kb-migration-v3.1-local-apply-20260917/baseline/qna-canonical.json").read_text(encoding="utf-8"))
            if r["status"] == 1}
        for target in built["targets"]:
            assert target["expected_qna_ids"] == gold[target["case_id"]]["expected_qna_ids"]
            assert set(target["expected_qna_ids"]) <= baseline

    def test_resolved_context_cases_have_real_history(self, built):
        for row in built["context"]:
            if row["resolution_status"] == "RESOLVED_FROM_REAL_SESSION":
                assert row["context_turns"] and row["session_id"]
                assert all(turn["message_id"] is not None for turn in row["context_turns"])
                assert all(turn["timestamp"] for turn in row["context_turns"])

    def test_turn_order_and_ids_are_sane(self, built):
        for session in built["sessions"]:
            indexes = [t["turn_index"] for t in session["turns"]]
            assert indexes == list(range(len(indexes)))
            stamps = [t["timestamp"] for t in session["turns"] if t["timestamp"]]
            assert stamps == sorted(stamps)
            ids = [t["message_id"] for t in session["turns"] if t["message_id"] is not None]
            assert len(ids) == len(set(ids))

    def test_no_case_in_two_evaluation_sessions(self, built):
        seen: dict[int, str] = {}
        for session in built["sessions"]:
            for case_id in session["case_ids"]:
                assert case_id not in seen
                seen[case_id] = session["evaluation_session_id"]

    def test_rebuild_is_byte_identical(self, tmp_path):
        first, second = tmp_path / "a", tmp_path / "b"
        build(first)
        build(second)
        for name in ("session-gold-v2.jsonl", "session-targets.jsonl", "context-resolutions.jsonl",
                     "unresolved-context.jsonl", "session-gold-report.json"):
            assert (first / name).read_bytes() == (second / name).read_bytes(), name

    @pytest.mark.skipif(not OUTPUT.exists(), reason="Dondurulmuş çıktı yok")
    def test_frozen_outputs_match_rebuild(self, built, tmp_path):
        build(tmp_path)
        for name in ("session-gold-v2.jsonl", "session-targets.jsonl", "context-resolutions.jsonl"):
            assert (tmp_path / name).read_bytes() == (OUTPUT / name).read_bytes(), name
