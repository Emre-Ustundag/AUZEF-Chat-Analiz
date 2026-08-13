"""ProblemDetails'in hem tel serileştirmesi hem OpenAPI şeması kilitli."""

from app.core.errors import ErrorCode
from app.schemas.common import ErrorItem, ProblemDetails

TRACE_ID = "9d8c7b6a-5e4f-4321-8abc-0123456789ab"


def _problem(*, rate_limited: bool = False) -> ProblemDetails:
    code = ErrorCode.PROVIDER_RATE_LIMITED if rate_limited else ErrorCode.INTERNAL_ERROR
    return ProblemDetails(
        type="/errors/example",
        title="Örnek",
        status=429 if rate_limited else 500,
        code=code,
        detail="Örnek detay.",
        trace_id=TRACE_ID,
        errors=[ErrorItem(field=None, message="Alan adı yok.")],
        retry_after=60 if rate_limited else None,
    )


def test_wire_omits_only_retry_after_when_none() -> None:
    wire = _problem().to_wire()

    assert "retry_after" not in wire
    assert wire["errors"][0]["field"] is None
    assert wire["trace_id"] == TRACE_ID


def test_wire_includes_retry_after_for_429() -> None:
    assert _problem(rate_limited=True).to_wire()["retry_after"] == 60


def test_serialization_schema_is_structured_and_non_nullable() -> None:
    schema = ProblemDetails.model_json_schema(mode="serialization")
    properties = schema["properties"]

    assert set(properties) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "trace_id",
        "errors",
        "retry_after",
    }
    assert set(schema["required"]) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "trace_id",
        "errors",
    }
    assert properties["trace_id"]["format"] == "uuid"
    assert properties["retry_after"]["type"] == "number"
    assert "anyOf" not in properties["retry_after"]
    assert "default" not in properties["retry_after"]
