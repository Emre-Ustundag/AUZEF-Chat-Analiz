"""Session-gold v2: frozen Gold v2 + gerçek konuşma kayıtlarından deterministik üretim.

Girdi olarak dondurulmuş Gold v2 (değiştirilmez) ve ham ``chatbot.xlsx``'ten
çıkarılan gerçek oturumlar kullanılır. Hiçbir turn üretilmez; bulunamayan
bağlam ``unresolved`` olarak raporlanır.

Kurallar:
- Hedef turn yalnız KENDİNDEN ÖNCEKİ turn'lerle değerlendirilir (leakage yok).
- Asistan mesajları yalnız bağlamdır; beklenen hedef her zaman Gold v2'den gelir.
- Aynı oturum kimliği uzun aradan sonra yeniden kullanıldıysa zaman eşiğiyle
  ayrı segmentlere bölünür; eşik veriden hesaplanır.
- Vaka 214 hiçbir evaluation hedefi olamaz.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASET_VERSION = "session-gold-v2"
GOLD_DIR = ROOT / "outputs" / "gold-v2-final-20260917"
SOURCES = ROOT / "outputs" / "gold-v2-sources-20260917"
EXTRACT_DIR = ROOT / "outputs" / "session-gold-v2-sources-20260917"
PENDING_CASE = 214
STATUS_EXCLUDED = "EXCLUDED_FROM_EVAL"
STATUS_HOLD = "SOURCE_MISSING_HOLD"
NON_TARGET_STATUSES = ("PENDING_CONTENT", STATUS_EXCLUDED, STATUS_HOLD)
WS = re.compile(r"\s+")
#: Segment eşiği bu adaylardan seçilir: boşlukların en fazla %1'inin aştığı en küçük değer.
GAP_CANDIDATES_MINUTES = (15, 30, 60, 120, 240, 480, 720, 1440)
GAP_TAIL_LIMIT = 0.01
#: Aynı yön + aynı metin bu kadar saniye içinde yineleniyorsa dışa aktarım kopyasıdır
#: (kaynakta aynı saniyede ardışık message_id'lerle 3-4 kez kayıtlı satırlar var).
EXPORT_DUPLICATE_WINDOW_SECONDS = 2
OUTPUT_FILES = (
    "session-gold-v2.jsonl",
    "context-resolutions.jsonl",
    "unresolved-context.jsonl",
    "session-targets.jsonl",
    "excluded-from-eval.jsonl",
    "source-missing-hold.jsonl",
    "multi-intent-targets.jsonl",
    "session-gold-report.json",
    "SESSION-GOLD-REPORT.md",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


#: Türkçe büyük harfler casefold'dan ÖNCE eşlenir: 'İ'.casefold() 'i' + U+0307
#: (birleşen nokta) üretiyor ve 'ÇÖZÜM MERKEZİ' ile 'Çözüm merkezi' eşleşmiyordu.
TURKISH_UPPER = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})
COMBINING_DOT_ABOVE = "\u0307"
NORMALIZATION_VERSION = "tr-normalize-v2"


def normalize(text: str) -> str:
    """Eşleştirme için kanonik biçim (yalnız karşılaştırmada kullanılır).

    Sıra: NFC → Türkçe büyük harf eşlemesi → casefold → NFC → artakalan
    birleşen nokta temizliği → boşluk sadeleştirme.
    """
    value = unicodedata.normalize("NFC", text or "")
    value = value.translate(TURKISH_UPPER).casefold()
    value = unicodedata.normalize("NFC", value).replace(COMBINING_DOT_ABOVE, "")
    return WS.sub(" ", value).strip()


# --------------------------------------------------------------------------
# Dedup + segmentasyon
# --------------------------------------------------------------------------


def is_conversational(message: dict[str, Any]) -> bool:
    """Konuşma turn'ü: kullanıcının gold filtresinden geçen metni ya da botun
    metin cevabı. Widget açma/kapama, sayfa değişimi, quick-reply ve KVKK gibi
    arayüz olayları bağlam sayılmaz."""
    if message["direction"] == "Kullanıcı":
        return bool(message.get("is_gold_user_turn"))
    return message.get("message_type") == "text"


def dedup_messages(sessions: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Message ID öncelikli tekilleştirme; gerçek kullanıcı tekrarları korunur."""
    cleaned: dict[str, list[dict[str, Any]]] = {}
    dropped_by_message_id = 0
    dropped_export_duplicates = 0
    kept_real_repeats = 0
    global_ids: Counter[int] = Counter()
    for session in sessions:
        seen_ids: set[int] = set()
        last_seen: dict[tuple[str, str], datetime | None] = {}
        messages = []
        for message in session["messages"]:
            message_id = message.get("message_id")
            if message_id is not None:
                global_ids[message_id] += 1
                if message_id in seen_ids:
                    dropped_by_message_id += 1
                    continue
                seen_ids.add(message_id)
            key = (message["direction"], normalize(message["text"]))
            current = parse_time(message["time"])
            previous = last_seen.get(key, "missing")
            if previous != "missing":
                if previous and current and (current - previous).total_seconds() <= EXPORT_DUPLICATE_WINDOW_SECONDS:
                    # Aynı saniyede yinelenen kayıt: insan tekrarı değil, dışa aktarım kopyası.
                    dropped_export_duplicates += 1
                    continue
                kept_real_repeats += 1
            last_seen[key] = current
            messages.append(message)
        cleaned[session["session_id"]] = messages
    return cleaned, {
        "dropped_duplicate_message_id": dropped_by_message_id,
        "dropped_export_duplicates_within_window": dropped_export_duplicates,
        "export_duplicate_window_seconds": EXPORT_DUPLICATE_WINDOW_SECONDS,
        "kept_real_repeats_outside_window": kept_real_repeats,
        "message_ids_seen_in_multiple_sessions": sum(1 for _, count in global_ids.items() if count > 1),
    }


def gap_statistics(messages_by_session: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    gaps: list[float] = []
    for messages in messages_by_session.values():
        times = [parse_time(m["time"]) for m in messages]
        for previous, current in zip(times, times[1:]):
            if previous and current:
                gaps.append(max((current - previous).total_seconds() / 60.0, 0.0))
    gaps.sort()

    def quantile(fraction: float) -> float:
        if not gaps:
            return 0.0
        return gaps[min(int(fraction * (len(gaps) - 1)), len(gaps) - 1)]

    stats = {
        "gap_count": len(gaps),
        "median_minutes": quantile(0.5),
        "p90_minutes": quantile(0.9),
        "p95_minutes": quantile(0.95),
        "p99_minutes": quantile(0.99),
        "max_minutes": gaps[-1] if gaps else 0.0,
        "tail_fractions": {str(c): sum(1 for g in gaps if g > c) / len(gaps) if gaps else 0.0 for c in GAP_CANDIDATES_MINUTES},
    }
    threshold = next((c for c in GAP_CANDIDATES_MINUTES if stats["tail_fractions"][str(c)] <= GAP_TAIL_LIMIT),
                     GAP_CANDIDATES_MINUTES[-1])
    stats["selected_threshold_minutes"] = threshold
    stats["rule"] = (f"Ardışık iki mesaj arası {threshold} dakikadan uzunsa yeni segment başlar "
                     f"(boşlukların %{GAP_TAIL_LIMIT * 100:.0f}'inden azı bu eşiği aşıyor).")
    return stats


def segment_session(messages: list[dict[str, Any]], threshold_minutes: int) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    for message in messages:
        current_time = parse_time(message["time"])
        if current and previous_time and current_time and (current_time - previous_time).total_seconds() / 60.0 > threshold_minutes:
            segments.append(current)
            current = []
        current.append(message)
        if current_time:
            previous_time = current_time
    if current:
        segments.append(current)
    return segments


# --------------------------------------------------------------------------
# Vaka → gerçek oturum eşlemesi
# --------------------------------------------------------------------------


def choose_occurrence(occurrences: list[dict[str, Any]], *, prefer_context: bool = False) -> tuple[dict[str, Any] | None, str]:
    """Deterministik geçiş seçimi.

    READY vakada bağlam gerekmiyor; en temiz yerleşim ilk turn olan gerçek
    geçiştir. CONTEXT_REQUIRED vakada ise insan kararı zaten "geçmiş olmadan
    anlaşılmıyor" dediği için önünde gerçek turn bulunan geçiş aranır.
    Hiçbir durumda turn uydurulmaz; yalnız var olan geçişler arasından seçilir.
    """
    if not occurrences:
        return None, "no_occurrence"
    if len(occurrences) == 1:
        return occurrences[0], "unique_occurrence"
    ordered = sorted(occurrences, key=lambda o: (o["session_id"], int(o["turn_index"])))
    if prefer_context:
        with_context = [o for o in ordered if int(o["turn_index"]) > 1]
        if with_context:
            return max(with_context, key=lambda o: (int(o["turn_index"]), o["session_id"])), "ambiguous_context_preferred"
        return ordered[0], "ambiguous_lowest_session_turn"
    first_turns = [o for o in ordered if int(o["turn_index"]) == 1]
    if first_turns:
        return first_turns[0], "ambiguous_first_turn_preferred"
    return ordered[0], "ambiguous_lowest_session_turn"


def locate_target(segments: list[list[dict[str, Any]]], turn_index: int, message_text: str) -> tuple[int, int, str] | None:
    """Hedef turn'ü METİNLE bulur.

    ``alias-session-matches`` turn_index'i, turn'lerin " / " ile birleştirilip
    yeniden bölünmesinden geliyor; bir mesajın içinde " / " geçtiğinde sıra
    kayıyor. Bu yüzden eşleşme metin üzerinden yapılır, turn_index yalnız
    aynı metin birden çok kez geçtiğinde ipucu olarak kullanılır.
    """
    wanted = normalize(message_text)
    exact = [(si, pi) for si, segment in enumerate(segments) for pi, m in enumerate(segment)
             if m["direction"] == "Kullanıcı" and normalize(m["text"]) == wanted]
    if exact:
        hinted = [(si, pi) for si, pi in exact if segments[si][pi].get("user_turn_index") == turn_index]
        segment_index, position = (hinted or exact)[0]
        return segment_index, position, "exact_text" if len(exact) == 1 else "exact_text_repeated"
    contained = [(si, pi) for si, segment in enumerate(segments) for pi, m in enumerate(segment)
                 if m["direction"] == "Kullanıcı" and wanted and wanted in normalize(m["text"])]
    if contained:
        segment_index, position = contained[0]
        return segment_index, position, "gold_message_is_fragment_of_real_turn"
    return None


def turn_record(message: dict[str, Any], turn_index: int) -> dict[str, Any]:
    return {
        "turn_index": turn_index,
        "role": "user" if message["direction"] == "Kullanıcı" else "assistant",
        "text": message["text"],
        "timestamp": message["time"],
        "message_id": message["message_id"],
        "message_order": message["message_order"],
        "user_turn_index": message.get("user_turn_index"),
        "case_id": None,
        "is_evaluation_target": False,
    }


def build(output_dir: Path, gold_dir: Path = GOLD_DIR) -> dict[str, Any]:
    gold_dir = (ROOT / gold_dir).resolve() if not gold_dir.is_absolute() else gold_dir
    reviewed = (gold_dir / "gold-v2-reviewed-all.jsonl").exists()
    gold = {int(r["case_id"]): r for r in read_jsonl(
        gold_dir / ("gold-v2-reviewed-all.jsonl" if reviewed else "gold-v2-all.jsonl"))}
    gold_manifest = json.loads(
        (gold_dir / ("gold-v2-reviewed-manifest.json" if reviewed else "gold-v2-manifest.json")).read_text(encoding="utf-8"))
    alias = {r["alias_id"]: r for r in read_jsonl(SOURCES / "alias-session-matches.jsonl")}
    sessions = read_jsonl(EXTRACT_DIR / "sessions-extract.jsonl")
    extract_report = json.loads((EXTRACT_DIR / "extract-report.json").read_text(encoding="utf-8"))

    messages_by_session, dedup = dedup_messages(sessions)
    gaps = gap_statistics(messages_by_session)
    threshold = gaps["selected_threshold_minutes"]
    conversational_by_session = {sid: [m for m in messages if is_conversational(m)]
                                 for sid, messages in messages_by_session.items()}
    segments_by_session = {sid: segment_session(messages, threshold)
                           for sid, messages in conversational_by_session.items()}

    assignments: dict[int, dict[str, Any]] = {}
    for case_id, record in sorted(gold.items()):
        match = alias[record["alias_id"]]
        occurrence, policy = choose_occurrence(match.get("occurrences") or [],
                                               prefer_context=record["status"] == "CONTEXT_REQUIRED")
        assignment: dict[str, Any] = {
            "case_id": case_id, "status": record["status"], "policy": policy,
            "match_status": match.get("match_status"), "occurrence_count": len(match.get("occurrences") or []),
        }
        if occurrence is None:
            assignment.update(session_id=None, resolution="SOURCE_NOT_FOUND")
            assignments[case_id] = assignment
            continue
        session_id = occurrence["session_id"]
        located = locate_target(segments_by_session.get(session_id, []), int(occurrence["turn_index"]),
                                record["user_message"])
        if located is None:
            assignment.update(session_id=session_id, resolution="SOURCE_NOT_FOUND",
                              note="Gold mesajı bu oturumun kullanıcı turn'lerinde bulunamadı")
            assignments[case_id] = assignment
            continue
        segment_index, position, match_method = located
        segment = segments_by_session[session_id][segment_index]
        prior_user_turns = sum(1 for m in segment[:position] if m["direction"] == "Kullanıcı")
        assignment.update(session_id=session_id, segment_index=segment_index, position=position,
                          turn_index_in_source=int(occurrence["turn_index"]), match_method=match_method,
                          prior_turns=position, prior_user_turns=prior_user_turns, resolution="LOCATED")
        assignments[case_id] = assignment

    # Segment başına hedefler
    targets_by_segment: dict[tuple[str, int], list[int]] = defaultdict(list)
    for case_id, assignment in assignments.items():
        if assignment["resolution"] != "LOCATED" or case_id == PENDING_CASE:
            continue
        if assignment["status"] in NON_TARGET_STATUSES:
            continue  # insan kararıyla test dışı (excluded) ya da kaynağı yok (hold)
        if assignment["status"] == "CONTEXT_REQUIRED" and assignment["prior_user_turns"] == 0:
            continue  # önünde gerçek kullanıcı turn'ü yok; uydurma yapılmaz
        targets_by_segment[(assignment["session_id"], assignment["segment_index"])].append(case_id)

    evaluation_sessions: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    placed: dict[int, dict[str, Any]] = {}

    for (session_id, segment_index), case_ids in sorted(targets_by_segment.items()):
        segment = segments_by_session[session_id][segment_index]
        turns = [turn_record(message, index) for index, message in enumerate(segment)]
        session_meta = next(s for s in sessions if s["session_id"] == session_id)
        for case_id in sorted(case_ids):
            assignment = assignments[case_id]
            record = gold[case_id]
            turn = turns[assignment["position"]]
            turn.update(case_id=case_id, is_evaluation_target=True, expected_qna_ids=record["expected_qna_ids"],
                        expected_intent_groups=[i["accepted_qna_ids"] for i in record["expected_intents"]],
                        multi_intent=record["multi_intent"])
            prior = [t for t in turns[:assignment["position"]]]
            prior_user = [t for t in prior if t["role"] == "user"]
            turn_type = (
                "FIRST_TURN" if not prior_user else
                "FOLLOW_UP_CONTEXT_REQUIRED" if record["status"] == "CONTEXT_REQUIRED" else
                "FOLLOW_UP_CONTEXT_AVAILABLE_BUT_NOT_REQUIRED"
            )
            placed[case_id] = {
                "evaluation_session_id": f"{session_id}#s{segment_index}",
                "turn_index": turn["turn_index"], "turn_type": turn_type,
                "context_turn_ids": [t["turn_index"] for t in prior],
            }
            target_rows.append({
                "evaluation_session_id": f"{session_id}#s{segment_index}",
                "source_session_id": session_id,
                "segment_index": segment_index,
                "turn_index": turn["turn_index"],
                "case_id": case_id,
                "expected_qna_ids": record["expected_qna_ids"],
                "expected_qna_refs": record["expected_qna_refs"],
                "gold_status": record["status"],
                "turn_type": turn_type,
                "context_turn_ids": [t["turn_index"] for t in prior],
                "context_length": len(prior),
                "context_user_turns": sum(1 for t in prior if t["role"] == "user"),
                "context_assistant_turns": sum(1 for t in prior if t["role"] == "assistant"),
                "temporal": record["temporal"],
                "routing_guarded": record["routing_guarded"],
                "guard_refs": record["guard_refs"],
                "as_of_date": record["as_of_date"],
                "temporal_meta": record["temporal_meta"],
                "expected_intent_groups": [i["accepted_qna_ids"] for i in record["expected_intents"]],
                "provenance": assignments[case_id]["policy"],
                "target_match_method": assignments[case_id].get("match_method"),
                "multi_intent": record["multi_intent"],
            })
        evaluation_sessions.append({
            "evaluation_session_id": f"{session_id}#s{segment_index}",
            "source_session_id": session_id,
            "segment_index": segment_index,
            "segment_count_in_source": len(segments_by_session[session_id]),
            "channel": session_meta["channel"],
            "session_start": session_meta["session_start"],
            "first_timestamp": segment[0]["time"],
            "last_timestamp": segment[-1]["time"],
            "case_ids": sorted(case_ids),
            "turns": turns,
        })

    # Kaynağı bulunamayan READY vakalar: gerçek mesaj, bağlamsız tek turn
    standalone = []
    for case_id, assignment in sorted(assignments.items()):
        if case_id == PENDING_CASE or case_id in placed or assignment["status"] != "READY":
            continue
        record = gold[case_id]
        turn = {
            "turn_index": 0, "role": "user", "text": record["user_message"], "timestamp": None,
            "message_id": None, "message_order": None, "user_turn_index": None,
            "case_id": case_id, "is_evaluation_target": True, "expected_qna_ids": record["expected_qna_ids"],
            "expected_intent_groups": [i["accepted_qna_ids"] for i in record["expected_intents"]],
            "multi_intent": record["multi_intent"],
        }
        session_id = f"standalone-case-{case_id}"
        evaluation_sessions.append({
            "evaluation_session_id": session_id, "source_session_id": None, "segment_index": 0,
            "segment_count_in_source": 0, "channel": None, "session_start": None,
            "first_timestamp": None, "last_timestamp": None, "case_ids": [case_id], "turns": [turn],
            "note": "Gerçek oturum kaydı bulunamadı; bağlamsız tek turn (mesaj gerçek, geçmiş uydurulmadı)",
        })
        placed[case_id] = {"evaluation_session_id": session_id, "turn_index": 0, "turn_type": "FIRST_TURN",
                           "context_turn_ids": []}
        target_rows.append({
            "evaluation_session_id": session_id, "source_session_id": None, "segment_index": 0,
            "turn_index": 0, "case_id": case_id, "expected_qna_ids": record["expected_qna_ids"],
            "expected_qna_refs": record["expected_qna_refs"], "gold_status": record["status"],
            "turn_type": "FIRST_TURN", "context_turn_ids": [], "context_length": 0,
            "context_user_turns": 0, "context_assistant_turns": 0, "temporal": record["temporal"],
            "routing_guarded": record["routing_guarded"], "guard_refs": record["guard_refs"],
            "as_of_date": record["as_of_date"], "temporal_meta": record["temporal_meta"],
            "provenance": "no_source_session", "target_match_method": None, "multi_intent": record["multi_intent"],
            "expected_intent_groups": [i["accepted_qna_ids"] for i in record["expected_intents"]],
        })
        standalone.append(case_id)

    evaluation_sessions.sort(key=lambda s: s["evaluation_session_id"])
    target_rows.sort(key=lambda t: (t["case_id"],))

    # Bağlam vakalarının çözüm kayıtları
    context_rows, unresolved_rows = [], []
    for case_id, record in sorted(gold.items()):
        if record["status"] != "CONTEXT_REQUIRED":
            continue
        assignment = assignments[case_id]
        placement = placed.get(case_id)
        session_id = assignment.get("session_id")
        segment = (segments_by_session.get(session_id) or [None] * (assignment.get("segment_index", 0) + 1))[
            assignment["segment_index"]] if placement else None
        context_turns = []
        if placement and segment:
            context_turns = [
                {"turn_index": index, "role": "user" if m["direction"] == "Kullanıcı" else "assistant",
                 "text": m["text"], "timestamp": m["time"], "message_id": m["message_id"]}
                for index, m in enumerate(segment[:assignment["position"]])
            ]
        prior_user_turns = [t for t in context_turns if t["role"] == "user"]
        resolution = (
            "RESOLVED_FROM_REAL_SESSION" if prior_user_turns else
            "SOURCE_NOT_FOUND" if assignment["resolution"] == "SOURCE_NOT_FOUND" else
            "INSUFFICIENT_REAL_CONTEXT"
        )
        row = {
            "case_id": case_id,
            "session_id": session_id,
            "evaluation_session_id": placement["evaluation_session_id"] if placement else None,
            "target_turn_id": segment[assignment["position"]]["message_id"] if placement and segment else None,
            "target_turn_index": placement["turn_index"] if placement else None,
            "target_message": record["user_message"],
            "required_context_found": bool(prior_user_turns),
            "prior_user_turn_count": len(prior_user_turns),
            "context_turn_ids": [t["message_id"] for t in context_turns],
            "context_turns": context_turns,
            "expected_qna_ids": record["expected_qna_ids"],
            "why_context_required": record["context"]["reason"],
            "source_file": str((EXTRACT_DIR / "sessions-extract.jsonl").relative_to(ROOT)) if placement else None,
            "source_evidence": {
                "match_status": assignment["match_status"], "occurrence_count": assignment["occurrence_count"],
                "assignment_policy": assignment["policy"],
                "source_session_turn_index": assignment.get("turn_index_in_source"),
                "review_session_source": record["context"].get("session_source"),
                "note": assignment.get("note"),
            },
            "resolution_status": resolution,
            "context_requirement_questionable": bool(prior_user_turns) and all(
                t["role"] == "assistant" or normalize(t["text"]) == normalize(record["user_message"]) for t in context_turns),
        }
        context_rows.append(row)
        if resolution != "RESOLVED_FROM_REAL_SESSION":
            unresolved_rows.append(row)

    audit_result = audit(gold, assignments, placed, evaluation_sessions, target_rows, context_rows, standalone)
    audit_result["normalization_version"] = NORMALIZATION_VERSION
    report = {
        "dataset_version": DATASET_VERSION,
        "gold": {"layer": "reviewed" if reviewed else "frozen-v2", "dir": str(gold_dir.relative_to(ROOT)),
                 "manifest_sha256": sha256(gold_dir / ("gold-v2-reviewed-manifest.json" if reviewed else "gold-v2-manifest.json")),
                 "gold_all_sha256": sha256(gold_dir / ("gold-v2-reviewed-all.jsonl" if reviewed else "gold-v2-all.jsonl")),
                 "counts": gold_manifest["counts"],
                 "review_workbook_sha256": gold_manifest.get("review_workbook", {}).get("sha256")},
        "sources": {
            "raw_workbook": extract_report["source"],
            "sessions_extract": {"file": str((EXTRACT_DIR / "sessions-extract.jsonl").relative_to(ROOT)),
                                 "blake2b": extract_report["extract_blake2b"],
                                 "sessions": extract_report["extracted_sessions"],
                                 "messages": extract_report["messages"]},
            "alias_matches_blake2b": extract_report["alias_matches"]["blake2b"],
        },
        "dedup": dedup,
        "segmentation": {**gaps, "source_sessions": len(messages_by_session),
                         "conversational_messages": sum(len(v) for v in conversational_by_session.values()),
                         "ui_events_dropped": sum(len(messages_by_session[sid]) - len(v)
                                                  for sid, v in conversational_by_session.items()),
                         "segments_total": sum(len(v) for v in segments_by_session.values()),
                         "sessions_split": sum(1 for v in segments_by_session.values() if len(v) > 1)},
        "assignment_policy": {
            "unique_occurrence": "tek kayıtlı geçiş doğrudan kullanılır",
            "ambiguous_first_turn_preferred": "aynı metin birden çok oturumda; ilk turn olan gerçek geçiş seçilir",
            "ambiguous_lowest_session_turn": "ilk turn yoksa (session_id, turn_index) sıralamasında ilk geçiş",
            "no_source_session": "kaynak oturumda bulunamayan READY vaka bağlamsız tek turn olarak yerleştirilir",
        },
        "audit": audit_result,
    }
    excluded_rows = [
        {"case_id": cid, "status": r["status"], "user_message": r["user_message"],
         "exclusion_reason": r.get("exclusion_reason"), "review": r.get("review"),
         "previous_status": (r.get("review") or {}).get("previous_status")}
        for cid, r in sorted(gold.items()) if r["status"] == STATUS_EXCLUDED
    ]
    hold_rows = [
        {"case_id": cid, "status": r["status"], "user_message": r["user_message"],
         "hold_type": r.get("hold_type", "CONTENT_PENDING" if r["status"] == "PENDING_CONTENT" else None),
         "hold_reason": r.get("hold_reason") or r.get("notes"), "review": r.get("review"),
         "blocks_freeze": False}
        for cid, r in sorted(gold.items()) if r["status"] in (STATUS_HOLD, "PENDING_CONTENT")
    ]
    multi_rows = [t for t in target_rows if t["multi_intent"]]
    files = {
        "session-gold-v2.jsonl": jsonl(evaluation_sessions),
        "context-resolutions.jsonl": jsonl(context_rows),
        "unresolved-context.jsonl": jsonl(unresolved_rows),
        "session-targets.jsonl": jsonl(target_rows),
        "excluded-from-eval.jsonl": jsonl(excluded_rows),
        "source-missing-hold.jsonl": jsonl(hold_rows),
        "multi-intent-targets.jsonl": jsonl(multi_rows),
        "session-gold-report.json": dump(report),
        "SESSION-GOLD-REPORT.md": render_markdown(report),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    return {"report": report, "sessions": evaluation_sessions, "targets": target_rows, "context": context_rows}


def audit(gold, assignments, placed, sessions, targets, context_rows, standalone) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    ready = {cid for cid, r in gold.items() if r["status"] == "READY"}
    context_cases = {cid for cid, r in gold.items() if r["status"] == "CONTEXT_REQUIRED"}
    target_cases = Counter(t["case_id"] for t in targets)

    missing_ready = sorted(ready - set(target_cases))
    if missing_ready:
        blockers.append({"check": "ready_case_not_a_target", "cases": missing_ready})
    duplicated = sorted(c for c, n in target_cases.items() if n > 1)
    if duplicated:
        blockers.append({"check": "case_targeted_more_than_once", "cases": duplicated})
    if PENDING_CASE in target_cases:
        blockers.append({"check": "pending_case_is_target", "case": PENDING_CASE})

    # Leakage: bağlam yalnız hedeften önceki turn'ler
    for target in targets:
        if target["context_turn_ids"] and max(target["context_turn_ids"]) >= target["turn_index"]:
            blockers.append({"check": "context_after_target", "case": target["case_id"]})
        if target["context_turn_ids"] != list(range(target["turn_index"])):
            blockers.append({"check": "context_not_full_prefix", "case": target["case_id"]})

    # Oturum yapısı
    for session in sessions:
        indexes = [t["turn_index"] for t in session["turns"]]
        if indexes != sorted(indexes) or indexes != list(range(len(indexes))):
            blockers.append({"check": "turn_order_not_monotonic", "session": session["evaluation_session_id"]})
        times = [t["timestamp"] for t in session["turns"] if t["timestamp"]]
        if times != sorted(times):
            blockers.append({"check": "timestamps_not_monotonic", "session": session["evaluation_session_id"]})
        ids = [t["message_id"] for t in session["turns"] if t["message_id"] is not None]
        if len(ids) != len(set(ids)):
            blockers.append({"check": "duplicate_message_id_in_session", "session": session["evaluation_session_id"]})
        for turn in session["turns"]:
            if turn["is_evaluation_target"] and turn["expected_qna_ids"] != gold[turn["case_id"]]["expected_qna_ids"]:
                blockers.append({"check": "expected_qna_differs_from_gold", "case": turn["case_id"]})

    session_of_case = defaultdict(set)
    for session in sessions:
        for case_id in session["case_ids"]:
            session_of_case[case_id].add(session["evaluation_session_id"])
    multi_session = sorted(c for c, s in session_of_case.items() if len(s) > 1)
    if multi_session:
        blockers.append({"check": "case_in_multiple_evaluation_sessions", "cases": multi_session})

    resolved = [r for r in context_rows if r["resolution_status"] == "RESOLVED_FROM_REAL_SESSION"]
    unresolved = [r for r in context_rows if r["resolution_status"] != "RESOLVED_FROM_REAL_SESSION"]
    excluded = sorted(cid for cid, r in gold.items() if r["status"] == STATUS_EXCLUDED)
    hold = sorted(cid for cid, r in gold.items() if r["status"] == STATUS_HOLD)
    pending = sorted(cid for cid, r in gold.items() if r["status"] == "PENDING_CONTENT")
    multi_intent_cases = sorted({t["case_id"] for t in targets if t["multi_intent"]})
    for case_id in multi_intent_cases:
        groups = next(t for t in targets if t["case_id"] == case_id)["expected_intent_groups"]
        if len(groups) < 2 or any(len(g) != 1 for g in groups):
            blockers.append({"check": "multi_intent_groups_malformed", "case": case_id, "groups": groups})
    for case_id in excluded + hold + pending:
        if case_id in target_cases:
            blockers.append({"check": "non_eval_case_is_target", "case": case_id})
    multi_intent_review = sorted(
        {t["case_id"] for t in targets if gold[t["case_id"]]["split_audit"].get("split_label") == "Gerekli"}
        | {480}
    )
    return {
        "evaluation_sessions": len(sessions),
        "sessions_with_real_source": sum(1 for s in sessions if s["source_session_id"]),
        "standalone_sessions": len(standalone),
        "targets": len(targets),
        "turn_types": dict(sorted(Counter(t["turn_type"] for t in targets).items())),
        "provenance": dict(sorted(Counter(t["provenance"] for t in targets).items())),
        "target_match_method": dict(sorted(Counter(str(t.get("target_match_method")) for t in targets).items())),
        "gold_message_fragment_cases": sorted(t["case_id"] for t in targets
                                              if t.get("target_match_method") == "gold_message_is_fragment_of_real_turn"),
        "ready_cases": len(ready),
        "ready_cases_targeted": len(ready & set(target_cases)),
        "context_cases": len(context_cases),
        "context_resolved": len(resolved),
        "excluded_from_eval": excluded,
        "source_missing_hold": hold,
        "pending_content": pending,
        "multi_intent_cases": multi_intent_cases,
        "evaluation_ready_cases": sorted(target_cases),
        "context_unresolved": [{"case_id": r["case_id"], "resolution_status": r["resolution_status"],
                                "match_status": r["source_evidence"]["match_status"],
                                "source_session_turn_index": r["source_evidence"]["source_session_turn_index"]}
                               for r in unresolved],
        "context_requirement_questionable": sorted(r["case_id"] for r in context_rows if r["context_requirement_questionable"]),
        "multi_intent_review_required_legacy_flag": multi_intent_review,
        "pending_case_excluded": PENDING_CASE not in target_cases,
        "blockers": blockers,
        "freeze": {
            "frozen": not blockers and not unresolved and len(ready & set(target_cases)) == len(ready),
            "requires": ["no_blockers", "all_context_cases_resolved", "all_ready_cases_targeted"],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    a = report["audit"]
    s = report["segmentation"]
    lines = [
        f"# Session-gold v2 — {'FREEZE PASS' if a['freeze']['frozen'] else 'FREEZE BEKLİYOR'}", "",
        f"- Kaynak oturum: {s['source_sessions']} · segment: {s['segments_total']} · bölünen oturum: {s['sessions_split']}",
        f"- Evaluation session: {a['evaluation_sessions']} (gerçek kaynaklı {a['sessions_with_real_source']}, bağlamsız {a['standalone_sessions']})",
        f"- Evaluation target: {a['targets']} · READY hedeflenen: {a['ready_cases_targeted']}/{a['ready_cases']}",
        f"- Turn tipleri: {a['turn_types']}",
        f"- Bağlam vakası çözülen: {a['context_resolved']}/{a['context_cases']}",
        f"- Vaka {PENDING_CASE} hedef dışında: {a['pending_case_excluded']}",
        f"- Test dışı (insan kararı): {a['excluded_from_eval']} · kaynak yok (hold): {a['source_missing_hold']} · "
        f"içerik bekleyen: {a['pending_content']}",
        f"- Çoklu niyet hedefleri: {a['multi_intent_cases']}",
        f"- Normalizasyon: {a['normalization_version']}",
        f"- Segment kuralı: {s['rule']}",
        f"- Boşluk dağılımı (dk): medyan {s['median_minutes']:.2f} · p90 {s['p90_minutes']:.2f} · p95 {s['p95_minutes']:.2f} · p99 {s['p99_minutes']:.2f} · max {s['max_minutes']:.1f}",
        "", "## Blokajlar", "",
        *([f"- `{b['check']}`: {json.dumps({k: v for k, v in b.items() if k != 'check'}, ensure_ascii=False)}" for b in a["blockers"]] or ["- Yok"]),
        "", "## Çözülemeyen bağlam vakaları", "",
        *([f"- Vaka {u['case_id']}: {u['resolution_status']} (eşleşme: {u['match_status']}, kaynak turn: {u['source_session_turn_index']})"
           for u in a["context_unresolved"]] or ["- Yok"]),
        "", "## İnceleme gerektiren multi-intent adayları", "",
        f"- Karar verilmiş çoklu niyet: {a['multi_intent_cases']} · geçmiş splitter etiketi taşıyanlar: "
        f"{a['multi_intent_review_required_legacy_flag']}", "",
    ]
    return "\n".join(lines)


def write_manifest(output_dir: Path, report: dict[str, Any], created_at: str) -> dict[str, Any]:
    gold_manifest = json.loads((GOLD_DIR / "gold-v2-manifest.json").read_text(encoding="utf-8"))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    a = report["audit"]
    manifest = {
        "dataset_version": DATASET_VERSION,
        "created_at": created_at,
        "frozen": a["freeze"]["frozen"],
        "gold_v2_manifest_sha256": sha256(GOLD_DIR / "gold-v2-manifest.json"),
        "reviewed_gold_manifest_sha256": report["gold"]["manifest_sha256"],
        "reviewed_gold_all_sha256": report["gold"]["gold_all_sha256"],
        "review_workbook_sha256": report["gold"].get("review_workbook_sha256"),
        "gold_v2_source_commit": gold_manifest["source_git"]["commit"],
        "kb_migration_report_sha256": gold_manifest["migration_report_sha256"],
        "canonical_baseline_sha256": gold_manifest["canonical_baseline_sha256"],
        "session_sources": {
            "raw_workbook_blake2b": report["sources"]["raw_workbook"]["blake2b"],
            "sessions_extract_blake2b": report["sources"]["sessions_extract"]["blake2b"],
            "alias_matches_blake2b": report["sources"]["alias_matches_blake2b"],
        },
        "builder_commit": {"commit": head, "tracked_changes": bool(dirty)},
        "counts": {
            "evaluation_sessions": a["evaluation_sessions"],
            "targets": a["targets"],
            "evaluation_ready_cases": len(a["evaluation_ready_cases"]),
            "first_turn": a["turn_types"].get("FIRST_TURN", 0),
            "follow_up_context_required": a["turn_types"].get("FOLLOW_UP_CONTEXT_REQUIRED", 0),
            "follow_up_context_available": a["turn_types"].get("FOLLOW_UP_CONTEXT_AVAILABLE_BUT_NOT_REQUIRED", 0),
            "standalone_targets": a["standalone_sessions"],
            "contextual_targets": a["turn_types"].get("FOLLOW_UP_CONTEXT_REQUIRED", 0),
            "context_resolved": a["context_resolved"],
            "context_unresolved": len(a["context_unresolved"]),
            "excluded_from_eval": len(a["excluded_from_eval"]),
            "source_missing_hold": len(a["source_missing_hold"]),
            "pending_content": len(a["pending_content"]),
            "multi_intent": len(a["multi_intent_cases"]),
        },
        "case_ids": {"excluded_from_eval": a["excluded_from_eval"], "source_missing_hold": a["source_missing_hold"],
                     "pending_content": a["pending_content"], "multi_intent": a["multi_intent_cases"]},
        "normalization_version": a["normalization_version"],
        "gold_layer": report["gold"],
        "segmentation_policy": report["segmentation"]["rule"],
        "outputs_sha256": {name: sha256(output_dir / name) for name in OUTPUT_FILES},
    }
    (output_dir / "session-gold-manifest.json").write_text(dump(manifest), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "session-gold-v2-final-20260917")
    parser.add_argument("--gold-dir", type=Path, default=GOLD_DIR,
                        help="Reviewed Gold katmanı (varsayılan: dondurulmuş Gold v2)")
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--verify-rebuild", action="store_true")
    return parser.parse_args()


def main() -> int:
    options = parse_args()
    result = build(options.output_dir, options.gold_dir)
    rebuild = None
    if options.verify_rebuild:
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp), options.gold_dir)
            rebuild = {name: sha256(Path(tmp) / name) == sha256(options.output_dir / name) for name in OUTPUT_FILES}
    manifest = write_manifest(options.output_dir, result["report"], options.created_at)
    a = result["report"]["audit"]
    print(json.dumps({
        "frozen": manifest["frozen"], "evaluation_sessions": a["evaluation_sessions"], "targets": a["targets"],
        "turn_types": a["turn_types"], "context_resolved": f"{a['context_resolved']}/{a['context_cases']}",
        "context_unresolved": a["context_unresolved"], "blockers": len(a["blockers"]),
        "rebuild_byte_identical": rebuild, "output_dir": str(options.output_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["frozen"] and (rebuild is None or all(rebuild.values())) else 2


if __name__ == "__main__":
    raise SystemExit(main())
