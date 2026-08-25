# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WebSearchRecord:
    title: str
    url: str
    snippet: str
    source: str
    content: str = ""
    update_time: Optional[float] = None


@dataclass
class ProviderRun:
    provider: str
    records: list[WebSearchRecord] = field(default_factory=list)
    answer: str = ""
    error: str = ""
    quality_passed: bool = False
    quality_reason: str = ""


@dataclass(frozen=True)
class WebSearchSettings:
    timeout_seconds: int
    max_results: int
    paid_provider_order: tuple[str, ...]
