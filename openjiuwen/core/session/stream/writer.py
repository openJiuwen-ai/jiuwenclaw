# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Type, Generic, TypeVar
from pydantic import BaseModel, ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.stream.base import OutputSchema, TraceSchema, CustomSchema
from openjiuwen.core.session.stream.emitter import StreamEmitter

T = TypeVar("T")
S = TypeVar("S", bound=BaseModel)


class StreamWriter(Generic[T, S]):

    def __init__(self, stream_emitter: StreamEmitter, schema_type: Type[S]):
        if stream_emitter is None:
            raise ValueError("stream_emitter can not be None")

        self._stream_emitter = stream_emitter
        self._schema_type = schema_type

    async def write(self, stream_data: T) -> None:
        if stream_data is None:
            raise build_error(StatusCode.STREAM_WRITER_WRITE_STREAM_VALIDATION_ERROR,
                              stream_type=self._schema_type.__name__,
                              stream_data=stream_data, reason="stream data is None")
        try:
            validated_data = self._schema_type.model_validate(stream_data)
        except ValidationError as e:
            raise build_error(StatusCode.STREAM_WRITER_WRITE_STREAM_VALIDATION_ERROR, cause=e,
                              stream_type=self._schema_type.__name__,
                              stream_data=stream_data, reason=e)
        try:
            await self._do_write(validated_data)
        except Exception as error:
            raise build_error(StatusCode.STREAM_WRITER_WRITE_STREAM_ERROR, cause=error, stream_data=stream_data,
                              reason=error)

    async def _do_write(self, validated_data: S) -> None:
        if self._stream_emitter and not self._stream_emitter.is_closed():
            await self._stream_emitter.emit(validated_data)
        else:
            session_logger.warning(
                "Stream message discarded, emitter already closed",
                event_type=LogEventType.SESSION_STREAM_CHUNK,
                metadata={"data_type": type(validated_data).__name__}
            )


class OutputStreamWriter(StreamWriter[dict, OutputSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[OutputSchema] = OutputSchema,
    ):
        super().__init__(stream_emitter, schema_type)


class TraceStreamWriter(StreamWriter[dict, TraceSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[TraceSchema] = TraceSchema,
    ):
        super().__init__(stream_emitter, schema_type)


class CustomStreamWriter(StreamWriter[dict, CustomSchema]):

    def __init__(
            self,
            stream_emitter: StreamEmitter,
            schema_type: Type[CustomSchema] = CustomSchema,
    ):
        super().__init__(stream_emitter, schema_type)
