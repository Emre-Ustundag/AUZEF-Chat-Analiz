from __future__ import annotations

import pytest

from scripts.run_e2e_baseline import (
    CONTEXT_POLICY,
    assert_no_leakage,
    build_targets,
    context_window,
    evaluate,
    mcnemar,
    used_asks,
)


def turn(role, text):
    return {"role": role, "text": text}


def test_context_window_matches_router_policy():
    prior = [turn("user", "a" * 500), turn("assistant", "b"), turn("user", "c"), turn("assistant", "d"), turn("user", "e")]
    window = context_window(prior)
    assert len(window) == CONTEXT_POLICY["max_messages"]
    assert [w["role"] for w in window] == ["bot", "user", "bot", "user"]
    assert all(len(w["content"]) <= CONTEXT_POLICY["max_chars"] // CONTEXT_POLICY["max_messages"] for w in window)


def test_leakage_guard_rejects_future_turns():
    prior = [turn("user", "önceki soru")]
    after = [turn("user", "hedef"), turn("user", "sonraki açıklama")]
    assert_no_leakage([{"role": "user", "content": "önceki soru"}], prior, after, 1)
    with pytest.raises(AssertionError):
        assert_no_leakage([{"role": "user", "content": "sonraki açıklama"}], prior, after, 1)
    with pytest.raises(AssertionError):
        assert_no_leakage([], prior, after, 1)


def test_all_context_targets_have_leak_free_context():
    targets = build_targets()
    context_targets = [t for t in targets if t["turn_type"] == "FOLLOW_UP_CONTEXT_REQUIRED"]
    assert len(context_targets) == 17
    assert all(t["context"] for t in context_targets)
    assert not any(t["context"] for t in targets if t["turn_type"] != "FOLLOW_UP_CONTEXT_REQUIRED")
    assert len(targets) == 507 and sum(t["scorable"] for t in targets) == 490
    assert all(t["case_id"] not in (214, 319, 106, 199, 237, 248, 416, 457, 514) for t in targets)


def ask(thread, selected, calendar=False):
    return {"kind": "ask", "thread": thread, "selected_qna_id": selected, "selected_calendar": calendar}


def test_used_asks_picks_speculative_for_single_and_sequential_for_multi():
    events = [{"kind": "split_result", "sub_questions": ["x"]}, ask("worker", 5), ask("main", 7)]
    assert [a["selected_qna_id"] for a in used_asks(events)[0]] == [5]
    events = [{"kind": "split_result", "sub_questions": ["x", "y"]}, ask("worker", 5), ask("main", 7), ask("main", 8)]
    asks, count = used_asks(events)
    assert count == 2 and [a["selected_qna_id"] for a in asks] == [7, 8]


def base_target(groups):
    ids = sorted({q for g in groups for q in g})
    return {"case_id": 1, "turn_type": "FIRST_TURN", "scorable": True, "expected_qna_ids": ids,
            "expected_intent_groups": groups, "user_message": "m"}


def base_result(events, source="llm"):
    return {"events": events, "source": source, "answer_qna_ids": [], "unresolved_api_error": False,
            "retries_used": 0, "elapsed": 1.0, "guard_decisions": {}}


def pool(ids, calendar=0):
    return {"kind": "pool", "qna_ids": ids, "calendar_candidates": calendar}


@pytest.mark.parametrize(
    ("events", "correct", "cause"),
    [
        ([pool([5, 9]), {"kind": "split_result", "sub_questions": ["m"]}, ask("worker", 5)], True, None),
        ([pool([9]), {"kind": "split_result", "sub_questions": ["m"]}, ask("worker", 9)], False, "RETRIEVAL_MISS"),
        ([pool([5, 9]), {"kind": "split_result", "sub_questions": ["m"]}, ask("worker", 9)], False, "SELECTOR_WRONG_CHOICE"),
        ([pool([5], calendar=19), {"kind": "split_result", "sub_questions": ["m"]}, ask("worker", None, calendar=True)],
         False, "CALENDAR_ROUTING_INTERFERENCE"),
    ],
)
def test_single_intent_scoring_and_primary_cause(events, correct, cause):
    record = evaluate(base_target([[5]]), base_result(events), set(), {})
    assert record["correct"] is correct
    assert record["primary_cause"] == cause


def test_multi_intent_requires_every_group_and_flags_missed_split():
    events = [pool([125, 344]), {"kind": "split_result", "sub_questions": ["m"]}, ask("worker", 344)]
    record = evaluate(base_target([[125], [344]]), base_result(events), set(), {})
    assert record["correct"] is False and record["group_hit"] == [False, True]
    assert record["primary_cause"] == "SPLITTER_MISSED_SPLIT"


def test_unscorable_context_target_is_not_scored():
    target = {**base_target([]), "scorable": False, "expected_qna_ids": [], "expected_intent_groups": []}
    record = evaluate(target, base_result([pool([1])]), set(), {})
    assert record["correct"] is None and record["primary_cause"] == "UNSCORED_NO_GOLD"


def test_mcnemar_is_symmetric_and_bounded():
    assert mcnemar(0, 0)["p_value"] == 1.0
    assert mcnemar(10, 2)["p_value"] == mcnemar(2, 10)["p_value"] < 0.05
