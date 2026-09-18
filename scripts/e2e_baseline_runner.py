"""Session-gold v2 E2E baseline — backend konteyneri içinde çalışan koşucu.

``run_e2e_baseline.py`` bu kaynağı ``python -c`` ile gönderir. stdin: JSON
payload (model konfigürasyonu + hedef listesi); stdout: hedef başına bir
NDJSON satırı.

Gerçek cevap hattı (``services.answer_pipeline.answer_question``) aynen
çağrılır. Buradaki sarmalayıcılar YALNIZ GÖZLEM içindir: retrieval, bölme,
seçim, guard ve fallback davranışı değiştirilmez. Model yalnız seçici/bölücü
sağlayıcısında değişir; model kimliği üretim kodunda sabit olduğu için
sağlayıcı süreç içinde, iki model için aynı sınıfla değiştirilir.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from typing import Any


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Recorder:
    """Tek hedefin olaylarını toplar (pipeline içi thread'ler için kilitli)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.events: list[dict[str, Any]] = []
            self.api_errors: list[str] = []

    def add(self, event: dict[str, Any]) -> None:
        event["thread"] = "main" if threading.current_thread() is threading.main_thread() else "worker"
        event["t"] = time.perf_counter()
        with self.lock:
            self.events.append(event)


def install(payload: dict[str, Any], recorder: Recorder):
    import services.answer_pipeline as ap
    from services.llm_provider import OpenRouterProvider

    config = payload["model"]

    class BenchProvider(OpenRouterProvider):
        """Üretim sağlayıcısıyla aynı prompt ve parse; yalnız model kimliği,
        (Luna için zorunlu) reasoning parametresi ve kullanım kaydı eklenir."""

        def __init__(self) -> None:
            super().__init__(model=config["model_id"])

        def _complete(self, system: str, user: str, max_tokens: int = 5) -> str:
            kind = "split" if max_tokens == 300 else "select"
            effective = max(max_tokens, config.get("min_max_tokens") or 0)
            extra_body = {"usage": {"include": True}}
            if config.get("reasoning"):
                extra_body["reasoning"] = config["reasoning"]
            started = time.perf_counter()
            event: dict[str, Any] = {"kind": f"llm_{kind}", "prompt_sha": digest(system + "\n" + user),
                                     "max_tokens": effective}
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=effective,
                    temperature=0,
                    extra_body=extra_body,
                    timeout=config["timeout_seconds"],
                )
                usage = getattr(response, "usage", None)
                event.update(
                    returned_model=getattr(response, "model", None),
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    reasoning_tokens=getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None),
                    cost=getattr(usage, "cost", None) if usage is not None else None,
                )
                content = response.choices[0].message.content  # üretimle aynı: boş choices istisna fırlatır
                event.update(raw_output=content, latency=time.perf_counter() - started)
                recorder.add(event)
                return content
            except Exception as exc:  # noqa: BLE001 - üretim davranışı korunur, olay kaydedilir
                event.update(error=f"{type(exc).__name__}: {str(exc)[:300]}", latency=time.perf_counter() - started)
                recorder.add(event)
                recorder.api_errors.append(event["error"])
                raise

        def split_questions(self, message: str) -> list:
            result = super().split_questions(message)
            recorder.add({"kind": "split_result", "message": message, "sub_questions": list(result)})
            return result

        def ask(self, question: str, context_list: list):
            answer = super().ask(question, context_list)
            selected = next((c for c in context_list if c.get("answer") == answer), None) if answer else None
            recorder.add({
                "kind": "ask",
                "question": question,
                "candidates": [{"qna_id": c.get("qna_id"), "calendar": c.get("qna_id") is None,
                                "question": (c.get("question") or "")[:160]} for c in context_list],
                "selected_qna_id": selected.get("qna_id") if selected else None,
                "selected_calendar": bool(selected) and selected.get("qna_id") is None,
                "selected_question": (selected or {}).get("question"),
                "declined": answer is None,
            })
            return answer

    provider = BenchProvider()
    ap.get_llm_provider = lambda _db: provider

    original_qdrant_search = ap.QDRANT_PROVIDER.search
    original_meili = ap.meili_search_safe
    original_pool = ap._build_candidate_pool
    original_fallback = ap._fallback_answer
    original_calendar = ap.search_calendar

    def qdrant_search(query: str, limit: int = 3):
        started = time.perf_counter()
        hits = original_qdrant_search(query, limit=limit)
        recorder.add({"kind": "qdrant", "query": query, "limit": limit, "latency": time.perf_counter() - started,
                      "hits": [{"qna_id": h.get("qna_id"), "score": round(float(h.get("score") or 0), 5),
                                "matched_query": (h.get("matched_query") or "")[:120] or None} for h in hits]})
        return hits

    def meili_search(query: str, limit: int):
        started = time.perf_counter()
        hits = original_meili(query, limit)
        recorder.add({"kind": "meili", "query": query, "limit": limit, "latency": time.perf_counter() - started,
                      "hits": [{"qna_id": h.get("qna_id"), "score": round(float(h.get("score") or 0), 5)} for h in hits]})
        return hits

    def build_pool(query, calendar_entries, conversation_context=(), routing_policy=None):
        pool = original_pool(query, calendar_entries, conversation_context, routing_policy)
        recorder.add({"kind": "pool", "query": query,
                      "qna_ids": [c.get("qna_id") for c in pool if c.get("qna_id") is not None],
                      "calendar_candidates": sum(1 for c in pool if c.get("qna_id") is None)})
        return pool

    def fallback(query, db, use_calendar=True, routing_policy=None):
        answer, source = original_fallback(query, db, use_calendar=use_calendar, routing_policy=routing_policy)
        recorder.add({"kind": "fallback", "use_calendar": use_calendar, "source": source,
                      "answer_sha": digest(answer) if answer else None})
        return answer, source

    def calendar(query, db, use_llm):
        answer = original_calendar(query, db, use_llm)
        recorder.add({"kind": "calendar_gate", "matched": bool(answer)})
        return answer

    ap.QDRANT_PROVIDER.search = qdrant_search
    ap.meili_search_safe = meili_search
    ap._build_candidate_pool = build_pool
    ap._fallback_answer = fallback
    ap.search_calendar = calendar
    return ap, provider


def run(payload: dict[str, Any]) -> None:
    from sqlalchemy import text

    from core.database import SessionLocal, admin_engine
    from services.routing_guards import RoutingGuardPolicy

    recorder = Recorder()
    ap, provider = install(payload, recorder)
    with admin_engine.connect() as connection:
        answers = {}
        for row in connection.execute(text("SELECT id, answer_text FROM qna WHERE status = 1")).mappings():
            answers.setdefault(row["answer_text"], []).append(int(row["id"]))

    retry = payload["retry"]
    for target in payload["targets"]:
        context = tuple(target["context"])
        attempts = []
        for attempt in range(retry["max_retries"] + 1):
            recorder.reset()
            db = SessionLocal()
            started = time.perf_counter()
            error = None
            try:
                answer, source = ap.answer_question(target["user_message"], db, context)
            except Exception as exc:  # noqa: BLE001
                answer, source, error = None, "exception", f"{type(exc).__name__}: {exc}"[:300]
            finally:
                policy = RoutingGuardPolicy.load(db)
                guard_decisions = {str(qid): policy.decision(qid).__dict__ for qid in target["expected_qna_ids"]
                                   if qid in policy.guards}
                db.close()
            elapsed = time.perf_counter() - started
            attempts.append({"attempt": attempt, "api_errors": list(recorder.api_errors), "error": error})
            if not recorder.api_errors and error is None:
                break
            if attempt < retry["max_retries"]:
                time.sleep(retry["backoff_seconds"][attempt])
        origin = recorder.events[0]["t"] if recorder.events else started
        events = [{**e, "t": round(e["t"] - origin, 4)} for e in recorder.events]
        sys.stdout.write(json.dumps({
            "type": "result",
            "case_id": target["case_id"],
            "model_label": payload["model"]["label"],
            "answer": answer,
            "answer_qna_ids": answers.get(answer, []) if answer else [],
            "source": source,
            "error": error,
            "elapsed": round(elapsed, 4),
            "attempts": attempts,
            "retries_used": len(attempts) - 1,
            "unresolved_api_error": bool(attempts[-1]["api_errors"]) or attempts[-1]["error"] is not None,
            "guard_decisions": guard_decisions,
            "events": events,
        }, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def main() -> int:
    run(json.loads(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
