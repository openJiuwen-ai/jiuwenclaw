# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import datetime
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, Literal, List, AsyncIterator, Iterator

from pydantic import BaseModel, field_validator

import aiofiles

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, sys_operation_logger
from openjiuwen.core.sys_operation.fs import BaseFsOperation, DEFAULT_READ_STREAM_CHUNK_SIZE, DEFAULT_READ_CHUNK_SIZE, \
    DEFAULT_UPLOAD_CHUNK_SIZE, DEFAULT_UPLOAD_STREAM_CHUNK_SIZE, DEFAULT_DOWNLOAD_CHUNK_SIZE, \
    DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE, TAIL_CHUNK_SIZE
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ReadFileResult, WriteFileResult, \
    UploadFileResult, DownloadFileResult, ListFilesResult, ListDirsResult, SearchFilesResult, \
    ReadFileStreamResult, DownloadFileStreamResult, UploadFileStreamResult,
    ReadFileData, ReadFileChunkData, WriteFileData, UploadFileData, DownloadFileData,
    FileSystemItem, FileSystemData, SearchFilesData, DownloadFileChunkData, UploadFileChunkData
)
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result


class _ListItemsSpec(BaseModel):
    path: str

    include_files: bool = True
    include_dirs: bool = True

    recursive: bool = False
    max_depth: Optional[int] = None

    sort_by: Literal["name", "modified_time", "size"] = "name"
    sort_descending: bool = False

    file_types: Optional[List[str]] = None


class _ReadParams(BaseModel):
    """Read parameters for file operations."""

    path: str
    head: Optional[int] = None
    tail: Optional[int] = None
    line_range: Optional[Tuple[int, int]] = None
    is_stream: bool = False
    encoding: str = "utf-8"
    mode: Literal['text', 'bytes'] = "text"
    file_path: Optional[pathlib.Path] = None
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE
    options: Optional[Dict[str, Any]] = None

    @field_validator('head', 'tail')
    @classmethod
    def validate_non_negative(cls, v):
        """Validate that head and tail are non-negative."""
        if v == 0:
            return None
        return v

    @field_validator('chunk_size')
    @classmethod
    def validate_chunk_size(cls, v, info):
        """Validate chunk size based on operation type."""
        # Get is_stream from the values dict if available
        is_stream = info.data.get('is_stream', False)

        if is_stream:
            # For streaming operations: use default stream chunk size if 0 or negative
            if v <= 0:
                return DEFAULT_READ_STREAM_CHUNK_SIZE
        else:
            # For non-streaming operations: use default chunk size if negative, keep 0 as is
            if v < 0:
                return DEFAULT_READ_CHUNK_SIZE
        return v

    def validate_mutually_exclusive(self):
        """Validate that mutually exclusive parameters are not specified together."""
        if self.tail is not None:
            if self.head is not None:
                raise build_error(
                    status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                    execution="validate_read_params",
                    error_msg="tail and head cannot be specified simultaneously"
                )
            if self.line_range is not None:
                raise build_error(
                    status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                    execution="validate_read_params",
                    error_msg="tail and line_range cannot be specified simultaneously"
                )
        elif self.head is not None and self.line_range is not None:
            raise build_error(
                status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                execution="validate_read_params",
                error_msg="head and line_range cannot be specified simultaneously"
            )

    def validate_binary_mode(self):
        """Validate that text mode only parameters are not specified in binary mode."""
        if self.mode == "bytes":
            if self.head is not None or self.tail is not None or self.line_range is not None:
                raise build_error(
                    status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                    execution="validate_read_params",
                    error_msg="Parameters 'head', 'tail', and 'line_range' are only supported in text mode"
                )


@dataclass
class _ErrorLogParams:
    """Error log params"""
    method_name: Optional[str] = None
    method_params: Optional[Dict[str, Any]] = None
    start_time: Optional[float] = None


@operation(name="fs", mode=OperationMode.LOCAL, description="local fs operation")
class FsOperation(BaseFsOperation):
    """File system operation"""

    async def read_file(
            self,
            path: str,
            *,
            mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None,
            tail: Optional[int] = None,
            line_range: Optional[Tuple[int, int]] = None,
            encoding: str = "utf-8",
            chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> ReadFileResult:
        """
        Asynchronously read file with specified mode and parameters.
        Mutually exclusive parameters: Only one of head, tail, or line_range can be specified.

        Args:
            path: Full or relative path to the file to read (required).
            mode: Reading mode - "text" (line-based, default) or "bytes" (raw bytes).
            head: Number of lines to read from the start (text mode only).0 is equivalent to None.
            tail: Number of lines to read from the end (text mode only).0 is equivalent to None.
            line_range: Specific line range to read (start, end) - 1-indexed, inclusive (text mode only).
                  If start <= 0 or end <= 0 or start > end, returns empty content.
            encoding: Character encoding for text mode (default: utf-8).
            chunk_size: Maximum number of bytes to read at once (default: 0, unlimited)
            options: Extended configuration options (dict, optional).

        Returns:
            ReadFileResult: Structured result.
        """
        method_name = self.read_file.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to read file", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            # Create _ReadParams object
            read_params = _ReadParams(
                path=path,
                head=head,
                tail=tail,
                line_range=line_range,
                is_stream=False,
                encoding=encoding,
                mode=mode,
                chunk_size=chunk_size,
                options=options
            )

            # Validate binary mode parameters
            read_params.validate_binary_mode()

            # Validate parameters and resolve path
            validated_params = await self._validate_and_resolve_path(
                read_params, "read_file"
            )

            # Extract validated parameters
            file_path = validated_params.file_path

            if mode == "bytes":
                final_content = await self._read_bytes(file_path, validated_params.chunk_size)
            else:
                # Line-based operations - lines already contain original line endings
                lines = []
                async for line in FsOperation._read_text_content(validated_params):
                    lines.append(line)
                final_content = "".join(lines)

            data = ReadFileData(path=str(file_path), content=final_content, mode=mode)
            success_result = ReadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=data
            )
            sys_operation_logger.info("End to read file", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("read_file", str(e), ReadFileResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def read_file_stream(
            self,
            path: str,
            *,
            mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None,
            tail: Optional[int] = None,
            line_range: Optional[Tuple[int, int]] = None,
            encoding: str = "utf-8",
            chunk_size: int = DEFAULT_READ_STREAM_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ReadFileStreamResult]:
        """
        Asynchronously read file streaming with specified mode and parameters.
        Mutually exclusive parameters: Only one of head, tail, or line_range can be specified.

        Args:
            path: Full or relative path to the file to read (required).
            mode: Reading mode - "text" (line-based, default) or "bytes" (raw bytes).
            head: Number of lines to read from the start (text mode only).0 is equivalent to None.
            tail: Number of lines to read from the end (text mode only).0 is equivalent to None.
            line_range: Specific line range to read (start, end) - 1-indexed, inclusive (text mode only).
                  If start <= 0 or end <= 0 or start > end, returns empty content.
            encoding: Character encoding for text mode (default: utf-8).
            chunk_size: Buffer size for bytes mode reading (default: 8192 bytes).
            options: Extended configuration options (dict, optional).

        Returns:
            AsyncIterator[ReadFileStreamResult]: Streaming structured results, line-by-line or chunk-by-chunk.
        """
        method_name = self.read_file_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to read file streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            # Create _ReadParams object
            read_params = _ReadParams(
                path=path,
                head=head,
                tail=tail,
                line_range=line_range,
                is_stream=True,
                encoding=encoding,
                mode=mode,
                chunk_size=chunk_size,
                options=options
            )

            # Validate binary mode parameters
            read_params.validate_binary_mode()

            # Validate parameters and resolve path
            validated_params = await self._validate_and_resolve_path(
                read_params, "read_file_stream"
            )

            # Extract validated parameters
            file_path = validated_params.file_path

            # bytes mode
            if mode != "text":
                async for chunk in self._read_bytes_stream(file_path, validated_params.chunk_size):
                    yield chunk
                    self._log_stream_chunk(chunk, method_name, method_params, start_time,
                                           stream_log="Receive read file stream",
                                           end_stream_log="End to read file streaming")
                return

            # text mode
            async for chunk in self._stream_text_file(validated_params):
                yield chunk
                self._log_stream_chunk(chunk, method_name, method_params, start_time,
                                       stream_log="Receive read file stream",
                                       end_stream_log="End to read file streaming")

        except Exception as e:
            yield self._create_error_result("read_file_stream", str(e), ReadFileStreamResult,
                                            _ErrorLogParams(method_name, method_params, start_time))

    async def write_file(
            self,
            path: str,
            content: str | bytes,
            *,
            mode: Literal['text', 'bytes'] = "text",
            prepend_newline: bool = True,
            append_newline: bool = False,
            create_if_not_exist: bool = True,
            permissions: str = "644",
            encoding: str = "utf-8",
            options: Optional[Dict[str, Any]] = None
    ) -> WriteFileResult:
        """
        Asynchronously writes content to a file with flexible configuration.

        Args:
            path: Full or relative path to the file to write (required).
            content: Data to write to the file (string for text mode, bytes for binary mode).
            mode: Writing mode: "text" (for string content) or "bytes" (for binary data) (default: "text").
            prepend_newline: Add a newline character (`\n`) before the content (text mode only; default: True).
            append_newline: Add a newline character (`\n`) after the content (text mode only; default: False).
            create_if_not_exist: Auto-create the file if it doesn't exist (default: True).
            permissions: Octal file permissions (Unix/Linux only; ignored on Windows) (default: "644").
            encoding: Character encoding for text mode (default: utf-8).
            options: Extended configuration options (dict, optional).

        Returns:
            WriteFileResult: Structured result.
        """
        method_name = self.write_file.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to write file", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            file_path = self._resolve_path(path, create_parent=True)
            if file_path.is_dir():
                return self._create_error_result("write_file", f"Target path is a directory:"
                                                               f" {file_path}", WriteFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))
            if not create_if_not_exist and not file_path.exists():
                return self._create_error_result("write_file", f"File does not exist: {file_path}",
                                                 WriteFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))

            if mode == "text":
                txt = str(content)
                if prepend_newline:
                    txt = "\n" + txt
                if append_newline:
                    txt = txt + "\n"

                data_bytes = txt.encode(encoding)
            else:
                data_bytes = content if isinstance(content, (bytes, bytearray)) else bytes(content)

            async with aiofiles.open(file_path, mode="wb") as f:
                await f.write(data_bytes)

            self._apply_permissions(file_path, permissions)
            success_result = WriteFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=WriteFileData(path=str(file_path), size=len(data_bytes), mode=mode)
            )
            sys_operation_logger.info("End to write file", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("write_file", str(e), WriteFileResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def upload_file(
            self,
            local_path: str,
            target_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> UploadFileResult:
        """
        Asynchronous file upload (semantics: local file → target path).

        Args:
            local_path: Local source file path (required, e.g. /tmp/local_file.txt).
            target_path: Upload destination path (required, e.g. /mnt/storage/file.txt or sandbox:/opt/bucket/file.txt).
            overwrite: Whether to overwrite existing target file (default: False).
            create_parent_dirs: Whether to auto-create target parent directories (default: True).
            preserve_permissions: Whether to preserve file permissions (default: True, Unix/Linux only).
            chunk_size: Maximum number of bytes to upload at once (default: 0, unlimited)
            options: Extended configuration options (dict, optional).

        Returns:
            UploadFileResult: Structured result.
        """
        method_name = self.upload_file.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to upload file", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            src = pathlib.Path(local_path).expanduser().resolve()
            dst = self._resolve_path(target_path, create_parent=create_parent_dirs)
            if not src.is_file():
                return self._create_error_result("upload_file", f"Source not found: {src}", UploadFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))
            if dst.exists() and not overwrite:
                return self._create_error_result("upload_file", f"Target exists: {dst}", UploadFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))

            size = await self._transfer_file(src, dst, chunk_size)
            if preserve_permissions:
                self._copy_permissions(src, dst)
            success_result = UploadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=UploadFileData(local_path=str(src), target_path=str(dst), size=size)
            )
            sys_operation_logger.info("End to upload file", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("upload_file", str(e), UploadFileResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def upload_file_stream(
            self,
            local_path: str,
            target_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[UploadFileStreamResult]:
        """
        Asynchronous file upload streaming(semantics: local file → target path).

        Args:
            local_path: Local source file path (required, e.g. /tmp/local_file.txt).
            target_path: Upload destination path (required, e.g. /mnt/storage/file.txt or sandbox:/opt/bucket/file.txt).
            overwrite: Whether to overwrite existing target file (default: False).
            create_parent_dirs: Whether to auto-create target parent directories (default: True).
            preserve_permissions: Whether to preserve file permissions (default: True, Unix/Linux only).
            chunk_size: Chunk size for cross-filesystem transfers (default: 1MB, bytes).
            options: Extended configuration options (dict, optional).

        Returns:
            AsyncIterator[UploadFileStreamResult]: Streaming structured results, chunk-by-chunk.
        """
        method_name = self.upload_file_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to upload file streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            src = pathlib.Path(local_path).expanduser().resolve()
            dst = self._resolve_path(target_path, create_parent=create_parent_dirs)
            if not src.is_file():
                yield self._create_error_result("upload_file_stream", f"Source not found: {src}",
                                                UploadFileStreamResult,
                                                _ErrorLogParams(method_name, method_params, start_time))
                return
            if dst.exists() and not overwrite:
                yield self._create_error_result("upload_file_stream", f"Target exists: {dst}", UploadFileStreamResult,
                                                _ErrorLogParams(method_name, method_params, start_time))
                return

            async with aiofiles.open(src, mode="rb") as src_f, aiofiles.open(dst, mode="wb") as dst_f:
                index = 0
                # Read first chunk
                chunk_bytes = await src_f.read(chunk_size)
                while chunk_bytes:
                    # Read next chunk to check if this is the last one
                    next_chunk = await src_f.read(chunk_size)
                    is_last = not next_chunk

                    await dst_f.write(chunk_bytes)
                    upload_file_res = UploadFileStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message=StatusCode.SUCCESS.errmsg,
                        data=UploadFileChunkData(
                            local_path=str(src), target_path=str(dst), chunk_size=len(chunk_bytes), chunk_index=index,
                            is_last_chunk=is_last
                        )
                    )
                    yield upload_file_res
                    self._log_stream_chunk(upload_file_res, method_name, method_params, start_time,
                                           stream_log="Receive upload file stream",
                                           end_stream_log="End to upload file streaming")
                    index += 1

                    # Move to next chunk
                    chunk_bytes = next_chunk

            if preserve_permissions:
                self._copy_permissions(src, dst)
        except Exception as e:
            yield self._create_error_result("upload_file_stream", str(e), UploadFileStreamResult,
                                            _ErrorLogParams(method_name, method_params, start_time))

    async def download_file(
            self,
            source_path: str,
            local_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> DownloadFileResult:
        """
        Asynchronous file download (semantics: source file → local destination path).

        Args:
            source_path: Source file path (required, e.g. /mnt/storage/file.txt or sandbox:/opt/bucket/file.txt).
            local_path: Local destination file path (required, e.g. /home/user/downloads/file.txt).
            overwrite: Whether to overwrite existing target file (default: False).
            create_parent_dirs: Whether to auto-create target parent directories (default: True).
            preserve_permissions: Whether to preserve file permissions (default: True, Unix/Linux only).
            chunk_size: Maximum number of bytes to download at once (default: 0, unlimited)
            options: Extended configuration options (dict, optional).

        Returns:
            DownloadFileResult: Structured result.
        """
        method_name = self.download_file.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to download file", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            src = self._resolve_path(source_path)
            dst = pathlib.Path(local_path).expanduser().resolve()
            if not src.is_file():
                return self._create_error_result("download_file", f"Source not found: {src}",
                                                 DownloadFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))
            if dst.exists() and not overwrite:
                return self._create_error_result("download_file", f"Destination exists: {dst}",
                                                 DownloadFileResult,
                                                 _ErrorLogParams(method_name, method_params, start_time))
            if create_parent_dirs:
                dst.parent.mkdir(parents=True, exist_ok=True)

            size = await self._transfer_file(src, dst, chunk_size)
            if preserve_permissions:
                self._copy_permissions(src, dst)
            success_result = DownloadFileResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=DownloadFileData(source_path=str(src), local_path=str(dst), size=size)
            )
            sys_operation_logger.info("End to download file", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("download_file", str(e), DownloadFileResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def download_file_stream(
            self,
            source_path: str,
            local_path: str,
            *,
            overwrite: bool = False,
            create_parent_dirs: bool = True,
            preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[DownloadFileStreamResult]:
        """
        Asynchronous file download streaming(semantics: source file → local destination path).

        Args:
            source_path: Source file path (required, e.g. /mnt/storage/file.txt or sandbox:/opt/bucket/file.txt).
            local_path: Local destination file path (required, e.g. /home/user/downloads/file.txt).
            overwrite: Whether to overwrite existing target file (default: False).
            create_parent_dirs: Whether to auto-create target parent directories (default: True).
            preserve_permissions: Whether to preserve file permissions (default: True, Unix/Linux only).
            chunk_size: Chunk size for cross-filesystem transfers (default: 1MB, bytes).
            options: Extended configuration options (dict, optional).

        Returns:
            AsyncIterator[DownloadFileStreamResult]: Streaming structured results, chunk-by-chunk.
        """
        method_name = self.download_file_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to download file streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            src = self._resolve_path(source_path)
            dst = pathlib.Path(local_path).expanduser().resolve()
            if not src.is_file():
                yield self._create_error_result("download_file_stream", f"Source not found: {src}",
                                                DownloadFileStreamResult,
                                                _ErrorLogParams(method_name, method_params, start_time))
                return
            if dst.exists() and not overwrite:
                yield self._create_error_result("download_file_stream", f"Destination exists: {dst}",
                                                DownloadFileStreamResult,
                                                _ErrorLogParams(method_name, method_params, start_time))
                return
            if create_parent_dirs:
                dst.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(src, mode="rb") as src_f, aiofiles.open(dst, mode="wb") as dst_f:
                index = 0
                # Read first chunk
                chunk_bytes = await src_f.read(chunk_size)
                while chunk_bytes:
                    # Read next chunk to check if this is the last one
                    next_chunk = await src_f.read(chunk_size)
                    is_last = not next_chunk

                    await dst_f.write(chunk_bytes)
                    download_file_res = DownloadFileStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message=StatusCode.SUCCESS.errmsg,
                        data=DownloadFileChunkData(
                            source_path=str(src), local_path=str(dst), chunk_size=len(chunk_bytes), chunk_index=index,
                            is_last_chunk=is_last
                        )
                    )
                    yield download_file_res
                    self._log_stream_chunk(download_file_res, method_name, method_params, start_time,
                                           stream_log="Receive download file stream",
                                           end_stream_log="End to download file streaming")

                    index += 1

                    # Move to next chunk
                    chunk_bytes = next_chunk

            if preserve_permissions:
                self._copy_permissions(src, dst)
        except Exception as e:
            yield self._create_error_result("download_file_stream", str(e), DownloadFileStreamResult,
                                            _ErrorLogParams(method_name, method_params, start_time))

    async def list_files(
            self,
            path: str,
            *,
            recursive: bool = False,
            max_depth: Optional[int] = None,
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False,
            file_types: Optional[List[str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ListFilesResult:
        """
        Asynchronously list files under the specified path.

        Args:
            path: Target parent directory path (required).
            recursive: Whether to list files in subdirectories recursively. Defaults to False.
            max_depth: Maximum recursion depth limit, only effective when recursive=True.
            sort_by: Sorting field, supports three options:
                'name' (sort by filename, default),
                'modified_time' (sort by last modification time),
                'size' (sort by file size in bytes).
            sort_descending: Whether to sort in descending order. Defaults to False (ascending order).
            file_types: Filter files by extension (list of extensions), e.g. ['.txt', '.pdf'].
            options: Extended configuration options (dict, optional).

        Returns:
            ListFilesResult: Structured result.
        """
        method_name = self.list_files.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to list files", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            spec = _ListItemsSpec(
                path=path,
                include_files=True,
                include_dirs=False,
                recursive=recursive,
                max_depth=max_depth,
                sort_by=sort_by,
                sort_descending=sort_descending,
                file_types=file_types,
            )
            items = await asyncio.to_thread(
                self._list_items_internal_sync,
                spec,
            )
            success_result = ListFilesResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=FileSystemData(total_count=len(items), list_items=items,
                                    root_path=str(self._resolve_path(path)), recursive=recursive,
                                    max_depth=max_depth)
            )
            sys_operation_logger.info("End to list files", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("list_files", str(e), ListFilesResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def list_directories(
            self,
            path: str,
            *,
            recursive: bool = False,
            max_depth: Optional[int] = None,
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False,
            options: Optional[Dict[str, Any]] = None
    ) -> ListDirsResult:
        """
        Asynchronously list directories under the specified path.

        Args:
            path: Target parent directory path (required).
            recursive: Whether to list subdirectories recursively. Defaults to False.
            max_depth: Maximum recursion depth limit, only effective when recursive=True.
            sort_by: Sorting field, supports three options:
                'name' (sort by filename, default),
                'modified_time' (sort by last modification time),
                'size' (sort by file size in bytes).
            sort_descending: Whether to sort in descending order. Defaults to False (ascending order).
            options: Extended configuration options (dict, optional).

        Returns:
            ListDirsResult: Structured result.
        """
        method_name = self.list_directories.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to list directories", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            spec = _ListItemsSpec(
                path=path,
                include_files=False,
                include_dirs=True,
                recursive=recursive,
                max_depth=max_depth,
                sort_by=sort_by,
                sort_descending=sort_descending,
                file_types=None,
            )

            items = await asyncio.to_thread(
                self._list_items_internal_sync,
                spec,
            )

            success_result = ListDirsResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=FileSystemData(
                    total_count=len(items),
                    list_items=items,
                    root_path=str(self._resolve_path(path)),
                    recursive=recursive,
                    max_depth=max_depth,
                ),
            )
            sys_operation_logger.info("End to list directories", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result

        except Exception as e:
            return self._create_error_result("list_directories", str(e), ListDirsResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    async def search_files(
            self,
            path: str,
            pattern: str,
            exclude_patterns: Optional[List[str]] = None
    ) -> SearchFilesResult:
        """
        Asynchronously search files under the specified path.

        Args:
            path: Base directory path to start the search (required).
            pattern: Search pattern to match file names.
            exclude_patterns: Optional list of patterns to exclude from results.

        Returns:
            SearchFilesResult: Structured result.
        """
        method_name = self.search_files.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to search files", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))
        try:
            items = await asyncio.to_thread(
                self._search_files_internal_sync,
                path,
                pattern,
                exclude_patterns
            )

            success_result = SearchFilesResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=SearchFilesData(
                    total_matches=len(items),
                    matching_files=items,
                    search_path=str(self._resolve_path(path)),
                    search_pattern=pattern,
                    exclude_patterns=exclude_patterns
                )
            )
            sys_operation_logger.info("End to search files", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return success_result
        except Exception as e:
            return self._create_error_result("search_files", str(e), SearchFilesResult,
                                             _ErrorLogParams(method_name, method_params, start_time))

    def _search_files_internal_sync(
            self,
            path: str,
            pattern: str,
            exclude_patterns: Optional[List[str]] = None
    ) -> List[FileSystemItem]:
        base = self._resolve_path(path)
        if not base.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {base}")

        matched_paths = list(base.rglob(pattern))
        if exclude_patterns:
            exclude_set = set()
            for pat in exclude_patterns:
                exclude_set.update(set(base.rglob(pat)))
            matched_paths = [p for p in matched_paths if p not in exclude_set]

        items = []
        for p in matched_paths:
            if p.is_file():
                item = self._create_fs_item(p)
                if item:
                    items.append(item)
        return items

    def _resolve_path(self, path: str, create_parent: bool = False) -> pathlib.Path:
        """Resolve path, enforce work_dir sandbox (if configured), and sanitize filenames."""
        work_dir_val = getattr(self._run_config, 'work_dir', None)

        if work_dir_val is None:
            # if work_dir is not configured
            final_path = pathlib.Path(path).expanduser().resolve()
        else:
            work_dir = pathlib.Path(work_dir_val).expanduser().resolve()
            try:
                raw_resolved = (work_dir / path).resolve()
                rel_path = raw_resolved.relative_to(work_dir)
            except ValueError as e:
                raise build_error(status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                                  execution="resolve_path",
                                  error_msg=f"Access denied: Path {path} traverses outside {work_dir}",
                                  cause=e) from e

            sanitized_parts = [re.sub(r'[^\w.-]', '_', part) for part in rel_path.parts]
            final_path = work_dir.joinpath(*sanitized_parts)

        if create_parent:
            final_path.parent.mkdir(parents=True, exist_ok=True)

        return final_path

    @staticmethod
    def _apply_permissions(path: pathlib.Path, permissions: str | int) -> None:
        """Apply octal permissions to path on Unix-like systems (Best effort)."""
        if os.name != "nt":
            try:
                perm_int = int(str(permissions), 8) if isinstance(permissions, str) else permissions
                os.chmod(path, perm_int)
            except Exception as e:
                # Permission application is best-effort; failures (e.g. on non-Unix) are ignored
                sys_operation_logger.warning("Failed to apply permissions",
                                             event_type=LogEventType.SYS_OP_ERROR,
                                             exception=e)

    @staticmethod
    def _copy_permissions(src: pathlib.Path, dst: pathlib.Path) -> None:
        """Copy permissions from src to dst on Unix-like systems (Best effort)."""
        if os.name != "nt":
            try:
                st = src.stat()
                os.chmod(dst, st.st_mode)
            except Exception as e:
                # Permission copy is best-effort; failures (e.g. source stat issues) are ignored
                sys_operation_logger.warning("Failed to copy permissions",
                                             event_type=LogEventType.SYS_OP_ERROR,
                                             exception=e)

    @staticmethod
    async def _transfer_file(src: pathlib.Path, dst: pathlib.Path, chunk_size: int) -> int:
        """Asynchronously copy file contents from src to dst in chunks. Returns total size."""
        total_size = 0
        async with aiofiles.open(src, mode="rb") as src_f, aiofiles.open(dst, mode="wb") as dst_f:
            if chunk_size <= 0:
                # Read entire file at once
                content = await src_f.read()
                await dst_f.write(content)
                total_size = len(content)
            else:
                # Read in chunks
                while True:
                    chunk = await src_f.read(chunk_size)
                    if not chunk:
                        break

                    await dst_f.write(chunk)
                    total_size += len(chunk)
        return total_size

    @staticmethod
    def _walk_path(base: pathlib.Path, recursive: bool = False, max_depth: Optional[int] = None) -> Iterator[
        pathlib.Path]:
        """Consolidated directory walker with recursion and depth control."""
        if not recursive:
            yield from base.iterdir()
            return

        if max_depth is None:
            yield from base.rglob("*")
            return

        root_depth = len(base.parts)
        for root, dirs, files in os.walk(base):
            current_root = pathlib.Path(root)
            current_depth = len(current_root.parts) - root_depth
            if current_depth > max_depth:
                del dirs[:]
                continue
            for d in dirs:
                yield current_root / d
            for f in files:
                yield current_root / f
            if current_depth == max_depth:
                del dirs[:]

    @staticmethod
    def _create_fs_item(p: pathlib.Path) -> Optional[FileSystemItem]:
        """Centralized FileSystemItem creation with stat error handling."""
        try:
            stat = p.stat()
            is_dir = p.is_dir()
            return FileSystemItem(
                name=p.name, path=str(p), size=stat.st_size,
                modified_time=str(datetime.datetime.fromtimestamp(stat.st_mtime)),
                is_directory=is_dir, type=p.suffix if not is_dir else None,
            )
        except Exception as e:
            sys_operation_logger.warning("Failed to create fs item",
                                         event_type=LogEventType.SYS_OP_ERROR,
                                         exception=e)
            return None

    @staticmethod
    def _sort_items(items: List[FileSystemItem], sort_by: str, reverse: bool) -> None:
        """Sort FS items by name, modified_time, or size."""
        if sort_by == "name":
            items.sort(key=lambda i: i.name, reverse=reverse)
        elif sort_by == "modified_time":
            items.sort(key=lambda i: i.modified_time, reverse=reverse)
        elif sort_by == "size":
            items.sort(key=lambda i: i.size, reverse=reverse)

    def _list_items_internal_sync(
            self,
            spec: _ListItemsSpec,
    ) -> List[FileSystemItem]:
        base = self._resolve_path(spec.path)
        if not base.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {base}")

        items: List[FileSystemItem] = []

        for p in self._walk_path(base, spec.recursive, spec.max_depth):
            is_dir = p.is_dir()

            if not spec.include_files and not is_dir:
                continue
            if not spec.include_dirs and is_dir:
                continue
            if spec.file_types and not is_dir and p.suffix not in spec.file_types:
                continue

            item = self._create_fs_item(p)
            if item:
                items.append(item)

        self._sort_items(items, spec.sort_by, spec.sort_descending)
        return items

    def _create_error_result(self, execution: str, error_msg: str, result_class: Any,
                             error_log_params: Optional[_ErrorLogParams] = None):
        """
        Create error result for file operations.

        Args:
            execution: The operation being executed.
            error_msg: The error message.
            result_class: The result class to instantiate.
            error_log_params: The params of the error log.

        Returns:
            An instance of result_class with error information.
        """
        err_result = build_operation_error_result(
            error_type=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
            msg_format_kwargs={"execution": execution, "error_msg": error_msg},
            result_cls=result_class
        )
        message = "Failed to execute " + execution
        sys_operation_logger.error(message, event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_ERROR,
            method_name=error_log_params.method_name \
                if error_log_params and error_log_params.method_name else execution,
            method_params=error_log_params.method_params if error_log_params else None,
            method_result=self._safe_model_dump(err_result),
            method_exec_time_ms=(asyncio.get_event_loop().time() - error_log_params.start_time) * 1000 \
                if error_log_params and error_log_params.start_time else None
        ))
        return err_result

    async def _validate_and_resolve_path(
            self,
            read_params: _ReadParams,
            execution: str,
    ):
        """
        Validate parameters and resolve path for file operations.
        
        Args:
            read_params: ReadParams object with parameters to validate
            execution: Name of the operation being executed
            
        Returns:
            _ReadParams: Validated ReadParams object with resolved file_path
        """
        # Validate mutually exclusive parameters
        read_params.validate_mutually_exclusive()

        # Resolve path
        file_path = self._resolve_path(read_params.path)
        if not file_path.is_file():
            raise build_error(
                status=StatusCode.SYS_OPERATION_FS_EXECUTION_ERROR,
                execution=execution,
                error_msg=f"File not found: {file_path}"
            )

        # Create a new _ReadParams with resolved file_path
        validated_params = read_params.model_copy()
        validated_params.file_path = file_path

        return validated_params

    @staticmethod
    async def _read_bytes(file_path: pathlib.Path, chunk_size: int):
        """
        Read file in binary mode.

        Args:
            file_path: The path to the file.
            chunk_size: Buffer size for reading. Pass 0 or -1 to read the entire file.

        Returns:
            The read bytes content.
        """
        async with aiofiles.open(file_path, mode="rb") as f:
            if chunk_size <= 0:
                return await f.read()
            return await f.read(chunk_size)

    @staticmethod
    async def _read_bytes_stream(file_path: pathlib.Path, chunk_size: int):
        """
        Read file in binary mode as a stream.

        Args:
            file_path: The path to the file.
            chunk_size: Buffer size for reading. Pass 0 or -1 to use the default 8192 bytes.
                       Pass a positive value to specify the chunk size for each read operation.

        Yields:
            ReadFileStreamResult with chunk data.
        
        Note:
            Streaming always reads the entire file, as the caller can cancel at any time.
            The chunk_size parameter only affects how much data is read at once per yield.
        """
        # Use default chunk size if 0 or -1 is passed
        if chunk_size <= 0:
            chunk_size = DEFAULT_READ_STREAM_CHUNK_SIZE

        async with aiofiles.open(file_path, mode="rb") as f:
            index = 0
            current_chunk = await f.read(chunk_size)
            while current_chunk:
                next_chunk = await f.read(chunk_size)
                is_last = not bool(next_chunk)
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=str(file_path), chunk_content=current_chunk, mode="bytes",
                        chunk_size=len(current_chunk), chunk_index=index, is_last_chunk=is_last
                    )
                )
                index += 1
                current_chunk = next_chunk

    @staticmethod
    async def _read_head(file_path: pathlib.Path, head: int, encoding: str):
        """
        Read the first n lines of a file as a generator.
        
        Args:
            file_path: Path to the file
            head: Number of lines to read from the start
            encoding: File encoding
            
        Yields:
            Lines from the start of the file (with original line endings)
        """
        if head <= 0:
            return

        count = 0

        async with aiofiles.open(file_path, mode="r", encoding=encoding) as f:
            content = await f.read()
            lines = content.splitlines(True)
            for line in lines:
                if count >= head:
                    break
                yield line
                count += 1

    @staticmethod
    async def _read_line_range(file_path: pathlib.Path, start: int, end: int, encoding: str):
        """
        Read lines from a specific range in a file as a generator.
        
        Args:
            file_path: Path to the file
            start: Start line number (1-indexed)
            end: End line number (1-indexed, inclusive)
            encoding: File encoding
            
        Yields:
            Lines within the specified range (with original line endings)
        """
        if start <= 0 or end <= 0 or start > end:
            return

        current_line = 1

        async with aiofiles.open(file_path, mode="r", encoding=encoding) as f:
            content = await f.read()
            for line in content.splitlines(True):
                if current_line >= start and current_line <= end:
                    yield line
                elif current_line > end:
                    break
                current_line += 1

    @staticmethod
    async def _read_tail(
            file_path: pathlib.Path,
            tail: int,
            encoding: str,
    ):
        """
        Read the last n lines of a file as a generator.
        
        Args:
            file_path: Path to the file
            tail: Number of lines to read from the end
            encoding: File encoding
            
        Yields:
            Lines from the end of the file (with original line endings preserved)
        """
        if tail <= 0:
            return

        lines_found: list[str] = []
        byte_buffer = b""

        async with aiofiles.open(file_path, mode="rb") as f:
            await f.seek(0, os.SEEK_END)
            current_pos = await f.tell()

            while current_pos > 0 and len(lines_found) < tail:
                read_size = min(TAIL_CHUNK_SIZE, current_pos)
                current_pos -= read_size

                await f.seek(current_pos)
                # Read a chunk and prepend it to the buffer
                byte_buffer = (await f.read(read_size)) + byte_buffer

                try:
                    # Attempt to decode. UnicodeDecodeError occurs if a char is split across chunks.
                    text = byte_buffer.decode(encoding)
                except UnicodeDecodeError as e:
                    sys_operation_logger.debug(
                        "Unicode decode error occurred while parsing byte buffer",
                        event_type=LogEventType.SYS_OP_ERROR,
                        exception=e,
                        metadata={"byte_buffer_length": len(byte_buffer) if byte_buffer is not None else 0,
                                  "encoding": encoding,
                                  "current_pos": current_pos}
                    )
                    if current_pos > 0:
                        continue  # Need more data to complete the multi-byte char
                    text = byte_buffer.decode(encoding, errors="replace")

                lines = text.splitlines(True)

                # If not at the start, the first line segment is partial (belongs to an earlier block).
                if current_pos > 0 and lines:
                    byte_buffer = lines.pop(0).encode(encoding)
                else:
                    byte_buffer = b""

                if lines:
                    # Prepend found lines to the list
                    lines_found = lines + lines_found
                    if len(lines_found) > tail:
                        lines_found = lines_found[-tail:]

            # Finish up any leftover in the buffer (first line of the file)
            if byte_buffer and len(lines_found) < tail:
                text = byte_buffer.decode(encoding, errors="replace")
                lines_found = text.splitlines(True) + lines_found

        # Yield results starting from the beginning of the captured tail
        for line in lines_found[-tail:]:
            yield line

    @staticmethod
    async def _read_full(file_path: pathlib.Path, encoding: str):
        """
        Read the full file as a generator.
        
        Args:
            file_path: Path to the file
            encoding: File encoding
            
        Yields:
            Lines from the file (with original line endings)
        """
        async with aiofiles.open(file_path, mode="r", encoding=encoding) as f:
            async for line in f:
                yield line

    @staticmethod
    async def _read_text_content(read_params: _ReadParams):
        """
        Unified text content reader that handles all cases (head, tail, range, full).
        
        Args:
            read_params: Read parameters including file path, reading mode, etc.
            
        Yields:
            Lines of text content based on the specified reading mode
        """
        file_path = read_params.file_path
        encoding = read_params.encoding
        head = read_params.head
        tail = read_params.tail
        line_range = read_params.line_range

        # tail mode
        if tail is not None:
            async for line in FsOperation._read_tail(file_path, tail, encoding):
                yield line
            return

        # head mode
        if head is not None:
            async for line in FsOperation._read_head(file_path, head, encoding):
                yield line
            return

        # line_range mode
        if line_range is not None:
            start, end = line_range
            async for line in FsOperation._read_line_range(file_path, start, end, encoding):
                yield line
            return

        # full file mode
        async for line in FsOperation._read_full(file_path, encoding):
            yield line

    @staticmethod
    async def _stream_text_file(read_params: _ReadParams):
        file_path = read_params.file_path
        encoding = read_params.encoding
        mode = read_params.mode
        head = read_params.head
        tail = read_params.tail
        line_range = read_params.line_range

        # Edge case: negative tail
        if tail is not None and tail < 0:
            yield ReadFileStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileChunkData(
                    path=str(file_path),
                    chunk_content="",
                    mode=mode,
                    chunk_size=0,
                    chunk_index=0,
                    is_last_chunk=True,
                ),
            )
            return

        # Edge case: negative head
        if head is not None and head < 0:
            yield ReadFileStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileChunkData(
                    path=str(file_path),
                    chunk_content="",
                    mode=mode,
                    chunk_size=0,
                    chunk_index=0,
                    is_last_chunk=True,
                ),
            )
            return

        # Edge case: invalid line_range
        if line_range is not None:
            start, end = line_range
            if start <= 0 or end <= 0 or start > end:
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=str(file_path),
                        chunk_content="",
                        mode=mode,
                        chunk_size=0,
                        chunk_index=0,
                        is_last_chunk=True,
                    ),
                )
                return

        # Tail mode: needs to collect all lines first to set is_last_chunk correctly
        if tail is not None:
            tail_lines = []
            # Collect all tail lines
            async for content in FsOperation._read_text_content(read_params):
                tail_lines.append(content)

            # Yield lines with proper is_last_chunk flag
            for i, content in enumerate(tail_lines):
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=str(file_path),
                        chunk_content=content,
                        mode=mode,
                        chunk_size=len(content.encode(encoding)),
                        chunk_index=i,
                        is_last_chunk=(i == len(tail_lines) - 1),
                    ),
                )

            # If no lines, return empty content
            if not tail_lines:
                yield ReadFileStreamResult(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=ReadFileChunkData(
                        path=str(file_path),
                        chunk_content="",
                        mode=mode,
                        chunk_size=0,
                        chunk_index=0,
                        is_last_chunk=True,
                    ),
                )
            return

        # For head, line_range, and full file modes: use real-time streaming
        # Use _read_text_content for all reading logic with peek-ahead to detect last chunk
        index = 0
        line_iter = FsOperation._read_text_content(read_params)

        # Get the first line
        current_line = await anext(line_iter, None)
        if current_line is None:
            # Empty result
            return

        while True:
            # Peek at the next line to determine if current is last
            next_line = await anext(line_iter, None)
            is_last = (next_line is None)

            yield ReadFileStreamResult(
                code=StatusCode.SUCCESS.code,
                message=StatusCode.SUCCESS.errmsg,
                data=ReadFileChunkData(
                    path=str(file_path),
                    chunk_content=current_line,
                    mode=mode,
                    chunk_size=len(current_line.encode(encoding)),
                    chunk_index=index,
                    is_last_chunk=is_last,
                ),
            )

            if is_last:
                break

            current_line = next_line
            index += 1

    def _log_stream_chunk(self, stream_chunk: Any,
                          method_name: str,
                          method_params: Dict[str, Any],
                          start_time: float,
                          *,
                          stream_log: str,
                          end_stream_log: str):
        """Record the log of stream chunk"""

        def _get_log_event_type():
            """Get log event type safely"""
            if stream_chunk is None:
                return LogEventType.SYS_OP_STREAM
            if not hasattr(stream_chunk, 'data') or stream_chunk.data is None:
                return LogEventType.SYS_OP_STREAM
            if not hasattr(stream_chunk.data, 'is_last_chunk'):
                return LogEventType.SYS_OP_STREAM
            return LogEventType.SYS_OP_STREAM if not stream_chunk.data.is_last_chunk else LogEventType.SYS_OP_END

        log_event_type = _get_log_event_type()
        exec_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000
        event = self._create_sys_operation_event(
            event_type=log_event_type,
            method_name=method_name,
            method_params=method_params,
            method_result=self._safe_model_dump(stream_chunk),
            method_exec_time_ms=exec_time_ms
        )

        if not stream_chunk.data.is_last_chunk:
            sys_operation_logger.debug(stream_log, event=event)
        else:
            sys_operation_logger.info(end_stream_log, event=event)
