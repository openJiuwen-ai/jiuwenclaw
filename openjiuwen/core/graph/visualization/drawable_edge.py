# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Protocol, Optional


class Stringifiable(Protocol):
    """Protocol for objects that can be converted to a string."""

    def __str__(self) -> str:
        """Convert the object to a string."""


@dataclass
class DrawableEdge:
    source: str
    target: str
    data: Optional[Stringifiable] = None
    conditional: bool = False
    streaming: bool = False


@dataclass
class DrawableBranchRouter:
    targets: list[str]
    datas: list[str]
