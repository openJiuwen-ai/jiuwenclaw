# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import operator
from typing import TypedDict, Annotated


class GraphState(TypedDict):
    source_node_id: Annotated[list, operator.add]
