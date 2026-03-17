# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass
from typing import List, Optional

from openjiuwen.core.graph.visualization.drawable_edge import DrawableEdge
from openjiuwen.core.graph.visualization.drawable_node import DrawableNode


@dataclass
class DrawableGraph:
    nodes: dict[str, DrawableNode]
    edges: List[DrawableEdge]
    start_nodes: List[DrawableNode]
    end_nodes: List[DrawableNode]
    break_nodes: Optional[List[DrawableNode]]