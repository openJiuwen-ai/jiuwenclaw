# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod
from typing import Callable, Any

from openjiuwen.core.session import BaseSession
from openjiuwen.core.graph.atomic_node import AtomicNode
from openjiuwen.core.graph.executable import Input, Output


class Condition(AtomicNode):
    def __init__(self, input_schema: Any = None):
        self._input_schema = input_schema

    def __call__(self, session: BaseSession) -> bool:
        return self.atomic_invoke(session=session)

    def _atomic_invoke(self, **kwargs) -> Any:
        session: BaseSession = kwargs["session"]
        inputs = session.state().get_inputs(self._input_schema) if self._input_schema is not None else {}
        result = self.invoke(inputs=inputs, session=session)
        if isinstance(result, tuple):
            session.state().set_outputs(result[1])
            result = result[0]
        return result

    @abstractmethod
    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        pass

    def trace_info(self, session: BaseSession = None):
        return ""


class FuncCondition(Condition):
    def __init__(self, func: Callable[[], bool]):
        super().__init__()
        self._func = func

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        return self._func()

    def trace_info(self, session: BaseSession = None):
        return self._func.__name__


class AlwaysTrue(Condition):
    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        return True

    def trace_info(self, session: BaseSession = None):
        return "True"
