# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from enum import Enum
from typing import Self, Union, Callable, Any, Optional, Dict

from pydantic import BaseModel, Field

from openjiuwen.core.common.constants.constant import INDEX, CONFIG_KEY, LOOP_ID, FINISH_INDEX
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow.components.component import ComponentComposable, WorkflowComponent
from openjiuwen.core.workflow.components.condition.array import ArrayConditionInSession
from openjiuwen.core.workflow.components.condition.condition import Condition, AlwaysTrue, FuncCondition
from openjiuwen.core.workflow.components.condition.expression import ExpressionCondition
from openjiuwen.core.workflow.components.condition.number import NumberConditionInSession
from openjiuwen.core.workflow.components.flow.loop.callback.intermediate_loop_var import \
    IntermediateLoopVarCallback
from openjiuwen.core.workflow.components.flow.loop.callback.loop_callback import LoopCallback, END_ROUND, \
    START_ROUND, OUT_LOOP, \
    FIRST_LOOP
from openjiuwen.core.workflow.components.flow.loop.callback.output import OutputCallback
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.atomic_node import AtomicNode
from openjiuwen.core.graph.base import Graph, INPUTS_KEY
from openjiuwen.core.graph.executable import Output, Input, Executable
from openjiuwen.core.session import LOOP_NUMBER_MAX_LIMIT_DEFAULT, LOOP_NUMBER_MAX_LIMIT_KEY, Transformer, \
    extract_origin_key, NESTED_PATH_SPLIT, is_ref_path
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.session import NodeSession, SubWorkflowSession
from openjiuwen.core.graph.stream_actor.manager import ActorManager
from openjiuwen.core.workflow._workflow import BaseWorkflow
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.graph.graph import PregelGraph
from openjiuwen.core.graph.pregel import GraphInterrupt, START, END

BROKEN = "_broken"
FIRST_IN_LOOP = "_first_in_loop"

CONDITION_NODE_ID = "condition"
BODY_NODE_ID = "body"
POST_BODY_NODE_ID = "post_body"


class LoopGroup(BaseWorkflow, Executable):

    def __init__(self):
        super().__init__()
        self.compiled_graph = None
        self.group_input_schema = {}
        self._break_components = []
        self._start_nodes = []
        self._end_nodes = []

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
        # Check for nested loop components
        if isinstance(workflow_comp, LoopComponent):
            raise build_error(
                StatusCode.COMPONENT_LOOP_GROUP_PARAM_INVALID,
                reason="cannot add 'LoopComponent' to a loop group."
            )
        super().add_workflow_comp(comp_id, workflow_comp, wait_for_all=wait_for_all, inputs_schema=inputs_schema,
                                  stream_inputs_schema=stream_inputs_schema,
                                  stream_outputs_schema=stream_outputs_schema,
                                  outputs_schema=outputs_schema, comp_ability=comp_ability)
        if isinstance(workflow_comp, LoopBreakComponent):
            self._break_components.append(workflow_comp)
        if self._drawable and isinstance(workflow_comp, LoopBreakComponent):
            self._drawable.set_break_node(comp_id)

    def start_nodes(self, nodes: list[str]) -> Self:
        for node in nodes:
            self.start_comp(node)
        self._start_nodes = nodes
        return self

    def start_comp(self, start_comp_id: str) -> Self:
        """Record start nodes even if caller uses BaseWorkflow API directly."""
        super().start_comp(start_comp_id)
        if start_comp_id not in self._start_nodes:
            self._start_nodes.append(start_comp_id)
        return self

    def end_nodes(self, nodes: list[str] | str) -> Self:
        if isinstance(nodes, str):
            self.end_comp(nodes)
            self._end_nodes.append(nodes)
        elif isinstance(nodes, list):
            for node in nodes:
                self.end_comp(node)
            self._end_nodes = nodes
        return self

    def end_comp(self, end_comp_id: str) -> Self:
        """Record end nodes even if caller uses BaseWorkflow API directly."""
        super().end_comp(end_comp_id)
        if end_comp_id not in self._end_nodes:
            self._end_nodes.append(end_comp_id)
        return self

    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        self.auto_complete_abilities()
        actor_manager = ActorManager(self._workflow_spec, self._stream_actor, sub_graph=True, session=session)
        loop_session = SubWorkflowSession(session.parent(), self._workflow_config.card.id, actor_manager)
        loop_session.config().add_workflow_config(workflow_id=self._workflow_config.card.id,
                                                  workflow_config=self._workflow_config)
        self.compiled_graph = self.compile(loop_session, context=kwargs.get("context"))
        await self.compiled_graph.invoke(inputs, loop_session)
        return None

    def skip_trace(self) -> bool:
        return True

    def graph_invoker(self) -> bool:
        return True

    @property
    def break_components(self):
        return self._break_components

    def check_validate(self):
        if not self._start_nodes:
            raise build_error(
                StatusCode.COMPONENT_LOOP_GROUP_PARAM_INVALID,
                reason="missing start_nodes in loop group"
            )
        if not self._end_nodes:
            raise build_error(
                StatusCode.COMPONENT_LOOP_GROUP_PARAM_INVALID,
                reason="missing end_nodes in loop group"
            )
        nodes = self._graph.get_nodes()
        if len(nodes) == 0:
            raise build_error(StatusCode.COMPONENT_LOOP_GROUP_PARAM_INVALID,
                              reason="loop group is empty (contains no nodes)")


class LoopComponent(WorkflowComponent):
    def __init__(self, loop_group: LoopGroup, output_schema: dict):
        super().__init__()
        self._loop_group = loop_group
        self._output_schema = output_schema
        self._loop_group.check_validate()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        try:
            if not isinstance(inputs, dict):
                raise KeyError(f"inputs must be a dict, but got {type(inputs).__name__}")

            if INPUTS_KEY not in inputs:
                raise KeyError(f"missing required key {INPUTS_KEY}")

            loop_input = LoopInput.model_validate(inputs.get(INPUTS_KEY))
            condition: Condition
            if loop_input.loop_type == LoopType.Array.value:
                condition = ArrayConditionInSession(loop_input.loop_array)
            elif loop_input.loop_type == LoopType.Number.value:
                max_loop_limit = session.get_env(LOOP_NUMBER_MAX_LIMIT_KEY) or LOOP_NUMBER_MAX_LIMIT_DEFAULT
                try:
                    max_loop_limit = int(max_loop_limit)
                except (TypeError, ValueError):
                    max_loop_limit = LOOP_NUMBER_MAX_LIMIT_DEFAULT

                if loop_input.loop_number is None:
                    raise ValueError("loop_number variable not found or is None")

                if loop_input.loop_number > max_loop_limit:
                    raise ValueError(f"loop_number exceeds maximum limit {max_loop_limit}")

                condition = NumberConditionInSession(loop_input.loop_number)
            elif loop_input.loop_type == LoopType.AlwaysTrue.value:
                condition = AlwaysTrue()
            elif loop_input.loop_type == LoopType.Expression.value:
                if isinstance(loop_input.bool_expression, bool):
                    condition = FuncCondition(lambda: loop_input.bool_expression)
                else:
                    condition = ExpressionCondition(loop_input.bool_expression)
            else:
                raise ValueError(f"invalid loop type '{loop_input.loop_type}' for LoopComponent")
            output_callback = OutputCallback(self._output_schema)
            callbacks: list = [output_callback]
            if loop_input.intermediate_var:
                callbacks.append(IntermediateLoopVarCallback(loop_input.intermediate_var))

            loop_component = AdvancedLoopComponent(self._loop_group, condition, self._loop_group.break_components,
                                                   callbacks)
            return await loop_component.on_invoke({INPUTS_KEY: {}, CONFIG_KEY: inputs.get(CONFIG_KEY)},
                                                  getattr(session, "_inner"))
        except GraphInterrupt:
            raise
        except BaseError:
            raise
        except Exception as e:
            raise build_error(
                StatusCode.COMPONENT_LOOP_EXECUTION_ERROR,
                cause=e,
                comp=session.get_component_descrip(),
                reason=str(e),
            )

    def graph_invoker(self) -> bool:
        return True

    @property
    def loop_group(self) -> LoopGroup:
        return self._loop_group


class LoopController(ABC):
    @abstractmethod
    def break_loop(self):
        raise NotImplementedError()

    @abstractmethod
    def is_broken(self) -> bool:
        raise NotImplementedError()


class LoopBreakComponent(WorkflowComponent):
    def __init__(self):
        super().__init__()
        self._loop_controller = None

    def set_controller(self, loop_controller: LoopController):
        self._loop_controller = loop_controller

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self._loop_controller is None:
            raise build_error(
                StatusCode.COMPONENT_LOOP_BREAK_EXECUTION_ERROR,
                reason="failed to initialize loop controller",
                comp=session.get_component_descrip()
            )
        self._loop_controller.break_loop()
        return {}


class LoopSetVariableComponent(WorkflowComponent):

    def __init__(self, variable_mapping: dict[str, Any]):
        super().__init__()
        if not variable_mapping:
            raise build_error(
                StatusCode.COMPONENT_LOOP_SET_VAR_PARAM_INVALID,
                error_msg=f'variable_mapping is None or empty'
            )
        self._variable_mapping = variable_mapping

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        root_session = getattr(session, "_inner").parent()
        for left, right in self._variable_mapping.items():
            left_ref_str = extract_origin_key(left)
            keys = left_ref_str.split(NESTED_PATH_SPLIT)

            if len(keys) == 0:
                raise build_error(
                    StatusCode.COMPONENT_LOOP_SET_VAR_EXECUTION_ERROR,
                    comp=session.get_component_descrip(),
                    reason=f'key[{left}] not supported format',

                )

            node_id = keys[0]
            node_session = NodeSession(root_session, node_id)
            node_session.state().set_outputs(LoopSetVariableComponent.generate_output(
                keys[1:], LoopSetVariableComponent.generate_value(session, right)
            ))
        return None

    @staticmethod
    def generate_value(session: Session, value: Any):
        if isinstance(value, str) and is_ref_path(value):
            ref_str = extract_origin_key(value)
            return session.get_global_state(ref_str)
        return value

    @staticmethod
    def generate_output(keys: list[str], value: Any):
        output = value
        for i in range(len(keys) - 1, -1, -1):
            key = keys[i]
            output = {key: output}

        return output


class EmptyExecutable(Executable):
    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        pass

    def skip_trace(self) -> bool:
        return True


class PostLoopBody(Executable):
    def __init__(self):
        self._finish_index = -1

    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        finish_index = session.state().get(FINISH_INDEX)
        if finish_index is not None:
            self._finish_index = finish_index
        self._finish_index += 1
        session.state().update({FINISH_INDEX: self._finish_index})
        session.state().commit()
        return None

    def skip_trace(self) -> bool:
        return True

    def get_finish_index(self) -> int:
        return self._finish_index

    def set_finish_index(self, finish_index: int) -> None:
        self._finish_index = finish_index


class AdvancedLoopComponent(ComponentComposable, LoopController, Executable, AtomicNode):

    def __init__(self, body: Executable,
                 condition: Union[str, Callable[[], bool], Condition] = None,
                 break_nodes: list[LoopBreakComponent] = None,
                 callbacks: list[LoopCallback] = None, new_graph: Graph = None):
        super().__init__()
        self._node_id = None
        self._body = body
        self._post_body = PostLoopBody()

        self._condition: Condition
        if condition is None:
            self._condition = AlwaysTrue()
        elif isinstance(condition, Condition):
            self._condition = condition
        elif isinstance(condition, Callable):
            self._condition = FuncCondition(condition)
        elif isinstance(condition, str):
            self._condition = ExpressionCondition(condition)

        if break_nodes:
            for break_node in break_nodes:
                break_node.set_controller(self)

        self._callbacks: list[LoopCallback] = []
        if callbacks:
            for callback in callbacks:
                self.register_callback(callback)

        self._graph = new_graph if new_graph is not None else PregelGraph()
        self._graph.add_node(BODY_NODE_ID, self._body)
        self._graph.add_node(CONDITION_NODE_ID, EmptyExecutable())
        self._graph.add_node(POST_BODY_NODE_ID, self._post_body)
        self._graph.add_edge(START, CONDITION_NODE_ID)
        self._graph.add_edge(BODY_NODE_ID, POST_BODY_NODE_ID)
        self._graph.add_edge(POST_BODY_NODE_ID, CONDITION_NODE_ID)
        self._graph.add_conditional_edges(CONDITION_NODE_ID, self)

        self._in_loop = [BODY_NODE_ID]
        self._out_loop = [END]
        self._node_session = None

    def register_callback(self, callback: LoopCallback):
        self._callbacks.append(callback)

    def __call__(self, *args, **kwargs) -> list[str]:
        return self.atomic_invoke(session=self._node_session)

    def _atomic_invoke(self, **kwargs) -> Any:
        try:
            outputs = self._condition_invoke(session=self._node_session)
            return outputs
        except Exception as e:
            if isinstance(e, BaseError):
                raise
            raise build_error(
                StatusCode.COMPONENT_LOOP_CONDITION_EXECUTION_ERROR,
                reason=str(e),
                comp=self._node_id,
                cause=e
            ) from e

    def _condition_invoke(self, session: BaseSession) -> Output:
        index = session.state().get(INDEX)
        if index is None:
            session.state().update({BROKEN: False, INDEX: 0})
            session.state().set_outputs({INDEX: 0})
            session.state().commit()
            index = 0

        finish_index = self._post_body.get_finish_index()
        if finish_index + 1 < index or finish_index > index:
            # resume from checkpoint
            finish_index = index - 1

        if finish_index == index:
            session.state().update({INDEX: index + 1})
            session.state().set_outputs({INDEX: index + 1})
            session.state().commit()

        continue_loop = False if self.is_broken() else self._condition(session=session)
        for callback in self._callbacks:
            if finish_index < 0:
                callback(FIRST_LOOP, session)
            elif finish_index == index:
                callback(END_ROUND, session, index + 1)
            if continue_loop:
                callback(START_ROUND, session)
            else:
                callback(OUT_LOOP, session)

        if not continue_loop:
            session.state().update({INDEX: None, BROKEN: False})
            self._post_body.set_finish_index(-1)
            session.parent().state().update({POST_BODY_NODE_ID: None})
            session.state().set_outputs({INDEX: None})

        return self._in_loop if continue_loop else self._out_loop

    def is_broken(self) -> bool:
        _is_broken = self._node_session.state().get(BROKEN)
        if isinstance(_is_broken, bool):
            return _is_broken
        return False

    def break_loop(self):
        self._node_session.state().update({BROKEN: True})

    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        loop_session = session
        self._node_id = loop_session.node_id()
        self._node_session = NodeSession(loop_session, self._node_id)

        loop_session.state().set_outputs({LOOP_ID: self._node_id})
        state = loop_session.state()._io_state.get_state()
        state = state.get(session.parent_id()) if session.parent_id() else state
        if state and self._node_id in state:
            del state[self._node_id]
        loop_session.state().set_outputs(state)
        loop_session.state().commit()

        if loop_session.tracer() is not None:
            loop_session.tracer().register_workflow_span_manager(loop_session.executable_id())
        compiled = self._graph.compile(loop_session, **kwargs)
        await compiled.invoke(inputs, loop_session)
        result = self._node_session.state().get_outputs(self._node_id)
        loop_session.state()._io_state.update_by_id(self._node_id, {self._node_id: None})
        return result

    def graph_invoker(self) -> bool:
        return True

    @property
    def body(self) -> LoopGroup:
        return self._body


class LoopType(str, Enum):
    Array = "array"
    Number = "number"
    AlwaysTrue = "always_true"
    Expression = "expression"


class LoopInput(BaseModel):
    loop_type: Optional[str] = Field("")
    loop_number: Optional[int] = Field(0)
    loop_array: Optional[Dict[str, Any]] = Field(default_factory=dict)
    bool_expression: Optional[Union[str, bool]] = Field("")
    intermediate_var: Dict[str, Union[str, Any]] = Field(default_factory=dict)
