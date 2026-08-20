"""Analiz ve rapor sözleşme testleri — frontend'in Zod şemalarıyla birebir uyum.

Karşılaştırma kaynağı: `apps/web/src/lib/api/schemas/analysis.ts` ve
`report.ts`. Buradaki iddialar o dosyalardan ELLE türetilmiştir; şema
değişirse bu testler de güncellenmelidir.

`test_contract.py` ile aynı gerekçe: sözleşme ihlalleri SESSİZ hatalardır.
Backend HTTP 200 döner, frontend `safeParse` ile reddeder, kullanıcı boş
ekran görür. Ne pytest ne `npm test` bunu kendiliğinden yakalar.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import build_problem
from app.schemas.analysis import (
    STAGE_PROGRESS,
    TERMINAL_STATUSES,
    AnalysisCreate,
    AnalysisCreated,
    AnalysisJobRead,
    AnalysisStatus,
)
from app.schemas.report import (
    AnalysisReport,
    PreprocessingSummary,
    SourceSummary,
    Theme,
    TokenUsage,
    TopQuestion,
)
from app.services import report_export

#: `analysis.ts` → `analysisStatusSchema`.
FRONTEND_ANALYSIS_STATUSES = {
    "queued",
    "validating",
    "preprocessing",
    "analyzing",
    "aggregating",
    "completed",
    "failed",
    "cancelled",
}

#: `analysis.ts` → `TERMINAL_STATUSES` / `isAnalysisSettled`.
FRONTEND_TERMINAL = {"completed", "failed", "cancelled"}

ISO_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ------------------------------------------------------------ durum kümesi


def test_analiz_durumlari_frontend_ile_birebir() -> None:
    assert {status.value for status in AnalysisStatus} == FRONTEND_ANALYSIS_STATUSES


def test_terminal_durumlar_frontend_ile_ayni() -> None:
    """Yanlış olsaydı frontend biten bir işi sonsuza kadar poll ederdi."""
    assert {status.value for status in TERMINAL_STATUSES} == FRONTEND_TERMINAL


def test_asama_ilerlemeleri_artan_ve_sinirlar_icinde() -> None:
    values = [
        STAGE_PROGRESS[status]
        for status in (
            AnalysisStatus.QUEUED,
            AnalysisStatus.VALIDATING,
            AnalysisStatus.PREPROCESSING,
            AnalysisStatus.ANALYZING,
            AnalysisStatus.AGGREGATING,
            AnalysisStatus.COMPLETED,
        )
    ]
    assert values == sorted(values)
    assert values[0] >= 0
    # Frontend `z.number().min(0).max(100)`; dışına çıkmak Zod hatası demek.
    assert values[-1] == 100


# --------------------------------------------------------- istek doğrulama


def _valid_request() -> dict[str, object]:
    return {
        "upload_id": str(uuid4()),
        "sheet_name": "Mesajlar",
        "text_column": "mesaj",
        "row_filters": [],
        "model": "anthropic/claude-sonnet-4.6",
        "prompt_version": "faq_analysis/v1",
        "top_n": 20,
        "max_cost_usd": 5.0,
    }


def test_gecerli_istek_kabul_edilir() -> None:
    assert AnalysisCreate.model_validate(_valid_request())


def test_satir_filtreleri_normalize_edilir() -> None:
    payload = _valid_request() | {
        "row_filters": [
            {"column": " direction ", "allowed_values": [" Kullanıcı ", "Temsilci"]},
            {"column": "message_type", "allowed_values": ["text"]},
        ]
    }
    parsed = AnalysisCreate.model_validate(payload)

    assert parsed.row_filters[0].column == "direction"
    assert parsed.row_filters[0].allowed_values == ["Kullanıcı", "Temsilci"]


@pytest.mark.parametrize(
    "row_filters",
    [
        [{"column": "direction", "allowed_values": []}],
        [{"column": "direction", "allowed_values": [""]}],
        [{"column": "direction", "allowed_values": ["Kullanıcı", "Kullanıcı"]}],
        [
            {"column": "direction", "allowed_values": ["Kullanıcı"]},
            {"column": "direction", "allowed_values": ["Bot"]},
        ],
    ],
)
def test_gecersiz_satir_filtreleri_reddedilir(row_filters: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        AnalysisCreate.model_validate(_valid_request() | {"row_filters": row_filters})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("top_n", 0),
        ("top_n", 101),
        ("max_cost_usd", 0),
        ("max_cost_usd", 101),
        ("text_column", ""),
        ("sheet_name", ""),
        ("model", ""),
    ],
)
def test_sinirlar_zod_ile_ayni(field: str, value: object) -> None:
    """Zod: `top_n` 1-100, `max_cost_usd` pozitif ve <= 100, metinler min 1."""
    payload = _valid_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        AnalysisCreate.model_validate(payload)


def test_istek_govdesinde_api_anahtari_alani_yok() -> None:
    """ADR §6/§9: anahtar yalnızca X-OpenRouter-Key header'ında taşınır."""
    fields = set(AnalysisCreate.model_fields)
    assert "api_key" not in fields
    assert "openrouter_key" not in fields
    assert fields == {
        "upload_id",
        "sheet_name",
        "text_column",
        "row_filters",
        "model",
        "prompt_version",
        "top_n",
        "max_cost_usd",
    }


# ------------------------------------------------------------ cevap gövdeleri


def test_analysis_created_govdesi() -> None:
    payload = AnalysisCreated(analysis_id=uuid4(), status=AnalysisStatus.QUEUED).model_dump(
        mode="json"
    )

    assert set(payload) == {"analysis_id", "status"}
    assert payload["status"] == "queued"
    assert isinstance(payload["analysis_id"], str)


def test_analysis_job_alanlari_sozlesmeyle_ayni() -> None:
    now = datetime.now(UTC)
    payload = AnalysisJobRead(
        analysis_id=uuid4(),
        status=AnalysisStatus.ANALYZING,
        progress=42.5,
        created_at=now,
        updated_at=now,
        estimated_seconds_remaining=30.0,
        error=None,
    ).model_dump(mode="json")

    assert set(payload) == {
        "analysis_id",
        "status",
        "progress",
        "created_at",
        "updated_at",
        "estimated_seconds_remaining",
        "error",
    }
    # `error` ve `estimated_seconds_remaining` frontend'de `.nullable()`:
    # açık null DOĞRUDUR (retry_after'ın aksine).
    assert payload["error"] is None
    assert ISO_Z_PATTERN.match(payload["created_at"])
    assert ISO_Z_PATTERN.match(payload["updated_at"])


def test_terminal_durumda_kalan_sure_null_olabilir() -> None:
    now = datetime.now(UTC)
    payload = AnalysisJobRead(
        analysis_id=uuid4(),
        status=AnalysisStatus.COMPLETED,
        progress=100,
        created_at=now,
        updated_at=now,
        estimated_seconds_remaining=None,
    ).model_dump(mode="json")

    assert payload["estimated_seconds_remaining"] is None


def test_hatali_isteki_gomulu_problem_retry_after_icermez() -> None:
    """`retry_after` frontend'de `.optional()` — null gönderilirse Zod reddeder."""
    now = datetime.now(UTC)
    payload = AnalysisJobRead(
        analysis_id=uuid4(),
        status=AnalysisStatus.FAILED,
        progress=100,
        created_at=now,
        updated_at=now,
        error=build_problem("PROVIDER_BAD_RESPONSE", "Geçersiz yanıt."),
    ).model_dump(mode="json")

    assert "retry_after" not in payload["error"]
    assert payload["error"]["code"] == "PROVIDER_BAD_RESPONSE"


# ------------------------------------------------------------------- rapor


def _report() -> AnalysisReport:
    return AnalysisReport(
        analysis_id=uuid4(),
        generated_at=datetime.now(UTC),
        source_summary=SourceSummary(
            filename="veri.xlsx",
            sheet_name="Mesajlar",
            text_column="mesaj",
            total_rows=100,
        ),
        preprocessing_summary=PreprocessingSummary(
            analyzed_count=90,
            discarded_count=10,
            duplicate_count=60,
            redacted_count=5,
            unique_count=30,
        ),
        top_questions=[
            TopQuestion(
                id="q1",
                canonical_question="Sınav ne zaman?",
                count=45,
                percentage=50.0,
                redacted_examples=["sınav ne zaman"],
            )
        ],
        themes=[
            Theme(id="t1", name="Sınav", count=45, percentage=50.0, related_question_ids=["q1"])
        ],
        executive_summary="Özet.",
        warnings=[],
        model="anthropic/claude-sonnet-4.6",
        prompt_version="faq_analysis/v1",
        prompt_hash="sha256:abc123",
        token_usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        estimated_cost_usd=0.0,
    )


def test_rapor_alanlari_sozlesmeyle_birebir() -> None:
    payload = _report().model_dump(mode="json")

    assert set(payload) == {
        "schema_version",
        "analysis_id",
        "status",
        "generated_at",
        "source_summary",
        "preprocessing_summary",
        "top_questions",
        "themes",
        "executive_summary",
        "warnings",
        "model",
        "prompt_version",
        "prompt_hash",
        "token_usage",
        "estimated_cost_usd",
        "cost_source",
        "pricing_snapshot",
    }
    assert payload["status"] == "completed"
    assert ISO_Z_PATTERN.match(payload["generated_at"])
    assert set(payload["source_summary"]) == {
        "filename",
        "sheet_name",
        "text_column",
        "row_filters",
        "total_rows",
    }
    assert set(payload["preprocessing_summary"]) == {
        "analyzed_count",
        "discarded_count",
        "duplicate_count",
        "redacted_count",
        "unique_count",
    }
    assert set(payload["top_questions"][0]) == {
        "id",
        "canonical_question",
        "count",
        "percentage",
        "redacted_examples",
    }
    assert set(payload["themes"][0]) == {
        "id",
        "name",
        "count",
        "percentage",
        "related_question_ids",
    }
    assert set(payload["token_usage"]) == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_write_tokens",
    }


def test_tarihsel_confidence_alani_yok_sayilir() -> None:
    payload = _report().model_dump(mode="json")
    payload["top_questions"][0]["confidence"] = 0.9

    parsed = AnalysisReport.model_validate(payload).model_dump(mode="json")

    assert "confidence" not in parsed["top_questions"][0]


def test_rapor_yalnizca_completed_durumunu_tasir() -> None:
    """Frontend `z.literal("completed")`; başka bir değer Zod'da hata."""
    payload = _report().model_dump(mode="json")
    assert payload["status"] == "completed"

    with pytest.raises(ValidationError):
        AnalysisReport.model_validate({**payload, "status": "analyzing"})


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("top_questions", 0, "percentage"), 148),
        (("themes", 0, "percentage"), -1),
        (("top_questions", 0, "count"), -5),
    ],
)
def test_sayisal_sinirlar_zod_ile_ayni(path: tuple[object, ...], value: object) -> None:
    """Zod: percentage 0-100, count >= 0."""
    payload = _report().model_dump(mode="json")
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        AnalysisReport.model_validate(payload)


def test_raporda_stack_trace_veya_sir_yok() -> None:
    serialized = json.dumps(_report().model_dump(mode="json"))
    for leak in ("Traceback", 'File "', "sk-", "postgresql://", "minioadmin"):
        assert leak not in serialized


# ------------------------------------------------------------------ export


def test_export_bicimleri_frontend_ile_birebir() -> None:
    """Frontend: `exportFormatSchema = z.enum(["xlsx", "json"])`.

    Backend tarafı da aynı enum'u doğrular; bilinmeyen biçim 422 üretir.
    """
    assert report_export.XLSX_MEDIA_TYPE == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert report_export.JSON_MEDIA_TYPE == "application/json"

    analysis_id = "11111111-2222-3333-4444-555555555555"
    for extension in ("xlsx", "json"):
        header = report_export.content_disposition(analysis_id, extension)
        # Frontend yalnızca düz `filename="..."` ayrıştırıyor (RFC 5987 yok)
        # ve başlığın ASCII kalması şart.
        assert header == f'attachment; filename="analiz-{analysis_id}.{extension}"'
        header.encode("ascii")
