# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import Dict, Optional, List, AsyncIterator, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.stream.base import StreamMode, BaseStreamMode
from openjiuwen.core.session.stream.emitter import StreamEmitter
from openjiuwen.core.session.stream.writer import StreamWriter, OutputStreamWriter, TraceStreamWriter, \
    CustomStreamWriter
from openjiuwen.core.common.security.user_config import UserConfig

_DEFAULT_FRAME_TIMEOUT = -1


class StreamWriterManager:

    def __init__(self,
                 stream_emitter: StreamEmitter,
                 modes: Optional[List[StreamMode]] = None):
        if stream_emitter is None:
            raise ValueError("stream_emitter is None")
        self._stream_emitter = stream_emitter
        self._default_modes = modes if modes is not None else [
            BaseStreamMode.OUTPUT, BaseStreamMode.TRACE, BaseStreamMode.CUSTOM
        ]
        self._writers: Dict[StreamMode, StreamWriter] = {}
        self._add_default_writers()

    @staticmethod
    def create_manager(stream_emitter: StreamEmitter,
                       modes: Optional[List[StreamMode]] = None):
        return StreamWriterManager(stream_emitter=stream_emitter, modes=modes)

    def stream_emitter(self) -> StreamEmitter:
        return self._stream_emitter

    async def stream_output(self, first_frame_timeout=_DEFAULT_FRAME_TIMEOUT, timeout=_DEFAULT_FRAME_TIMEOUT,
                            need_close: bool = True) -> AsyncIterator[Any]:
        is_first_frame = True
        while True:
            if is_first_frame:
                try:
                    data = await self._stream_emitter.stream_queue.receive(timeout=first_frame_timeout)
                    is_first_frame = False
                except asyncio.TimeoutError as e:
                    raise build_error(StatusCode.STREAM_OUTPUT_FIRST_CHUNK_INTERVAL_TIMEOUT, cause=e,
                                      timeout=first_frame_timeout, reason=e)
            else:
                try:
                    data = await self._stream_emitter.stream_queue.receive(timeout=timeout)
                except asyncio.TimeoutError as e:
                    raise build_error(StatusCode.STREAM_OUTPUT_CHUNK_INTERVAL_TIMEOUT, cause=e, timeout=timeout,
                                      reason=e)

            if data is not None:
                if data == StreamEmitter.END_FRAME:
                    if need_close:
                        await self._stream_emitter.stream_queue.close(timeout=timeout)
                    break
                else:
                    if UserConfig.is_sensitive():
                        session_logger.debug(
                            "Stream data received",
                            event_type=LogEventType.SESSION_STREAM_CHUNK,
                            metadata={"sensitive_mode": True}
                        )
                    else:
                        session_logger.debug(
                            "Stream data received",
                            event_type=LogEventType.SESSION_STREAM_CHUNK,
                            metadata={"data_type": type(data).__name__}
                        )
                    yield data
            else:
                session_logger.debug(
                    "No stream data received, waiting",
                    event_type=LogEventType.SESSION_STREAM_CHUNK,
                    metadata={"status": "waiting"}
                )

    def add_writer(self, key: StreamMode, writer: StreamWriter) -> None:
        self._writers[key] = writer

    def get_writer(self, key: StreamMode) -> Optional[StreamWriter]:
        return self._writers.get(key)

    def get_output_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.OUTPUT)

    def get_trace_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.TRACE)

    def get_custom_writer(self) -> Optional[StreamWriter]:
        return self.get_writer(BaseStreamMode.CUSTOM)

    def remove_writer(self, key: StreamMode) -> Optional[StreamWriter]:
        if key in self._default_modes:
            raise build_error(StatusCode.STREAM_WRITER_MANAGER_REMOVE_WRITER_ERROR,
                              reason=f"Can not remove default writer for mode {key}")

        return self._writers.pop(key, None)

    def _add_default_writers(self) -> None:
        for mode in self._default_modes:
            if mode == BaseStreamMode.OUTPUT:
                self.add_writer(mode, OutputStreamWriter(self._stream_emitter))
            elif mode == BaseStreamMode.TRACE:
                self.add_writer(mode, TraceStreamWriter(self._stream_emitter))
            elif mode == BaseStreamMode.CUSTOM:
                self.add_writer(mode, CustomStreamWriter(self._stream_emitter))
            else:
                raise build_error(StatusCode.STREAM_WRITER_MANAGER_ADD_WRITER_ERROR, mode=mode,
                                  reason=f"default modes must be OUTPUT, TRACE, CUSTOM, {mode} is not supported.")
