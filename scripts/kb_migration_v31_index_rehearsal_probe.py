"""v3.1 migration izole indeks provası için backend-içi probe.

``rehearse_kb_migration_v31_indexes.py`` bu kaynağı ``python -c`` ile backend
konteynerine gönderir. stdin: JSON payload (``mode`` + ``runner_source`` + mod
parametreleri); stdout: tek satır JSON sonuç.

Gerçek kaynaklara (``auzef_qna_index``, ``auzef_qna_vectors``, canlı DB) yalnız
``fingerprint`` ve test kaynağı kurarken ayar/konfigürasyon okumak için
erişilir; tüm yazmalar ``qna_migration_v31_test_`` önekli kaynaklara ve
DATABASE_URL ile verilen disposable DB'ye gider.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

REAL_MEILI_INDEX = "auzef_qna_index"
REAL_QDRANT_COLLECTION = "auzef_qna_vectors"
TEST_PREFIX = "qna_migration_v31_test_"


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=32).hexdigest()


def load_runner(source: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "kb_migration_v31_runner"}
    exec(compile(source, "kb_migration_v31_runner.py", "exec"), namespace)  # noqa: S102 - kendi repo kaynağımız
    return namespace


def require_test_name(name: str) -> None:
    if not name or not name.startswith(TEST_PREFIX):
        raise ValueError(f"Test kaynağı değil: {name!r}")


def providers():
    from core.deps import MEILI_PROVIDER, QDRANT_PROVIDER

    return MEILI_PROVIDER, QDRANT_PROVIDER


def bind_test_target(name: str):
    require_test_name(name)
    meili, qdrant = providers()
    meili.client.get_index(name)
    if not qdrant.client.collection_exists(name):
        raise RuntimeError(f"Test collection yok: {name}")
    meili.index = meili.client.index(name)
    qdrant.collection_name = name
    return meili, qdrant


REAL_TARGET = "__real__"


def bind_read_target(name: str):
    """Salt-okunur doğrulama modları için hedef: test önekli kaynak ya da
    açıkça ``__real__`` (gerçek index/collection; bu modlar indekse yazmaz)."""
    if name != REAL_TARGET:
        return bind_test_target(name)
    meili, qdrant = providers()
    if meili.index.uid != REAL_MEILI_INDEX or qdrant.collection_name != REAL_QDRANT_COLLECTION:
        raise RuntimeError("Gerçek hedef istendi ama provider'lar varsayılan kaynaklarda değil")
    return meili, qdrant


def meili_documents(index) -> list[dict[str, Any]]:
    documents, offset = [], 0
    while True:
        batch = [dict(item) for item in index.get_documents({"limit": 1000, "offset": offset}).results]
        documents.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return sorted(documents, key=lambda item: int(item["id"]))


def qdrant_points(client, collection: str, with_vectors: bool) -> list[Any]:
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=512, offset=offset, with_payload=True, with_vectors=with_vectors
        )
        points.extend(batch)
        if offset is None:
            break
    return sorted(points, key=lambda point: int(point.id))


def db_state(runner: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import text

    from core.database import admin_engine

    with runner["admin_session"]() as session:
        snapshot = runner["read_snapshot"](session)
        view = [dict(row) for row in session.execute(text("SELECT * FROM qna_search_view ORDER BY id")).mappings().all()]
        session.rollback()
    return {"database": admin_engine.url.database, "snapshot": snapshot, "view": view}


def expected_index(view: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    from services.providers import ALIAS_ID_OFFSET, MAX_ALIASES_PER_QNA, _usable_aliases

    docs, points = {}, {}
    for row in view:
        if row["status"] != 1:
            continue
        qid = int(row["id"])
        docs[qid] = {
            "id": qid,
            "question": row["question"],
            "answer": row["answer"],
            "queries": sorted(row.get("queries") or []),
            "tags": sorted(row.get("tags") or []),
        }
        points[qid] = {"question": row["question"], "answer": row["answer"], "qna_id": qid}
        for position, alias in enumerate(_usable_aliases(row.get("queries")), start=1):
            points[ALIAS_ID_OFFSET + qid * MAX_ALIASES_PER_QNA + position] = {
                "question": row["question"], "answer": row["answer"], "qna_id": qid, "matched_query": alias,
            }
    return docs, points


def normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(doc["id"]),
        "question": doc.get("question"),
        "answer": doc.get("answer"),
        "queries": sorted(doc.get("queries") or []),
        "tags": sorted(doc.get("tags") or []),
    }


def owners(docs: list[dict[str, Any]], points: list[Any], alias: str) -> dict[str, list[int]]:
    wanted = alias.strip()
    return {
        "meili": sorted({int(d["id"]) for d in docs if wanted in [q.strip() for q in (d.get("queries") or [])]}),
        "qdrant": sorted({int(p.payload["qna_id"]) for p in points if (p.payload.get("matched_query") or "").strip() == wanted}),
    }


def db_owners(snapshot: dict[str, Any], alias: str) -> list[int]:
    active = {int(row["id"]) for row in snapshot["qna"] if row["status"] == 1}
    return sorted({int(row["qna_id"]) for row in snapshot["aliases"]
                   if int(row["qna_id"]) in active and row["query_text"].strip() == alias.strip()})


# --------------------------------------------------------------------------
# Modlar
# --------------------------------------------------------------------------


def mode_fingerprint(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    """Gerçek kaynakların salt-okunur parmak izi."""
    meili, qdrant = providers()
    if meili.index.uid != REAL_MEILI_INDEX or qdrant.collection_name != REAL_QDRANT_COLLECTION:
        raise RuntimeError("Parmak izi varsayılan kaynaklardan alınmalı")
    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Parmak izi beklenmeyen DB'den: {state['database']}")
    real_index = meili.client.get_index(REAL_MEILI_INDEX)
    documents = meili_documents(meili.index)
    points = qdrant_points(qdrant.client, REAL_QDRANT_COLLECTION, with_vectors=True)
    info = qdrant.client.get_collection(REAL_QDRANT_COLLECTION)
    snapshot = state["snapshot"]
    return {
        "db": {
            "database": state["database"],
            "snapshot_digest": snapshot["digest"],
            "active_qna": sum(1 for row in snapshot["qna"] if row["status"] == 1),
            "aliases": len(snapshot["aliases"]),
            "guards": len(snapshot["guards"]),
        },
        "meili": {
            "indexes": sorted(item.uid for item in meili.client.get_indexes({"limit": 1000})["results"]),
            "index": REAL_MEILI_INDEX,
            "updated_at": str(real_index.updated_at),
            "documents": len(documents),
            "documents_digest": digest(documents),
            "settings_digest": digest(meili.index.get_settings()),
        },
        "qdrant": {
            "collections": sorted(item.name for item in qdrant.client.get_collections().collections),
            "collection": REAL_QDRANT_COLLECTION,
            "points_count": info.points_count,
            "points": len(points),
            "points_digest": digest([[int(p.id), p.payload, p.vector] for p in points]),
            "vector_config": str(info.config.params.vectors),
        },
    }


def mode_create_resources(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    from qdrant_client.http.models import VectorParams

    name = payload["name"]
    require_test_name(name)
    meili, qdrant = providers()
    client = meili.client
    if name in {item.uid for item in client.get_indexes({"limit": 1000})["results"]}:
        raise RuntimeError(f"Meili test index'i zaten var: {name}")
    if qdrant.client.collection_exists(name):
        raise RuntimeError(f"Qdrant test collection'ı zaten var: {name}")

    real_settings = client.index(REAL_MEILI_INDEX).get_settings()  # salt-okunur
    task = client.create_index(name, {"primaryKey": "id"})
    if client.wait_for_task(task.task_uid, timeout_in_ms=60_000).status != "succeeded":
        raise RuntimeError("Meili test index'i oluşturulamadı")
    task = client.index(name).update_settings(real_settings)
    if client.wait_for_task(task.task_uid, timeout_in_ms=60_000).status != "succeeded":
        raise RuntimeError("Meili test index ayarları uygulanamadı")
    test_settings = client.index(name).get_settings()

    real_vectors = qdrant.client.get_collection(REAL_QDRANT_COLLECTION).config.params.vectors  # salt-okunur
    qdrant.client.create_collection(
        collection_name=name, vectors_config=VectorParams(size=real_vectors.size, distance=real_vectors.distance)
    )
    return {
        "meili_index": name,
        "meili_settings_match_real": digest(test_settings) == digest(real_settings),
        "qdrant_collection": name,
        "qdrant_vectors": str(qdrant.client.get_collection(name).config.params.vectors),
    }


def mode_db_info(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Beklenmeyen DB: {state['database']}")
    snapshot = state["snapshot"]
    return {
        "database": state["database"],
        "snapshot_digest": snapshot["digest"],
        "active_ids": sorted(int(row["id"]) for row in snapshot["qna"] if row["status"] == 1),
        "active_qna": sum(1 for row in snapshot["qna"] if row["status"] == 1),
        "aliases": len(snapshot["aliases"]),
        "guards": len(snapshot["guards"]),
    }


def mode_consistency(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    """Disposable DB ile test Meili/Qdrant'ı global olarak karşılaştırır ve durumu diske alır."""
    import numpy as np

    name = payload["name"]
    meili, qdrant = bind_read_target(name)
    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Beklenmeyen DB: {state['database']}")
    expected_docs, expected_points = expected_index(state["view"])
    documents = meili_documents(meili.index)
    points = qdrant_points(qdrant.client, qdrant.collection_name, with_vectors=True)
    failures: list[dict[str, Any]] = []

    actual_docs = {int(d["id"]): normalize_doc(d) for d in documents}
    missing_docs = sorted(set(expected_docs) - set(actual_docs))
    extra_docs = sorted(set(actual_docs) - set(expected_docs))
    mismatched_docs = sorted(qid for qid in set(expected_docs) & set(actual_docs) if expected_docs[qid] != actual_docs[qid])
    for check, ids in (("meili_missing_docs", missing_docs), ("meili_orphan_docs", extra_docs), ("meili_doc_mismatch", mismatched_docs)):
        if ids:
            failures.append({"check": check, "count": len(ids), "ids": ids[:20]})

    actual_points = {int(p.id): p for p in points}
    missing_points = sorted(set(expected_points) - set(actual_points))
    extra_points = sorted(set(actual_points) - set(expected_points))
    payload_mismatch = sorted(
        pid for pid in set(expected_points) & set(actual_points) if actual_points[pid].payload != expected_points[pid]
    )
    orphan_qna = sorted({int(p.payload.get("qna_id")) for p in points if int(p.payload.get("qna_id")) not in expected_docs})
    pairs: dict[tuple[int, str], int] = {}
    for p in points:
        key = (int(p.payload.get("qna_id")), (p.payload.get("matched_query") or "<canonical>").casefold())
        pairs[key] = pairs.get(key, 0) + 1
    duplicate_pairs = [list(key) for key, count in pairs.items() if count > 1]
    bad_dims = [int(p.id) for p in points if len(p.vector) != 1024 or not np.any(p.vector)]
    for check, ids in (
        ("qdrant_missing_points", missing_points), ("qdrant_stale_or_orphan_points", extra_points),
        ("qdrant_payload_mismatch", payload_mismatch), ("qdrant_points_for_inactive_qna", orphan_qna),
        ("qdrant_duplicate_qna_alias_points", duplicate_pairs), ("qdrant_bad_vectors", bad_dims),
    ):
        if ids:
            failures.append({"check": check, "count": len(ids), "ids": ids[:20]})

    reencoded = {}
    check_ids = {int(qid) for qid in payload.get("reencode_qna_ids", [])}
    if check_ids:
        selected = [p for p in points if int(p.payload["qna_id"]) in check_ids]
        texts = [p.payload.get("matched_query") or p.payload["question"] for p in selected]
        fresh = qdrant.model.encode(texts, normalize_embeddings=True)
        stored = np.array([p.vector for p in selected], dtype=np.float32)
        stored /= np.linalg.norm(stored, axis=1, keepdims=True)
        cosines = np.sum(stored * fresh, axis=1)
        low = [int(selected[i].id) for i in np.where(cosines < 0.999)[0]]
        reencoded = {"points": len(selected), "min_cosine": float(cosines.min()), "below_0_999": low[:20]}
        if low:
            failures.append({"check": "qdrant_vector_not_matching_text", "count": len(low), "ids": low[:20]})

    work = Path(payload["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{payload['label']}-meili.json").write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    np.save(work / f"{payload['label']}-qdrant-vectors.npy", np.array([p.vector for p in points], dtype=np.float32))
    (work / f"{payload['label']}-qdrant-points.json").write_text(
        json.dumps([[int(p.id), p.payload] for p in points], ensure_ascii=False), encoding="utf-8"
    )
    snapshot = state["snapshot"]
    return {
        "label": payload["label"],
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "db": {"database": state["database"], "snapshot_digest": snapshot["digest"],
               "active_qna": len(expected_docs), "aliases": len(snapshot["aliases"]), "guards": len(snapshot["guards"])},
        "meili": {"index": meili.index.uid, "documents": len(documents), "expected": len(expected_docs)},
        "qdrant": {"collection": qdrant.collection_name, "points": len(points), "expected": len(expected_points)},
        "reencode": reencoded,
    }


def migration_facts(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    plan, backup = payload["plan"], payload["backup_snapshot"]
    resolved, units = runner["resolve_plan"](plan, backup)
    created = {ref: int(qid) for ref, qid in (payload.get("created_ids") or {}).items()}
    moves = []
    for unit in units:
        for alias in unit["aliases"]:
            source = resolved["alias_sources"][str(int(alias["case_no"]))]
            target = unit.get("qna_id") if unit["kind"] in {"update", "alias_move"} else created.get(unit["ref"])
            moves.append({"case": int(alias["case_no"]), "alias": alias["alias"], "source": source["source_qna_id"],
                          "target": target, "target_ref": unit["ref"], "policy": alias["exact_alias_policy"]})
    promotion = plan["atomic_promotions"][0]
    return {"resolved": resolved, "units": units, "moves": moves, "created": created, "promotion": promotion,
            "promotion_source": resolved["promotion_sources"][promotion["operation_ref"]]}


NAMED_REGRESSIONS = (
    ("muafiyet_otomatik", lambda a: a["alias"] == "Muafiyetim otomatik gerçekleşmemiş"),
    ("tercih_sistemi_giris", lambda a: a["case"] == 62),
    ("okula_ara_verme", lambda a: a["case"] in {202, 203}),
    ("sinavlar_ayni_oturum", lambda a: a["case"] == 312),
    ("edevlet_transkript", lambda a: a["case"] == 429),
)


def mode_migration_assertions(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    phase = payload["phase"]  # pre | post
    name = payload["name"]
    meili, qdrant = bind_read_target(name)
    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Beklenmeyen DB: {state['database']}")
    facts = migration_facts(payload, runner)
    documents = meili_documents(meili.index)
    points = qdrant_points(qdrant.client, qdrant.collection_name, with_vectors=False)
    docs_by_id = {int(d["id"]): d for d in documents}
    failures: list[dict[str, Any]] = []
    moves_report = []

    for move in facts["moves"]:
        found = owners(documents, points, move["alias"])
        expected_db = db_owners(state["snapshot"], move["alias"])
        entry = {**move, "db_owners": expected_db, **found}
        if phase == "pre":
            ok = move["source"] in found["meili"] and move["source"] in found["qdrant"] \
                and (move["target"] is None or move["target"] not in found["meili"] + found["qdrant"])
        else:
            ok = (
                move["target"] is not None
                and found["meili"] == expected_db and found["qdrant"] == expected_db
                and move["target"] in expected_db and move["source"] not in expected_db
            )
            hits = meili.search(move["alias"], limit=20)
            entry["meili_search_hit_target"] = any(int(h["qna_id"]) == move["target"] for h in hits)
            ok = ok and entry["meili_search_hit_target"]
        entry["ok"] = ok
        moves_report.append(entry)
        if not ok:
            failures.append({"check": f"alias_{phase}:{move['case']}", "entry": entry})

    named = {}
    for label, predicate in NAMED_REGRESSIONS:
        matched = [m for m in moves_report if predicate(m)]
        if not matched:
            failures.append({"check": f"named_regression_not_found:{label}"})
            continue
        named[label] = [{k: m[k] for k in ("case", "alias", "source", "target", "target_ref", "meili", "qdrant", "ok")}
                        for m in matched]

    promotion, source = facts["promotion"], facts["promotion_source"]
    alias = promotion["expected_source"]["alias"]
    found = owners(documents, points, alias)
    promo = {"alias": alias, "source": source["qna_id"], **found}
    if phase == "pre":
        promo["ok"] = source["qna_id"] in found["meili"] and source["qna_id"] in found["qdrant"]
    else:
        new_id = facts["created"].get(promotion["operation_ref"])
        doc = docs_by_id.get(new_id) or {}
        own_alias = [q for q in doc.get("queries") or [] if runner["normalize_text"](q) == runner["normalize_text"](doc.get("question", ""))]
        meili_top = [int(h["qna_id"]) for h in meili.search(promotion["target"]["question"], limit=3)]
        qdrant_top = [int(h["qna_id"]) for h in qdrant.search(promotion["target"]["question"], limit=3)]
        promo.update(new_id=new_id, meili_top=meili_top, qdrant_top=qdrant_top, own_alias=own_alias)
        promo["ok"] = (
            source["qna_id"] not in found["meili"] and source["qna_id"] not in found["qdrant"]
            and doc.get("question") == promotion["target"]["question"] and not own_alias
            and meili_top[:1] == [new_id] and qdrant_top[:1] == [new_id]
        )
    if not promo["ok"]:
        failures.append({"check": f"new11_promotion_{phase}", "entry": promo})

    created_report = []
    if phase == "post":
        questions = {ref_of_unit(u): u for u in facts["units"] if u["kind"] in {"create", "promotion"}}
        for ref, qid in facts["created"].items():
            unit = questions[ref]
            question = unit["mutation"]["set"]["question"] if unit["kind"] == "create" else unit["promotion"]["target"]["question"]
            meili_top = [int(h["qna_id"]) for h in meili.search(question, limit=3)]
            qdrant_top = [int(h["qna_id"]) for h in qdrant.search(question, limit=3)]
            ok = meili_top[:1] == [qid] and qdrant_top[:1] == [qid]
            created_report.append({"ref": ref, "qna_id": qid, "meili_top": meili_top, "qdrant_top": qdrant_top, "ok": ok})
            if not ok:
                failures.append({"check": f"new_qna_searchable:{ref}", "entry": created_report[-1]})

        backup_319 = next(row for row in payload["backup_snapshot"]["qna"] if int(row["id"]) == 319)
        doc_319 = docs_by_id.get(319) or {}
        point_319 = next((p for p in points if int(p.id) == 319), None)
        if doc_319.get("answer") != backup_319["answer_text"] or point_319 is None \
                or point_319.payload.get("answer") != backup_319["answer_text"]:
            failures.append({"check": "ex319_index_content_unchanged"})

    self_alias = sorted(
        int(d["id"]) for d in documents
        if any(runner["normalize_text"](q) == runner["normalize_text"](d["question"]) for q in d.get("queries") or [])
    )
    multi_owner: dict[str, set[int]] = {}
    for p in points:
        if p.payload.get("matched_query"):
            multi_owner.setdefault(p.payload["matched_query"].strip(), set()).add(int(p.payload["qna_id"]))
    return {
        "phase": phase,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "alias_moves_checked": len(moves_report),
        "alias_moves_ok": sum(1 for m in moves_report if m["ok"]),
        "named_regressions": named,
        "new11": promo,
        "created_searchable": created_report,
        "docs_with_self_canonical_alias": self_alias,
        "multi_owner_alias_texts": sorted(text for text, ids in multi_owner.items() if len(ids) > 1),
    }


def ref_of_unit(unit: dict[str, Any]) -> str:
    return unit["ref"]


def mode_smoke(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    """Gerçek answer pipeline'ı test kaynaklarıyla çalıştırır; LLM sahte seçicidir (ağ yok)."""
    import services.answer_pipeline as ap
    import services.routing_guards as rg
    from core.database import SessionLocal

    name = payload["name"]
    bind_read_target(name)
    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Beklenmeyen DB: {state['database']}")
    facts = migration_facts(payload, runner)
    snapshot = state["snapshot"]
    active = {int(row["id"]): row for row in snapshot["qna"] if row["status"] == 1}
    answers: dict[str, list[int]] = {}
    for qid, row in active.items():
        answers.setdefault(row["answer_text"], []).append(qid)
    guards = {int(row["qna_id"]): row for row in snapshot["guards"]}
    ref_by_id = {qid: ref for ref, qid in facts["created"].items()}
    for unit in facts["units"]:
        if unit.get("qna_id") is not None:
            ref_by_id.setdefault(int(unit["qna_id"]), unit["ref"])

    class FakeSelector:
        def __init__(self, pick: int | None):
            self.pick, self.pools = pick, []

        def split_questions(self, message: str) -> list[str]:
            return [message]

        def ask(self, question: str, candidates: list[dict[str, Any]]):
            self.pools.append([c.get("qna_id") for c in candidates])
            return next((c["answer"] for c in candidates if c.get("qna_id") == self.pick), None)

    class NoGuards:
        @staticmethod
        def load(db, **_kwargs):
            return rg.RoutingGuardPolicy.empty()

        @staticmethod
        def empty(**_kwargs):
            return rg.RoutingGuardPolicy.empty()

    originals = (ap.is_llm_enabled, ap.get_llm_provider, rg._today, ap.RoutingGuardPolicy)
    db = SessionLocal()

    def ask(query: str, selector: FakeSelector | None = None, today: date | None = None, guards_enabled: bool = True):
        ap.is_llm_enabled = (lambda _db: True) if selector else (lambda _db: False)
        ap.get_llm_provider = (lambda _db: selector) if selector else (lambda _db: None)
        if today:
            rg._today = lambda: today
        if not guards_enabled:
            ap.RoutingGuardPolicy = NoGuards
        try:
            return ap.answer_question(query, db)
        finally:
            ap.is_llm_enabled, ap.get_llm_provider, rg._today, ap.RoutingGuardPolicy = originals

    def qna_chain(query: str, guards_enabled: bool = True):
        """Takvim kapısı olmadan eşik zinciri (Meili → Qdrant); takvim anahtar
        kelime eşleşmesi QnA yolunu maskelemesin diye ayrıca sınanır."""
        policy = rg.RoutingGuardPolicy.load(db) if guards_enabled else rg.RoutingGuardPolicy.empty()
        return ap._fallback_answer(query, db, use_calendar=False, routing_policy=policy)

    failures: list[dict[str, Any]] = []
    results: dict[str, Any] = {"normal": [], "guarded": [], "ex319": {}, "temporal_counterfactual": []}
    try:
        normal_ids = [facts["created"]["NEW-01"], 14, next(qid for qid in sorted(active) if qid not in guards
                      and qid not in {m["source"] for m in facts["moves"]} and qid not in ref_by_id)]
        for qid in normal_ids:
            row = active[qid]
            fallback_answer, fallback_source = ask(row["question_text"])
            chain_answer, chain_source = qna_chain(row["question_text"])
            selector = FakeSelector(qid)
            llm_answer, llm_source = ask(row["question_text"], selector)
            entry = {"qna_id": qid, "ref": ref_by_id.get(qid),
                     "full_pipeline_source": fallback_source, "full_pipeline_hit": fallback_answer == row["answer_text"],
                     "qna_chain_source": chain_source, "qna_chain_hit": chain_answer == row["answer_text"],
                     "selector_in_pool": any(qid in pool for pool in selector.pools),
                     "selector_source": llm_source, "selector_hit": llm_answer == row["answer_text"]}
            # Tam pipeline'da takvim kapısı QnA'dan önce gelir; guard'sız QnA'nın
            # eşik zincirinden dönebilmesi routing sözleşmesinin kendisidir.
            entry["ok"] = entry["qna_chain_hit"] and chain_source in {"meilisearch", "qdrant_vector"} \
                and entry["selector_in_pool"] and entry["selector_hit"] and llm_source == "llm" \
                and (entry["full_pipeline_hit"] or fallback_source == "academic_calendar")
            results["normal"].append(entry)
            if not entry["ok"]:
                failures.append({"check": f"normal_qna_routes:{qid}", "entry": entry})

        aliases_by_target: dict[int, list[str]] = {}
        for move in facts["moves"]:
            aliases_by_target.setdefault(int(move["target"]), []).append(move["alias"])
        for qid in sorted(guards):
            row = active[qid]
            if len(answers[row["answer_text"]]) > 1:
                failures.append({"check": f"guarded_answer_not_unique:{qid}"})
                continue
            for query in [row["question_text"], *aliases_by_target.get(qid, [])]:
                fallback_answer, fallback_source = ask(query)
                picker = FakeSelector(qid)
                selected_answer, selected_source = ask(query, picker)
                decliner = FakeSelector(None)
                declined_answer, declined_source = ask(query, decliner)
                chain_answer, chain_source = qna_chain(query)
                unguarded_answer, unguarded_source = qna_chain(query, guards_enabled=False)
                entry = {
                    "qna_id": qid, "ref": ref_by_id.get(qid), "guard_ref": guards[qid]["guard_ref"], "query": query,
                    "fallback_leak": fallback_answer == row["answer_text"], "fallback_source": fallback_source,
                    "qna_chain_leak": chain_answer == row["answer_text"], "qna_chain_source": chain_source,
                    "without_guard_qna_chain_returns_it": unguarded_answer == row["answer_text"],
                    "without_guard_qna_chain_source": unguarded_source,
                    "selector_in_pool": any(qid in pool for pool in picker.pools),
                    "selector_answered": selected_answer == row["answer_text"] and selected_source == "llm",
                    "declined_in_pool": any(qid in pool for pool in decliner.pools),
                    "declined_leak": declined_answer == row["answer_text"], "declined_source": declined_source,
                }
                entry["ok"] = not entry["fallback_leak"] and not entry["declined_leak"] and not entry["qna_chain_leak"] \
                    and entry["selector_in_pool"] and entry["selector_answered"]
                results["guarded"].append(entry)
                if not entry["ok"]:
                    failures.append({"check": f"guarded_routing:{qid}", "entry": entry})

        for ref in ("NEW-10", "NEW-12", "NEW-13"):
            qid = facts["created"][ref]
            row = active[qid]
            for query in [row["question_text"], *aliases_by_target.get(qid, [])]:
                unguarded_answer, unguarded_source = qna_chain(query, guards_enabled=False)
                guarded_answer, guarded_source = qna_chain(query)
                full_answer, full_source = ask(query)
                entry = {"ref": ref, "qna_id": qid, "query": query,
                         "without_guard_fallback_returns_it": unguarded_answer == row["answer_text"],
                         "without_guard_source": unguarded_source,
                         "with_guard_fallback_returns_it": guarded_answer == row["answer_text"],
                         "with_guard_source": guarded_source,
                         "full_pipeline_returns_it": full_answer == row["answer_text"], "full_pipeline_source": full_source}
                results["temporal_counterfactual"].append(entry)
                if entry["with_guard_fallback_returns_it"] or entry["full_pipeline_returns_it"]:
                    failures.append({"check": f"temporal_guard_leak:{ref}", "entry": entry})
        demonstrated = sorted({e["ref"] for e in results["temporal_counterfactual"] if e["without_guard_fallback_returns_it"]})
        results["temporal_counterfactual_demonstrated_refs"] = demonstrated
        if not demonstrated:
            failures.append({"check": "temporal_counterfactual_not_demonstrated"})

        backup_rows = {int(row["id"]): row for row in payload["backup_snapshot"]["qna"]}
        live_319 = next(row for row in snapshot["qna"] if int(row["id"]) == 319)
        planned_out_of_319 = {int(m["case"]): m["alias"] for m in facts["moves"] if int(m["source"]) == 319}
        planned_ids = {facts["resolved"]["alias_sources"][str(case)]["alias_id"] for case in planned_out_of_319}
        backup_aliases_319 = sorted((r["id"], r["query_text"]) for r in payload["backup_snapshot"]["aliases"]
                                    if int(r["qna_id"]) == 319 and int(r["id"]) not in planned_ids)
        live_aliases_319 = sorted((r["id"], r["query_text"]) for r in snapshot["aliases"] if int(r["qna_id"]) == 319)
        meili, qdrant = bind_read_target(name)
        doc_319 = next((dict(d) for d in meili_documents(meili.index) if int(d["id"]) == 319), {})
        points_319 = [p for p in qdrant_points(qdrant.client, qdrant.collection_name, with_vectors=False)
                      if int(p.payload["qna_id"]) == 319]
        retained = []
        for case, alias_text in sorted((int(k), v) for k, v in payload["retained_319_aliases"].items()):
            retained.append({
                "case": case, "alias": alias_text,
                "db": any(r["query_text"] == alias_text for r in snapshot["aliases"] if int(r["qna_id"]) == 319),
                "meili": alias_text in (doc_319.get("queries") or []),
                "qdrant": any(p.payload.get("matched_query") == alias_text.strip() for p in points_319),
            })
        guard_319 = guards.get(319) or {}
        in_window, expired = FakeSelector(319), FakeSelector(319)
        in_window_answer, _ = ask(live_319["question_text"], in_window)
        expired_answer, expired_source = ask(live_319["question_text"], expired, today=date(2026, 12, 10))
        expired_fallback, _ = ask(live_319["question_text"], today=date(2026, 12, 10))
        ex319 = {
            "guard_ref": guard_319.get("guard_ref"), "valid_until": guard_319.get("valid_until"),
            "row_identical_to_backup": live_319 == backup_rows[319],
            "planned_moves_out_of_319": sorted(planned_out_of_319),
            "aliases_equal_backup_minus_planned_moves": live_aliases_319 == backup_aliases_319,
            "retained_cases_on_319": retained,
            "in_window_selector_pool_has_319": any(319 in pool for pool in in_window.pools),
            "in_window_selector_answered": in_window_answer == live_319["answer_text"],
            "expired_selector_pool_has_319": any(319 in pool for pool in expired.pools),
            "expired_answer_is_319": expired_answer == live_319["answer_text"],
            "expired_fallback_is_319": expired_fallback == live_319["answer_text"],
        }
        ex319["ok"] = (
            ex319["guard_ref"] == "GUARD-EX-319" and ex319["valid_until"] == "2026-12-09"
            and ex319["row_identical_to_backup"] and ex319["aliases_equal_backup_minus_planned_moves"]
            and {r["case"] for r in retained} >= {214} and all(r["db"] and r["meili"] and r["qdrant"] for r in retained)
            and ex319["in_window_selector_pool_has_319"] and ex319["in_window_selector_answered"]
            and not ex319["expired_selector_pool_has_319"] and not ex319["expired_answer_is_319"]
            and not ex319["expired_fallback_is_319"]
        )
        results["ex319"] = ex319
        if not ex319["ok"]:
            failures.append({"check": "ex319_guard_and_content", "entry": ex319})
    finally:
        ap.is_llm_enabled, ap.get_llm_provider, rg._today, ap.RoutingGuardPolicy = originals
        db.close()

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "counts": {"normal": len(results["normal"]), "guarded_queries": len(results["guarded"]),
                   "guarded_qna": len(guards), "temporal_queries": len(results["temporal_counterfactual"])},
        "results": results,
    }


def mode_compare_states(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    """İki kaydedilmiş test indeks durumunu karşılaştırır (ör. seed vs rollback)."""
    import numpy as np

    work = Path(payload["work_dir"])
    left, right = payload["left"], payload["right"]
    load_docs = lambda label: json.loads((work / f"{label}-meili.json").read_text(encoding="utf-8"))  # noqa: E731
    load_points = lambda label: json.loads((work / f"{label}-qdrant-points.json").read_text(encoding="utf-8"))  # noqa: E731
    docs_left, docs_right = load_docs(left), load_docs(right)
    points_left, points_right = load_points(left), load_points(right)
    vectors_left = np.load(work / f"{left}-qdrant-vectors.npy")
    vectors_right = np.load(work / f"{right}-qdrant-vectors.npy")
    failures = []
    if docs_left != docs_right:
        left_ids = {d["id"]: d for d in docs_left}
        right_ids = {d["id"]: d for d in docs_right}
        differing = sorted(i for i in set(left_ids) | set(right_ids) if left_ids.get(i) != right_ids.get(i))
        failures.append({"check": "meili_documents_differ", "ids": differing[:20], "count": len(differing)})
    min_cosine = None
    if points_left != points_right:
        left_ids = {p[0]: p[1] for p in points_left}
        right_ids = {p[0]: p[1] for p in points_right}
        differing = sorted(i for i in set(left_ids) | set(right_ids) if left_ids.get(i) != right_ids.get(i))
        failures.append({"check": "qdrant_points_differ", "ids": differing[:20], "count": len(differing)})
    else:
        a = vectors_left / np.linalg.norm(vectors_left, axis=1, keepdims=True)
        b = vectors_right / np.linalg.norm(vectors_right, axis=1, keepdims=True)
        cosines = np.sum(a * b, axis=1)
        min_cosine = float(cosines.min())
        if min_cosine < 0.9999:
            failures.append({"check": "qdrant_vectors_differ", "min_cosine": min_cosine,
                             "count": int(np.sum(cosines < 0.9999))})
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "left": left, "right": right,
            "meili_documents": [len(docs_left), len(docs_right)], "qdrant_points": [len(points_left), len(points_right)],
            "min_vector_cosine": min_cosine}


def mode_cleanup(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    name = payload["name"]
    require_test_name(name)
    meili, qdrant = providers()
    result: dict[str, Any] = {"name": name}
    indexes = {item.uid for item in meili.client.get_indexes({"limit": 1000})["results"]}
    if name in indexes:
        task = meili.client.delete_index(name)
        result["meili_delete_status"] = meili.client.wait_for_task(task.task_uid, timeout_in_ms=60_000).status
    if qdrant.client.collection_exists(name):
        result["qdrant_deleted"] = qdrant.client.delete_collection(name)
    work = Path(payload["work_dir"])
    if work.name.startswith(TEST_PREFIX) and work.exists():
        shutil.rmtree(work)
    result["meili_index_remaining"] = name in {item.uid for item in meili.client.get_indexes({"limit": 1000})["results"]}
    result["qdrant_collection_remaining"] = qdrant.client.collection_exists(name)
    result["work_dir_remaining"] = work.exists()
    result["status"] = "PASS" if not (result["meili_index_remaining"] or result["qdrant_collection_remaining"]
                                      or result["work_dir_remaining"]) else "FAIL"
    return result


def mode_export_baseline(payload: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    """Post-migration DB'nin deterministik QnA / alias / guard export'u (salt-okunur)."""
    from sqlalchemy import text

    state = db_state(runner)
    if state["database"] != payload["expected_database"]:
        raise RuntimeError(f"Beklenmeyen DB: {state['database']}")
    with runner["admin_session"]() as session:
        tags = {
            int(row["qna_id"]): sorted(row["tags"] or [])
            for row in session.execute(text(
                "SELECT qt.qna_id, ARRAY_AGG(t.name) AS tags FROM qna_tags qt JOIN tags t ON t.id = qt.tag_id GROUP BY qt.qna_id"
            )).mappings().all()
        }
        sequences = {
            name: dict(session.execute(text(f"SELECT last_value, is_called FROM {name}")).mappings().one())
            for name in ("qna_id_seq", "qna_queries_id_seq")
        }
        session.rollback()
    snapshot = state["snapshot"]
    qna = [{**row, "tags": tags.get(int(row["id"]), [])} for row in snapshot["qna"]]
    return {"snapshot_digest": snapshot["digest"], "qna": qna, "aliases": snapshot["aliases"],
            "guards": snapshot["guards"], "sequences": sequences}


MODES = {
    "fingerprint": mode_fingerprint,
    "create-resources": mode_create_resources,
    "db-info": mode_db_info,
    "consistency": mode_consistency,
    "migration-assertions": mode_migration_assertions,
    "smoke": mode_smoke,
    "compare-states": mode_compare_states,
    "cleanup": mode_cleanup,
    "export-baseline": mode_export_baseline,
}


def main() -> int:
    payload = json.loads(sys.stdin.read())
    runner = load_runner(payload.pop("runner_source"))
    result = MODES[payload["mode"]](payload, runner)
    sys.stdout.write(json.dumps({"type": "probe", "result": result}, ensure_ascii=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
