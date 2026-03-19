# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.graph.base import Graph
from .react_executable import ReActAgentCompExecutable
from .react_config import ReActAgentCompConfig


class ReActAgentComp(ComponentComposable):
    def __init__(self, config: ReActAgentCompConfig):
        super().__init__()
        self._config = config
        self._executable = None

    @property
    def executable(self) -> ReActAgentCompExecutable:
        if self._executable is None:
            self._executable = self.to_executable()
        return self._executable

    def to_executable(self) -> ReActAgentCompExecutable:
        return ReActAgentCompExecutable(self._config)

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        """Add this component to a workflow graph."""
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)