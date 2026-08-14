"""Model listesi şemaları.

SÖZLEŞME KAYNAĞI: `apps/web/src/lib/api/schemas/analysis.ts` →
`modelOptionSchema` ve `modelListSchema`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelOption(BaseModel):
    id: str
    label: str
    #: 1M girdi tokenı başına USD.
    input_cost_per_million: float = Field(ge=0)
    output_cost_per_million: float = Field(ge=0)
    context_window: int = Field(gt=0)


class ModelList(BaseModel):
    models: list[ModelOption]
    default_model: str
    default_prompt_version: str
