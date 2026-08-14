from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List


@dataclass
class TraceItem:
    type: str
    tool_name: str = None
    arguments: str = None
    output: str = None
    ctx_status: Optional[Dict[str, Any]] = None
    init_image_base64:List[str] = field(default_factory=list)
    image_index_map:Optional[Dict[str, str]] = None
    image_queries:Optional[Dict[str, str]] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class ReactLoopResult:
    success: bool
    trace_list: list[TraceItem]
    finish_reason: str
    turn_num: int
    failure_reason: Optional[str] = None
    other_info: Optional[str] = None
    ctx_status: Optional[Dict[str, Any]] = None
    total_usage: Optional[Dict[str, int]] = None

    def to_dict(self):
        return asdict(self)