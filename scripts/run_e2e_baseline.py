"""Session-gold v2 üzerinde gerçek E2E baseline (ölçüm; sistem değiştirilmez).

Alt komutlar:
  run     --model {4o-mini,luna-high}  → hedefleri koşturur (checkpoint/resume)
  report                               → iki model tamamsa metrik, karşılaştırma ve rapor üretir

Girdi kapısı (her ``run`` başında ve ``report`` sonunda): dondurulmuş dataset
sayıları, KB digest'i, Meili/Qdrant–DB tutarlılığı. Konfigürasyon digest'i
değişirse resume reddedilir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_ROOT = Path.home() / "Masaüstü" / "AUZEF Chatbot"
SESSION_GOLD = ROOT / "outputs" / "session-gold-v2-freeze-20260917"
REVIEWED_GOLD = ROOT / "outputs" / "gold-v2-reviewed-final-20260917"
BASELINE = ROOT / "outputs" / "kb-migration-v3.1-local-apply-20260917" / "baseline"
RUNNER = ROOT / "scripts" / "e2e_baseline_runner.py"
PROBE = ROOT / "scripts" / "kb_migration_v31_index_rehearsal_probe.py"
KB_RUNNER = ROOT / "scripts" / "kb_migration_v31_runner.py"
OUT = ROOT / "outputs" / "e2e-baseline-session-gold-v2-20260917"
BENCHMARK_VERSION = "e2e-baseline-session-gold-v2"
EXPECTED_GATE = {"frozen": True, "targets": 507, "evaluation_sessions": 377, "multi_intent": 2,
                 "excluded_from_eval": 7, "source_missing_hold": [319], "pending_content": [214],
                 "kb": {"active_qna": 326, "aliases": 2695, "guards": 11}}
#: Router'daki bağlam penceresi (routers/chat.py varsayılanları): son 4 mesaj, 1200 karakter.
CONTEXT_POLICY = {"max_messages": 4, "max_chars": 1200, "roles": {"user": "user", "assistant": "bot"},
                  "applies_to": ["FOLLOW_UP_CONTEXT_REQUIRED"]}
RATE_LIMIT_BACKOFF = [2, 4, 8, 16, 30, 60]
RETRY = {"max_retries": 2, "backoff_seconds": [10, 30], "sdk_max_retries": 2,
         "rate_limit_backoff_seconds": RATE_LIMIT_BACKOFF,
         "scope": ("çağrı düzeyi: gövdeye gömülü 429 (OpenRouter upstream rate-limit) deterministik bekleyişle "
                   "yeniden denenir; hedef düzeyi: yine de API hatası kalırsa hedef baştan en fazla 2 kez koşar")}
MODELS = {
    "4o-mini": {"label": "4o-mini", "model_id": "openai/gpt-4o-mini", "min_max_tokens": None, "reasoning": None,
                "timeout_seconds": 120, "rate_limit_backoff_seconds": RATE_LIMIT_BACKOFF,
                "notes": "Üretimdeki OpenRouterProvider varsayılan modeli; üretim max_tokens (split 300 / select 5)."},
    "luna-high": {"label": "luna-high", "model_id": "openai/gpt-5.6-luna", "min_max_tokens": 1280,
                  "reasoning": {"effort": "high", "exclude": True}, "timeout_seconds": 120,
                  "rate_limit_backoff_seconds": RATE_LIMIT_BACKOFF,
                  "notes": "Reasoning tokenları max_tokens bütçesini tükettiği için min 1280 zorunlu (beyan edilen sapma)."},
}
TAXONOMY = ("KB_GAP", "GOLD_ANNOTATION", "RETRIEVAL_MISS", "RETRIEVAL_RANKING", "CALENDAR_ROUTING_INTERFERENCE",
            "SPLITTER_FALSE_SPLIT", "SPLITTER_MISSED_SPLIT", "SELECTOR_WRONG_CHOICE", "CONTEXT_FAILURE",
            "ROUTING_GUARD_BLOCK", "FALLBACK_ERROR", "MODEL_FORMAT_ERROR", "TIMEOUT_OR_API_ERROR", "OTHER",
            "NEEDS_HUMAN_ERROR_REVIEW")


def sha256(path: Path) -> str:
    return hashlib.new("sha256", path.read_bytes()).hexdigest()


def text_sha(value: str) -> str:
    return hashlib.new("sha256", value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def git_head(path: Path) -> dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=path,
                           capture_output=True, text=True).stdout.strip()
    return {"commit": head, "tracked_changes": bool(dirty)}


# --------------------------------------------------------------------------
# Girdi kapısı
# --------------------------------------------------------------------------


def probe(mode: str, **payload: Any) -> dict[str, Any]:
    body = json.dumps({"mode": mode, "runner_source": KB_RUNNER.read_text(encoding="utf-8"), **payload}).encode("utf-8")
    result = subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c", PROBE.read_text(encoding="utf-8")],
                            cwd=COMPOSE_ROOT, input=body, capture_output=True)
    lines = [line for line in result.stdout.decode("utf-8").splitlines() if line.startswith('{"type": "probe"')]
    if result.returncode != 0 or not lines:
        raise RuntimeError(f"probe {mode} başarısız: {result.stderr.decode('utf-8')[-2000:]}")
    return json.loads(lines[-1])["result"]


def gate() -> dict[str, Any]:
    manifest = json.loads((SESSION_GOLD / "session-gold-manifest.json").read_text(encoding="utf-8"))
    counts, ids = manifest["counts"], manifest["case_ids"]
    fingerprint = probe("fingerprint", expected_database="auzef_bot")
    consistency = probe("consistency", name="__real__", expected_database="auzef_bot", label="bench", work_dir="/tmp/bench_gate")
    subprocess.run(["docker", "compose", "exec", "-T", "backend", "rm", "-rf", "/tmp/bench_gate"], cwd=COMPOSE_ROOT)
    checks = {
        "frozen": manifest["frozen"] is EXPECTED_GATE["frozen"],
        "targets": counts["targets"] == EXPECTED_GATE["targets"],
        "sessions": counts["evaluation_sessions"] == EXPECTED_GATE["evaluation_sessions"],
        "multi_intent": counts["multi_intent"] == EXPECTED_GATE["multi_intent"],
        "excluded": counts["excluded_from_eval"] == EXPECTED_GATE["excluded_from_eval"],
        "hold": ids["source_missing_hold"] == EXPECTED_GATE["source_missing_hold"],
        "pending": ids["pending_content"] == EXPECTED_GATE["pending_content"],
        "kb": {k: fingerprint["db"][k] for k in ("active_qna", "aliases", "guards")} == EXPECTED_GATE["kb"],
        "index_consistency": consistency["status"] == "PASS",
    }
    return {
        "checks": checks, "ok": all(checks.values()),
        "db_snapshot_digest": fingerprint["db"]["snapshot_digest"],
        "meili_documents_digest": fingerprint["meili"]["documents_digest"],
        "meili_settings_digest": fingerprint["meili"]["settings_digest"],
        "qdrant_points_digest": fingerprint["qdrant"]["points_digest"],
        "consistency_failures": consistency["failures"],
        "session_gold_manifest_sha256": sha256(SESSION_GOLD / "session-gold-manifest.json"),
        "reviewed_gold_manifest_sha256": sha256(REVIEWED_GOLD / "gold-v2-reviewed-manifest.json"),
        "kb_canonical_sha256": sha256(BASELINE / "qna-canonical.json"),
        "kb_aliases_sha256": sha256(BASELINE / "qna-aliases.json"),
        "kb_guards_sha256": sha256(BASELINE / "qna-routing-guards.json"),
    }


# --------------------------------------------------------------------------
# Hedefler ve bağlam
# --------------------------------------------------------------------------


def context_window(prior_turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """routers/chat.py::_load_recent_context ile aynı pencere (en yeni 4, 1200 karakter)."""
    remaining = CONTEXT_POLICY["max_chars"]
    per_message = max(1, CONTEXT_POLICY["max_chars"] // CONTEXT_POLICY["max_messages"])
    newest_first = []
    for turn in reversed(prior_turns[-CONTEXT_POLICY["max_messages"]:]):
        content = (turn["text"] or "").strip()
        if not content:
            continue
        clipped = content[:min(remaining, per_message)]
        if clipped:
            newest_first.append({"role": CONTEXT_POLICY["roles"][turn["role"]], "content": clipped})
            remaining -= len(clipped)
        if remaining <= 0:
            break
    return list(reversed(newest_first))


def assert_no_leakage(context: list[dict[str, str]], prior: list[dict[str, Any]], target_and_after: list[dict[str, Any]],
                      case_id: int) -> None:
    """Bağlamdaki her mesaj hedeften ÖNCEKİ bir turn'ün (kırpılmış) metnidir; hedef ve sonrası giremez."""
    assert context, f"bağlam hedefinde bağlam boş: {case_id}"
    prior_texts = [(t["text"] or "").strip() for t in prior]
    later_only = {(t["text"] or "").strip() for t in target_and_after} - set(prior_texts)
    for item in context:
        assert any(p.startswith(item["content"]) for p in prior_texts), f"bağlam önceki turn değil: {case_id}"
        assert item["content"] not in later_only, f"hedef/sonraki turn bağlama sızdı: {case_id}"


def build_targets() -> list[dict[str, Any]]:
    sessions = {s["evaluation_session_id"]: s for s in read_jsonl(SESSION_GOLD / "session-gold-v2.jsonl")}
    gold = {int(r["case_id"]): r for r in read_jsonl(REVIEWED_GOLD / "gold-v2-reviewed-all.jsonl")}
    targets = []
    for row in sorted(read_jsonl(SESSION_GOLD / "session-targets.jsonl"), key=lambda r: r["case_id"]):
        session = sessions[row["evaluation_session_id"]]
        turn = session["turns"][row["turn_index"]]
        assert turn["is_evaluation_target"] and turn["case_id"] == row["case_id"]
        prior = session["turns"][:row["turn_index"]]
        context = context_window(prior) if row["turn_type"] in CONTEXT_POLICY["applies_to"] else []
        if row["turn_type"] in CONTEXT_POLICY["applies_to"]:
            assert_no_leakage(context, prior, session["turns"][row["turn_index"]:], row["case_id"])
        targets.append({
            "case_id": row["case_id"],
            "evaluation_session_id": row["evaluation_session_id"],
            "turn_type": row["turn_type"],
            "user_message": turn["text"],
            "gold_user_message": gold[row["case_id"]]["user_message"],
            "expected_qna_ids": row["expected_qna_ids"],
            "expected_intent_groups": row["expected_intent_groups"],
            "multi_intent": row["multi_intent"],
            "temporal": row["temporal"],
            "routing_guarded": row["routing_guarded"],
            "guard_refs": row["guard_refs"],
            "context": context,
            "scorable": bool(row["expected_qna_ids"]),
        })
    return targets


def config_for(model_key: str) -> dict[str, Any]:
    return {"benchmark_version": BENCHMARK_VERSION, "model": MODELS[model_key], "retry": RETRY,
            "context_policy": CONTEXT_POLICY, "runner_sha256": sha256(RUNNER),
            "session_gold_manifest_sha256": sha256(SESSION_GOLD / "session-gold-manifest.json")}


# --------------------------------------------------------------------------
# Koşu (checkpoint / resume)
# --------------------------------------------------------------------------


def run_model(model_key: str) -> int:
    directory = OUT / model_key
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint_path = directory / "checkpoint.json"
    results_path = directory / "results.jsonl"
    config = config_for(model_key)
    config_digest = text_sha(json.dumps(config, sort_keys=True))
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint["config_digest"] != config_digest:
            raise SystemExit("HATA: farklı konfigürasyonla resume reddedildi")
    else:
        checkpoint = {"config_digest": config_digest, "config": config, "started_at": datetime.now(timezone.utc).isoformat(),
                      "complete": False, "completed_case_ids": [], "gate_before": None}
    if checkpoint["gate_before"] is None:
        gate_result = gate()
        if not gate_result["ok"]:
            raise SystemExit(f"HATA: girdi kapısı geçmedi: {gate_result['checks']}")
        checkpoint["gate_before"] = gate_result
    targets = build_targets()
    done = {r["case_id"] for r in read_jsonl(results_path)}
    remaining = [t for t in targets if t["case_id"] not in done]
    print(f"[{model_key}] toplam {len(targets)}, tamamlanan {len(done)}, kalan {len(remaining)}", flush=True)
    checkpoint_path.write_text(dump(checkpoint), encoding="utf-8")
    if remaining:
        payload = {"model": MODELS[model_key], "retry": RETRY,
                   "targets": [{k: t[k] for k in ("case_id", "user_message", "expected_qna_ids", "context")} for t in remaining]}
        process = subprocess.Popen(["docker", "compose", "exec", "-T", "backend", "python", "-c", RUNNER.read_text(encoding="utf-8")],
                                   cwd=COMPOSE_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        assert process.stdin and process.stdout
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        with results_path.open("a", encoding="utf-8") as handle:
            for line in process.stdout:
                if not line.startswith('{"type": "result"'):
                    continue
                record = json.loads(line)
                handle.write(line if line.endswith("\n") else line + "\n")
                handle.flush()
                checkpoint["completed_case_ids"].append(record["case_id"])
                if len(checkpoint["completed_case_ids"]) % 10 == 0:
                    checkpoint_path.write_text(dump(checkpoint), encoding="utf-8")
                    print(f"[{model_key}] {len(done) + len(checkpoint['completed_case_ids'])}/{len(targets)}", flush=True)
        code = process.wait()
        if code != 0:
            checkpoint_path.write_text(dump(checkpoint), encoding="utf-8")
            raise SystemExit(f"HATA: koşucu {code} ile çıktı; kaldığı yerden devam edilebilir")
    final_ids = {r["case_id"] for r in read_jsonl(results_path)}
    checkpoint["completed_case_ids"] = sorted(final_ids)
    checkpoint["complete"] = final_ids == {t["case_id"] for t in targets}
    checkpoint["finished_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint_path.write_text(dump(checkpoint), encoding="utf-8")
    print(f"[{model_key}] complete={checkpoint['complete']}", flush=True)
    return 0 if checkpoint["complete"] else 2


# --------------------------------------------------------------------------
# Değerlendirme
# --------------------------------------------------------------------------


def used_asks(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Kullanılan seçici çağrıları: tek alt soruda spekülatif (worker), çoklu alt soruda sıralı (main)."""
    splits = [e for e in events if e["kind"] == "split_result"]
    sub_count = len(splits[-1]["sub_questions"]) if splits else 1
    asks = [e for e in events if e["kind"] == "ask"]
    if sub_count <= 1:
        return [e for e in asks if e["thread"] == "worker"] or asks[:1], max(sub_count, 1)
    return [e for e in asks if e["thread"] == "main"], sub_count


def evaluate(target: dict[str, Any], result: dict[str, Any], guards: set[int], alias_owners: dict[str, set[int]]) -> dict[str, Any]:
    events = result["events"]
    asks, sub_count = used_asks(events)
    pools = [e for e in events if e["kind"] == "pool"]
    first_pool = pools[0] if pools else {"qna_ids": [], "calendar_candidates": 0}
    fallback = [e for e in events if e["kind"] == "fallback"]
    llm_calls = [e for e in events if e["kind"].startswith("llm_")]

    if result["source"] == "llm":
        selected = sorted({a["selected_qna_id"] for a in asks if a["selected_qna_id"] is not None})
        selected_calendar = any(a["selected_calendar"] for a in asks)
    else:
        selected = sorted(result["answer_qna_ids"])
        selected_calendar = result["source"] == "academic_calendar"
    groups = [set(g) for g in target["expected_intent_groups"]]
    accepted = set(target["expected_qna_ids"])

    record: dict[str, Any] = {
        "case_id": target["case_id"], "turn_type": target["turn_type"], "scorable": target["scorable"],
        "source": result["source"], "selected_qna_ids": selected, "selected_calendar": selected_calendar,
        "split_count": sub_count, "expected_intents": len(groups),
        "fallback_used": result["source"] != "llm", "api_error": result["unresolved_api_error"],
        "retries_used": result["retries_used"], "elapsed": result["elapsed"],
        "calendar_candidates_in_first_pool": first_pool["calendar_candidates"],
        "model_ids_returned": sorted({e.get("returned_model") for e in llm_calls if e.get("returned_model")}),
        "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in llm_calls),
        "completion_tokens": sum(e.get("completion_tokens") or 0 for e in llm_calls),
        "reasoning_tokens": sum(e.get("reasoning_tokens") or 0 for e in llm_calls),
        "rate_limit_retries": sum(e.get("rate_limit_retries") or 0 for e in llm_calls),
        "rate_limit_wait": sum(e.get("rate_limit_wait") or 0.0 for e in llm_calls),
        "cost": sum(e.get("cost") or 0 for e in llm_calls) if any(e.get("cost") is not None for e in llm_calls) else None,
        "latency": {
            "retrieval": round(sum(e.get("latency", 0) for e in events if e["kind"] in ("qdrant", "meili")), 4),
            "splitter": round(sum(e.get("latency", 0) for e in llm_calls if e["kind"] == "llm_split"), 4),
            "selector": round(sum(e.get("latency", 0) for e in llm_calls if e["kind"] == "llm_select"), 4),
        },
        "exact_alias_owners": sorted(alias_owners.get(target["user_message"].strip(), set())),
        "runtime_exact_path_used": False,
    }
    record["exact_alias_matches_gold"] = bool(record["exact_alias_owners"]) and bool(set(record["exact_alias_owners"]) & accepted)
    if not target["scorable"]:
        record.update(correct=None, exact=None, primary_cause="UNSCORED_NO_GOLD", secondary_causes=[])
        return record

    group_hit = [bool(g & set(selected)) for g in groups]
    correct = all(group_hit)
    exact = correct and set(selected) <= accepted
    # Retrieval: hedef grubunun ilk havuzdaki (orijinal mesaj) sırası ve herhangi bir havuzda bulunması
    def rank(ids: list[int], group: set[int]) -> int | None:
        return next((i + 1 for i, q in enumerate(ids) if q in group), None)

    first_ranks = [rank(first_pool["qna_ids"], g) for g in groups]
    any_pool = [any(g & set(p["qna_ids"]) for p in pools) for g in groups]
    qdrant_first = next((e for e in events if e["kind"] == "qdrant"), None)
    meili_first = next((e for e in events if e["kind"] == "meili"), None)
    source_hits = {
        "qdrant": [bool(qdrant_first) and bool(g & {h["qna_id"] for h in qdrant_first["hits"]}) for g in groups],
        "meili": [bool(meili_first) and bool(g & {h["qna_id"] for h in meili_first["hits"]}) for g in groups],
    }
    guarded_expected = accepted & guards
    blocked = {int(k) for k, v in result["guard_decisions"].items() if not v.get("selector_allowed")}
    selector_in_pool = [any(g & set(p["qna_ids"]) for p in pools) for g in groups]
    format_errors = [a for a in llm_calls if a["kind"] == "llm_select" and a.get("raw_output") is not None
                     and not any(ch.isdigit() for ch in a["raw_output"])]

    secondary = []
    if len(groups) == 1 and sub_count > 1:
        secondary.append("SPLITTER_FALSE_SPLIT")
    if len(groups) > 1 and sub_count < len(groups):
        secondary.append("SPLITTER_MISSED_SPLIT")
    primary = None
    if not correct:
        if result["unresolved_api_error"]:
            primary = "TIMEOUT_OR_API_ERROR"
        elif format_errors:
            primary = "MODEL_FORMAT_ERROR"
        elif guarded_expected & blocked:
            primary = "ROUTING_GUARD_BLOCK"
        elif selected_calendar:
            primary = "CALENDAR_ROUTING_INTERFERENCE"
        elif len(groups) > 1 and sub_count < len(groups):
            # Bölünmeyen niyet için ayrı alt soru aranmadığından retrieval eksiği bunun sonucudur.
            primary = "SPLITTER_MISSED_SPLIT"
        elif not all(any_pool):
            primary = "RETRIEVAL_MISS"
        elif record["fallback_used"]:
            primary = "FALLBACK_ERROR"
        elif all(selector_in_pool):
            primary = "SELECTOR_WRONG_CHOICE"
        else:
            primary = "NEEDS_HUMAN_ERROR_REVIEW"
    elif not exact:
        secondary.append("EXTRA_ANSWER_COMPOSED")
    record.update(
        correct=correct, exact=exact, group_hit=group_hit, first_pool_ranks=first_ranks, in_any_pool=any_pool,
        source_hits=source_hits, gold_guarded=bool(guarded_expected), guard_blocked=bool(guarded_expected & blocked),
        selector_had_gold=selector_in_pool, primary_cause=primary, secondary_causes=secondary,
        needs_human_review=primary is not None,
    )
    return record


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 4)


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"n": numerator, "of": denominator, "rate": round(numerator / denominator, 4) if denominator else None}


def metrics(evaluated: list[dict[str, Any]], targets: dict[int, dict[str, Any]]) -> dict[str, Any]:
    scorable = [e for e in evaluated if e["scorable"]]
    by_type: dict[str, dict[str, Any]] = {}
    for turn_type in ("FIRST_TURN", "FOLLOW_UP_CONTEXT_AVAILABLE_BUT_NOT_REQUIRED", "FOLLOW_UP_CONTEXT_REQUIRED"):
        rows = [e for e in evaluated if e["turn_type"] == turn_type]
        scored = [e for e in rows if e["scorable"]]
        by_type[turn_type] = {"targets": len(rows), "scorable": len(scored),
                              "correct": rate(sum(e["correct"] for e in scored), len(scored)),
                              "exact": rate(sum(e["exact"] for e in scored), len(scored))}
    single = [e for e in scorable if e["expected_intents"] == 1]
    multi = [e for e in scorable if e["expected_intents"] > 1]
    ranks = [min([r for r in e["first_pool_ranks"] if r is not None], default=None) if e["expected_intents"] == 1 else None
             for e in single]
    recall = {f"recall@{k}": rate(sum(1 for r in ranks if r is not None and r <= k), len(single)) for k in (1, 3, 5, 10)}
    recall["in_first_pool"] = rate(sum(1 for r in ranks if r is not None), len(single))
    recall["in_any_pool"] = rate(sum(all(e["in_any_pool"]) for e in single), len(single))
    recall["qdrant_top24"] = rate(sum(e["source_hits"]["qdrant"][0] for e in single), len(single))
    recall["meili_top5"] = rate(sum(e["source_hits"]["meili"][0] for e in single), len(single))
    recall["only_qdrant"] = sum(1 for e in single if e["source_hits"]["qdrant"][0] and not e["source_hits"]["meili"][0])
    recall["only_meili"] = sum(1 for e in single if e["source_hits"]["meili"][0] and not e["source_hits"]["qdrant"][0])
    recall["both"] = sum(1 for e in single if e["source_hits"]["meili"][0] and e["source_hits"]["qdrant"][0])
    recall["neither"] = sum(1 for e in single if not e["source_hits"]["meili"][0] and not e["source_hits"]["qdrant"][0])
    conditional = [e for e in scorable if all(e["selector_had_gold"]) and not e["api_error"] and not e["fallback_used"]]
    guarded = [e for e in scorable if e["gold_guarded"]]
    llm_path = [e for e in evaluated if not e["fallback_used"]]
    latencies = [e["elapsed"] for e in evaluated]
    costs = [e["cost"] for e in evaluated if e["cost"] is not None]
    causes = Counter(e["primary_cause"] for e in scorable if e["primary_cause"])
    return {
        "targets": len(evaluated), "scorable_targets": len(scorable),
        "overall": {"correct": rate(sum(e["correct"] for e in scorable), len(scorable)),
                    "exact": rate(sum(e["exact"] for e in scorable), len(scorable))},
        "by_turn_type": by_type,
        "multi_intent": {str(e["case_id"]): {"split_count": e["split_count"], "group_hit": e["group_hit"],
                                             "in_any_pool": e["in_any_pool"], "selected": e["selected_qna_ids"],
                                             "correct": e["correct"]} for e in multi},
        "retrieval": recall,
        "splitter": {
            "single_intent_kept_single": rate(sum(1 for e in single if e["split_count"] == 1), len(single)),
            "false_split": sum(1 for e in single if e["split_count"] > 1),
            "multi_intent_split_correct": rate(sum(1 for e in multi if e["split_count"] >= e["expected_intents"]), len(multi)),
            "missed_split": sum(1 for e in multi if e["split_count"] < e["expected_intents"]),
            "split_count_distribution": dict(sorted(Counter(e["split_count"] for e in evaluated).items())),
        },
        "selector": {
            "conditional_accuracy": rate(sum(e["correct"] for e in conditional), len(conditional)),
            "overall_accuracy": rate(sum(e["correct"] for e in scorable), len(scorable)),
            "declined_all_when_gold_in_pool": sum(1 for e in conditional if not e["selected_qna_ids"] and not e["selected_calendar"]),
        },
        "routing_guard": {
            "guarded_targets": len(guarded),
            "correct": rate(sum(e["correct"] for e in guarded), len(guarded)),
            "gold_reachable_in_pool": rate(sum(all(e["selector_had_gold"]) for e in guarded), len(guarded)),
            "guard_blocked_gold": sum(e["guard_blocked"] for e in guarded),
            "fallback_leak": sum(1 for e in guarded if e["fallback_used"] and set(e["selected_qna_ids"]) & set(targets[e["case_id"]]["expected_qna_ids"])),
        },
        "calendar": {
            "targets_with_calendar_candidates": sum(1 for e in evaluated if e["calendar_candidates_in_first_pool"]),
            "gold_calendar_intents": 0,
            "final_answer_from_calendar": sum(1 for e in evaluated if e["selected_calendar"]),
            "wrong_due_to_calendar": causes.get("CALENDAR_ROUTING_INTERFERENCE", 0),
            "note": "Gold'da takvim niyeti tanımlı değil; LLM yolunda tüm takvim kayıtları havuzun başına eklenir (yapısal).",
        },
        "exact_alias": {
            "targets_with_exact_alias": sum(1 for e in evaluated if e["exact_alias_owners"]),
            "exact_alias_owner_is_gold": sum(1 for e in evaluated if e["exact_alias_matches_gold"]),
            "runtime_exact_path_used": 0,
            "correct_when_exact_alias_is_gold": rate(sum(1 for e in scorable if e["exact_alias_matches_gold"] and e["correct"]),
                                                     sum(1 for e in scorable if e["exact_alias_matches_gold"])),
        },
        "error_taxonomy": dict(sorted(causes.items())),
        "secondary_causes": dict(sorted(Counter(c for e in scorable for c in e["secondary_causes"]).items())),
        "fallback_used": sum(1 for e in evaluated if e["fallback_used"]),
        "source_distribution": dict(sorted(Counter(e["source"] for e in evaluated).items())),
        "api": {"unresolved_errors": sum(e["api_error"] for e in evaluated),
                "rate_limit_retries": sum(e.get("rate_limit_retries", 0) for e in evaluated),
                "rate_limit_wait_seconds": round(sum(e.get("rate_limit_wait", 0.0) for e in evaluated), 1),
                "targets_with_retry": sum(1 for e in evaluated if e["retries_used"]),
                "total_retries": sum(e["retries_used"] for e in evaluated)},
        "model_ids_returned": sorted({m for e in evaluated for m in e["model_ids_returned"]}),
        "latency_seconds": {"total": round(sum(latencies), 2), "mean": round(statistics.mean(latencies), 4) if latencies else None,
                            "p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99),
                            "retrieval_mean": round(statistics.mean(e["latency"]["retrieval"] for e in evaluated), 4),
                            "splitter_mean": round(statistics.mean(e["latency"]["splitter"] for e in evaluated), 4),
                            "selector_mean": round(statistics.mean(e["latency"]["selector"] for e in evaluated), 4)},
        "tokens": {"prompt": sum(e["prompt_tokens"] for e in evaluated), "completion": sum(e["completion_tokens"] for e in evaluated),
                   "reasoning": sum(e["reasoning_tokens"] for e in evaluated),
                   "total": sum(e["prompt_tokens"] + e["completion_tokens"] for e in evaluated)},
        "cost_usd": {"reported_total": round(sum(costs), 6) if costs else None, "targets_with_cost": len(costs),
                     "source": "OpenRouter usage.cost (usage accounting)"},
        "llm_path_targets": len(llm_path),
    }


def mcnemar(only_a: int, only_b: int) -> dict[str, Any]:
    """Kesin (binom) iki yönlü McNemar testi."""
    n = only_a + only_b
    if n == 0:
        return {"n_discordant": 0, "p_value": 1.0}
    k = min(only_a, only_b)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return {"n_discordant": n, "p_value": round(min(1.0, 2 * p), 6), "method": "exact binomial McNemar"}


def bootstrap(a: list[bool], b: list[bool], iterations: int = 5000, seed: int = 20260917) -> dict[str, Any]:
    rng = random.Random(seed)
    n = len(a)
    diffs = []
    for _ in range(iterations):
        sample = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(a[i] for i in sample) / n - sum(b[i] for i in sample) / n)
    diffs.sort()
    return {"mean_diff": round(sum(a) / n - sum(b) / n, 4), "ci95": [round(diffs[int(0.025 * iterations)], 4),
            round(diffs[int(0.975 * iterations)], 4)], "iterations": iterations, "seed": seed, "method": "paired bootstrap"}


def report() -> int:
    targets = {t["case_id"]: t for t in build_targets()}
    guards = {int(r["qna_id"]) for r in json.loads((BASELINE / "qna-routing-guards.json").read_text(encoding="utf-8"))}
    alias_owners: dict[str, set[int]] = defaultdict(set)
    for row in json.loads((BASELINE / "qna-aliases.json").read_text(encoding="utf-8")):
        alias_owners[row["query_text"].strip()].add(int(row["qna_id"]))
    evaluations, model_metrics, completeness = {}, {}, {}
    for model_key in MODELS:
        checkpoint = json.loads((OUT / model_key / "checkpoint.json").read_text(encoding="utf-8"))
        results = {r["case_id"]: r for r in read_jsonl(OUT / model_key / "results.jsonl")}
        completeness[model_key] = checkpoint["complete"] and set(results) == set(targets)
        rows = [evaluate(targets[c], results[c], guards, alias_owners) for c in sorted(results)]
        evaluations[model_key] = {r["case_id"]: r for r in rows}
        model_metrics[model_key] = metrics(rows, targets)
        (OUT / model_key / "metrics.json").write_text(dump(model_metrics[model_key]), encoding="utf-8")
        (OUT / model_key / "errors.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                                             for r in rows if r["scorable"] and not r["correct"]), encoding="utf-8")
    a_key, b_key = "4o-mini", "luna-high"
    common = sorted(c for c in targets if targets[c]["scorable"] and c in evaluations[a_key] and c in evaluations[b_key])
    paired = []
    buckets = defaultdict(list)
    for case_id in common:
        a, b = evaluations[a_key][case_id]["correct"], evaluations[b_key][case_id]["correct"]
        bucket = "both_correct" if a and b else "only_4o_mini" if a else "only_luna_high" if b else "both_wrong"
        buckets[bucket].append(case_id)
        paired.append({"case_id": case_id, "bucket": bucket, "turn_type": targets[case_id]["turn_type"],
                       "expected_qna_ids": targets[case_id]["expected_qna_ids"],
                       "4o_mini_selected": evaluations[a_key][case_id]["selected_qna_ids"],
                       "luna_high_selected": evaluations[b_key][case_id]["selected_qna_ids"],
                       "4o_mini_cause": evaluations[a_key][case_id]["primary_cause"],
                       "luna_high_cause": evaluations[b_key][case_id]["primary_cause"]})
    exact_buckets = defaultdict(list)
    for case_id in common:
        a, b = evaluations[a_key][case_id]["exact"], evaluations[b_key][case_id]["exact"]
        exact_buckets["both_correct" if a and b else "only_4o_mini" if a else "only_luna_high" if b else "both_wrong"].append(case_id)
    comparison = {
        "paired_scorable_targets": len(common),
        "exact_metric": {
            "counts": {k: len(v) for k, v in sorted(exact_buckets.items())},
            "mcnemar": mcnemar(len(exact_buckets["only_4o_mini"]), len(exact_buckets["only_luna_high"])),
            "bootstrap_4o_minus_luna": bootstrap([evaluations[a_key][c]["exact"] for c in common],
                                                 [evaluations[b_key][c]["exact"] for c in common]) if common else None,
        },
        "counts": {k: len(v) for k, v in sorted(buckets.items())},
        "cases": {k: v for k, v in sorted(buckets.items()) if k != "both_correct"},
        "mcnemar": mcnemar(len(buckets["only_4o_mini"]), len(buckets["only_luna_high"])),
        "bootstrap_4o_minus_luna": bootstrap([evaluations[a_key][c]["correct"] for c in common],
                                             [evaluations[b_key][c]["correct"] for c in common]) if common else None,
    }
    gate_after = gate()
    gate_before = json.loads((OUT / a_key / "checkpoint.json").read_text(encoding="utf-8"))["gate_before"]
    stable_keys = ("db_snapshot_digest", "meili_documents_digest", "meili_settings_digest", "qdrant_points_digest",
                   "session_gold_manifest_sha256", "reviewed_gold_manifest_sha256", "kb_canonical_sha256",
                   "kb_aliases_sha256", "kb_guards_sha256")
    mutation = {k: gate_before[k] == gate_after[k] for k in stable_keys}
    luna_before = json.loads((OUT / b_key / "checkpoint.json").read_text(encoding="utf-8"))["gate_before"]
    mutation.update({f"luna_before_{k}": luna_before[k] == gate_after[k] for k in stable_keys})
    complete = all(completeness.values()) and all(mutation.values()) and gate_after["ok"]
    error_rows = []
    for model_key, rows in evaluations.items():
        for row in rows.values():
            if row["scorable"] and not row["correct"]:
                error_rows.append({"model": model_key, "case_id": row["case_id"], "turn_type": row["turn_type"],
                                   "primary_cause": row["primary_cause"], "secondary_causes": row["secondary_causes"],
                                   "expected_qna_ids": targets[row["case_id"]]["expected_qna_ids"],
                                   "selected_qna_ids": row["selected_qna_ids"], "source": row["source"],
                                   "in_any_pool": row["in_any_pool"], "split_count": row["split_count"]})
    manifest = {
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "completeness": completeness,
        "chatbot_git": git_head(COMPOSE_ROOT),
        "analysis_git": git_head(ROOT),
        "gate_before": {"4o-mini": gate_before, "luna-high": luna_before},
        "gate_after": gate_after,
        "mutation_gate": mutation,
        "models": MODELS,
        "model_ids_returned": {k: m["model_ids_returned"] for k, m in model_metrics.items()},
        "prompt_template": {"source": "backend/services/llm_provider.py (_build_prompt, _build_split_prompt)",
                            "sha256": sha256(COMPOSE_ROOT / "backend" / "services" / "llm_provider.py")},
        "pipeline": {"source": "backend/services/answer_pipeline.py::answer_question",
                     "sha256": sha256(COMPOSE_ROOT / "backend" / "services" / "answer_pipeline.py")},
        "retrieval": {"qdrant_limit": 24, "meili_limit": 5, "contextual_qdrant_limit": 12, "contextual_meili_limit": 3,
                      "calendar": "tüm akademik takvim kayıtları havuzun başına", "fallback_thresholds": {"meili": 0.90, "qdrant": 0.75}},
        "splitter": {"max_tokens": 300, "fallback": "regex split on exception"},
        "exact_match": "runtime'da exact alias kısa yolu yok; yalnız gözlem",
        "context_policy": CONTEXT_POLICY,
        "retry_policy": RETRY,
        "input_order": "case_id artan",
        "concurrency": "hedefler sıralı; pipeline içi split + spekülatif seçim paralel (üretimle aynı)",
        "declared_deviations": [
            "Model kimliği üretimde sabit olduğu için süreç içinde aynı sınıfla (BenchProvider) değiştirildi.",
            "Luna-high için min max_tokens 1280 ve reasoning={effort: high, exclude: true}; 4o-mini üretim değerlerinde.",
            "Her iki modelde OpenRouter usage accounting (extra_body.usage.include) ve 120 s istek zaman aşımı eklendi.",
            "Hedef düzeyinde API hatası retry'ı benchmark politikasıdır (üretim sessizce fallback'e düşer).",
            "Bağlam yalnız FOLLOW_UP_CONTEXT_REQUIRED hedeflerine router penceresiyle verildi; üretimde bağlam varsayılan kapalı.",
            "17 bağlam hedefinin dondurulmuş gold'da beklenen QnA'sı yok; bu hedefler koşturuldu ama puanlanmadı.",
        ],
        "outputs_sha256": {},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "comparison.json").write_text(dump({"metrics": model_metrics, "paired": comparison}), encoding="utf-8")
    (OUT / "paired-cases.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in paired), encoding="utf-8")
    (OUT / "error-analysis.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in error_rows), encoding="utf-8")
    (OUT / "BENCHMARK-REPORT.md").write_text(render(model_metrics, comparison, manifest), encoding="utf-8")
    names = ["comparison.json", "paired-cases.jsonl", "error-analysis.jsonl", "BENCHMARK-REPORT.md"]
    for model_key in MODELS:
        names += [f"{model_key}/{n}" for n in ("results.jsonl", "metrics.json", "errors.jsonl", "checkpoint.json")]
    manifest["outputs_sha256"] = {n: sha256(OUT / n) for n in names}
    (OUT / "benchmark-manifest.json").write_text(dump(manifest), encoding="utf-8")
    print(json.dumps({"complete": complete, "mutation_gate_ok": all(mutation.values()),
                      "overall": {k: m["overall"] for k, m in model_metrics.items()},
                      "paired": comparison["counts"]}, ensure_ascii=False, indent=2))
    return 0 if complete else 2


def render(model_metrics: dict[str, Any], comparison: dict[str, Any], manifest: dict[str, Any]) -> str:
    def pct(entry: dict[str, Any]) -> str:
        return f"{entry['n']}/{entry['of']} ({entry['rate'] * 100:.1f}%)" if entry and entry["of"] else "—"

    lines = [f"# Session-gold v2 E2E baseline — {'TAMAM' if manifest['complete'] else 'EKSİK'}", "",
             f"Chatbot `{manifest['chatbot_git']['commit'][:8]}` · analiz `{manifest['analysis_git']['commit'][:8]}`", "",
             "| Metrik | 4o-mini | Luna-high |", "|---|---|---|"]
    a, b = model_metrics["4o-mini"], model_metrics["luna-high"]
    rows = [
        ("E2E doğru (puanlanan)", pct(a["overall"]["correct"]), pct(b["overall"]["correct"])),
        ("E2E birebir", pct(a["overall"]["exact"]), pct(b["overall"]["exact"])),
        *[(f"{t}", pct(a["by_turn_type"][t]["correct"]), pct(b["by_turn_type"][t]["correct"])) for t in a["by_turn_type"]],
        ("Retrieval ilk havuzda", pct(a["retrieval"]["in_first_pool"]), pct(b["retrieval"]["in_first_pool"])),
        ("Recall@1 / @5 (havuz sırası)", f"{pct(a['retrieval']['recall@1'])} / {pct(a['retrieval']['recall@5'])}",
         f"{pct(b['retrieval']['recall@1'])} / {pct(b['retrieval']['recall@5'])}"),
        ("Seçici koşullu doğruluk", pct(a["selector"]["conditional_accuracy"]), pct(b["selector"]["conditional_accuracy"])),
        ("Gereksiz bölme (tek niyet)", str(a["splitter"]["false_split"]), str(b["splitter"]["false_split"])),
        ("Takvimden final cevap", str(a["calendar"]["final_answer_from_calendar"]), str(b["calendar"]["final_answer_from_calendar"])),
        ("Fallback kullanımı", str(a["fallback_used"]), str(b["fallback_used"])),
        ("Çözülemeyen API hatası", str(a["api"]["unresolved_errors"]), str(b["api"]["unresolved_errors"])),
        ("Gecikme p50 / p95 (s)", f"{a['latency_seconds']['p50']} / {a['latency_seconds']['p95']}",
         f"{b['latency_seconds']['p50']} / {b['latency_seconds']['p95']}"),
        ("Maliyet (OpenRouter, $)", str(a["cost_usd"]["reported_total"]), str(b["cost_usd"]["reported_total"])),
    ]
    lines += [f"| {name} | {x} | {y} |" for name, x, y in rows]
    lines += ["", "## Eşli karşılaştırma", "", f"`{json.dumps(comparison['counts'], ensure_ascii=False)}`",
              f"McNemar: `{json.dumps(comparison['mcnemar'], ensure_ascii=False)}`",
              f"Bootstrap (4o-mini − Luna): `{json.dumps(comparison['bootstrap_4o_minus_luna'], ensure_ascii=False)}`",
              f"Birebir metrik: `{json.dumps(comparison['exact_metric'], ensure_ascii=False)}`", "",
              "Not: 'doğru' metriği, gereksiz bölmeyle birden çok cevap birleştiren koşuyu kayırır; "
              "'birebir' metriği fazladan cevabı hata sayar.", "",
              "Not: Benchmark mesajları KB alias'larından türetildiği için exact-alias ölçümleri iyimser yanlıdır.", "",
              "## Hata sınıfları", "",
              f"- 4o-mini: `{json.dumps(a['error_taxonomy'], ensure_ascii=False)}`",
              f"- Luna-high: `{json.dumps(b['error_taxonomy'], ensure_ascii=False)}`", "",
              "## Beyan edilen sapmalar", "", *[f"- {d}" for d in manifest["declared_deviations"]], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--model", choices=sorted(MODELS), required=True)
    sub.add_parser("report")
    sub.add_parser("targets-check")
    args = parser.parse_args()
    if args.command == "run":
        return run_model(args.model)
    if args.command == "targets-check":
        targets = build_targets()
        print(json.dumps({"targets": len(targets), "scorable": sum(t["scorable"] for t in targets),
                          "with_context": sum(1 for t in targets if t["context"]),
                          "turn_types": dict(Counter(t["turn_type"] for t in targets))}, ensure_ascii=False))
        return 0
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
