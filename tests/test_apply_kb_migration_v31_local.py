from __future__ import annotations

import hashlib

from scripts.apply_kb_migration_v31_local import EXPECTED_POST, EXPECTED_PRE, PLAN, PLAN_CONFIRM
from scripts.kb_migration_v31_runner import build_units
import json


def test_committed_plan_matches_confirmed_digest():
    digest = hashlib.blake2b(PLAN.read_bytes(), digest_size=32).hexdigest()
    assert digest[:16] == PLAN_CONFIRM


def test_expected_counts_follow_plan():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    units = build_units(plan)
    created = sum(1 for u in units if u["kind"] in {"create", "promotion"})
    assert EXPECTED_POST["active_qna"] == EXPECTED_PRE["active_qna"] + created == plan["stats"]["expected_active_qna_after_full_apply"]
    assert EXPECTED_POST["aliases"] == EXPECTED_PRE["aliases"] - len(plan["atomic_promotions"])
    assert EXPECTED_POST["guards"] == len(plan["routing_guard_mutations"])
