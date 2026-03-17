from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from openjiuwen.core.common.schema.part import Part
from openjiuwen.core.common.schema.task import TaskStatus


class Artifact(BaseModel):
    artifactId: str = None
    name: Optional[str] = None  # Semantic name, e.g. "summary", "chart"
    description: Optional[str] = None
    parts: List[Part] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    task_id: str = None
    sessionId: Optional[str] = None
    status: TaskStatus = None
    artifacts: List[Artifact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
