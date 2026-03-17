# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class DrawableNode:
    id: str
    name: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None