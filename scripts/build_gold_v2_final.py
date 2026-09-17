"""Gold v2 final: v3.1 migration sonrası KB baseline'ına göre deterministik üretim.

Kaynak zinciri (hepsi Git'te versioned):
1. 516 alias vaka evreni + 174 insan incelemesi → mevcut ``build_gold_v2``
   hattı (taslakla bayt-özdeş olduğu doğrulanır). Bu aşamada vakaların eski
   QnA kimlikleri migration öncesi katalogla çözülür; katalog, post-migration
   baseline + v3.1 planındaki ``expected_current`` alanlarından yeniden kurulup
   kaynak katalogla birebir karşılaştırılır.
2. KB içeriği gerektiren 68 vaka → v3.1 final workbook insan kararı + v3.1
   plan + migration raporundaki gerçek QnA kimliği (üçü birbirine karşı
   doğrulanır).
3. Beklenen QnA kimlikleri ve donmuş cevaplar yalnız post-migration
   baseline'dan gelir. Routing guard bilgisi yalnız evaluation metadata'dır.

Blokaj yoksa ve sayılar beklentiyle uyuşuyorsa manifest ``frozen: true`` olur;
aksi halde çıktılar yazılır ama ``frozen: false`` kalır ve rapor nedenini söyler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_gold_v2 import (  # noqa: E402
    apply_simple_review_result,
    file_digest,
    load_baseline_aliases,
    load_catalog,
    load_review_workbook,
    merge_reviewed_cases,
    validate_rows,
)
from scripts.dry_run_kb_consolidation import workbook_tables  # noqa: E402

DATASET_VERSION = "gold-v2-final"
SOURCES = ROOT / "outputs" / "gold-v2-sources-20260917"
PLAN = ROOT / "outputs" / "kb-consolidation-v3.1-final-dry-run-guard-20260917" / "kb-mutation-plan-v3.1-final.json"
MIGRATION_GLOB = "outputs/kb-migration-v3.1-local-apply-*/baseline/migration-report.json"
EXPECTED_COUNTS = {"READY": 490, "CONTEXT_REQUIRED": 25, "PENDING_CONTENT": 1}
EXPECTED_PENDING = [214]
STATUS_MAP = {"ready": "READY", "needs_context": "CONTEXT_REQUIRED", "needs_kb": "NEEDS_KB"}
TEMPORAL_KIND = {
    "historical_term_snapshot": "historical",
    "blocked_shared_qna_update": "dated_content_with_expiry",
    "policy_sensitive": "policy",
    "policy_review": "policy",
    "policy_snapshot": "policy",
}
OUTPUT_FILES = (
    "gold-v2-all.jsonl",
    "gold-v2-ready.jsonl",
    "gold-v2-context-required.jsonl",
    "gold-v2-pending.jsonl",
    "gold-v2-report.json",
    "gold-v2-diff-from-previous.json",
    "GOLD-V2-REPORT.md",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def temporal_kind(content_mode: str) -> str:
    return TEMPORAL_KIND.get(content_mode, "dynamic_current_status" if content_mode.startswith("dynamic") else "other")


# --------------------------------------------------------------------------
# Kaynak yükleme
# --------------------------------------------------------------------------


def discover_migration_report(root: Path) -> Path:
    candidates = sorted(root.glob(MIGRATION_GLOB))
    if not candidates:
        raise FileNotFoundError(f"Migration baseline bulunamadı: {MIGRATION_GLOB}")
    return candidates[-1]


def load_baseline(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    base = report_path.parent
    mismatched = [name for name, digest in report["baseline_files"].items() if file_digest(base / name) != digest]
    if mismatched:
        raise ValueError(f"Baseline dosyaları migration raporuyla eşleşmiyor: {mismatched}")
    if report["counts"]["post"] != {"active_qna": 326, "aliases": 2695, "guards": 11}:
        raise ValueError(f"Beklenmeyen post-migration sayıları: {report['counts']['post']}")
    qna = json.loads((base / "qna-canonical.json").read_text(encoding="utf-8"))
    aliases = json.loads((base / "qna-aliases.json").read_text(encoding="utf-8"))
    guards = json.loads((base / "qna-routing-guards.json").read_text(encoding="utf-8"))
    active = {int(r["id"]): r for r in qna if r["status"] == 1}
    owners: dict[str, set[int]] = defaultdict(set)
    aliases_by_qna: dict[int, set[str]] = defaultdict(set)
    for row in aliases:
        if int(row["qna_id"]) in active:
            owners[text(row["query_text"])].add(int(row["qna_id"]))
            aliases_by_qna[int(row["qna_id"])].add(text(row["query_text"]))
    return {
        "report_path": report_path, "report": report, "dir": base, "qna": qna, "active": active,
        "aliases": aliases, "alias_owners": owners, "aliases_by_qna": aliases_by_qna,
        "guards": {int(g["qna_id"]): g for g in guards},
        "created": {ref: int(qid) for ref, qid in report["created_qna_ids"].items()},
    }


def reconstruct_pre_catalog(baseline: dict[str, Any], plan: dict[str, Any]) -> dict[int, dict[str, str]]:
    """Post baseline − yeni QnA'lar, güncellenen QnA'larda plandaki expected_current."""
    created = set(baseline["created"].values())
    updates = {
        int(m["qna_id"]): m["expected_current"]
        for m in plan["qna_mutations"]
        if m["action"] == "update_qna" and not m["status"].startswith("BLOCKED")
    }
    applied = {int(u["qna_id"]) for u in baseline["report"]["units"] if u["kind"] == "update"}
    if applied != set(updates):
        raise ValueError("Migration raporundaki update birimleri plandaki güncellemelerle eşleşmiyor")
    catalog = {}
    for qid, row in baseline["active"].items():
        if qid in created:
            continue
        current = updates.get(qid, {"question": row["question_text"], "answer": row["answer_text"]})
        catalog[qid] = {"id": qid, "question": text(current["question"]), "answer": text(current["answer"])}
    return catalog


def review_pipeline(sources: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Önceki (doğrulanmış) inceleme hattını aynen koşturur."""
    catalog = load_catalog(sources / "qna-catalog-20260915.json")
    baseline_cases = load_baseline_aliases(sources / "alias-session-matches.jsonl", catalog)
    rows, split_map = load_review_workbook(sources / "yanit-gold-inceleme-174.xlsx")
    rows = apply_simple_review_result(
        rows, sources / "auzef-inceleme-174-174.resolved.json",
        expected_source_digest=file_digest(sources / "yanit-gold-inceleme-174.xlsx"),
    )
    reviewed, errors, _ = validate_rows(rows, split_map, catalog)
    cases, alignment = merge_reviewed_cases(baseline_cases, reviewed)
    errors.extend(alignment)
    if errors:
        raise ValueError(f"İnceleme hattı hatalı: {errors[:5]}")
    return cases, catalog


def v31_decisions(workbook: Path, plan: dict[str, Any], baseline: dict[str, Any]) -> dict[int, dict[str, Any]]:
    tables = workbook_tables(workbook)
    holds = {text(r["Bekleme ID"]): r for r in tables["Teyit Bekleyenler"]}
    controls = {text(r["Hedef"]): r for r in tables["Dönemsel İçerik"]}
    alias_plan = {int(a["case_no"]): a for a in plan["alias_mutations"]}
    promotion = {int(p["case_no"]): p for p in plan["atomic_promotions"]}
    pending = {int(p["case_no"]): p for p in plan["pending"]}
    decisions = {}
    for row in tables["Alias Haritası"]:
        case = int(row["Vaka no"])
        target_ref = text(row["Hedef"])
        record: dict[str, Any] = {
            "case_id": case,
            "message": text(row["Öğrenci mesajı"]),
            "alias_text": text(row["CSV'deki birebir alias"]),
            "abi_karari": text(row["Abi kararı"]),
            "kontrol_turu": text(row["Kontrol türü"]),
            "alias_islemi": text(row["Alias işlemi"]),
            "hedef": target_ref,
            "hedef_icerik_islemi": text(row["Hedef içerik işlemi"]),
            "durum": text(row["Durum"]),
            "abi_notu": text(row["Abi notu"]) if text(row["Abi notu"]) != "None" else "",
            "gerekce": text(row["Gerekçe"]),
            "target_question": text(row["Hedef kanonik soru"]),
            "errors": [],
        }
        if target_ref.startswith("HOLD-"):
            record["pending"] = True
            record["hold"] = {k: text(v) for k, v in holds[target_ref].items()}
            if case not in pending:
                record["errors"].append("hold_case_not_pending_in_plan")
            decisions[case] = record
            continue
        record["pending"] = False
        if target_ref.startswith("NEW-"):
            target_id = baseline["created"].get(target_ref)
            record["ref"] = target_ref
        else:
            target_id = int(float(text(row["Hedef QnA ID"])))
            record["ref"] = f"EX-{target_id}"
        record["target_id"] = target_id
        control_key = target_ref if target_ref.startswith("NEW-") else f"EX-{target_id}"
        if control_key in controls:
            record["control"] = {k: text(v) for k, v in controls[control_key].items()}
        target = baseline["active"].get(target_id) if target_id else None
        if target is None:
            record["errors"].append("target_not_active_in_baseline")
        elif text(target["question_text"]) != record["target_question"]:
            record["errors"].append("target_question_differs_from_baseline")
        if case in alias_plan:
            planned = alias_plan[case]["target"]
            planned_id = planned.get("qna_id") or baseline["created"].get(planned.get("temp_ref"))
            record["plan_action"] = "move_alias"
            record["plan_source_question"] = alias_plan[case]["expected_source"]["canonical_question"]
            if planned_id != target_id:
                record["errors"].append("plan_alias_target_differs")
            if record["alias_text"] not in baseline["aliases_by_qna"].get(target_id, set()):
                record["errors"].append("moved_alias_not_under_target_in_baseline")
        elif case in promotion:
            record["plan_action"] = "promote_alias_to_new_qna_canonical"
            if baseline["created"].get(promotion[case]["operation_ref"]) != target_id:
                record["errors"].append("plan_promotion_target_differs")
            if baseline["alias_owners"].get(record["alias_text"]):
                record["errors"].append("promoted_alias_still_exists_in_baseline")
        else:
            record["plan_action"] = "alias_stays"
            if record["alias_text"] not in baseline["aliases_by_qna"].get(target_id, set()):
                record["errors"].append("staying_alias_not_under_target_in_baseline")
        decisions[case] = record
    return decisions


# --------------------------------------------------------------------------
# Vaka üretimi
# --------------------------------------------------------------------------


def guard_metadata(expected_ids: list[int], decision: dict[str, Any] | None, baseline: dict[str, Any],
                   controls_by_guard: dict[str, dict[str, Any]]) -> dict[str, Any]:
    guards = [baseline["guards"][qid] for qid in expected_ids if qid in baseline["guards"]]
    control = (decision or {}).get("control")
    if not control and guards:
        control = controls_by_guard.get(guards[0]["guard_ref"])
    if not guards and not control:
        return {"temporal": False, "routing_guarded": False, "guard_refs": [], "temporal_meta": None, "as_of_date": None}
    mode = guards[0]["content_mode"] if guards else text(control["İçerik modu"])
    as_of = text(control["Bilgi tarihi"]) if control else None
    valid_until = guards[0]["valid_until"] if guards else None
    return {
        "temporal": True,
        "routing_guarded": bool(guards),
        "guard_refs": sorted(g["guard_ref"] for g in guards),
        "as_of_date": as_of,
        "temporal_meta": {
            "content_mode": mode,
            "temporal_kind": temporal_kind(mode),
            "as_of_date": as_of,
            "valid_until": valid_until,
            "source_of_truth": guards[0]["source_of_truth"] if guards else text(control["Doğruluk kaynağı"]),
            "production_rule": text(control["Üretim kuralı"]) if control else None,
            "gold_policy": (
                "Tarihli geçmiş dönem içeriği; puanlama as_of_date bağlamında yapılır."
                if temporal_kind(mode) == "historical" else
                "Beklenen hedef niyet/QnA'dır; sabit eski tarih cevabı zorunlu tutulmaz, güncel kaynak yönlendirmesi kabul edilir."
            ),
        },
    }


def intent_groups(case: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """İnsan kararındaki niyet gruplarını döndürür; aynı QnA kümesini kabul eden
    gruplar tek niyete indirgenir (ikinci cümle birincinin açıklaması)."""
    groups = [(i["intent_text"], sorted(i["accepted_qna_ids"])) for i in case["intents"]]
    distinct = sorted({tuple(ids) for _, ids in groups})
    audit = {"split_label": case["split_decision"], "raw_intent_groups": len(groups), "distinct_id_groups": len(distinct)}
    if len(groups) > 1 and len(distinct) == 1:
        audit["cleanup"] = "identical_accepted_sets_collapsed_to_single_intent"
        return [{"intent_index": 1, "intent_text": case["student_message"], "accepted_qna_ids": list(distinct[0]),
                 "split_texts": [t for t, _ in groups]}], audit
    return [{"intent_index": n, "intent_text": t, "accepted_qna_ids": ids} for n, (t, ids) in enumerate(groups, start=1)], audit


def build_cases(review_cases: list[dict[str, Any]], decisions: dict[int, dict[str, Any]], baseline: dict[str, Any],
                resolutions: dict[int, dict[str, Any]], controls_by_guard: dict[str, dict[str, Any]],
                op_refs: dict[int, str]) -> list[dict[str, Any]]:
    records = []
    for case in review_cases:
        cid = int(case["case_id"])
        prior_status = STATUS_MAP[case["status"]]
        decision = decisions.get(cid)
        record: dict[str, Any] = {
            "case_id": cid,
            "alias_id": case["alias_id"],
            "user_message": case["student_message"],
            "previous_gold": {
                "status": prior_status,
                "qna_ids": sorted({q for i in case["intents"] for q in i["accepted_qna_ids"]}),
                "baseline_question": case["previous_gold"]["question"],
                "baseline_qna_id": str(case["previous_gold"]["qna_id"]),
            },
            "replay_session_ids": case["replay"]["session_ids"],
        }
        intents: list[dict[str, Any]] = []
        split_audit = {"split_label": case["split_decision"], "raw_intent_groups": len(case["intents"])}
        if prior_status == "NEEDS_KB":
            if decision is None:
                record.update(status="INVALID", notes="needs_kb vakası v3.1 workbook'unda yok")
            elif decision["pending"]:
                record.update(status="PENDING_CONTENT", notes=decision["hold"]["Beklenen netlik"])
            else:
                record["status"] = "READY"
                intents = [{"intent_index": 1, "intent_text": case["student_message"], "accepted_qna_ids": [decision["target_id"]]}]
                split_audit["cleanup"] = "v31_single_target_decision" if case["split_decision"] != "Bölünmedi" else None
                record["notes"] = decision["abi_notu"] or decision["gerekce"]
            record["source_decision"] = {
                "origin": "v31_content_review",
                "human_review_174": {"decision": case["review_decision"], "note": case["review_note"]},
                **({k: decision[k] for k in ("abi_karari", "kontrol_turu", "alias_islemi", "hedef", "hedef_icerik_islemi",
                                              "durum", "abi_notu", "gerekce", "plan_action", "errors") if k in decision}
                   if decision else {}),
            }
        elif prior_status == "READY":
            record["status"] = "READY"
            intents, audit = intent_groups(case)
            split_audit.update(audit)
            record["source_decision"] = (
                {"origin": "human_review_174", "decision": case["review_decision"], "note": case["review_note"],
                 "data_issue": case["data_issue"]}
                if case["reviewed"] else
                {"origin": "baseline_alias_mapping", "note": "İnceleme dışı alias; mevcut QnA eşleşmesi korunur"}
            )
            record["notes"] = case["review_note"]
        else:
            record["status"] = "CONTEXT_REQUIRED"
            resolution = resolutions.get(cid)
            record["context"] = {
                "reason": case["review_note"],
                "required_context": "Önceki konuşma turnleri; oturum geçmişi olmadan hedef niyet belirlenemiyor.",
                "session_ids": case["replay"]["session_ids"],
                "session_source": resolution["source"] if resolution else None,
                "known_intent_info": {"baseline_question": case["previous_gold"]["question"],
                                      "baseline_qna_id": str(case["previous_gold"]["qna_id"])},
            }
            record["source_decision"] = {"origin": "human_review_174", "decision": case["review_decision"],
                                         "status_override": case.get("evaluation_status_override"),
                                         "resolution_reason": resolution["reason"] if resolution else None}
            record["notes"] = case["review_note"]

        for intent in intents:
            intent["accepted_answers"] = [
                {"qna_id": qid, "question": baseline["active"][qid]["question_text"], "answer": baseline["active"][qid]["answer_text"]}
                for qid in intent["accepted_qna_ids"] if qid in baseline["active"]
            ]
        expected = sorted({q for i in intents for q in i["accepted_qna_ids"]})
        record["expected_intents"] = intents
        record["expected_qna_ids"] = expected
        record["expected_qna_refs"] = [op_refs.get(q, f"QNA-{q}") for q in expected]
        record["context_required"] = record["status"] == "CONTEXT_REQUIRED"
        record["multi_intent"] = len(intents) > 1
        record["split_audit"] = split_audit
        record.update(guard_metadata(expected, decision, baseline, controls_by_guard))
        records.append(record)
    return sorted(records, key=lambda r: r["case_id"])


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


def audit(records: list[dict[str, Any]], decisions: dict[int, dict[str, Any]], baseline: dict[str, Any],
          plan: dict[str, Any], updated_ids: set[int]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    counts = Counter(r["status"] for r in records)

    ids = Counter(r["case_id"] for r in records)
    aliases = Counter(r["alias_id"] for r in records)
    if any(v > 1 for v in ids.values()):
        blockers.append({"check": "duplicate_case_id", "cases": sorted(k for k, v in ids.items() if v > 1)})
    if any(v > 1 for v in aliases.values()):
        blockers.append({"check": "duplicate_alias_id", "aliases": sorted(k for k, v in aliases.items() if v > 1)})
    invalid = [r["case_id"] for r in records if r["status"] not in EXPECTED_COUNTS]
    if invalid:
        blockers.append({"check": "invalid_status", "cases": invalid})

    pending = sorted(r["case_id"] for r in records if r["status"] == "PENDING_CONTENT")
    if pending != EXPECTED_PENDING:
        blockers.append({"check": "pending_set", "expected": EXPECTED_PENDING, "actual": pending})
    by_status = defaultdict(set)
    for r in records:
        by_status[r["status"]].add(r["case_id"])
    overlap = sorted(by_status["READY"] & (by_status["CONTEXT_REQUIRED"] | by_status["PENDING_CONTENT"]))
    if overlap:
        blockers.append({"check": "status_overlap", "cases": overlap})

    for r in records:
        if r["status"] == "READY":
            if not r["expected_qna_ids"] or any(not i["accepted_qna_ids"] for i in r["expected_intents"]):
                blockers.append({"check": "ready_without_expected_qna", "case": r["case_id"]})
            missing = [q for q in r["expected_qna_ids"] if q not in baseline["active"]]
            if missing:
                blockers.append({"check": "expected_qna_not_active_in_baseline", "case": r["case_id"], "ids": missing})
        elif r["expected_qna_ids"]:
            blockers.append({"check": "non_ready_has_expected_qna", "case": r["case_id"]})

    for case, decision in decisions.items():
        if decision["errors"]:
            blockers.append({"check": "v31_decision_inconsistent", "case": case, "errors": decision["errors"]})

    by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_message[" ".join(r["user_message"].casefold().split())].append(r)
    for message, group in by_message.items():
        if len(group) < 2:
            continue
        signatures = {(r["status"], tuple(r["expected_qna_ids"])) for r in group}
        entry = {"cases": sorted(r["case_id"] for r in group), "message": group[0]["user_message"],
                 "targets": sorted({f"{r['status']}:{r['expected_qna_ids']}" for r in group})}
        if len(signatures) > 1:
            blockers.append({"check": "same_message_conflicting_gold", **entry})
        else:
            warnings.append({"check": "duplicate_message_consistent_gold", **entry})

    alias_mapping = {"owner_in_expected": 0, "not_an_alias_after_migration": [], "human_override": [], "conflict": []}
    for r in records:
        if r["status"] != "READY":
            continue
        owners = sorted(baseline["alias_owners"].get(r["user_message"], set()))
        if not owners:
            alias_mapping["not_an_alias_after_migration"].append(r["case_id"])
        elif set(owners) & set(r["expected_qna_ids"]):
            alias_mapping["owner_in_expected"] += 1
        else:
            entry = {"case": r["case_id"], "alias_owners": owners, "expected": r["expected_qna_ids"],
                     "origin": r["source_decision"]["origin"]}
            if r["source_decision"]["origin"] == "baseline_alias_mapping":
                alias_mapping["conflict"].append(entry)
            else:
                alias_mapping["human_override"].append(entry)
    for entry in alias_mapping["conflict"]:
        blockers.append({"check": "unreviewed_case_alias_owner_differs", **entry})
    promoted = {int(p["case_no"]) for p in plan["atomic_promotions"]}
    unexpected_non_alias = [c for c in alias_mapping["not_an_alias_after_migration"] if c not in promoted]
    if unexpected_non_alias:
        blockers.append({"check": "ready_message_not_alias_unexpectedly", "cases": unexpected_non_alias})

    moved = {int(a["case_no"]) for a in plan["alias_mutations"]}
    moved_ok = [c for c in moved if next(r for r in records if r["case_id"] == c)["expected_qna_ids"]
                == sorted(baseline["alias_owners"].get(decisions[c]["alias_text"], set()))]
    if len(moved_ok) != len(moved):
        blockers.append({"check": "moved_alias_expected_differs_from_baseline_owner",
                         "cases": sorted(moved - set(moved_ok))})

    multi = [r for r in records if r["multi_intent"]]
    split_cleanups = [{"case": r["case_id"], **r["split_audit"]} for r in records if r["split_audit"].get("cleanup")]
    content_changed = sorted({r["case_id"] for r in records if r["status"] == "READY"
                              and set(r["expected_qna_ids"]) & updated_ids})
    return {
        "counts": dict(sorted(counts.items())),
        "total": len(records),
        "expected_counts": EXPECTED_COUNTS,
        "counts_match_expectation": {k: counts.get(k, 0) for k in EXPECTED_COUNTS} == EXPECTED_COUNTS,
        "blockers": blockers,
        "warnings": warnings,
        "alias_mapping": {**alias_mapping, "human_override_count": len(alias_mapping["human_override"])},
        "moved_alias_cases_verified": len(moved_ok),
        "multi_intent_cases": [{"case": r["case_id"], "groups": [i["accepted_qna_ids"] for i in r["expected_intents"]]} for r in multi],
        "split_label_cleanups": split_cleanups,
        "ready_cases_with_content_updated_by_migration": content_changed,
    }


def diff_from_previous(records: list[dict[str, Any]]) -> dict[str, Any]:
    changes = []
    for r in records:
        before, after = r["previous_gold"], {"status": r["status"], "qna_ids": r["expected_qna_ids"]}
        if before["status"] == after["status"] and before["qna_ids"] == after["qna_ids"]:
            continue
        reason = {
            ("NEEDS_KB", "READY"): "v3.1 içerik kararı + migration sonrası gerçek QnA",
            ("NEEDS_KB", "PENDING_CONTENT"): "v3.1 teyit bekleyen içerik (HOLD)",
        }.get((before["status"], after["status"]), "status/target değişikliği")
        if before["status"] == after["status"] == "READY":
            reason = r["split_audit"].get("cleanup") or "hedef değişikliği"
        changes.append({"case_id": r["case_id"], "status": [before["status"], after["status"]],
                        "qna_ids": [before["qna_ids"], after["qna_ids"]], "refs": r["expected_qna_refs"], "reason": reason})
    return {"previous": "outputs/gold-v2-sources-20260917/gold-v2-draft.json", "changed_case_count": len(changes),
            "status_transitions": dict(sorted(Counter("→".join(c["status"]) for c in changes).items())), "changes": changes}


# --------------------------------------------------------------------------
# Çıktı
# --------------------------------------------------------------------------


def git_head(path: Path) -> dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=path,
                           capture_output=True, text=True).stdout.strip()
    return {"commit": head, "tracked_changes": bool(dirty)}


def render_markdown(report: dict[str, Any], records: list[dict[str, Any]]) -> str:
    a = report["audit"]
    temporal = [r for r in records if r["temporal"] and r["status"] == "READY"]
    lines = [
        f"# Gold v2 final — {'FREEZE PASS' if report['freeze']['frozen'] else 'FREEZE BEKLİYOR'}", "",
        f"Toplam {a['total']} vaka · " + " · ".join(f"{k}: {v}" for k, v in a["counts"].items()), "",
        f"- Beklenen dağılımla uyum (490/25/1): **{a['counts_match_expectation']}**",
        f"- Blokaj: **{len(a['blockers'])}** · uyarı: {len(a['warnings'])}",
        f"- Post-migration baseline: `{report['baseline']['migration_report']}`",
        f"- Migration sonrası taşınan alias vakaları doğrulandı: {a['moved_alias_cases_verified']}",
        f"- Multi-intent (READY): {len(a['multi_intent_cases'])} · split etiketi temizlenen: {len(a['split_label_cleanups'])}",
        f"- Temporal/guard'lı READY vaka: {len(temporal)}",
        f"- İnsan kararıyla alias sahibinden farklı hedef (bilgi): {a['alias_mapping']['human_override_count']}", "",
        "## Blokajlar", "", *([f"- `{b['check']}`: {json.dumps({k: v for k, v in b.items() if k != 'check'}, ensure_ascii=False)}"
                               for b in a["blockers"]] or ["- Yok"]), "",
        "## Açık noktalar", "", *([f"- {item}" for item in report["open_points"]] or ["- Yok"]), "",
        "## Temporal / guard'lı READY vakalar", "", "| Vaka | QnA | Ref | Guard | Tür | as_of |", "|---|---|---|---|---|---|",
        *[f"| {r['case_id']} | {r['expected_qna_ids']} | {r['expected_qna_refs']} | {', '.join(r['guard_refs']) or '—'} | "
          f"{r['temporal_meta']['temporal_kind']} | {r['as_of_date']} |" for r in temporal], "",
    ]
    return "\n".join(lines)


def build(output_dir: Path) -> dict[str, Any]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    migration_report_path = discover_migration_report(ROOT)
    baseline = load_baseline(migration_report_path)
    if not baseline["report"]["plan"]["blake2b"] == file_digest(PLAN):
        raise ValueError("Migration raporundaki plan digest'i commit'lenmiş planla eşleşmiyor")

    reconstructed = reconstruct_pre_catalog(baseline, plan)
    source_catalog = load_catalog(SOURCES / "qna-catalog-20260915.json")
    if reconstructed != source_catalog:
        raise ValueError("Yeniden kurulan migration öncesi katalog kaynak katalogla eşleşmiyor")

    review_cases, _ = review_pipeline(SOURCES)
    decisions = v31_decisions(SOURCES / "AUZEF-QnA-dogrulanmis-konsolidasyon-v3.1-final-20260917.xlsx", plan, baseline)
    resolutions = {int(r["case_no"]): r for r in json.loads((SOURCES / "review-resolutions-v1.json").read_text(encoding="utf-8"))["resolutions"]}
    tables = workbook_tables(SOURCES / "AUZEF-QnA-dogrulanmis-konsolidasyon-v3.1-final-20260917.xlsx")
    controls_by_guard = {f"GUARD-{text(r['Hedef'])}": r for r in tables["Dönemsel İçerik"]}
    op_refs = {qid: ref for ref, qid in baseline["created"].items()}
    op_refs.update({int(m["qna_id"]): m["operation_ref"] for m in plan["qna_mutations"] if m["action"] == "update_qna"})
    updated_ids = {int(u["qna_id"]) for u in baseline["report"]["units"] if u["kind"] == "update"}

    records = build_cases(review_cases, decisions, baseline, resolutions, controls_by_guard, op_refs)
    audit_result = audit(records, decisions, baseline, plan, updated_ids)
    open_points = []
    if not audit_result["counts_match_expectation"]:
        open_points.append(f"Dağılım beklentiden farklı: {audit_result['counts']}")
    ambiguous = [c for c in audit_result["split_label_cleanups"] if c["case"] == 480]
    if ambiguous:
        open_points.append("Vaka 480: iki ayrı konu (İstanbulkart / YÖK kaydı) için insan kararı her iki gruba da "
                           "'125, 344' girmiş; tek niyet + iki kabul edilen QnA olarak işlendi, teyit önerilir.")
    frozen = not audit_result["blockers"] and audit_result["counts_match_expectation"]

    report = {
        "dataset_version": DATASET_VERSION,
        "baseline": {
            "migration_report": str(migration_report_path.relative_to(ROOT)),
            "migration_report_sha256": sha256(migration_report_path),
            "files_sha256": {n: sha256(baseline["dir"] / n) for n in sorted(baseline["report"]["baseline_files"])},
            "post_counts": baseline["report"]["counts"]["post"],
            "created_qna_ids": baseline["created"],
        },
        "pre_migration_catalog_reconstruction": "equal_to_source_catalog",
        "review_pipeline": "reproduces gold-v2-draft source decisions (516 cases)",
        "audit": audit_result,
        "open_points": open_points,
        "freeze": {"frozen": frozen, "requires": ["no_blockers", "counts_match_expectation"]},
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "gold-v2-all.jsonl": jsonl(records),
        "gold-v2-ready.jsonl": jsonl([r for r in records if r["status"] == "READY"]),
        "gold-v2-context-required.jsonl": jsonl([r for r in records if r["status"] == "CONTEXT_REQUIRED"]),
        "gold-v2-pending.jsonl": jsonl([r for r in records if r["status"] == "PENDING_CONTENT"]),
        "gold-v2-report.json": dump(report),
        "gold-v2-diff-from-previous.json": dump(diff_from_previous(records)),
        "GOLD-V2-REPORT.md": render_markdown(report, records),
    }
    for name, content in files.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    return {"report": report, "records": records}


def write_manifest(output_dir: Path, report: dict[str, Any], created_at: str, chatbot_root: Path | None) -> dict[str, Any]:
    sources = sorted(p for p in SOURCES.iterdir() if p.is_file())
    migration = json.loads((ROOT / report["baseline"]["migration_report"]).read_text(encoding="utf-8"))
    chatbot = git_head(chatbot_root) if chatbot_root else None
    manifest = {
        "dataset_version": DATASET_VERSION,
        "created_at": created_at,
        "frozen": report["freeze"]["frozen"],
        "source_git": git_head(ROOT),
        "chatbot_commit": {"from_migration_report": migration["git"]["chatbot_commit"],
                           "chatbot_repo_head": chatbot["commit"] if chatbot else None},
        "migration_report_sha256": report["baseline"]["migration_report_sha256"],
        "canonical_baseline_sha256": report["baseline"]["files_sha256"]["qna-canonical.json"],
        "alias_baseline_sha256": report["baseline"]["files_sha256"]["qna-aliases.json"],
        "routing_guard_baseline_sha256": report["baseline"]["files_sha256"]["qna-routing-guards.json"],
        "plan_sha256": sha256(PLAN),
        "counts": report["audit"]["counts"],
        "total": report["audit"]["total"],
        "source_files_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in sources},
        "builder_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in (
            Path(__file__).resolve(), ROOT / "scripts" / "build_gold_v2.py", ROOT / "scripts" / "dry_run_kb_consolidation.py")},
        "outputs_sha256": {name: sha256(output_dir / name) for name in OUTPUT_FILES},
    }
    (output_dir / "gold-v2-manifest.json").write_text(dump(manifest), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "gold-v2-final-20260917")
    parser.add_argument("--created-at", required=True, help="Manifest oluşturma zamanı (ISO-8601)")
    parser.add_argument("--chatbot-root", type=Path, help="Chatbot repo HEAD'ini manifest'e eklemek için")
    parser.add_argument("--verify-rebuild", action="store_true", help="İkinci bir geçici build ile bayt eşitliğini doğrula")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build(args.output_dir)
    rebuild = None
    if args.verify_rebuild:
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp))
            rebuild = {name: sha256(Path(tmp) / name) == sha256(args.output_dir / name) for name in OUTPUT_FILES}
    manifest = write_manifest(args.output_dir, result["report"], args.created_at, args.chatbot_root)
    audit_result = result["report"]["audit"]
    print(json.dumps({
        "frozen": manifest["frozen"], "counts": audit_result["counts"], "total": audit_result["total"],
        "blockers": len(audit_result["blockers"]), "warnings": len(audit_result["warnings"]),
        "rebuild_byte_identical": rebuild, "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["frozen"] and (rebuild is None or all(rebuild.values())) else 2


if __name__ == "__main__":
    raise SystemExit(main())
