from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

from ..core.enterprise_config.expressions import validate_match_expr


def _validate_match_expr(value: Any) -> Any:
    validate_match_expr(value)
    return value


MatchExprField = Annotated[Any, BeforeValidator(_validate_match_expr)]


class InstanceAgentResourceGrantRequest(BaseModel):
    match_expr: MatchExprField
    granted_by: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    expires_at: datetime | None = None
    data: dict[str, Any] | None = None


class InstanceAgentResourceUpsertRequest(BaseModel):
    resource_id: str = Field(..., min_length=1, max_length=100)
    ref_template_id: str = Field(..., min_length=1, max_length=100)
    resource_name: str = Field(..., min_length=1, max_length=128)
    resource_desc: str | None = Field(default=None, max_length=512)
    grants: list[InstanceAgentResourceGrantRequest] = Field(..., min_length=1)
