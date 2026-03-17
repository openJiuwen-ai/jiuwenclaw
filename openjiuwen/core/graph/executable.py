# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import TypeVar, Generic, AsyncIterator, Any

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.session import BaseSession

Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", covariant=True)


class Executable(Generic[Input, Output]):
    async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        class_name = type(self).__name__
        raise NotImplementedError(
            f"Component '{class_name}' does not implement the on_invoke method. "
            f"Please override this method in the subclass to provide inference logic. "
            f"Required implementation: async def on_invoke(self, inputs: Input, session: BaseSession, **kwargs) -> "
            f"Output:"
        )

    async def on_stream(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        class_name = type(self).__name__
        raise NotImplementedError(
            f"Component '{class_name}' does not implement the on_stream method. "
            f"Please override this method in the subclass to provide streaming logic. "
            f"Required implementation: async def on_stream(self, inputs: Input, session: BaseSession, **kwargs) -> "
            f"AsyncIterator[Output]:"
        )

    async def on_collect(self, inputs: Input, session: BaseSession, **kwargs) -> Output:
        class_name = type(self).__name__
        raise NotImplementedError(
            f"Component '{class_name}' does not implement the on_collect method. "
            f"Please override this method in the subclass to provide collection logic. "
            f"Required implementation: async def on_collect(self, inputs: Input, session: BaseSession, **kwargs) -> "
            f"Output:"
        )

    async def on_transform(self, inputs: Input, session: BaseSession, **kwargs) -> AsyncIterator[Output]:
        class_name = type(self).__name__
        raise NotImplementedError(
            f"Component '{class_name}' does not implement the on_transform method. "
            f"Please override this method in the subclass to provide transformation logic. "
            f"Required implementation: async def on_transform(self, inputs: Input, session: BaseSession, **kwargs) -> "
            f"AsyncIterator[Output]:"
        )

    def skip_trace(self) -> bool:
        return False

    def graph_invoker(self) -> bool:
        return False

    def post_commit(self) -> bool:
        return True

    def component_type(self) -> str:
        return ""


GeneralExecutor = Executable[dict[str, Any], dict[str, Any]]
