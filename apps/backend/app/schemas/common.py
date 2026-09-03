"""RFC 9457 Problem Details ve uyarı sözlüğü.

Frontend aynası: `apps/web/src/lib/api/schemas/common.ts`.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import Field, GetJsonSchemaHandler, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from app.core.errors import ERROR_STATUS, ErrorCode
from app.schemas.base import ApiModel


def to_iso_z(value: datetime) -> str:
    """Datetime değerini frontend'in beklediği UTC `Z` biçimine çevirir."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WarningCode(StrEnum):
    """`AnalysisReport.warnings[].code` için üretici sözlüğü — ADR-0002 #2.

    Üretici-kapalı, tüketici-açık: backend yalnızca bu üyeleri yayabilir ama
    tel üstündeki alan `str` kalır. Zod tarafını enum yapmak yeni bir uyarı
    eklendiğinde `apiRequest` içinde 500 muadili bir kırılma üretirdi — uyarı
    işi durdurmayan bir sinyal, sözleşme kırılması değil.
    """

    ROW_LIMIT_TRUNCATED = "ROW_LIMIT_TRUNCATED"
    CHUNK_PARTIAL_FAILURE = "CHUNK_PARTIAL_FAILURE"
    LOW_CONFIDENCE_THEMES = "LOW_CONFIDENCE_THEMES"
    PII_REDACTION_INCOMPLETE = "PII_REDACTION_INCOMPLETE"
    COST_LIMIT_APPROACHED = "COST_LIMIT_APPROACHED"
    LLM_UNKNOWN_RECORD_ID = "LLM_UNKNOWN_RECORD_ID"
    LLM_DUPLICATE_ASSIGNMENT = "LLM_DUPLICATE_ASSIGNMENT"
    LLM_UNASSIGNED_RECORDS = "LLM_UNASSIGNED_RECORDS"
    LLM_REDUCE_PARTIAL_COVERAGE = "LLM_REDUCE_PARTIAL_COVERAGE"


class ErrorItem(ApiModel):
    """Alan bazlı doğrulama hatası."""

    #: Alan adı olmayan validation hatalarında `null`; frontend aynası
    #: `nullish()` olduğu için hem `null` hem eksik alanı kabul eder.
    field: str | None = None
    message: str


class ProblemDetails(ApiModel):
    """ADR-0001 §7 / ADR-0002 #6.

    `type`, `title`, `status`, `code`, `detail` ve `trace_id` HER cevapta
    bulunur — frontend'in `problemDetailsSchema`'sı altısını da zorunlu tutar.

    Bu garantiyi veren `json_schema_serialization_defaults_required` artık
    `ApiModel`'de; burada tekrarlanmıyor. Bayrağın YALNIZCA bu modelde olması,
    diğer sekiz cevap modelinin openapi.json'da yanlış `required` listesiyle
    yayımlanmasının sebebiydi.
    """

    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str
    trace_id: UUID
    errors: list[ErrorItem] = Field(default_factory=list)
    #: `null` DEĞİL: yalnızca 429'da yayılır, diğer cevaplarda anahtar yoktur.
    retry_after: float | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _error_invariants(self) -> Self:
        expected_status = ERROR_STATUS[self.code]
        if self.status != expected_status:
            raise ValueError(f"{self.code} status={expected_status} taşımak zorunda.")

        is_rate_limited = self.code is ErrorCode.PROVIDER_RATE_LIMITED
        if is_rate_limited and self.retry_after is None:
            raise ValueError("PROVIDER_RATE_LIMITED cevabı retry_after içermek zorunda.")
        if not is_rate_limited and self.retry_after is not None:
            raise ValueError("retry_after yalnızca PROVIDER_RATE_LIMITED cevaplarında olur.")
        return self

    def to_wire(self) -> dict[str, Any]:
        """Gerçek HTTP/fixture gövdesi; `retry_after=None` otomatik düşer."""
        return self.model_dump(mode="json")

    def to_payload(self) -> dict[str, Any]:
        """Eski entegrasyon kodunun adı; tel çıktısı ile birebir aynıdır."""
        return self.to_wire()

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """`retry_after` tel üstünde opsiyonel fakat asla `null` değildir."""
        schema = handler(core_schema)
        resolved = handler.resolve_ref_schema(schema)
        retry_after = resolved.get("properties", {}).get("retry_after")
        if retry_after:
            non_null = [
                variant for variant in retry_after.get("anyOf", []) if variant.get("type") != "null"
            ]
            if len(non_null) == 1:
                retry_after.pop("anyOf", None)
                retry_after.update(non_null[0])
            retry_after.pop("default", None)
        required = resolved.get("required")
        if isinstance(required, list) and "retry_after" in required:
            required.remove("retry_after")
        return schema
