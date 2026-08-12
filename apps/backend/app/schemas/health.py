"""Liveness ve readiness API sözleşmeleri."""

from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class LivenessResponse(ApiModel):
    status: Literal["ok"] = "ok"


class ReadinessCheckResponse(ApiModel):
    name: str
    status: Literal["ok"] = "ok"


class ReadinessResponse(ApiModel):
    status: Literal["ready"] = "ready"
    checks: list[ReadinessCheckResponse] = Field(min_length=1)
