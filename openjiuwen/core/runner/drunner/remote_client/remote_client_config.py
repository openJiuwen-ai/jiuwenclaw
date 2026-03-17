# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class ProtocolEnum(str, Enum):
    MQ = "MQ"


@dataclass
class RemoteClientConfig:
    id: Optional[str] = None
    version: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    protocol: str = ProtocolEnum.MQ.value
    type: Optional[str] = None
    topic: Optional[str] = None
    url: Optional[str] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)
