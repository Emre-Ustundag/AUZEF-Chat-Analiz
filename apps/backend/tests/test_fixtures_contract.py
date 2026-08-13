"""Fixture'lar Pydantic tarafında geçerli ve GÜNCEL mi?

Frontend aynası: `apps/web/src/lib/api/schemas/contract-fixtures.test.ts`.
Aynı dosyaları iki dil de doğruladığı için sözleşmenin tek okuması olduğu
kanıtlanmış olur.
"""

import json
from typing import Any

import pytest
from pydantic import BaseModel

from app.schemas.analysis import AnalysisCreated, AnalysisJob, AnalysisRequest, ModelList
from app.schemas.common import ProblemDetails
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.schemas.report import AnalysisReport
from app.schemas.upload import Upload, UploadCreated
from scripts.export_fixtures import main as export_fixtures_main
from tests.conftest import FIXTURE_DIR, read_fixture

MODELS: dict[str, type[BaseModel]] = {
    "Upload": Upload,
    "UploadCreated": UploadCreated,
    "ModelList": ModelList,
    "AnalysisRequest": AnalysisRequest,
    "AnalysisCreated": AnalysisCreated,
    "AnalysisJob": AnalysisJob,
    "AnalysisReport": AnalysisReport,
    "LivenessResponse": LivenessResponse,
    "ReadinessResponse": ReadinessResponse,
    "ProblemDetails": ProblemDetails,
}


def _cases_with_payload(manifest: Any) -> list[dict[str, Any]]:
    return [c for c in manifest["cases"] if c["file"]]


def test_manifest_models_all_have_a_pydantic_class(manifest: Any) -> None:
    unknown = {c["model"] for c in _cases_with_payload(manifest)} - set(MODELS)
    assert unknown == set()


def test_every_model_is_exercised(manifest: Any) -> None:
    referenced = {c["model"] for c in _cases_with_payload(manifest)}
    assert set(MODELS) - referenced == set()


def test_fixtures_are_not_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--check` modunun kendisi.

    Bu iddia olmadan biri Pydantic modelini değiştirip yeniden üretmeyi
    unutur ve hem pytest hem vitest bayat dosyalara karşı yeşil kalır — çoğu
    fixture tabanlı drift kontrolünü dekoratif yapan şey tam olarak bu.
    """
    monkeypatch.setattr("sys.argv", ["export_fixtures.py", "--check"])
    assert export_fixtures_main() == 0


def _payload_cases() -> list[tuple[str, str, str]]:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return [(c["id"], c["model"], c["file"]) for c in _cases_with_payload(manifest)]


@pytest.mark.parametrize(("case_id", "model_name", "file_name"), _payload_cases())
def test_fixture_validates_and_round_trips(case_id: str, model_name: str, file_name: str) -> None:
    raw = read_fixture(file_name)
    instance = MODELS[model_name].model_validate(raw)

    # Round-trip: serializer idempotent olmalı ve dosya elle düzenlenmemiş
    # olmalı. Elle eklenmiş bir alan burada düşer.
    assert json.loads(instance.model_dump_json()) == raw, case_id


def _problem_cases() -> list[tuple[str, str, str]]:
    return [case for case in _payload_cases() if case[1] == "ProblemDetails"]


@pytest.mark.parametrize(("case_id", "model_name", "file_name"), _problem_cases())
def test_problem_fixtures_respect_retry_after_rule(
    case_id: str, model_name: str, file_name: str
) -> None:
    raw = read_fixture(file_name)
    if raw["status"] == 429:
        assert "retry_after" in raw, case_id
    else:
        # `null` DEĞİL, anahtarın hiç bulunmaması gerekiyor.
        assert "retry_after" not in raw, case_id


def test_truncated_report_encodes_adr_0002_rules() -> None:
    """ADR-0002 #2 ve #5'in tek dosyalık spesifikasyonu."""
    full = AnalysisReport.model_validate(read_fixture("analyses.result.200.json"))
    truncated = AnalysisReport.model_validate(read_fixture("analyses.result.200.truncated.json"))

    assert len(truncated.top_questions) < len(full.top_questions)

    present = {q.id for q in truncated.top_questions}
    for theme in truncated.themes:
        assert set(theme.related_question_ids) <= present

    # Tema büyüklüğü kırpmadan etkilenmez.
    full_counts = {t.id: t.count for t in full.themes}
    assert {t.id: t.count for t in truncated.themes} == full_counts

    assert any(w.code == "ROW_LIMIT_TRUNCATED" for w in truncated.warnings)


def test_row_limit_upload_is_ready_not_rejected() -> None:
    upload = Upload.model_validate(read_fixture("uploads.get.200.row-limit.json"))

    assert upload.status.value == "ready"
    assert upload.error is None
    assert upload.profile is not None
    assert upload.profile.exceeds_row_limit is True
