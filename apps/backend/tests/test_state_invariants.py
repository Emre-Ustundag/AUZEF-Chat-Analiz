"""Status'a ve sayısal özetlere bağlı çapraz alan kuralları."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisCreated, AnalysisJob, ModelList
from app.schemas.report import AnalysisReport, AnalysisWarning, percentage_half_up
from app.schemas.upload import Upload, UploadCreated
from tests.conftest import read_fixture


def _invalid(model: type[Any], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_created_responses_are_always_queued() -> None:
    upload = read_fixture("uploads.create.202.json") | {"status": "ready"}
    analysis = read_fixture("analyses.create.202.json") | {"status": "completed"}
    _invalid(UploadCreated, upload)
    _invalid(AnalysisCreated, analysis)


def test_upload_payload_matches_status() -> None:
    ready = read_fixture("uploads.get.200.ready.json")
    _invalid(Upload, ready | {"profile": None})
    _invalid(Upload, ready | {"status": "queued"})

    failed = read_fixture("uploads.get.200.failed.json")
    _invalid(Upload, failed | {"error": None})


def test_upload_profile_counts_are_consistent() -> None:
    ready = read_fixture("uploads.get.200.row-limit.json")
    profile = ready["profile"]
    _invalid(Upload, ready | {"profile": profile | {"total_row_count": 1}})

    sheets = [*profile["sheets"]]
    first = sheets[0]
    columns = [*first["columns"]]
    columns[0] = columns[0] | {"empty_count": columns[0]["empty_count"] + 1}
    sheets[0] = first | {"columns": columns}
    _invalid(Upload, ready | {"profile": profile | {"sheets": sheets}})


def test_analysis_job_payload_matches_status() -> None:
    active = read_fixture("analyses.get.200.analyzing.json")
    failed = read_fixture("analyses.get.200.failed.json")
    cancelled = read_fixture("analyses.get.200.cancelled.json")

    _invalid(AnalysisJob, failed | {"error": None})
    _invalid(AnalysisJob, active | {"error": failed["error"]})
    _invalid(AnalysisJob, cancelled | {"estimated_seconds_remaining": 5})


def test_model_list_defaults_are_consistent() -> None:
    catalog = read_fixture("models.list.200.json")
    _invalid(ModelList, catalog | {"default_model": "unknown/model"})
    _invalid(ModelList, catalog | {"models": [*catalog["models"], catalog["models"][0]]})


def test_report_numeric_invariants() -> None:
    report = read_fixture("analyses.result.200.truncated.json")
    prep = report["preprocessing_summary"]

    _invalid(
        AnalysisReport,
        report | {"preprocessing_summary": prep | {"analyzed_count": prep["analyzed_count"] + 1}},
    )
    _invalid(
        AnalysisReport,
        report | {"token_usage": report["token_usage"] | {"total_tokens": 1}},
    )
    # `estimated_cost_usd` BİLEREK burada değil: katalog fiyatına bağlı bir
    # cevap invariant'ı, fiyat değiştiğinde geçmiş raporları 500'e çevirirdi
    # (report.py alan docstring'i). Yalnızca `ge=0` kısıtı doğrulanır.
    _invalid(AnalysisReport, report | {"estimated_cost_usd": -1})


def test_report_version_and_historical_model_policy() -> None:
    report = read_fixture("analyses.result.200.json")

    _invalid(AnalysisReport, report | {"schema_version": "garbage"})
    historical = AnalysisReport.model_validate(report | {"model": "retired/provider-model-v1"})
    assert historical.model == "retired/provider-model-v1"


def test_report_count_percentage_and_id_invariants() -> None:
    report = read_fixture("analyses.result.200.json")
    prep = report["preprocessing_summary"]

    _invalid(
        AnalysisReport,
        report | {"preprocessing_summary": prep | {"redacted_count": prep["analyzed_count"] + 1}},
    )

    questions = [*report["top_questions"]]
    questions[0] = questions[0] | {"count": prep["analyzed_count"] + 1, "percentage": 100}
    _invalid(AnalysisReport, report | {"top_questions": questions})

    questions = [*report["top_questions"]]
    questions[0] = questions[0] | {"percentage": questions[0]["percentage"] + 0.1}
    _invalid(AnalysisReport, report | {"top_questions": questions})

    _invalid(
        AnalysisReport,
        report | {"top_questions": [*report["top_questions"], report["top_questions"][0]]},
    )
    _invalid(AnalysisReport, report | {"themes": [*report["themes"], report["themes"][0]]})

    themes = [*report["themes"]]
    themes[0] = themes[0] | {
        "related_question_ids": [
            themes[0]["related_question_ids"][0],
            themes[0]["related_question_ids"][0],
        ]
    }
    _invalid(AnalysisReport, report | {"themes": themes})


def test_percentage_rounding_is_exact_half_up() -> None:
    assert percentage_half_up(1, 16) == 6.3
    assert percentage_half_up(0, 0) == 0.0


def test_warning_codes_are_producer_closed() -> None:
    with pytest.raises(ValidationError):
        AnalysisWarning(code="ROW_LMIT_TRUNCATED", message="Yazım hatalı kod")
