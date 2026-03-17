# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import inspect
from abc import ABCMeta, abstractmethod
from functools import wraps
from typing import Any, AsyncIterator, Dict, Type
from typing import TypeVar
from pydantic import BaseModel, Field

from openjiuwen.core.common import BaseCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.schema import ToolInfo

Input = TypeVar('Input', contravariant=True)
Output = TypeVar('Output', contravariant=True)


class ToolCard(BaseCard):
    input_params: Dict[str, Any] | Type[BaseModel] = Field(default_factory=dict)
    properties: Dict[str, Any] = Field(default_factory=dict)

    def tool_info(self):
        return ToolInfo(name=self.name, description=self.description, parameters=self.input_params)


class _ToolMeta(ABCMeta):
    def __call__(cls, *args, **kwargs):
        instance = super().__call__(*args, **kwargs)
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.runner.callback.events import ToolCallEvents
        _fw = Runner.callback_framework

        _original_invoke = instance.invoke

        @wraps(_original_invoke)
        async def _lifecycle_invoke(*a, **kw):
            await _fw.trigger(ToolCallEvents.TOOL_CALL_STARTED,
                              tool_name=instance.card.name,
                              tool_id=instance.card.id,
                              inputs=(a, kw))
            try:
                result = await _original_invoke(*a, **kw)
                await _fw.trigger(ToolCallEvents.TOOL_CALL_FINISHED,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  inputs=(a, kw),
                                  result=result)
                return result
            except Exception as e:
                await _fw.trigger(ToolCallEvents.TOOL_CALL_ERROR,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  error=e)
                raise

        instance.invoke = _lifecycle_invoke

        _original_stream = instance.stream

        if inspect.isasyncgenfunction(_original_stream):
            @wraps(_original_stream)
            async def _lifecycle_stream(*a, **kw):
                await _fw.trigger(ToolCallEvents.TOOL_CALL_STARTED,
                                  tool_name=instance.card.name,
                                  tool_id=instance.card.id,
                                  inputs=(a, kw))
                try:
                    async for chunk in _original_stream(*a, **kw):
                        await _fw.trigger(ToolCallEvents.TOOL_RESULT_RECEIVED,
                                          tool_name=instance.card.name,
                                          tool_id=instance.card.id,
                                          inputs=(a, kw),
                                          result=chunk)
                        yield chunk
                    await _fw.trigger(ToolCallEvents.TOOL_CALL_FINISHED,
                                      tool_name=instance.card.name,
                                      tool_id=instance.card.id)
                except Exception as e:
                    await _fw.trigger(ToolCallEvents.TOOL_CALL_ERROR,
                                      tool_name=instance.card.name,
                                      tool_id=instance.card.id,
                                      error=e)
                    raise

            instance.stream = _lifecycle_stream

        fn = instance.invoke
        fn = _fw.emit_before(ToolCallEvents.TOOL_INVOKE_INPUT)(fn)
        fn = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_INVOKE_INPUT,
            output_event=ToolCallEvents.TOOL_INVOKE_OUTPUT,
        )(fn)
        fn = _fw.emit_after(ToolCallEvents.TOOL_INVOKE_OUTPUT)(fn)
        instance.invoke = fn

        fn = instance.stream
        fn = _fw.emit_before(ToolCallEvents.TOOL_STREAM_INPUT)(fn)
        fn = _fw.transform_io(
            input_event=ToolCallEvents.TOOL_STREAM_INPUT,
            output_event=ToolCallEvents.TOOL_STREAM_OUTPUT,
        )(fn)
        fn = _fw.emit_after(ToolCallEvents.TOOL_STREAM_OUTPUT, item_key="result")(fn)
        instance.stream = fn
        return instance


class Tool(metaclass=_ToolMeta):
    """tool class that defined the data types and content for LLM modules"""

    def __init__(self, card: ToolCard):
        """Constructs a new tool instance with the given configuration.

        Args:
            card: ToolCard configuration defining tool behavior and parameters

        Note:
            The tool card is stored internally and used for validation and
            metadata purposes throughout the tool's lifecycle.
        """
        if card is None:
            raise build_error(StatusCode.TOOP_CARD_INVALID, card=card, reason="card is None")
        if not card.id:
            raise build_error(StatusCode.TOOP_CARD_INVALID, card=card, reason="card is is None or empty")
        self._card = card

    @property
    def card(self) -> ToolCard:
        return self._card

    @abstractmethod
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Execute the tool with provided inputs and return final result.

        This method performs complete tool execution in a single call,
        processing all inputs and returning the final output when the
        operation is fully completed.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters such as timeout,
                     retry policies, or tool-specific options

        Returns:
            Output: The complete result of tool execution

        """
        pass

    @abstractmethod
    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Execute the tool and stream incremental results.

        This method supports long-running operations by yielding partial
        results as they become available, enabling real-time processing
        and progress tracking.

        Args:
            inputs: Structured input data conforming to the tool's input schema
            **kwargs: Additional execution parameters for streaming behavior

        Yields:
            Output: Incremental results during tool execution

        """
        pass
