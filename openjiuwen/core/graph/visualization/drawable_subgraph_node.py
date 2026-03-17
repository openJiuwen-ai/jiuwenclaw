# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.graph.visualization.drawable_graph import DrawableGraph
from openjiuwen.core.graph.visualization.drawable_node import DrawableNode


@dataclass
class DrawableSubgraphNode(DrawableNode):
    subgraph: Optional[DrawableGraph] = None