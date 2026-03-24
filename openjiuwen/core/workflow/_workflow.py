# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Self, Union, Optional, Dict, Any

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.utils.dict_utils import flatten_dict
from openjiuwen.core.graph.pregel import PregelConfig, GraphInterrupt
from openjiuwen.core.graph.pregel.config import InnerPregelConfig
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.workflow.components.component import ComponentComposable
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter, WORKFLOW_DRAWABLE
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph, Router, ExecutableGraph

from openjiuwen.core.session import ProxySession, NodeSession, InteractiveInput
from openjiuwen.core.session import Transformer
from openjiuwen.core.session import SubWorkflowSession
from openjiuwen.core.session import RouterSession
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session import WorkflowSession

from openjiuwen.core.graph.stream_actor.base import StreamGraph
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, CompIOConfig, NodeSpec
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.graph.graph import PregelGraph


@dataclass
class EdgeTopology:
    """Edge topology context for ability inference."""
    source_map: dict[str, list[str]]
    target_map: dict[str, list[str]]
    source_stream_map: dict[str, list[str]]
    target_stream_map: dict[str, list[str]]

    def all_edge_nodes(self) -> set[str]:
        """Get all nodes referenced in edges."""
        return (
                set(self.source_map.keys()) |
                set(self.target_map.keys()) |
                set(self.source_stream_map.keys()) |
                set(self.target_stream_map.keys())
        )


async def execute_single_component(
        component_id: str,
        session: Session,
        executor: ComponentComposable,
        inputs: Any,  # Input data
        *,
        inputs_schema: dict = None,  # Input schema
        outputs_schema: dict = None,  # Output schema
        context: ModelContext = None  # Context, optional parameter
):
    # 1. Create WorkflowSession
    workflow_session = WorkflowSession(
        workflow_id=component_id,
        parent=None,
        session_id=session.get_session_id()
    )
    await workflow_session.checkpointer().pre_workflow_execute(workflow_session, inputs)
    # 2. Create NodeSession
    node_session = NodeSession(workflow_session, component_id, type(executor).__name__)
    if not isinstance(inputs, InteractiveInput):
        node_session.state().commit_user_inputs(inputs)
    # 3. Create Vertex
    executable = executor.to_executable()
    vertex = Vertex(component_id, executable)

    # 4. Initialize Vertex
    # When context is None, it can still be initialized normally
    vertex.init(node_session, context=context)

    # 5. Directly set _node_config attribute
    # Create simple configuration objects
    class SimpleIOConfig:
        def __init__(self, inputs_schema, outputs_schema=None):
            self.inputs_schema = inputs_schema
            self.outputs_schema = outputs_schema  # Add outputs_schema attribute

    class SimpleNodeConfig:
        def __init__(self, inputs_schema, outputs_schema=None):
            self.io_configs = SimpleIOConfig(inputs_schema, outputs_schema)
            self.abilities = [ComponentAbility.INVOKE]

            # Set input schema and output schema

    vertex._node_config = SimpleNodeConfig(
        inputs_schema=inputs_schema,
        outputs_schema=outputs_schema
    )

    # 7. Create PregelConfig
    sub_workflow_comp = executable.graph_invoker() if hasattr(executable, "graph_invoker") else False
    if sub_workflow_comp:
        config = InnerPregelConfig(session_id=workflow_session.session_id(),
                                   ns=component_id,  # Simulate workflow_id
                                   recursion_limit=100,  # Recursion limit,
                                   parent_ns=component_id)
    else:
        config = PregelConfig(
            session_id=workflow_session.session_id(),
            ns=component_id,  # Simulate workflow_id
            recursion_limit=100  # Recursion limit
        )

    exception = None
    try:
        # 8. Execute component
        await vertex.call(config)
    except GraphInterrupt as e:
        exception = e
        return e.value[0].value
    finally:
        # 9. Commit all state updates
        node_session.state().commit()
        # 10. Get execution result
        result = node_session.state().get_outputs(component_id)
        result = result if result else {}
        try:
            await workflow_session.checkpointer().post_workflow_execute(workflow_session, result, exception=exception)
        except Exception:
            pass
    return result.get(component_id)


class ConnectionType(Enum):
    """Type of workflow connection."""
    CONNECTION = "connection"
    STREAM_CONNECTION = "stream_connection"


class BaseWorkflow:
    def __init__(self, workflow_config: WorkflowConfig = None, new_graph: Graph = None):
        self._graph = new_graph if new_graph else PregelGraph()
        self._workflow_config = workflow_config if workflow_config else WorkflowConfig(
            card=WorkflowCard(id=uuid.uuid4().hex))
        self._workflow_spec = self._workflow_config.spec
        self._stream_actor = StreamGraph()
        self._session = ProxySession()
        self._drawable = None
        drawable = os.environ.get(WORKFLOW_DRAWABLE, "false").lower() == "true"
        if drawable:
            from openjiuwen.core.graph.visualization.drawable import Drawable
            self._drawable = Drawable()

    def config(self):
        return self._workflow_config

    def stream_actor(self):
        return self._stream_actor

    def add_workflow_comp(
            self,
            comp_id: str,
            workflow_comp: ComponentComposable,
            *,
            wait_for_all: bool = None,
            inputs_schema: dict | Transformer = None,
            outputs_schema: dict | Transformer = None,
            stream_inputs_schema: dict | Transformer = None,
            stream_outputs_schema: dict | Transformer = None,
            comp_ability: list[ComponentAbility] = None
    ) -> Self:
        self._validate_comp_id(comp_id)
        self._validate_schemas(comp_id, inputs_schema, outputs_schema, stream_inputs_schema, stream_outputs_schema)
        self._validate_comp_ability(comp_id, comp_ability, wait_for_all)
        node_spec = NodeSpec(
            io_configs=CompIOConfig(inputs_schema=inputs_schema, outputs_schema=outputs_schema),
            stream_io_configs=CompIOConfig(inputs_schema=stream_inputs_schema, outputs_schema=stream_outputs_schema),
            abilities=comp_ability if comp_ability is not None else [])
        self._workflow_spec.comp_configs[comp_id] = node_spec
        if wait_for_all is None:
            wait_for_all = False
        workflow_comp.add_component(graph=self._graph, node_id=comp_id, wait_for_all=wait_for_all)

        if self._drawable:
            self._drawable.add_node(comp_id, workflow_comp)
        return self

    def start_comp(
            self,
            start_comp_id: str,
    ) -> Self:
        self._validate_comp_id(start_comp_id)
        self._graph.start_node(start_comp_id)
        if self._drawable:
            self._drawable.set_start_node(start_comp_id)
        self._workflow_spec.start_nodes.append(start_comp_id)
        return self

    def end_comp(
            self,
            end_comp_id: str,
    ) -> Self:
        self._validate_comp_id(end_comp_id)
        self._graph.end_node(end_comp_id)
        if self._drawable:
            self._drawable.set_end_node(end_comp_id)
        return self

    def add_connection(self, src_comp_id: Union[str, list[str]], target_comp_id: str) -> Self:
        self._validate_edge(src_comp_id, target_comp_id, StatusCode.WORKFLOW_EDGE_INVALID)
        self._graph.add_edge(src_comp_id, target_comp_id)
        if isinstance(src_comp_id, list):
            for source_id in src_comp_id:
                if source_id not in self._workflow_spec.edges:
                    self._workflow_spec.edges[source_id] = [target_comp_id]
                else:
                    self._workflow_spec.edges[source_id].append(target_comp_id)
                if self._drawable:
                    self._drawable.add_edge(source_id, target_comp_id)
        else:
            if src_comp_id not in self._workflow_spec.edges:
                self._workflow_spec.edges[src_comp_id] = [target_comp_id]
            else:
                self._workflow_spec.edges[src_comp_id].append(target_comp_id)
            if self._drawable:
                self._drawable.add_edge(src_comp_id, target_comp_id)
        return self

    def add_stream_connection(self, src_comp_id: str, target_comp_id: str) -> Self:
        self._validate_edge(src_comp_id, target_comp_id, StatusCode.WORKFLOW_STREAM_EDGE_INVALID)
        self._graph.add_edge(src_comp_id, target_comp_id)
        stream_executables = self._graph.get_nodes()
        self._stream_actor.add_stream_consumer(stream_executables[target_comp_id], target_comp_id)
        if src_comp_id not in self._workflow_spec.stream_edges:
            self._workflow_spec.stream_edges[src_comp_id] = [target_comp_id]
        else:
            self._workflow_spec.stream_edges[src_comp_id].append(target_comp_id)

        if self._drawable:
            self._drawable.add_edge(src_comp_id, target_comp_id, False, True)
        return self

    def add_conditional_connection(self, src_comp_id: str, router: Router) -> Self:
        if not src_comp_id:
            raise build_error(StatusCode.WORKFLOW_CONDITION_EDGE_INVALID, src_comp_id=src_comp_id,
                              reason="src_comp_id cannot be empty or None",
                              workflow=self._workflow_config.card.str())
        if not isinstance(src_comp_id, str):
            raise build_error(StatusCode.WORKFLOW_CONDITION_EDGE_INVALID, src_comp_id=src_comp_id,
                              reason=f"src_comp_id must be a string, got {type(src_comp_id).__name__}",
                              workflow=self._workflow_config.card.str())
        if not router:
            raise build_error(StatusCode.WORKFLOW_CONDITION_EDGE_INVALID, src_comp_id=src_comp_id,
                              reason="router function is required for conditional edges",
                              workflow=self._workflow_config.card.str())
        elif not isinstance(router, Callable):
            raise build_error(StatusCode.WORKFLOW_CONDITION_EDGE_INVALID, src_comp_id=src_comp_id,
                              reason=f"router must be a callable function, got {type(router).__name__}",
                              workflow=self._workflow_config.card.str())
        if isinstance(router, BranchRouter):
            router.set_session(self._session)
            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=router)
        else:
            def new_router(state):
                sig = inspect.signature(router)
                if 'session' in sig.parameters:
                    return router(session=RouterSession(self._session))
                else:
                    return router()

            self._graph.add_conditional_edges(source_node_id=src_comp_id, router=new_router)

        if self._drawable:
            self._drawable.add_edge(source=src_comp_id, conditional=True, data=router)
        return self

    def compile(self, session: BaseSession, context: ModelContext = None) -> ExecutableGraph:
        if isinstance(session, WorkflowSession):
            session.set_workflow_id(self._workflow_config.card.id)
        if isinstance(session, SubWorkflowSession):
            main_workflow_config = session.config().get_workflow_config(
                session.main_workflow_id())
            if (main_workflow_config and
                    session.workflow_nesting_depth() > main_workflow_config.workflow_max_nesting_depth):
                raise build_error(StatusCode.WORKFLOW_COMPILE_ERROR,
                                  reason=f"workflow nesting hierarchy is too big, must <= "
                                         f"{main_workflow_config.workflow_max_nesting_depth}",
                                  workflow=main_workflow_config.card.str() if main_workflow_config
                                  else self._workflow_config.card.str())
        self._session.set_session(session)
        try:
            return self._graph.compile(session, context=context)
        except Exception as e:
            raise build_error(StatusCode.WORKFLOW_COMPILE_ERROR, cause=e, reason=str(e),
                              workflow=self._workflow_config.card)

    @property
    def drawable(self):
        return self._drawable

    def to_mermaid(self, title: str = "", expand_subgraph: int | bool = False, enable_animation: bool = False) -> str:
        if self._drawable:
            return self._drawable.to_mermaid(title=title, expand_subgraph=expand_subgraph,
                                             enable_animation=enable_animation)
        return ""

    def to_mermaid_png(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_png(title=title, expand_subgraph=expand_subgraph)
        return b""

    def to_mermaid_svg(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        if self._drawable:
            return self._drawable.to_mermaid_svg(title=title, expand_subgraph=expand_subgraph)
        return b""

    async def reset(self):
        await self._graph.reset()

    def auto_complete_abilities(self):
        """Auto-complete component abilities based on edge topology."""
        edge_topology = self._build_edge_topology()
        self._validate_edge_nodes(edge_topology)

        user_provided = self._get_user_provided_abilities()

        self._complete_loop_node_abilities(edge_topology, user_provided)
        self._complete_stream_node_abilities(edge_topology, user_provided)
        self._complete_invoke_abilities(edge_topology, user_provided)

    def _build_edge_topology(self) -> EdgeTopology:
        """Build edge topology context for ability inference."""
        source_map = self._workflow_spec.edges
        source_stream_map = self._workflow_spec.stream_edges
        return EdgeTopology(
            source_map=source_map,
            target_map=self._source_to_target_map(source_map),
            source_stream_map=source_stream_map,
            target_stream_map=self._source_to_target_map(source_stream_map),
        )

    def _validate_edge_nodes(self, edge_topology: EdgeTopology) -> None:
        """DFX: Validate all nodes in edges exist in comp_configs."""
        registered_comps = set(self._workflow_spec.comp_configs.keys())
        all_edge_nodes = edge_topology.all_edge_nodes()
        missing_nodes = all_edge_nodes - registered_comps
        if not missing_nodes:
            return

        edge_details = self._collect_problematic_edges(edge_topology, missing_nodes)
        raise build_error(StatusCode.WORKFLOW_COMPILE_ERROR,
                          reason=f"Component ID mismatch: nodes {sorted(missing_nodes)} are referenced in edges "
                                 f"but not registered via add_workflow_comp/set_start_comp/set_end_comp.\n"
                                 f"Registered components: {sorted(registered_comps)}\n"
                                 f"Problematic edges:\n" + "\n".join(edge_details))

    @staticmethod
    def _collect_problematic_edges(edge_topology: EdgeTopology, missing_nodes: set) -> list[str]:
        """Collect edge details that reference missing nodes."""
        edge_details = []
        for connection_type, edge_map in [(ConnectionType.CONNECTION, edge_topology.source_map),
                                          (ConnectionType.STREAM_CONNECTION, edge_topology.source_stream_map)]:
            for src, targets in edge_map.items():
                for tgt in targets:
                    if src in missing_nodes or tgt in missing_nodes:
                        edge_details.append(f"  - {connection_type.value}: '{src}' -> '{tgt}'")
        return edge_details

    def _get_user_provided_abilities(self) -> dict[str, bool]:
        """Check which components have user-provided ability configurations."""
        return {
            comp_id: len(comp_conf.abilities) > 0
            for comp_id, comp_conf in self._workflow_spec.comp_configs.items()
        }

    def _complete_loop_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for loop start/end nodes."""
        loop_start_nodes = getattr(self, '_start_nodes', None) or []
        loop_end_nodes = getattr(self, '_end_nodes', None) or []

        for node in loop_start_nodes:
            if not user_provided[node]:
                if node in edge_topology.source_stream_map:
                    self._add_ability_to_node(node, ComponentAbility.STREAM)
                if node in edge_topology.source_map:
                    self._add_ability_to_node(node, ComponentAbility.INVOKE)

        for node in loop_end_nodes:
            if not user_provided[node] and node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_stream_node_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete abilities for stream connection nodes (STREAM/TRANSFORM/COLLECT)."""
        # Nodes that output stream
        for node in edge_topology.source_stream_map:
            if user_provided[node]:
                continue
            # Has regular input + streaming output -> STREAM
            if node in edge_topology.target_map:
                self._add_ability_to_node(node, ComponentAbility.STREAM)
            # Has streaming input + streaming output -> TRANSFORM
            if node in edge_topology.target_stream_map:
                self._add_ability_to_node(node, ComponentAbility.TRANSFORM)
            elif node not in self._workflow_spec.start_nodes:
                # when branch taget node with stream edge
                self._add_ability_to_node(node, ComponentAbility.STREAM)

        # Nodes that receive stream
        for node in edge_topology.target_stream_map:
            if user_provided[node]:
                continue
            # Has streaming input + regular output -> COLLECT
            if node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.COLLECT)

    def _complete_invoke_abilities(self, edge_topology: EdgeTopology, user_provided: dict[str, bool]) -> None:
        """Complete INVOKE ability for regular connection nodes."""
        for node in edge_topology.target_map:
            if not user_provided[node] and node in edge_topology.source_map:
                self._add_ability_to_node(node, ComponentAbility.INVOKE)

    def _add_ability_to_node(self, comp_id: str, ability: ComponentAbility) -> None:
        """Add ability to a component if not already present."""
        abilities = self._workflow_spec.comp_configs[comp_id].abilities
        if ability not in abilities:
            abilities.append(ability)

    @staticmethod
    def _source_to_target_map(source_map: dict[str, list[str]]) -> dict[str, list[str]]:
        """Convert source->targets map to target->sources map."""
        target_map = {}
        for source, targets in source_map.items():
            for target in targets:
                if target not in target_map:
                    target_map[target] = []
                target_map[target].append(source)
        return target_map

    def _validate_comp_id(self, comp_id: str) -> None:
        """validate component id"""
        if not comp_id:
            raise build_error(StatusCode.WORKFLOW_COMPONENT_ID_INVALID, comp_id=comp_id, reason="is None or empty",
                              workflow=self._workflow_config.card.str())
        if not isinstance(comp_id, str):
            raise build_error(StatusCode.WORKFLOW_COMPONENT_ID_INVALID, comp_id=comp_id, reason="type is not string",
                              workflow=self._workflow_config.card.str())
        if len(comp_id) > 100:
            raise build_error(StatusCode.WORKFLOW_COMPONENT_ID_INVALID, comp_id=comp_id,
                              reason="length must not between [1, 100]", workflow=self._workflow_config.card.str())
        if not re.match(r'^[A-Za-z0-9_-]+$', comp_id):
            raise build_error(StatusCode.WORKFLOW_COMPONENT_ID_INVALID, comp_id=comp_id,
                              reason="only support letters (a–z, A–Z), "
                                     "digits (0–9), underscores (_) or hyphens (-)",
                              workflow=self._workflow_config.card.str())

    def _validate_comp_ability(self, comp_id: str, abilities, wait_for_all):
        if abilities is None:
            return
        for ability in abilities:
            if ability in [ComponentAbility.TRANSFORM, ComponentAbility.COLLECT]:
                if wait_for_all is None:
                    wait_for_all = True
                if not wait_for_all:
                    raise build_error(StatusCode.WORKFLOW_COMPONENT_ABILITY_INVALID, comp_id=comp_id,
                                      reason="stream components (TRANSFORM/COLLECT) must set 'wait_for_all' to True",
                                      workflow=self._workflow_config.card.str())

    def _validate_edge(self, src_comp_id: Union[str, list[str]], target_comp_id: str, error_code):
        if not src_comp_id:
            raise build_error(error_code, src_comp_id=src_comp_id,
                              target_comp_id=target_comp_id, reason="src_comp_id cannot be empty or None",
                              workflow=self._workflow_config.card.str())
        if isinstance(src_comp_id, list):
            for idx, comp_id in enumerate(src_comp_id):
                if not comp_id:
                    raise build_error(error_code, src_comp_id=src_comp_id,
                                      target_comp_id=target_comp_id,
                                      reason="src_comp_id list contains empty or None value at index {idx}",
                                      workflow=self._workflow_config.card.str())
                if not isinstance(comp_id, str):
                    raise build_error(error_code, src_comp_id=src_comp_id,
                                      target_comp_id=target_comp_id,
                                      reason="src_comp_id list contains non-string value at index {idx}: "
                                             f"{type(comp_id).__name__}",
                                      workflow=self._workflow_config.card.str())
        elif not isinstance(src_comp_id, str):
            raise build_error(error_code, src_comp_id=src_comp_id,
                              target_comp_id=target_comp_id,
                              reason=f"src_comp_id must be a string or list[string], got {type(src_comp_id).__name__}",
                              workflow=self._workflow_config.card.str())

    def _validate_schemas(self, comp_id, inputs_schema: dict | Transformer = None,
                          outputs_schema: dict | Transformer = None,
                          stream_inputs_schema: dict | Transformer = None,
                          stream_outputs_schema: dict | Transformer = None):
        if isinstance(inputs_schema, dict) and isinstance(stream_inputs_schema, dict):
            flatten_inputs_schema = flatten_dict(inputs_schema)
            flatten_stream_inputs_schema = flatten_dict(stream_inputs_schema)
            for key in flatten_inputs_schema.keys():
                if key in flatten_stream_inputs_schema.keys():
                    raise build_error(
                        StatusCode.WORKFLOW_COMPONENT_SCHEMA_INVALID,
                        comp_id=comp_id,
                        reason=f"duplicate key both exist in inputs_schema with stream_inputs_schema, "
                               f"key={key}",
                        workflow=self._workflow_config.card.str()
                    )
        if isinstance(outputs_schema, dict) and isinstance(stream_outputs_schema, dict):
            flatten_outputs_schema = flatten_dict(outputs_schema)
            flatten_stream_outputs_schema = flatten_dict(stream_outputs_schema)
            for key in flatten_outputs_schema.keys():
                if key in flatten_stream_outputs_schema.keys():
                    raise build_error(
                        StatusCode.WORKFLOW_COMPONENT_SCHEMA_INVALID,
                        reason=f"duplicate key both exist in outputs_schema with stream_outputs_schema, "
                               f"key={key}")
