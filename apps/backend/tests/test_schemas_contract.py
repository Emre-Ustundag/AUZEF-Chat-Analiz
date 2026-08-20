"""Model alan adlarının dondurulması.

Bir alanın yeniden adlandırılması fixture katmanına varmadan burada düşer ve
hata mesajı doğrudan hangi modelin değiştiğini söyler.
"""

import warnings

import pytest
from pydantic import BaseModel

from app.schemas.analysis import (
    AnalysisCreated,
    AnalysisJob,
    AnalysisRequest,
    ChatbotLogConfig,
    ModelList,
    ModelOption,
)
from app.schemas.common import ErrorItem, ProblemDetails
from app.schemas.report import (
    AnalysisReport,
    AnalysisTimeSeries,
    AnalysisWarning,
    PreprocessingSummary,
    SourceSummary,
    Theme,
    TokenUsage,
    TopQuestion,
    TrendPoint,
    TrendSeries,
)
from app.schemas.upload import (
    ColumnProfile,
    SheetProfile,
    Upload,
    UploadCreated,
    UploadProfile,
)

#: Frontend'in Zod şemalarıyla birebir aynı alan adları (snake_case, çeviren
#: bir katman YOK).
EXPECTED_FIELDS: list[tuple[type[BaseModel], set[str]]] = [
    (ErrorItem, {"field", "message"}),
    (
        ProblemDetails,
        {"type", "title", "status", "code", "detail", "trace_id", "errors", "retry_after"},
    ),
    (
        ColumnProfile,
        {
            "name",
            "index",
            "non_empty_count",
            "empty_count",
            "unique_count",
            "avg_length",
            "is_likely_text",
            "sample_values",
        },
    ),
    (SheetProfile, {"name", "row_count", "column_count", "columns"}),
    (UploadProfile, {"sheets", "total_row_count", "exceeds_row_limit"}),
    (
        Upload,
        {"upload_id", "status", "filename", "size_bytes", "created_at", "profile", "error"},
    ),
    (UploadCreated, {"upload_id", "status"}),
    (
        AnalysisRequest,
        {
            "upload_id",
            "sheet_name",
            "text_column",
            "model",
            "prompt_version",
            "top_n",
            "max_cost_usd",
            "dataset_type",
            "chatbot_config",
        },
    ),
    (
        ChatbotLogConfig,
        {
            "role_column",
            "role_user_values",
            "session_id_column",
            "timestamp_column",
            "message_type_column",
            "allowed_message_types",
        },
    ),
    (AnalysisCreated, {"analysis_id", "status"}),
    (
        AnalysisJob,
        {
            "analysis_id",
            "status",
            "progress",
            "created_at",
            "updated_at",
            "estimated_seconds_remaining",
            "error",
        },
    ),
    (
        ModelOption,
        {
            "id",
            "label",
            "input_cost_per_million",
            "output_cost_per_million",
            "context_window",
        },
    ),
    (ModelList, {"models", "default_model", "default_prompt_version"}),
    (SourceSummary, {"filename", "sheet_name", "text_column", "total_rows"}),
    (
        PreprocessingSummary,
        {
            "analyzed_count",
            "discarded_count",
            "duplicate_count",
            "redacted_count",
            "unique_count",
            "session_count",
        },
    ),
    (
        TopQuestion,
        {
            "id",
            "canonical_question",
            "count",
            "percentage",
            "confidence",
            "redacted_examples",
            "session_count",
        },
    ),
    (Theme, {"id", "name", "count", "percentage", "related_question_ids", "session_count"}),
    (TrendPoint, {"date", "count"}),
    (TrendSeries, {"id", "daily"}),
    (AnalysisTimeSeries, {"daily_totals", "question_trends", "theme_trends"}),
    (TokenUsage, {"prompt_tokens", "completion_tokens", "total_tokens"}),
    (AnalysisWarning, {"code", "message"}),
    (
        AnalysisReport,
        {
            "schema_version",
            "analysis_id",
            "status",
            "generated_at",
            "dataset_type",
            "source_summary",
            "preprocessing_summary",
            "top_questions",
            "themes",
            "time_series",
            "executive_summary",
            "warnings",
            "model",
            "prompt_version",
            "prompt_hash",
            "token_usage",
            "estimated_cost_usd",
        },
    ),
]


@pytest.mark.parametrize(
    ("model", "fields"), EXPECTED_FIELDS, ids=[m.__name__ for m, _ in EXPECTED_FIELDS]
)
def test_field_names_are_frozen(model: type[BaseModel], fields: set[str]) -> None:
    assert set(model.model_fields) == fields


def test_model_field_name_is_safe_under_protected_namespaces() -> None:
    """`model` alanı Pydantic'in korumalı ad alanını tetiklemiyor.

    Kontrol `name.startswith("model_")` üzerinden yapılıyor ve `"model"` buna
    uymuyor. Yine de sabitliyoruz: ad alanını genişleten bir Pydantic sürümü
    kimsenin okumadığı bir uyarı yerine burada patlasın.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        class Probe(BaseModel):
            model: str

        assert Probe(model="x").model == "x"

    assert "model" in AnalysisRequest.model_fields
    assert "model" in AnalysisReport.model_fields
