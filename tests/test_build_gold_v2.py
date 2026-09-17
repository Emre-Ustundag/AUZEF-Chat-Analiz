from __future__ import annotations

import json

from scripts.build_gold_v2 import (
    apply_simple_review_result,
    load_baseline_aliases,
    merge_reviewed_cases,
    parse_id_groups,
    validate_rows,
)

CATALOG = {
    10: {"id": 10, "question": "Q10", "answer": "A10"},
    11: {"id": 11, "question": "Q11", "answer": "A11"},
    12: {"id": 12, "question": "Q12", "answer": "A12"},
}


def row(**changes) -> dict:
    base = {
        "_row": 5,
        "Vaka": 1,
        "Öğrenci mesajı": "Birinci ve ikinci sorum",
        "Mevcut gold soru": "Eski soru",
        "Gold QnA ID": "10",
        "Veri sorunu": "Belirgin veri sorunu yok",
        "Split sayısı": 1,
        "Split kararı": "Bölünmedi",
        "İnsan kararı": "4o daha iyi",
        "Kabul edilen QnA ID'leri": "10",
        "İnceleme notu": "",
    }
    return {**base, **changes}


def test_parse_id_groups_preserves_intents_and_sorts_alternatives() -> None:
    assert parse_id_groups("11, 10 | 12") == [[10, 11], [12]]


def test_ready_case_contains_catalog_answers() -> None:
    cases, errors, statuses = validate_rows([row()], {}, CATALOG)

    assert errors == []
    assert statuses == {"ready": 1}
    assert cases[0]["intents"][0]["accepted_answers"] == ["A10"]


def test_required_split_needs_one_id_group_per_intent() -> None:
    rows = [
        row(
            **{
                "Split sayısı": 2,
                "Split kararı": "Gerekli",
                "Kabul edilen QnA ID'leri": "10,11 | 12",
            }
        )
    ]
    cases, errors, _ = validate_rows(
        rows, {1: ["İlk niyet", "İkinci niyet"]}, CATALOG
    )

    assert errors == []
    assert [intent["accepted_qna_ids"] for intent in cases[0]["intents"]] == [
        [10, 11],
        [12],
    ]


def test_missing_human_decision_produces_one_blocking_error() -> None:
    _, errors, statuses = validate_rows(
        [row(**{"İnsan kararı": "", "Kabul edilen QnA ID'leri": ""})],
        {},
        CATALOG,
    )

    assert [error["code"] for error in errors] == ["missing_human_decision"]
    assert statuses == {"invalid": 1}


def test_unknown_qna_id_is_rejected() -> None:
    _, errors, _ = validate_rows(
        [row(**{"Kabul edilen QnA ID'leri": "999"})], {}, CATALOG
    )

    assert [error["code"] for error in errors] == ["unknown_qna_ids"]


def test_context_and_excluded_cases_require_notes_but_no_ids() -> None:
    rows = [
        row(
            **{
                "Vaka": 1,
                "İnsan kararı": "Bağlam olmadan değerlendirilemez",
                "Kabul edilen QnA ID'leri": "",
                "İnceleme notu": "Önceki öğrenci mesajı gerekli",
            }
        ),
        row(
            **{
                "Vaka": 2,
                "İnsan kararı": "Testten çıkar",
                "Kabul edilen QnA ID'leri": "",
                "İnceleme notu": "Mesaj bozuk",
            }
        ),
    ]

    cases, errors, statuses = validate_rows(rows, {}, CATALOG)

    assert errors == []
    assert [case["status"] for case in cases] == ["needs_context", "excluded"]
    assert statuses == {"excluded": 1, "needs_context": 1}


def test_both_problematic_without_ids_becomes_needs_kb() -> None:
    cases, errors, statuses = validate_rows(
        [
            row(
                **{
                    "İnsan kararı": "İkisi de sorunlu",
                    "Kabul edilen QnA ID'leri": "",
                    "İnceleme notu": "Uygun QnA henüz yok",
                }
            )
        ],
        {},
        CATALOG,
    )

    assert errors == []
    assert cases[0]["status"] == "needs_kb"
    assert statuses == {"needs_kb": 1}


def test_technical_status_override_preserves_human_decision() -> None:
    cases, errors, statuses = validate_rows(
        [
            row(
                **{
                    "İnsan kararı": "Luna daha iyi",
                    "Kabul edilen QnA ID'leri": "",
                    "İnceleme notu": "Kısa ifade birden çok session'da geçiyor",
                    "_evaluation_status_override": "needs_context",
                }
            )
        ],
        {},
        CATALOG,
    )

    assert errors == []
    assert cases[0]["review_decision"] == "Luna daha iyi"
    assert cases[0]["status"] == "needs_context"
    assert statuses == {"needs_context": 1}


def test_baseline_aliases_resolve_to_active_catalog(tmp_path) -> None:
    source = tmp_path / "aliases.jsonl"
    source.write_text(
        '{"alias_id":"KB001-Q01","canonical_question":"Q10",'
        '"expected_answer":"A10","query_text":"öğrenci sorusu",'
        '"automatic_replay_eligible":true,"match_status":"unique_occurrence"}\n',
        encoding="utf-8",
    )

    cases = load_baseline_aliases(source, CATALOG)

    assert cases[0]["case_id"] == 1
    assert cases[0]["alias_id"] == "KB001-Q01"
    assert cases[0]["intents"][0]["accepted_qna_ids"] == [10]
    assert cases[0]["reviewed"] is False


def test_review_overrides_matching_baseline_case() -> None:
    baseline = [
        {
            "case_id": 1,
            "alias_id": "KB001-Q01",
            "student_message": "öğrenci sorusu",
            "status": "ready",
            "replay": {"automatic_replay_eligible": True},
        }
    ]
    review = {
        "case_id": 1,
        "source_row": 5,
        "student_message": "öğrenci sorusu",
        "status": "excluded",
        "review_decision": "Testten çıkar",
    }

    merged, errors = merge_reviewed_cases(baseline, [review])

    assert errors == []
    assert merged[0]["status"] == "excluded"
    assert merged[0]["reviewed"] is True
    assert merged[0]["alias_id"] == "KB001-Q01"


def test_simple_review_result_overlays_human_fields(tmp_path) -> None:
    result = tmp_path / "review.json"
    result.write_text(
        json.dumps(
            {
                "schema_version": "simple-gold-review-result-v1",
                "source_digest": "workbook-digest",
                "reviews": [
                    {
                        "case_no": 1,
                        "human_decision": "Luna daha iyi",
                        "accepted_qna_ids": "11, 12",
                        "review_note": "Kısa ve doğru",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    overlaid = apply_simple_review_result(
        [row(**{"İnsan kararı": "", "Kabul edilen QnA ID'leri": ""})],
        result,
        expected_source_digest="workbook-digest",
    )

    assert overlaid[0]["İnsan kararı"] == "Luna daha iyi"
    assert overlaid[0]["Kabul edilen QnA ID'leri"] == "11, 12"
    assert overlaid[0]["İnceleme notu"] == "Kısa ve doğru"
