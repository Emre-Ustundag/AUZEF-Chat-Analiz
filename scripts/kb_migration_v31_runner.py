"""AUZEF KB konsolidasyon v3.1-final migration runner'ı.

Bu dosya iki yerde kullanılır:
- Host tarafında saf fonksiyonlar (birim kurma, önkoşul, bütünlük, rollback
  farkı) test edilir; bu fonksiyonlar backend import etmez.
- ``scripts/kb_migration_v31.py`` bu kaynağı ``python -c`` ile backend
  runtime'ına gönderir; stdin'den JSON payload okur, stdout'a NDJSON yazar.

Modlar: snapshot, dry-run, apply, rollback, verify, index-sync.
Tüm QnA tabloları DB-ADMIN'dedir; yazma yalnız ``admin_engine`` üzerinden yapılır.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime
from typing import Any

PLAN_SCHEMA = "auzef-kb-mutation-plan-v3.1-final"
PLAN_WORKBOOK_DIGEST = "cb244512805fa1b4500d58226b1c235f99a4b20a3271eee47ec626bb9edcedbe"
ACTOR = "kb-migration-v3.1"
GUARD_REQUIRED_STATUS = "READY_WITH_GUARD"
SELECTOR_ONLY_ALIAS_POLICY = "selector_only_no_permanent_bypass"
UNIT_ORDER = ("guard_only", "update", "alias_move", "create", "promotion")


class PlanError(ValueError):
    """Plan yapısı migration sözleşmesine uymuyor."""


class UnitAbort(RuntimeError):
    """Birim transaction'ı içinde canlı durum beklenenden farklı."""


# --------------------------------------------------------------------------
# Saf yardımcılar
# --------------------------------------------------------------------------


def ref_of(item: dict[str, Any]) -> str:
    return item.get("operation_ref") or item.get("temp_ref")


def guard_target_ref(guard: dict[str, Any]) -> str:
    return guard["target"].get("operation_ref") or guard["target"].get("temp_ref")


def normalize_text(value: str) -> str:
    return value.strip().rstrip("?").strip().casefold()


def jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        {key: snapshot[key] for key in ("qna", "aliases", "guards")},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=32).hexdigest()


def validate_plan_header(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise PlanError(f"Beklenmeyen plan şeması: {plan.get('schema_version')}")
    if plan.get("source_workbook_blake2b") != PLAN_WORKBOOK_DIGEST:
        raise PlanError("Plan, onaylı v3.1-final workbook digest'inden üretilmemiş")


def build_units(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan işlemlerini transaction birimlerine ayırır.

    - Bloklu içerik güncellemesi (EX-319) yalnız guard birimi olur.
    - Mevcut QnA güncellemesi: guard (varsa) → update → o QnA'ya gelen alias'lar.
    - Yeni QnA: create → gerçek id → guard (varsa) → alias'lar.
    - NEW-11: kaynak alias silme + create + guard tek birim.
    - İçerik değişmeyen hedeflere alias taşımaları hedef başına bir birim.
    """
    validate_plan_header(plan)
    guards: dict[str, dict[str, Any]] = {}
    for guard in plan["routing_guard_mutations"]:
        target = guard_target_ref(guard)
        if target in guards:
            raise PlanError(f"Aynı hedefe birden fazla guard: {target}")
        if guard.get("exact_bypass") != "disabled":
            raise PlanError(f"{guard['guard_ref']} exact bypass kapalı değil")
        guards[target] = guard

    aliases_by_target: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for alias in plan["alias_mutations"]:
        target = alias["target"]
        key = ("qna", int(target["qna_id"])) if target.get("qna_id") is not None else ("temp", target["temp_ref"])
        aliases_by_target.setdefault(key, []).append(alias)

    used_guards: set[str] = set()
    units: list[dict[str, Any]] = []

    def take_guard(ref: str, required: bool) -> dict[str, Any] | None:
        guard = guards.get(ref)
        if required and guard is None:
            raise PlanError(f"{ref} guard gerektiriyor ama planda guard yok")
        if guard is not None:
            used_guards.add(ref)
        return guard

    for mutation in plan["qna_mutations"]:
        ref = ref_of(mutation)
        needs_guard = mutation["status"] == GUARD_REQUIRED_STATUS or bool(mutation.get("content_control_ref"))
        if mutation["status"].startswith("BLOCKED"):
            if mutation["action"] != "update_qna":
                raise PlanError(f"Bloklu create desteklenmiyor: {ref}")
            if ("qna", int(mutation["qna_id"])) in aliases_by_target:
                raise PlanError(f"Bloklu {ref} hedefine alias taşınamaz")
            guard = take_guard(ref, required=False)
            if guard is not None:
                units.append({
                    "unit": f"guard_only:{ref}",
                    "kind": "guard_only",
                    "ref": ref,
                    "qna_id": int(mutation["qna_id"]),
                    "guard": guard,
                    "skipped_content_update": {"ref": ref, "status": mutation["status"]},
                    "aliases": [],
                })
            continue
        if mutation["status"] not in {"READY", GUARD_REQUIRED_STATUS}:
            raise PlanError(f"Bilinmeyen işlem durumu: {ref}={mutation['status']}")
        if mutation["action"] == "update_qna":
            units.append({
                "unit": f"update:{ref}",
                "kind": "update",
                "ref": ref,
                "qna_id": int(mutation["qna_id"]),
                "mutation": mutation,
                "guard": take_guard(ref, needs_guard),
                "aliases": aliases_by_target.pop(("qna", int(mutation["qna_id"])), []),
            })
        elif mutation["action"] == "create_qna":
            units.append({
                "unit": f"create:{ref}",
                "kind": "create",
                "ref": ref,
                "mutation": mutation,
                "guard": take_guard(ref, needs_guard),
                "aliases": aliases_by_target.pop(("temp", ref), []),
            })
        else:
            raise PlanError(f"Bilinmeyen QnA işlemi: {mutation['action']}")

    for promotion in plan["atomic_promotions"]:
        if promotion["action"] != "promote_alias_to_new_qna_canonical" or not promotion.get("atomic"):
            raise PlanError(f"Desteklenmeyen promotion: {ref_of(promotion)}")
        ref = ref_of(promotion)
        needs_guard = promotion["status"] == GUARD_REQUIRED_STATUS or bool(promotion.get("content_control_ref"))
        units.append({
            "unit": f"promotion:{ref}",
            "kind": "promotion",
            "ref": ref,
            "promotion": promotion,
            "guard": take_guard(ref, needs_guard),
            "aliases": aliases_by_target.pop(("temp", ref), []),
        })

    for key in sorted(aliases_by_target, key=str):
        kind, target = key
        if kind == "temp":
            raise PlanError(f"Alias hedefi planda oluşturulmuyor: {target}")
        units.append({
            "unit": f"alias_move:{target}",
            "kind": "alias_move",
            "ref": f"QNA-{target}",
            "qna_id": target,
            "guard": None,
            "aliases": aliases_by_target[key],
        })

    unused = set(guards) - used_guards
    if unused:
        raise PlanError(f"Hiçbir birime bağlanmayan guard: {sorted(unused)}")

    for unit in units:
        for alias in unit["aliases"]:
            if alias["exact_alias_policy"] == SELECTOR_ONLY_ALIAS_POLICY and unit["guard"] is None:
                raise PlanError(f"Vaka {alias['case_no']} selector-only ama hedefinde guard yok")

    order = {kind: index for index, kind in enumerate(UNIT_ORDER)}
    return sorted(units, key=lambda unit: order[unit["kind"]])


class SnapshotIndex:
    def __init__(self, snapshot: dict[str, Any]):
        self.qna_by_id = {int(row["id"]): row for row in snapshot["qna"]}
        self.active = {qid: row for qid, row in self.qna_by_id.items() if row["status"] == 1}
        self.active_by_question: dict[str, list[int]] = {}
        for qid, row in self.active.items():
            self.active_by_question.setdefault(row["question_text"], []).append(qid)
        self.alias_by_id = {int(row["id"]): row for row in snapshot["aliases"]}
        self.active_alias_texts: set[str] = {
            row["query_text"] for row in snapshot["aliases"] if int(row["qna_id"]) in self.active
        }
        self.guards = {int(row["qna_id"]): row for row in snapshot["guards"]}

    def alias_rows(self, qna_id: int, text_value: str) -> list[dict[str, Any]]:
        return [
            row for row in self.alias_by_id.values()
            if int(row["qna_id"]) == qna_id and row["query_text"] == text_value
        ]


def resolve_plan(plan: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Önkoşulları snapshot üzerinde kontrol eder ve metin referanslarını id'ye çözer.

    Kaynaklar yalnız burada, yazmadan önce bir kez çözülür; birimler çalışırken
    soru metniyle yeniden arama yapılmaz.
    """
    units = build_units(plan)
    index = SnapshotIndex(snapshot)
    errors: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {"alias_sources": {}, "promotion_sources": {}}

    expected_active = int(plan["preconditions"]["expected_active_qna_count"])
    if len(index.active) != expected_active:
        errors.append({"code": "ACTIVE_QNA_COUNT", "expected": expected_active, "actual": len(index.active)})

    new_questions: dict[str, str] = {}
    for unit in units:
        if unit["kind"] in {"update", "guard_only", "alias_move"}:
            current = index.active.get(unit["qna_id"])
            if current is None:
                errors.append({"code": "TARGET_QNA_NOT_ACTIVE", "unit": unit["unit"]})
                continue
        if unit["kind"] == "update":
            expected = unit["mutation"]["expected_current"]
            if current["question_text"] != expected["question"] or current["answer_text"] != expected["answer"]:
                errors.append({"code": "QNA_CURRENT_MISMATCH", "unit": unit["unit"]})
        if unit["kind"] == "guard_only":
            blocked = next(m for m in plan["qna_mutations"] if ref_of(m) == unit["ref"])
            expected = blocked["expected_current"]
            if current["question_text"] != expected["question"] or current["answer_text"] != expected["answer"]:
                errors.append({"code": "BLOCKED_QNA_CURRENT_MISMATCH", "unit": unit["unit"]})
        if unit["kind"] in {"create", "promotion"}:
            question = unit["mutation"]["set"]["question"] if unit["kind"] == "create" else unit["promotion"]["target"]["question"]
            if question in new_questions:
                errors.append({"code": "NEW_CANONICAL_DUPLICATE_IN_PLAN", "unit": unit["unit"]})
            new_questions[question] = unit["ref"]
            if question in index.active_by_question:
                errors.append({"code": "NEW_CANONICAL_COLLISION", "unit": unit["unit"]})
            if question in index.active_alias_texts:
                errors.append({"code": "NEW_CANONICAL_ALIAS_COLLISION", "unit": unit["unit"]})
        if unit["kind"] == "promotion":
            source = unit["promotion"]["expected_source"]
            owners = index.active_by_question.get(source["canonical_question"], [])
            rows = index.alias_rows(owners[0], source["alias"]) if len(owners) == 1 else []
            if len(owners) != 1 or len(rows) != 1:
                errors.append({"code": "PROMOTION_SOURCE_MISMATCH", "unit": unit["unit"], "owners": len(owners), "rows": len(rows)})
            else:
                resolved["promotion_sources"][unit["ref"]] = {
                    "qna_id": owners[0],
                    "alias_id": int(rows[0]["id"]),
                    "alias_row": rows[0],
                }
        if unit["guard"] is not None:
            existing_target = unit.get("qna_id")
            if existing_target is not None and existing_target in index.guards:
                if index.guards[existing_target]["guard_ref"] != unit["guard"]["guard_ref"]:
                    errors.append({"code": "FOREIGN_GUARD_ON_TARGET", "unit": unit["unit"]})

        target_question = None
        if unit["kind"] in {"update", "alias_move"}:
            target_question = index.active[unit["qna_id"]]["question_text"] if unit["qna_id"] in index.active else None
        elif unit["kind"] == "create":
            target_question = unit["mutation"]["set"]["question"]
        elif unit["kind"] == "promotion":
            target_question = unit["promotion"]["target"]["question"]
        for alias in unit["aliases"]:
            case = int(alias["case_no"])
            if alias["target"]["canonical_question"] != target_question:
                errors.append({"code": "ALIAS_TARGET_MISMATCH", "case": case})
            owners = index.active_by_question.get(alias["expected_source"]["canonical_question"], [])
            if len(owners) != 1:
                errors.append({"code": "ALIAS_SOURCE_QNA_NOT_UNIQUE", "case": case, "owners": len(owners)})
                continue
            source_id = owners[0]
            rows = index.alias_rows(source_id, alias["alias"])
            if len(rows) != 1:
                errors.append({"code": "ALIAS_SOURCE_ROW_NOT_UNIQUE", "case": case, "rows": len(rows)})
                continue
            if unit.get("qna_id") is not None and index.alias_rows(unit["qna_id"], alias["alias"]):
                errors.append({"code": "ALIAS_ALREADY_ON_TARGET", "case": case})
            if unit.get("qna_id") == source_id:
                errors.append({"code": "ALIAS_SOURCE_EQUALS_TARGET", "case": case})
            resolved["alias_sources"][str(case)] = {"source_qna_id": source_id, "alias_id": int(rows[0]["id"])}

    alias_ids = [item["alias_id"] for item in resolved["alias_sources"].values()]
    alias_ids += [item["alias_id"] for item in resolved["promotion_sources"].values()]
    if len(alias_ids) != len(set(alias_ids)):
        errors.append({"code": "ALIAS_ROW_USED_TWICE"})
    resolved["errors"] = errors
    return resolved, units


def scope_ids(units: list[dict[str, Any]], resolved: dict[str, Any]) -> set[int]:
    """Migration'ın dokunabileceği mevcut QnA id'leri (yeni oluşturulanlar hariç)."""
    ids: set[int] = set()
    for unit in units:
        if unit.get("qna_id") is not None:
            ids.add(int(unit["qna_id"]))
    ids.update(int(item["source_qna_id"]) for item in resolved["alias_sources"].values())
    ids.update(int(item["qna_id"]) for item in resolved["promotion_sources"].values())
    return ids


def created_rows(before: dict[str, Any], after: dict[str, Any]) -> dict[int, dict[str, Any]]:
    before_ids = {int(row["id"]) for row in before["qna"]}
    return {int(row["id"]): row for row in after["qna"] if int(row["id"]) not in before_ids}


def _multi_owner_alias_texts(snapshot: dict[str, Any]) -> set[str]:
    index = SnapshotIndex(snapshot)
    owners: dict[str, set[int]] = {}
    for row in snapshot["aliases"]:
        if int(row["qna_id"]) in index.active:
            owners.setdefault(row["query_text"], set()).add(int(row["qna_id"]))
    return {text_value for text_value, qna_ids in owners.items() if len(qna_ids) > 1}


def _duplicate_canonicals(snapshot: dict[str, Any]) -> set[str]:
    index = SnapshotIndex(snapshot)
    return {question for question, ids in index.active_by_question.items() if len(ids) > 1}


def _self_canonical_aliases(snapshot: dict[str, Any]) -> set[int]:
    index = SnapshotIndex(snapshot)
    return {
        int(row["id"]) for row in snapshot["aliases"]
        if int(row["qna_id"]) in index.active
        and normalize_text(row["query_text"]) == normalize_text(index.active[int(row["qna_id"])]["question_text"])
    }


def integrity_checks(
    plan: dict[str, Any],
    units: list[dict[str, Any]],
    resolved: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Tam apply sonrası DB durumunu plana ve apply öncesi snapshot'a karşı doğrular."""
    b, a = SnapshotIndex(before), SnapshotIndex(after)
    failures: list[dict[str, Any]] = []
    passes: list[str] = []

    def check(name: str, ok: bool, **details: Any) -> None:
        if ok:
            passes.append(name)
        else:
            failures.append({"check": name, **details})

    creates = [u for u in units if u["kind"] in {"create", "promotion"}]
    expected_active = len(b.active) + len(creates)
    check("active_qna_count", len(a.active) == expected_active, expected=expected_active, actual=len(a.active))
    plan_expected = plan["stats"].get("expected_active_qna_after_full_apply")
    check("active_qna_count_matches_plan", plan_expected is None or len(a.active) == plan_expected,
          expected=plan_expected, actual=len(a.active))
    expected_aliases = len(before["aliases"]) - len(resolved["promotion_sources"])
    check("alias_row_count", len(after["aliases"]) == expected_aliases, expected=expected_aliases, actual=len(after["aliases"]))

    new_rows = created_rows(before, after)
    check("created_rows_are_migration_owned", all(row["updated_by"] == ACTOR for row in new_rows.values()),
          foreign=[qid for qid, row in new_rows.items() if row["updated_by"] != ACTOR])
    created_by_ref: dict[str, int] = {}
    for unit in creates:
        question = unit["mutation"]["set"]["question"] if unit["kind"] == "create" else unit["promotion"]["target"]["question"]
        answer = unit["mutation"]["set"]["answer"] if unit["kind"] == "create" else unit["promotion"]["target"]["answer"]
        ids = [qid for qid in a.active_by_question.get(question, []) if qid in new_rows]
        ok = len(ids) == 1 and len(a.active_by_question.get(question, [])) == 1
        check(f"created:{unit['ref']}", ok and a.active[ids[0]]["answer_text"] == answer, ids=ids)
        if ok:
            created_by_ref[unit["ref"]] = ids[0]
    check("created_row_count", len(new_rows) == len(creates), expected=len(creates), actual=len(new_rows))

    for unit in units:
        if unit["kind"] == "update":
            row = a.active.get(unit["qna_id"])
            wanted = unit["mutation"]["set"]
            check(f"updated:{unit['ref']}", row is not None and row["question_text"] == wanted["question"]
                  and row["answer_text"] == wanted["answer"] and row["updated_by"] == ACTOR)
        if unit["kind"] == "guard_only":
            check(f"content_unchanged:{unit['ref']}",
                  b.qna_by_id.get(unit["qna_id"]) == a.qna_by_id.get(unit["qna_id"]))

    moved_alias_ids: set[int] = set()
    for unit in units:
        target_id = unit.get("qna_id") if unit["kind"] in {"update", "alias_move"} else created_by_ref.get(unit["ref"])
        for alias in unit["aliases"]:
            source = resolved["alias_sources"][str(int(alias["case_no"]))]
            moved_alias_ids.add(source["alias_id"])
            row = a.alias_by_id.get(source["alias_id"])
            check(f"alias_moved:{alias['case_no']}", row is not None and target_id is not None
                  and int(row["qna_id"]) == target_id and row["query_text"] == alias["alias"])

    deleted_alias_ids = {item["alias_id"] for item in resolved["promotion_sources"].values()}
    for ref, source in resolved["promotion_sources"].items():
        new_id = created_by_ref.get(ref)
        promoted_question = next(u for u in creates if u["ref"] == ref)["promotion"]["target"]["question"]
        duplicate = [
            row for row in after["aliases"]
            if new_id is not None and int(row["qna_id"]) == new_id
            and normalize_text(row["query_text"]) == normalize_text(promoted_question)
        ]
        check(f"promotion_alias_removed:{ref}", source["alias_id"] not in a.alias_by_id)
        check(f"promotion_no_duplicate_canonical_alias:{ref}", new_id is not None and not duplicate)

    plan_guards = {g["guard_ref"]: g for g in plan["routing_guard_mutations"]}
    check("guard_row_count", len(after["guards"]) == len(before["guards"]) + len(
        [g for g in plan["routing_guard_mutations"] if (g["target"].get("qna_id") not in b.guards)]),
        actual=len(after["guards"]))
    guards_by_ref = {row["guard_ref"]: row for row in after["guards"]}
    for guard_ref, guard in plan_guards.items():
        row = guards_by_ref.get(guard_ref)
        target = guard["target"].get("qna_id") or created_by_ref.get(guard["target"].get("temp_ref"))
        ok = (
            row is not None
            and target is not None
            and int(row["qna_id"]) == int(target)
            and row["exact_bypass_enabled"] == 0
            and row["selector_mode"] == guard["selector_mode"]
            and row["content_mode"] == guard["content_mode"]
            and row["valid_from"] == guard["valid_from"]
            and row["valid_until"] == guard["valid_until"]
            and row["on_expiry"] == guard["on_expiry"]
            and row["source_of_truth"] == guard["source_of_truth"]
        )
        check(f"guard:{guard_ref}", ok)

    guarded_ids = {int(row["qna_id"]) for row in after["guards"]}
    for unit in units:
        if unit["guard"] is None:
            continue
        target_id = unit.get("qna_id") if unit.get("qna_id") is not None else created_by_ref.get(unit["ref"])
        check(f"guard_precedes_activation:{unit['ref']}", target_id in guarded_ids)
        for alias in unit["aliases"]:
            if alias["exact_alias_policy"] == SELECTOR_ONLY_ALIAS_POLICY:
                check(f"selector_only_alias_guarded:{alias['case_no']}", target_id in guarded_ids)

    check("no_new_multi_owner_alias", _multi_owner_alias_texts(after) <= _multi_owner_alias_texts(before),
          new=sorted(_multi_owner_alias_texts(after) - _multi_owner_alias_texts(before)))
    check("no_new_duplicate_canonical", _duplicate_canonicals(after) <= _duplicate_canonicals(before),
          new=sorted(_duplicate_canonicals(after) - _duplicate_canonicals(before)))
    check("no_new_self_canonical_alias", _self_canonical_aliases(after) <= _self_canonical_aliases(before),
          new=sorted(_self_canonical_aliases(after) - _self_canonical_aliases(before)))

    touched = scope_ids(units, resolved)
    content_touched = {u["qna_id"] for u in units if u["kind"] == "update"}
    untouched_changed = [
        qid for qid, row in b.qna_by_id.items()
        if qid not in content_touched and a.qna_by_id.get(qid) != row
    ]
    check("untouched_qna_rows_identical", not untouched_changed, changed=untouched_changed[:20])
    alias_changed = [
        aid for aid, row in b.alias_by_id.items()
        if aid not in moved_alias_ids | deleted_alias_ids and a.alias_by_id.get(aid) != row
    ]
    check("untouched_alias_rows_identical", not alias_changed, changed=alias_changed[:20])
    new_alias_rows = sorted(set(a.alias_by_id) - set(b.alias_by_id))
    check("no_alias_rows_inserted", not new_alias_rows, rows=new_alias_rows[:20])
    foreign_guards = [
        qid for qid, row in b.guards.items() if a.guards.get(qid) != row and qid not in touched
    ]
    check("untouched_guards_identical", not foreign_guards, changed=foreign_guards)

    return {
        "status": "PASS" if not failures else "FAIL",
        "passes": len(passes),
        "failures": failures,
        "created_ids": created_by_ref,
    }


def restore_actions(
    units: list[dict[str, Any]],
    resolved: dict[str, Any],
    before: dict[str, Any],
    live: dict[str, Any],
) -> dict[str, Any]:
    """Canlı durumu apply öncesi snapshot'a döndürecek işlemleri hesaplar.

    Journal'a değil snapshot farkına dayanır: commit edilip journal'a yazılamamış
    bir birim de geri alınır. Migration kapsamı dışındaki her fark rollback'i
    durdurur (başka biri de yazmış demektir; o durumda pg_restore gerekir).
    """
    b, l = SnapshotIndex(before), SnapshotIndex(live)
    scope = scope_ids(units, resolved)
    moved = {item["alias_id"] for item in resolved["alias_sources"].values()}
    deleted = {item["alias_id"] for item in resolved["promotion_sources"].values()}
    new_rows = created_rows(before, live)
    foreign: list[dict[str, Any]] = []

    delete_qna = []
    for qid, row in new_rows.items():
        if row["updated_by"] != ACTOR:
            foreign.append({"table": "qna", "id": qid, "reason": "migration dışı yeni kayıt"})
        else:
            delete_qna.append(qid)

    # İçerik yalnız migration'ın güncellediği ve hâlâ migration'ın yazdığı
    # hâlde duran satırlarda geri yüklenir; sonradan yapılan admin düzenlemesi ezilmez.
    written = {u["qna_id"]: u["mutation"]["set"] for u in units if u["kind"] == "update"}
    restore_qna = []
    for qid, row in b.qna_by_id.items():
        current = l.qna_by_id.get(qid)
        if current == row:
            continue
        wanted = written.get(qid)
        if (
            wanted is None
            or current is None
            or current["question_text"] != wanted["question"]
            or current["answer_text"] != wanted["answer"]
            or current["updated_by"] != ACTOR
            or current["status"] != row["status"]
        ):
            foreign.append({"table": "qna", "id": qid, "reason": "migration dışı içerik değişikliği veya silinmiş kayıt"})
        else:
            restore_qna.append(row)

    restore_alias_owner, reinsert_alias = [], []
    for aid, row in b.alias_by_id.items():
        current = l.alias_by_id.get(aid)
        if current == row:
            continue
        if current is None and aid in deleted:
            reinsert_alias.append(row)
        elif current is not None and aid in moved and {k: v for k, v in current.items() if k != "qna_id"} == {
            k: v for k, v in row.items() if k != "qna_id"
        }:
            restore_alias_owner.append(row)
        else:
            foreign.append({"table": "qna_queries", "id": aid, "reason": "beklenmeyen alias farkı"})
    new_ids = set(delete_qna)
    # Migration hiç alias satırı eklemez; yeni satır başka bir yazarın işidir.
    for aid in sorted(set(l.alias_by_id) - set(b.alias_by_id)):
        foreign.append({"table": "qna_queries", "id": aid, "reason": "migration dışı yeni alias"})

    guard_restore, guard_delete = [], []
    guard_scope = {int(u["qna_id"]) for u in units if u["guard"] is not None and u.get("qna_id") is not None}
    for qid in sorted(set(b.guards) | set(l.guards)):
        was, now = b.guards.get(qid), l.guards.get(qid)
        if was == now:
            continue
        if qid not in guard_scope and qid not in new_ids:
            foreign.append({"table": "qna_routing_guards", "id": qid, "reason": "kapsam dışı guard farkı"})
        elif was is None:
            guard_delete.append(qid)
        else:
            guard_restore.append(was)

    reindex = sorted(scope | new_ids)
    return {
        "foreign_changes": foreign,
        "delete_qna": sorted(delete_qna),
        "restore_qna": restore_qna,
        "restore_alias_owner": restore_alias_owner,
        "reinsert_alias": reinsert_alias,
        "guard_restore": guard_restore,
        "guard_delete": sorted(guard_delete),
        "reindex_ids": reindex,
        "noop": not any([delete_qna, restore_qna, restore_alias_owner, reinsert_alias, guard_restore, guard_delete]),
    }


def index_targets(units: list[dict[str, Any]], resolved: dict[str, Any], created_ids: dict[str, int]) -> list[int]:
    """İçeriği veya alias listesi değişen, yeniden indekslenecek QnA id'leri."""
    ids = {int(u["qna_id"]) for u in units if u["kind"] in {"update", "alias_move"}}
    ids.update(int(item["source_qna_id"]) for item in resolved["alias_sources"].values())
    ids.update(int(item["qna_id"]) for item in resolved["promotion_sources"].values())
    ids.update(int(qid) for qid in created_ids.values())
    return sorted(ids)


# --------------------------------------------------------------------------
# Backend runtime tarafı
# --------------------------------------------------------------------------


def emit(message_type: str, **payload: Any) -> None:
    sys.stdout.write(json.dumps({"type": message_type, **payload}, ensure_ascii=False, default=jsonable) + "\n")
    sys.stdout.flush()


def read_snapshot(session) -> dict[str, Any]:
    from sqlalchemy import text

    def rows(sql: str) -> list[dict[str, Any]]:
        return [
            {key: jsonable(value) for key, value in row.items()}
            for row in session.execute(text(sql)).mappings().all()
        ]

    snapshot = {
        "qna": rows(
            "SELECT id, question_text, answer_text, status, updated_by, created_at, updated_at "
            "FROM qna ORDER BY id"
        ),
        "aliases": rows("SELECT id, qna_id, query_text, query_type, created_at FROM qna_queries ORDER BY id"),
        "guards": rows(
            "SELECT qna_id, guard_ref, exact_bypass_enabled, selector_mode, content_mode, valid_from, "
            "valid_until, on_expiry, source_of_truth, created_at, updated_at FROM qna_routing_guards ORDER BY qna_id"
        ),
    }
    snapshot["digest"] = snapshot_digest(snapshot)
    return snapshot


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _write_guard(session, qna_id: int, guard: dict[str, Any]) -> None:
    from services.routing_guards import RoutingGuardPolicy, upsert_routing_guard

    upsert_routing_guard(
        session,
        qna_id=qna_id,
        guard_ref=guard["guard_ref"],
        exact_bypass_enabled=guard["exact_bypass"] != "disabled",
        selector_mode=guard["selector_mode"],
        content_mode=guard["content_mode"],
        valid_from=_parse_date(guard["valid_from"]),
        valid_until=_parse_date(guard["valid_until"]),
        on_expiry=guard["on_expiry"],
        source_of_truth=guard["source_of_truth"],
    )
    decision = RoutingGuardPolicy.load(session).decision(qna_id)
    if decision.fallback_allowed or not decision.selector_allowed:
        raise UnitAbort(f"{guard['guard_ref']} yazıldı ama runtime kararı beklenen değil: {decision}")


def _insert_qna(session, question: str, answer: str) -> int:
    from sqlalchemy import text

    # status açıkça 1: ham INSERT'te NULL status kaydı arama view'ından düşürür.
    return int(session.execute(
        text(
            "INSERT INTO qna (question_text, answer_text, status, updated_by) "
            "VALUES (:q, :a, 1, :by) RETURNING id"
        ),
        {"q": question, "a": answer, "by": ACTOR},
    ).scalar_one())


def _move_aliases(session, unit: dict[str, Any], resolved: dict[str, Any], target_id: int) -> list[dict[str, Any]]:
    from sqlalchemy import text

    moves = []
    for alias in unit["aliases"]:
        source = resolved["alias_sources"][str(int(alias["case_no"]))]
        result = session.execute(
            text(
                "UPDATE qna_queries SET qna_id = :target WHERE id = :id AND qna_id = :source AND query_text = :alias"
            ),
            {"target": target_id, "id": source["alias_id"], "source": source["source_qna_id"], "alias": alias["alias"]},
        )
        if result.rowcount != 1:
            raise UnitAbort(f"Vaka {alias['case_no']} alias satırı beklenen kaynakta değil")
        moves.append({"case": int(alias["case_no"]), "alias_id": source["alias_id"],
                      "from": source["source_qna_id"], "to": target_id})
    return moves


def run_unit(session, unit: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import text

    record: dict[str, Any] = {"unit": unit["unit"], "kind": unit["kind"], "ref": unit["ref"]}
    kind = unit["kind"]
    if kind in {"update", "guard_only", "alias_move"}:
        row = session.execute(
            text("SELECT question_text, answer_text, status FROM qna WHERE id = :id FOR UPDATE"),
            {"id": unit["qna_id"]},
        ).mappings().first()
        if row is None or row["status"] != 1:
            raise UnitAbort(f"{unit['unit']}: hedef QnA aktif değil")
        record["qna_id"] = unit["qna_id"]

    if kind == "guard_only":
        _write_guard(session, unit["qna_id"], unit["guard"])
        record["guard_ref"] = unit["guard"]["guard_ref"]
        record["skipped_content_update"] = unit["skipped_content_update"]
    elif kind == "update":
        expected = unit["mutation"]["expected_current"]
        if row["question_text"] != expected["question"] or row["answer_text"] != expected["answer"]:
            raise UnitAbort(f"{unit['unit']}: mevcut soru/cevap değişmiş")
        # Guard, içerik aktifleşmeden önce aynı transaction'da kurulur.
        if unit["guard"] is not None:
            _write_guard(session, unit["qna_id"], unit["guard"])
            record["guard_ref"] = unit["guard"]["guard_ref"]
        session.execute(
            text("UPDATE qna SET question_text = :q, answer_text = :a, updated_by = :by, updated_at = now() WHERE id = :id"),
            {"q": unit["mutation"]["set"]["question"], "a": unit["mutation"]["set"]["answer"], "by": ACTOR, "id": unit["qna_id"]},
        )
        record["alias_moves"] = _move_aliases(session, unit, resolved, unit["qna_id"])
    elif kind == "alias_move":
        record["alias_moves"] = _move_aliases(session, unit, resolved, unit["qna_id"])
    elif kind in {"create", "promotion"}:
        if kind == "promotion":
            source = resolved["promotion_sources"][unit["ref"]]
            expected = unit["promotion"]["expected_source"]
            result = session.execute(
                text("DELETE FROM qna_queries WHERE id = :id AND qna_id = :qna AND query_text = :alias"),
                {"id": source["alias_id"], "qna": source["qna_id"], "alias": expected["alias"]},
            )
            if result.rowcount != 1:
                raise UnitAbort(f"{unit['unit']}: promotion alias'ı beklenen kaynakta değil")
            record["deleted_alias"] = source["alias_row"]
            wanted = unit["promotion"]["target"]
        else:
            wanted = unit["mutation"]["set"]
        exists = session.execute(
            text("SELECT count(*) FROM qna WHERE status = 1 AND question_text = :q"), {"q": wanted["question"]}
        ).scalar_one()
        if exists:
            raise UnitAbort(f"{unit['unit']}: kanonik soru zaten var")
        new_id = _insert_qna(session, wanted["question"], wanted["answer"])
        record["qna_id"] = new_id
        record["created"] = True
        if unit["guard"] is not None:
            _write_guard(session, new_id, unit["guard"])
            record["guard_ref"] = unit["guard"]["guard_ref"]
        record["alias_moves"] = _move_aliases(session, unit, resolved, new_id)
    else:
        raise UnitAbort(f"Bilinmeyen birim: {kind}")
    return record


def apply_restore(session, actions: dict[str, Any]) -> None:
    from sqlalchemy import text

    if actions["foreign_changes"]:
        raise UnitAbort(f"Kapsam dışı değişiklik var, otomatik rollback yapılmaz: {actions['foreign_changes'][:5]}")
    for qid in actions["guard_delete"]:
        session.execute(text("DELETE FROM qna_routing_guards WHERE qna_id = :id"), {"id": qid})
    for row in actions["guard_restore"]:
        session.execute(
            text(
                "INSERT INTO qna_routing_guards (qna_id, guard_ref, exact_bypass_enabled, selector_mode, content_mode, "
                "valid_from, valid_until, on_expiry, source_of_truth, created_at, updated_at) VALUES (:qna_id, :guard_ref, "
                ":exact_bypass_enabled, :selector_mode, :content_mode, :valid_from, :valid_until, :on_expiry, "
                ":source_of_truth, :created_at, :updated_at) ON CONFLICT (qna_id) DO UPDATE SET guard_ref = EXCLUDED.guard_ref, "
                "exact_bypass_enabled = EXCLUDED.exact_bypass_enabled, selector_mode = EXCLUDED.selector_mode, "
                "content_mode = EXCLUDED.content_mode, valid_from = EXCLUDED.valid_from, valid_until = EXCLUDED.valid_until, "
                "on_expiry = EXCLUDED.on_expiry, source_of_truth = EXCLUDED.source_of_truth, "
                "created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at"
            ),
            {**row, "valid_from": _parse_date(row["valid_from"]), "valid_until": _parse_date(row["valid_until"]),
             "created_at": _parse_ts(row["created_at"]), "updated_at": _parse_ts(row["updated_at"])},
        )
    for row in actions["restore_alias_owner"]:
        session.execute(text("UPDATE qna_queries SET qna_id = :qna_id WHERE id = :id"), row)
    for row in actions["reinsert_alias"]:
        session.execute(
            text("INSERT INTO qna_queries (id, qna_id, query_text, query_type, created_at) "
                 "VALUES (:id, :qna_id, :query_text, :query_type, :created_at)"),
            {**row, "created_at": _parse_ts(row["created_at"])},
        )
    for row in actions["restore_qna"]:
        session.execute(
            text("UPDATE qna SET question_text = :question_text, answer_text = :answer_text, status = :status, "
                 "updated_by = :updated_by, updated_at = :updated_at WHERE id = :id"),
            {**row, "updated_at": _parse_ts(row["updated_at"])},
        )
    if actions["delete_qna"]:
        session.execute(text("DELETE FROM qna WHERE id = ANY(:ids)"), {"ids": actions["delete_qna"]})


def rollback_to_snapshot(session, units, resolved, before) -> dict[str, Any]:
    live = read_snapshot(session)
    actions = restore_actions(units, resolved, before, live)
    apply_restore(session, actions)
    session.flush()
    restored = read_snapshot(session)
    if restored["digest"] != before["digest"]:
        raise UnitAbort(f"Rollback sonrası digest snapshot ile eşleşmiyor: {restored['digest']}")
    return {
        "restored_digest": restored["digest"],
        "counts": {key: len(value) for key, value in actions.items() if isinstance(value, list)},
        "reindex_ids": actions["reindex_ids"],
    }


def admin_session():
    from sqlalchemy.orm import Session

    from core.database import admin_engine

    return Session(bind=admin_engine, autoflush=True, expire_on_commit=False)


def mode_snapshot(payload: dict[str, Any]) -> int:
    with admin_session() as session:
        snapshot = read_snapshot(session)
        session.rollback()
    emit("snapshot", snapshot=snapshot)
    return 0


def mode_dry_run(payload: dict[str, Any]) -> int:
    """Canlı DB'de tam rehearsal: birimler SAVEPOINT içinde gerçekten çalışır,
    bütünlük ve rollback denenir, en sonda dış transaction ROLLBACK edilir."""
    plan = payload["plan"]
    session = admin_session()
    report: dict[str, Any] = {"mode": "dry-run", "writes_committed": False}
    try:
        session.begin()
        before = read_snapshot(session)
        report["before_digest"] = before["digest"]
        resolved, units = resolve_plan(plan, before)
        report["preflight_errors"] = resolved["errors"]
        report["units"] = []
        if not resolved["errors"]:
            for unit in units:
                savepoint = session.begin_nested()
                try:
                    record = run_unit(session, unit, resolved)
                    savepoint.commit()
                    report["units"].append({**record, "status": "OK"})
                except Exception as exc:  # noqa: BLE001 - rehearsal raporu
                    savepoint.rollback()
                    report["units"].append({"unit": unit["unit"], "status": "FAILED", "error": str(exc)})
                    break
            after = read_snapshot(session)
            report["integrity"] = integrity_checks(plan, units, resolved, before, after)
            report["index_plan"] = {
                "reindex_ids": index_targets(units, resolved, report["integrity"]["created_ids"]),
                "strategy": "Qdrant delete_point + batch upsert; Meili add_documents + wait_for_task; sonra doğrulama",
            }
            rollback_savepoint = session.begin_nested()
            try:
                report["rollback_rehearsal"] = {"status": "PASS", **rollback_to_snapshot(session, units, resolved, before)}
            except Exception as exc:  # noqa: BLE001
                report["rollback_rehearsal"] = {"status": "FAIL", "error": str(exc)}
            rollback_savepoint.rollback()
    finally:
        session.rollback()
        session.close()

    with admin_session() as check_session:
        live_after = read_snapshot(check_session)
        check_session.rollback()
    report["live_digest_after_rollback"] = live_after["digest"]
    report["live_unchanged"] = live_after["digest"] == report.get("before_digest")
    units_ok = bool(report.get("units")) and all(item["status"] == "OK" for item in report["units"])
    report["status"] = "PASS" if (
        not report.get("preflight_errors")
        and units_ok
        and report.get("integrity", {}).get("status") == "PASS"
        and report.get("rollback_rehearsal", {}).get("status") == "PASS"
        and report["live_unchanged"]
    ) else "FAIL"
    emit("result", report=report)
    return 0 if report["status"] == "PASS" else 2


def mode_apply(payload: dict[str, Any]) -> int:
    plan, backup = payload["plan"], payload["backup_snapshot"]
    session = admin_session()
    try:
        with session.begin():
            before = read_snapshot(session)
        if before["digest"] != backup["digest"] or snapshot_digest(backup) != backup["digest"]:
            emit("result", report={"mode": "apply", "status": "ABORTED",
                                   "reason": "Canlı DB backup snapshot'ından farklı; yeni backup al"})
            return 2
        resolved, units = resolve_plan(plan, before)
        if resolved["errors"]:
            emit("result", report={"mode": "apply", "status": "ABORTED", "preflight_errors": resolved["errors"]})
            return 2
        for unit in units:
            try:
                with session.begin():
                    record = run_unit(session, unit, resolved)
            except Exception as exc:  # noqa: BLE001 - kalan birimler uygulanmaz
                emit("journal", status="FAILED", unit=unit["unit"], error=str(exc))
                emit("result", report={"mode": "apply", "status": "PARTIAL_FAILED", "failed_unit": unit["unit"],
                                       "next": "rollback modunu backup snapshot ile çalıştır"})
                return 3
            emit("journal", status="COMMITTED", **record)
        with session.begin():
            after = read_snapshot(session)
        integrity = integrity_checks(plan, units, resolved, before, after)
        emit("result", report={
            "mode": "apply",
            "status": "APPLIED" if integrity["status"] == "PASS" else "APPLIED_INTEGRITY_FAILED",
            "integrity": integrity,
            "after_digest": after["digest"],
            "index_ids": index_targets(units, resolved, integrity["created_ids"]),
        })
        return 0 if integrity["status"] == "PASS" else 4
    finally:
        session.close()


def mode_verify(payload: dict[str, Any]) -> int:
    plan, backup = payload["plan"], payload["backup_snapshot"]
    with admin_session() as session:
        after = read_snapshot(session)
        session.rollback()
    resolved, units = resolve_plan(plan, backup)
    integrity = integrity_checks(plan, units, resolved, backup, after)
    emit("result", report={"mode": "verify", "status": integrity["status"], "integrity": integrity,
                           "index_ids": index_targets(units, resolved, integrity["created_ids"])})
    return 0 if integrity["status"] == "PASS" else 4


def mode_rollback(payload: dict[str, Any]) -> int:
    plan, backup = payload["plan"], payload["backup_snapshot"]
    session = admin_session()
    try:
        resolved, units = resolve_plan(plan, backup)
        with session.begin():
            result = rollback_to_snapshot(session, units, resolved, backup)
        emit("result", report={"mode": "rollback", "status": "ROLLED_BACK", **result})
        return 0
    except Exception as exc:  # noqa: BLE001
        emit("result", report={"mode": "rollback", "status": "ROLLBACK_FAILED", "error": str(exc),
                               "next": "Otomatik geri alma yapılmadı; pg_dump yedeğinden restore et"})
        return 5
    finally:
        session.close()


def mode_index_sync(payload: dict[str, Any]) -> int:
    """Verilen QnA id'lerini indekslerde DB ile birebir eşitler ve doğrular.

    Qdrant ``upsert_points`` alias sayısı azalınca eski alias noktalarını
    silmez; taşınan alias eski QnA'ya yönlendirmeye devam eder. Bu yüzden önce
    kayıt ve tüm alias noktaları silinir, sonra yeniden yazılır. Router'daki
    sync fonksiyonları hataları yuttuğu için burada provider'lar doğrudan çağrılır.
    """
    from sqlalchemy import text

    from core.deps import MEILI_PROVIDER, QDRANT_PROVIDER
    from services.providers import ALIAS_ID_OFFSET, MAX_ALIASES_PER_QNA, _usable_aliases
    from services.routing_guards import RoutingGuardPolicy

    ids = sorted({int(qid) for qid in payload["ids"]})
    with admin_session() as session:
        rows = {
            int(row["id"]): dict(row)
            for row in session.execute(
                text("SELECT * FROM qna_search_view WHERE id = ANY(:ids)"), {"ids": ids}
            ).mappings().all()
        }
        policy = RoutingGuardPolicy.load(session)
        session.rollback()
    active = {qid: row for qid, row in rows.items() if row["status"] == 1}
    inactive = [qid for qid in ids if qid not in active]

    client = MEILI_PROVIDER.client
    for qid in ids:
        QDRANT_PROVIDER.delete_point(qid)
    if inactive:
        task = MEILI_PROVIDER.index.delete_documents(inactive)
        client.wait_for_task(task.task_uid, timeout_in_ms=120_000)
    docs = []
    for row in active.values():
        doc = dict(row)
        doc.pop("status", None)
        docs.append(doc)
    if docs:
        task = MEILI_PROVIDER.index.add_documents(docs)
        finished = client.wait_for_task(task.task_uid, timeout_in_ms=120_000)
        if finished.status != "succeeded":
            raise RuntimeError(f"Meili görevi başarısız: {finished}")
        QDRANT_PROVIDER.upsert_points([(d["id"], d["question"], d["answer"], d.get("queries")) for d in docs])

    failures: list[dict[str, Any]] = []
    for qid in ids:
        all_point_ids = [qid] + [ALIAS_ID_OFFSET + qid * MAX_ALIASES_PER_QNA + pos for pos in range(1, MAX_ALIASES_PER_QNA)]
        points = QDRANT_PROVIDER.client.retrieve(
            collection_name=QDRANT_PROVIDER.collection_name, ids=all_point_ids, with_payload=True, with_vectors=False
        )
        present = {int(point.id): point for point in points}
        if qid not in active:
            if present:
                failures.append({"id": qid, "check": "qdrant_inactive_points_remain", "count": len(present)})
            try:
                MEILI_PROVIDER.index.get_document(qid)
                failures.append({"id": qid, "check": "meili_inactive_document_remains"})
            except Exception:  # noqa: BLE001 - bulunamaması beklenen
                pass
            continue
        row = active[qid]
        aliases = _usable_aliases(row.get("queries"))
        expected_ids = {qid} | {ALIAS_ID_OFFSET + qid * MAX_ALIASES_PER_QNA + pos for pos in range(1, len(aliases) + 1)}
        if set(present) != expected_ids:
            failures.append({"id": qid, "check": "qdrant_point_set", "expected": len(expected_ids), "actual": len(present)})
        if any(p.payload.get("answer") != row["answer"] or p.payload.get("qna_id") != qid for p in present.values()):
            failures.append({"id": qid, "check": "qdrant_payload"})
        indexed_aliases = sorted(p.payload.get("matched_query") for p in present.values() if int(p.id) != qid)
        if indexed_aliases != sorted(aliases):
            failures.append({"id": qid, "check": "qdrant_alias_texts"})
        document = dict(MEILI_PROVIDER.index.get_document(qid))
        if document.get("question") != row["question"] or document.get("answer") != row["answer"] \
                or sorted(document.get("queries") or []) != sorted(row.get("queries") or []):
            failures.append({"id": qid, "check": "meili_document"})
        decision = policy.decision(qid)
        if qid in policy.guards:
            hits = MEILI_PROVIDER.search(row["question"], limit=5) + QDRANT_PROVIDER.search(row["question"], limit=5)
            leaked = [hit for hit in hits if hit.get("qna_id") == qid and policy.fallback_allows(hit)]
            if leaked or decision.fallback_allowed:
                failures.append({"id": qid, "check": "guarded_fallback_leak"})

    emit("result", report={
        "mode": "index-sync",
        "status": "PASS" if not failures else "FAIL",
        "ids": ids,
        "active": len(active),
        "removed": inactive,
        "failures": failures,
    })
    return 0 if not failures else 6


MODE_HANDLERS = {
    "snapshot": mode_snapshot,
    "dry-run": mode_dry_run,
    "apply": mode_apply,
    "verify": mode_verify,
    "rollback": mode_rollback,
    "index-sync": mode_index_sync,
}


def main() -> int:
    payload = json.loads(sys.stdin.read())
    mode = payload["mode"]
    if mode in {"dry-run", "apply", "verify", "rollback"}:
        validate_plan_header(payload["plan"])
    return MODE_HANDLERS[mode](payload)


if __name__ == "__main__":
    raise SystemExit(main())
