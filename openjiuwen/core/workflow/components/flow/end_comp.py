# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import re
import string
from typing import AsyncIterator, Union, AsyncGenerator, Any

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.common.constants.constant import END_NODE_STREAM
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import workflow_logger, LogEventType
from openjiuwen.core.common.utils.dict_utils import extract_leaf_nodes, format_path
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session import END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY, \
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY, get_value_by_nested_path
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.stream import OutputSchema


class EndConfig(BaseModel):
    response_template: str = Field(
        alias="responseTemplate",
        description="Response template for formatting the final output, format result {\"response\": \"...\"}",
        examples=["Hello {{name}}", "Error: {{error_msg}}"],
        min_length=1
    )
    model_config = ConfigDict(
        populate_by_name=True,
    )


class End(WorkflowComponent):
    def __init__(self, conf: Union[EndConfig, dict] = None):
        super().__init__()
        if conf:
            try:
                self._conf = EndConfig.model_validate(conf)
                self._template = TemplateProcessor(self._conf.response_template)
            except Exception as e:
                raise build_error(StatusCode.COMPONENT_END_PARAM_INVALID, reason=f"conf is invalid, {str(e)}", cause=e)
        else:
            self._conf = None
            self._template = None
        self._batch_template = None
        self._mix = False

    def set_mix(self):
        self._mix = True
        if self._template:
            self._template.set_data_source_count(2)
            self._template.reset()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        workflow_logger.debug(
            "End component invoke started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_component_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            inputs=inputs,
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        result = None
        if self._template is not None:
            if inputs is None:
                inputs = {}
            result = await self._render(inputs, session.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY), session)
        else:
            if inputs is not None:
                output = {k: v for k, v in inputs.items() if v is not None} if isinstance(inputs, dict) else inputs
            else:
                output = None
            result = {"output": output} if output is not None else output
        workflow_logger.debug(
            "End component invoke succeed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=session.get_component_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            outputs=result,
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        return result

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        workflow_logger.debug(
            "End component stream started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_component_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            inputs=inputs,
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        frame_count = 0
        if inputs is None:
            inputs = {}
        try:
            frame_count = 0
            if self._template is not None:
                generator = self._template.render_stream(inputs,
                                                         session,
                                                         session.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY),
                                                         )
                async for frame in generator:
                    frame_count += 1
                    yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                       payload=dict(response=frame.get("data")))
            else:
                if isinstance(inputs, dict):
                    for key, value in inputs.items():
                        yield dict(output={key: value})
                        frame_count += 1
                else:
                    yield dict(output=inputs)
                    frame_count += 1
            workflow_logger.debug(
                "End component stream succeed",
                event_type=LogEventType.WORKFLOW_COMPONENT_END,
                component_id=session.get_component_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                metadata={"has_template": self._template is not None, "mix": self._mix, "chunk_num": frame_count}
            )

        except Exception as e:
            workflow_logger.error(
                "End component stream failed",
                event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                component_id=session.get_component_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                exception=e,
                metadata={"has_template": self._template is not None, "mix": self._mix, "chunk_num": frame_count}
            )
            raise

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        workflow_logger.debug(
            "End component transform started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        frame_count = 0
        if self._template is not None:
            generator = self._template.render_stream(inputs,
                                                     session,
                                                     session.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY)
                                                     )
            async for frame in generator:
                yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                   payload=dict(response=frame.get("data")))
                frame_count += 1
        else:
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        yield dict(output={format_path(path): frame})
                        frame_count += 1
                else:
                    yield dict(output={format_path(path): value})
                    frame_count += 1
        workflow_logger.debug(
            "End component transform succeed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_template": self._template is not None, "mix": self._mix, "chunk_num": frame_count}
        )

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        workflow_logger.debug(
            "End component collect started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        result = None
        if self._template is not None:
            result = await self._render(inputs, session.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY), session)
        else:
            chunks = []
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        chunks.append({format_path(path): frame})
                else:
                    chunks.append({format_path(path): value})

            result = {"output": chunks}
        workflow_logger.debug(
            "End component collect succeed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            ouputs=result,
            metadata={"has_template": self._template is not None, "mix": self._mix}
        )
        return result

    async def _render(self, inputs: Input, timeout: float = 0.2, session: Session = None):
        render_timeout = timeout if timeout and timeout > 0 else None
        if self._batch_template is None:
            processor = TemplateBatchProcessor(self._template, inputs)
            self._batch_template = processor
            if self._mix:
                async with self._batch_template.condition:
                    try:
                        await asyncio.wait_for(self._batch_template.condition.wait(), render_timeout)
                    except asyncio.TimeoutError as e:
                        workflow_logger.error(
                            f"End component render timeout {render_timeout}s",
                            event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                            component_id=session.get_component_id(),
                            component_type_str="End",
                            metadata={"error": str(e)}
                        )
                        if not self._batch_template.is_rendered():
                            answer = await self._batch_template.render(inputs, session)
                            self._batch_template = None
                            return {"response": answer}
                        return None
                self._batch_template = None
                return None
        answer = await self._batch_template.render(inputs, session)
        async with self._batch_template.condition:
            self._batch_template.condition.notify_all()
        self._batch_template = None
        return {
            "response": answer,
        }


class TemplateProcessor:
    def __init__(self, template: str):
        self._template = template
        response_list = TemplateUtils.render_template_to_list(template)
        self._segments = response_list
        self._variables_positions: set[int] = set()
        self._current_position = 0
        for pos, res in enumerate(response_list):
            if res.startswith("{{") and res.endswith("}}"):
                self._variables_positions.add(pos)
                self._segments[pos] = res[2:-2]

        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._chunk_index = 0

        self._data_source_count = 1
        self._count = 0

    def set_data_source_count(self, data_source_count: int):
        self._data_source_count = data_source_count
        self._count = 0

    def current_position(self) -> int:
        return self._current_position

    def get_current_segment(self) -> str:
        return self._get_segment(self._current_position)

    def _need_render(self, inputs) -> bool:
        if not isinstance(inputs, dict):
            return False
        # Only check variable segments, not static text segments
        for pos, seg in enumerate(self._segments):
            if pos in self._variables_positions:
                if get_value_by_nested_path(seg, inputs) is not None:
                    return True
        return False

    def _get_segment(self, pos: int) -> str:
        if pos >= len(self._segments):
            return ""
        return self._segments[pos]

    def should_render(self) -> bool:
        return self._current_position in self._variables_positions

    def advance_position(self) -> int:
        self._current_position += 1
        return self._current_position

    def render(self, inputs: dict) -> str:
        return TemplateUtils.render_template(self._template, inputs)

    def reset(self):
        if self._current_position != 0:
            self._current_position = 0
        self._chunk_index = 0
        self._count = 0

    async def render_stream(self, inputs: dict, session: Session, timeout: float = 0.2) -> AsyncGenerator:
        try:
            async for frame in self._render_stream(inputs, timeout, session):
                yield frame
        finally:
            self._count += 1
            if self._count == self._data_source_count:
                self.reset()

    async def _render_stream(self, inputs: dict, timeout: float, session: Session) -> AsyncGenerator:
        # Even if _need_render returns False, static text segments should still be output
        # If all variables are None, at least output the static text segments
        has_any_value = self._need_render(inputs)
        should_wait = False
        wait_timeout = timeout if timeout > 0 else None
        while True:
            if should_wait:
                async with self._condition:
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=wait_timeout)
                    except asyncio.TimeoutError as e:
                        workflow_logger.warning(
                            f"Template render stream timeout {wait_timeout}s",
                            event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                            component_type_str="End",
                            component_id=session.get_component_id(),
                            exception=e
                        )
                        self.advance_position()
                    except asyncio.CancelledError as e:
                        workflow_logger.warning(
                            "Template render stream cancelled",
                            event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                            component_type_str="End",
                            component_id=session.get_component_id(),
                            exception=e
                        )
                        break
                should_wait = False
            async with self._lock:
                if self.is_finished():
                    break

                segment = self.get_current_segment()
                if not self.should_render():
                    # Static text segment, output directly
                    yield {"data": segment, "index": self._chunk_index}
                    self._chunk_index += 1
                    self.advance_position()
                    continue

                # Variable segment, render and output
                value = get_value_by_nested_path(segment, inputs)
                if value is None:
                    # In mixed mode (concurrent render_stream calls), should wait instead of skipping
                    if self._count < self._data_source_count:
                        should_wait = True
                        continue
                    # If only one call and no other values exist, skip None values
                    if not has_any_value:
                        workflow_logger.debug(
                            "Template segment skip none value position",
                            event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                            component_type_str="End",
                            component_id=session.get_component_id(),
                            metadata={"segment": segment}
                        )
                        self.advance_position()
                        continue
                    should_wait = True
                    continue

                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        yield {"data": frame, "index": self._chunk_index}
                        self._chunk_index += 1
                else:
                    yield {"data": value, "index": self._chunk_index}
                    self._chunk_index += 1
                self.advance_position()
                async with self._condition:
                    self._condition.notify_all()

    def is_finished(self) -> bool:
        return self._current_position >= len(self._segments)


class TemplateBatchProcessor:
    def __init__(self, template: TemplateProcessor, inputs: dict):
        self._template = template
        self._inputs = inputs if inputs is not None else {}
        self.condition = asyncio.Condition()
        self._is_rendered = False

    def is_rendered(self) -> bool:
        return self._is_rendered

    async def render(self, inputs: dict, session: Session) -> str:
        self._is_rendered = True
        if inputs is None:
            inputs = self._inputs
        else:
            inputs = self._inputs | inputs
        generator = self._template.render_stream(inputs, session)
        answer = []
        async for frame in generator:
            answer.append(str(frame.get("data")))
        return "".join(answer)


class TemplateUtils:

    @staticmethod
    def render_template(template: str, inputs: dict) -> str:

        if not isinstance(template, str):
            raise TypeError("template must be a string")
        if not isinstance(inputs, dict):
            raise TypeError("inputs must be a dict")

        template = template.replace("{{", "$").replace("}}", "")
        t = string.Template(template)
        return t.safe_substitute(**inputs)

    @staticmethod
    def render_template_to_list(template: str) -> list[str | Any]:
        return list(filter(None, re.split(r'(\{\{[^}]+\}\})', template)))
