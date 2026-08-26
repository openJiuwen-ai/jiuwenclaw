"""content_builder.py — Safe content.json builder for xiaoyi-pdf.

Provides a dataclass-based API so callers never have to hand-write JSON strings.
Special characters (quotes, backslashes, newlines) are automatically escaped
by the standard library json module.
"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import json


@dataclass
class ContentBlock:
    type: str
    text: Optional[str] = None
    headers: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None
    col_widths: Optional[List[float]] = None
    caption: Optional[str] = None
    path: Optional[str] = None
    src: Optional[str] = None
    language: Optional[str] = None
    chart_type: Optional[str] = None
    labels: Optional[List[str]] = None
    datasets: Optional[List[Dict[str, Any]]] = None
    title: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    figure: Optional[bool] = None
    nodes: Optional[List[Dict[str, str]]] = None
    edges: Optional[List[Dict[str, str]]] = None
    items: Optional[List[Dict[str, str]]] = None
    label: Optional[str] = None
    pt: Optional[float] = None


def save_content(blocks: List[ContentBlock], output_path: str) -> None:
    """Serialize a list of ContentBlock objects to a JSON array file.

    None fields are omitted to keep the output compact.
    """
    data = []
    for block in blocks:
        d = {k: v for k, v in asdict(block).items() if v is not None}
        data.append(d)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
