"""Paylaşılan kısıt tablosu — Pydantic tarafı.

Frontend aynası: `apps/web/src/lib/api/schemas/contract-constraints.test.ts`.
Aynı `constraints.json` iki tarafta da çalıştığı için `le=100` ile
`.max(100)` arasındaki bir ayrışma yakalanır; ne fixture doğrulaması ne de
enum parity bunu görebilir.
"""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.analysis import AnalysisJob, AnalysisRequest
from app.schemas.upload import Upload
from tests.conftest import read_fixture

MODELS: dict[str, type[BaseModel]] = {
    "AnalysisRequest": AnalysisRequest,
    "AnalysisJob": AnalysisJob,
    "Upload": Upload,
}


def _constraint_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = read_fixture("constraints.json")["cases"]
    return cases


def test_table_is_not_empty() -> None:
    assert len(_constraint_cases()) > 10


@pytest.mark.parametrize("case", _constraint_cases(), ids=lambda c: f"{c['field']}={c['value']}")
def test_constraint(case: dict[str, Any]) -> None:
    model = MODELS[case["model"]]
    payload = read_fixture(f"{case['base']}.json") | {case["field"]: case["value"]}

    if case["valid"]:
        model.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            model.model_validate(payload)
