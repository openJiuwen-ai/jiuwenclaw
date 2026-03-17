# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import TypedDict, Optional

from openjiuwen.core.graph.pregel.constants import MAX_RECURSIVE_LIMIT, RECURSION_LIMIT


class PregelConfig(TypedDict, total=False):
    """External configuration passed by the user."""
    session_id: Optional[str]
    recursion_limit: int
    ns: Optional[str]


class InnerPregelConfig(PregelConfig, total=False):
    """Internal configuration used by PregelLoop."""
    parent_ns: Optional[str]


def create_inner_config(config: PregelConfig) -> InnerPregelConfig:
    inner_config: InnerPregelConfig = config.copy()
    if not inner_config.get(RECURSION_LIMIT):
        inner_config[RECURSION_LIMIT] = MAX_RECURSIVE_LIMIT
    return inner_config


DEFAULT_PREGEL_CONFIG = PregelConfig(session_id=None, ns=None, recursion_limit=MAX_RECURSIVE_LIMIT)
