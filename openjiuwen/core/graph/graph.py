# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Hashable,
    Self,
    Sequence,
    Tuple,
    Union,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import graph_logger, LogEventType
from openjiuwen.core.graph.base import (
    ExecutableGraph,
    Graph,
    Router,
)
from openjiuwen.core.graph.executable import (
    Executable,
    Input,
    Output,
)
from openjiuwen.core.graph.pregel import (
    END,
    MAX_RECURSIVE_LIMIT,
    Pregel,
    PregelBuilder,
    PregelConfig,
    START,
)
from openjiuwen.core.graph.pregel.constants import SESSION_ID
from openjiuwen.core.graph.store import GraphStore
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.session import (
    BaseSession,
    Checkpointer,
    InteractiveInput,
)
from openjiuwen.core.session.workflow import Session


@dataclass(slots=True)
class Branch:
    condition: Callable[..., Hashable | Sequence[Hashable]]


class PregelGraph(Graph):

    def __init__(self):
        self.pregel: Pregel | None = None
        self.edges: list[Tuple[str | list[str], str]] = []
        self.waits: set[str] = set()
        self.nodes: dict[str, Vertex] = {}
        self.branches: defaultdict[str, dict[str, Branch]] = defaultdict(dict)
        self.checkpointer = None
        self._session = None

    def start_node(self, node_id: str) -> Self:
        self._validate_node_id(node_id)
        self.add_edge([START], node_id)
        return self

    def _validate_node_id(self, node_id):
        if not node_id:
            raise build_error(StatusCode.PREGEL_GRAPH_NODE_ID_INVALID, node_id=node_id, reason="is None or empty")

    def end_node(self, node_id: str) -> Self:
        self._validate_node_id(node_id)
        vertex = self.nodes.get(node_id)
        if vertex:
            vertex.is_end_node = True
        self.add_edge([node_id], END)
        return self

    def add_node(self, node_id: str, node: Executable, *, wait_for_all: bool = False) -> Self:
        self._validate_node_id(node_id)
        if node is None:
            raise build_error(StatusCode.PREGEL_GRAPH_NODE_INVALID, node_id=node_id, reason="node is None")

        if node_id in self.nodes:
            raise build_error(StatusCode.PREGEL_GRAPH_NODE_ID_INVALID, node_id=node_id,
                              reason="already exist, can not add again")
        vertex_node = Vertex(node_id, node)
        self.nodes[node_id] = vertex_node
        if wait_for_all:
            self.waits.add(node_id)
        return self

    def get_nodes(self) -> dict[str, Vertex]:
        return {key: vertex for key, vertex in self.nodes.items()}

    def add_edge(self, source_node_id: Union[str, list[str]], target_node_id: str) -> Self:
        if not source_node_id:
            raise build_error(StatusCode.PREGEL_GRAPH_EDGE_INVALID, source_id=source_node_id,
                              target_node_id=target_node_id,
                              reason="source_node_id is None or empty")
        if isinstance(source_node_id, list):
            for item in source_node_id:
                if not item:
                    raise build_error(StatusCode.PREGEL_GRAPH_EDGE_INVALID, source_id=source_node_id,
                                      target_node_id=target_node_id,
                                      reason="source_node_id list has None or empty")

        if not target_node_id:
            raise build_error(StatusCode.PREGEL_GRAPH_EDGE_INVALID, source_id=source_node_id,
                              target_node_id=target_node_id,
                              reason="target_node_id is None or empty")
        self.edges.append((source_node_id, target_node_id))
        return self

    def add_conditional_edges(self, source_node_id: str, router: Router) -> Self:
        if not source_node_id:
            raise build_error(StatusCode.PREGEL_GRAPH_CONDITION_EDGE_INVALID, source_id=source_node_id,
                              reason="source_node_is is None or empty")
        if router is None:
            raise build_error(StatusCode.PREGEL_GRAPH_CONDITION_EDGE_INVALID, source_id=source_node_id,
                              reason="router is None")

        name = _get_callable_name(router)
        self.branches[source_node_id][name] = Branch(router)
        return self

    def compile(self, session: BaseSession, **kwargs) -> ExecutableGraph:
        for node_id, node in self.nodes.items():
            node.init(session, **kwargs)

        def after_step(loop):
            if self._session:
                self._session.state().commit()
            graph_logger.debug(
                f"Finished to run graph super-step [{loop.step}]",
                event_type=LogEventType.GRAPH_SUPER_STEP_END,
                graph_id=loop.config['ns'],
                session_id=loop.config.get(SESSION_ID),
                metadata={
                    "ns": loop.config['ns'],
                    "step": loop.step,
                    "active_nodes": list(loop.active_nodes)
                }
            )

        if self.pregel is None:
            self.checkpointer = session.checkpointer()
            store = GraphStore(self.checkpointer.graph_store())
            self.pregel = self._compile(graph_store=store, step_callback=after_step)
            self._session = session
        else:
            self._session = session
        return CompiledGraph(self.pregel, self.checkpointer)

    def _compile(self, graph_store=None, step_callback=None) -> Pregel:
        edges: list[Tuple[str | list[str], str]] = []
        sources: dict[str, set[str]] = {}
        builder = PregelBuilder()
        for node_id, action in self.nodes.items():
            builder.add_node(node_id, action)

        for (source_node_id, target_node_id) in self.edges:
            if target_node_id in self.waits:
                if target_node_id not in sources:
                    sources[target_node_id] = set()
                if isinstance(source_node_id, str):
                    sources[target_node_id].add(source_node_id)
                elif isinstance(source_node_id, list):
                    sources[target_node_id].update(source_node_id)
            else:
                edges.append((source_node_id, target_node_id))
        for (target_node_id, source_node_id) in sources.items():
            builder.add_edge(source_node_id, target_node_id)
        for (source_node_id, target_node_id) in edges:
            builder.add_edge(source_node_id, target_node_id)

        for start, branches in self.branches.items():
            for name, branch in branches.items():
                builder.add_branch(start, branch.condition)
        return builder.build(graph_store, after_step_callback=step_callback)

    async def reset(self):
        for node in self.nodes.values():
            await node.reset()


class CompiledGraph(ExecutableGraph):
    def __init__(self, pregel: Pregel, checkpointer: Checkpointer):
        self._pregel = pregel
        self._checkpointer = checkpointer

    async def _invoke(self, inputs: Input, session: BaseSession, config: Any = None) -> Output:
        is_main = False
        session_id = session.session_id()
        workflow_id = session.workflow_id()

        if config is None:
            is_main = True
            config = PregelConfig(session_id=session_id, ns=workflow_id, recursion_limit=MAX_RECURSIVE_LIMIT)

        try:
            if is_main:
                await self._checkpointer.pre_workflow_execute(session, inputs)
            if not isinstance(inputs, InteractiveInput):
                session.state().commit_user_inputs(inputs)

            result = None
            exception = None

            try:
                result = await self._pregel.run(config=config)
            except asyncio.CancelledError:
                graph_logger.debug(
                    "Pregel execution cancelled",
                    event_type=LogEventType.GRAPH_END,
                    metadata={"session_id": session_id, "workflow_id": workflow_id, "cancelled": True}
                )
                raise
            except Exception as e:
                exception = e

            if is_main:
                await self._checkpointer.post_workflow_execute(session, result, exception)
            elif exception is not None:
                raise exception
        except asyncio.CancelledError:
            if is_main:
                await self._checkpointer.post_workflow_execute(session, {}, None)
            raise

    async def stream(self, inputs: Input, session: Session) -> AsyncIterator[Output]:
        pass

    async def interrupt(self, message: dict):
        return


def _get_callable_name(func) -> str:
    if hasattr(func, '__name__'):
        return func.__name__
    elif hasattr(func, '__class__'):
        return func.__class__.__name__
    else:
        return repr(func)
