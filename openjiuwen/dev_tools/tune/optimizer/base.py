# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod
from typing import Dict, Optional, Any, List, Callable
import threading

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import agent_logger, LogEventType
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.session.agent import Session
from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.dev_tools.tune.base import EvaluatedCase


class BaseOptimizer:
    def __init__(self,
                 parameters: Optional[Dict[str, LLMCall]] = None,
                 **kwargs
                 ):
        self._parameters: Dict[str, TextualParameter] = {}
        self._history = OptimizeHistory()
        self._bad_cases: List[EvaluatedCase] = []
        self.bind_parameter(parameters)

    def __enter__(self):
        self._batch_set_optimizer_callback(self.trace_callback)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._batch_set_optimizer_callback(None)

    async def __aenter__(self):
        self._batch_set_optimizer_callback(self.trace_callback)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._batch_set_optimizer_callback(None)

    def bind_parameter(self, parameters: Dict[str, LLMCall]):
        if parameters is None:
            return
        for name, llm_call in parameters.items():
            if not llm_call:
                raise build_error(
                    StatusCode.TOOLCHAIN_OPTIMIZER_PARAM_ERROR,
                    error_msg=f"cannot bind a None parameter of {name}"
                )
            self._parameters[name] = TextualParameter(llm_call)
        self._history = OptimizeHistory()
        self._bad_cases: List[EvaluatedCase] = []

    def backward(self,
                 evaluated_cases: List[EvaluatedCase],
                 ):
        self._validate_parameters()
        self._get_bad_cases(evaluated_cases)
        try:
            self._backward(evaluated_cases)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR,
                error_msg=f"{str(e)}",
                cause=e
            ) from e

    def update(self):
        self._validate_parameters()
        try:
            self._update()
            for name, param in self._parameters.items():
                agent_logger.info(
                    "LLM call basic system prompt info",
                    metadata={
                        "llm_call_name": name,
                        "frozen_system_prompt": param.llm_call.get_freeze_system_prompt(),
                        "system_prompt_content": str(param.llm_call.get_system_prompt().content)
                    },
                    event_type=LogEventType.AGENT_START
                )
                agent_logger.info(
                    "LLM call basic user prompt info",
                    metadata={
                        "llm_call_name": name,
                        "frozen_system_prompt": param.llm_call.get_freeze_user_prompt(),
                        "system_prompt_content": str(param.llm_call.get_user_prompt().content)
                    },
                    event_type=LogEventType.AGENT_START
                )
            self._history.clear_history()
        except Exception as e:
            self._history.clear_history()
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR,
                error_msg=f"{str(e)}",
                cause=e
            ) from e

    @abstractmethod
    def _update(self):
        pass

    @abstractmethod
    def _backward(self,
                 evaluated_cases: List[EvaluatedCase],
                 ):
        pass

    def parameters(self) -> Dict[str, "TextualParameter"]:
        return self._parameters

    async def trace_callback(self,
                             llm_call_id: str,
                             node_input: Dict[str, str],
                             output: BaseMessage,
                             session: Session
                             ):
        trace_node = TraceNode(
            case_id=session.get_session_id(),
            llm_call_id=llm_call_id,
            inputs=node_input,
            outputs=TuneUtils.get_output_string_from_message(output)
        )
        self._history.add_history(session.get_session_id(), trace_node)

    def _batch_set_optimizer_callback(self, callback: Optional[Callable]) -> None:
        for _, param in self._parameters.items():
            param.llm_call.set_optimizer_callback(callback)

    def _get_bad_cases(self, evaluated_cases: List[EvaluatedCase]) -> List[EvaluatedCase]:
        bad_cases = [case for case in evaluated_cases if case.score == 0]
        self._bad_cases = bad_cases
        return bad_cases

    def _validate_parameters(self):
        if not self._parameters:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg="cannot optimize empty parameters"
            )


class TextualParameter:
    def __init__(self, llm_call: LLMCall):
        self.llm_call = llm_call
        self.gradients: Dict[str, str] = {}
        self.description: str = ""

    def set_gradient(self, name: str, gradient: str):
        self.gradients[name] = gradient

    def get_gradient(self, name: str) -> Optional[str]:
        return self.gradients.get(name)

    def set_description(self, description: str):
        self.description = description

    def get_description(self) -> str:
        return self.description


class TraceNode(BaseModel):
    case_id: str = Field(...)
    llm_call_id: str = Field(...)
    inputs: Dict[str, Any] = Field(...)
    outputs: str = Field(default="")
    history: List[BaseMessage] = Field(default=[])
    tools: List[ToolInfo] | List[Dict] = Field(default=[])


class OptimizeHistory:
    def __init__(self):
        self._trajectory: Dict[str, List[TraceNode]] = {}
        self._lock = threading.Lock()

    def add_history(self, case_id: str, node: TraceNode):
        with self._lock:
            if case_id not in self._trajectory:
                self._trajectory[case_id] = []
            self._trajectory[case_id].append(node)

    def get_history(self, case_id: str) -> Optional[List[TraceNode]]:
        if case_id not in self._trajectory:
            return None
        return self._trajectory[case_id]

    def get_llm_call_history(self, case_id: str, llm_call_id: str) -> Optional[List[TraceNode]]:
        trace_node_list = self.get_history(case_id)
        if not trace_node_list:
            return None
        llm_call_trace_node_list = []
        for node in trace_node_list:
            if node.llm_call_id == llm_call_id:
                llm_call_trace_node_list.append(node)
        return llm_call_trace_node_list

    def clear_history(self):
        self._trajectory = {}