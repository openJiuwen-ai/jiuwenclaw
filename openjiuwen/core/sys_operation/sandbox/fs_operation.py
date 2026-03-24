# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Dict, List, Optional, Tuple, Literal, AsyncIterator

from openjiuwen.core.sys_operation.fs import (
    BaseFsOperation, DEFAULT_READ_CHUNK_SIZE, DEFAULT_READ_STREAM_CHUNK_SIZE,
    DEFAULT_UPLOAD_CHUNK_SIZE, DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
    DEFAULT_DOWNLOAD_CHUNK_SIZE, DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE
)
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.sandbox.run_config import SandboxRunConfig
from openjiuwen.core.sys_operation.sandbox.sandbox_mixin import BaseSandboxMixin
from openjiuwen.core.sys_operation.result import (
    ReadFileResult, ReadFileStreamResult, WriteFileResult,
    UploadFileResult, UploadFileStreamResult, DownloadFileResult,
    DownloadFileStreamResult, ListFilesResult, ListDirsResult, SearchFilesResult
)


@operation(name="fs", mode=OperationMode.SANDBOX, description="Sandbox file system operation")
class FsOperation(BaseFsOperation, BaseSandboxMixin):
    """Sandbox mode file system operation

    Registered via @operation. All methods delegate to Gateway full-chain routing.
    """

    def __init__(self, name: str, mode: OperationMode, description: str, run_config: SandboxRunConfig):
        super().__init__(name, mode, description, run_config)
        self._init_sandbox_context(run_config, op_type="fs")

    async def read_file(
            self, path: str, *, mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None, tail: Optional[int] = None,
            line_range: Optional[Tuple[int, int]] = None, encoding: str = "utf-8",
            chunk_size: int = DEFAULT_READ_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> ReadFileResult:
        raw = await self.invoke(
            "read_file", path=path, mode=mode, head=head, tail=tail,
            line_range=line_range, encoding=encoding, chunk_size=chunk_size, options=options
        )
        return raw if isinstance(raw, ReadFileResult) else ReadFileResult(**raw)

    async def read_file_stream(
            self, path: str, *, mode: Literal['text', 'bytes'] = "text",
            head: Optional[int] = None, tail: Optional[int] = None,
            line_range: Optional[Tuple[int, int]] = None, encoding: str = "utf-8",
            chunk_size: int = DEFAULT_READ_STREAM_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ReadFileStreamResult]:
        async for item in self.invoke_stream(
            "read_file_stream", path=path, mode=mode, head=head, tail=tail,
            line_range=line_range, encoding=encoding, chunk_size=chunk_size, options=options
        ):
            yield ReadFileStreamResult(**item) if isinstance(item, dict) else item

    async def write_file(
            self, path: str, content: str | bytes, *, mode: Literal['text', 'bytes'] = "text",
            prepend_newline: bool = True, append_newline: bool = False,
            create_if_not_exist: bool = True, permissions: str = "644",
            encoding: str = "utf-8", options: Optional[Dict[str, Any]] = None
    ) -> WriteFileResult:
        raw = await self.invoke(
            "write_file", path=path, content=content, mode=mode,
            prepend_newline=prepend_newline, append_newline=append_newline,
            create_if_not_exist=create_if_not_exist, permissions=permissions,
            encoding=encoding, options=options
        )
        return raw if isinstance(raw, WriteFileResult) else WriteFileResult(**raw)

    async def upload_file(
            self, local_path: str, target_path: str, *, overwrite: bool = False,
            create_parent_dirs: bool = True, preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> UploadFileResult:
        raw = await self.invoke(
            "upload_file", local_path=local_path, target_path=target_path,
            overwrite=overwrite, create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions, chunk_size=chunk_size, options=options
        )
        return raw if isinstance(raw, UploadFileResult) else UploadFileResult(**raw)

    async def upload_file_stream(
            self, local_path: str, target_path: str, *, overwrite: bool = False,
            create_parent_dirs: bool = True, preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_UPLOAD_STREAM_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[UploadFileStreamResult]:
        async for item in self.invoke_stream(
            "upload_file_stream", local_path=local_path, target_path=target_path,
            overwrite=overwrite, create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions, chunk_size=chunk_size, options=options
        ):
            yield UploadFileStreamResult(**item) if isinstance(item, dict) else item

    async def download_file(
            self, source_path: str, local_path: str, *, overwrite: bool = False,
            create_parent_dirs: bool = True, preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> DownloadFileResult:
        raw = await self.invoke(
            "download_file", source_path=source_path, local_path=local_path,
            overwrite=overwrite, create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions, chunk_size=chunk_size, options=options
        )
        return raw if isinstance(raw, DownloadFileResult) else DownloadFileResult(**raw)

    async def download_file_stream(
            self, source_path: str, local_path: str, *, overwrite: bool = False,
            create_parent_dirs: bool = True, preserve_permissions: bool = True,
            chunk_size: int = DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE, options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[DownloadFileStreamResult]:
        async for item in self.invoke_stream(
            "download_file_stream", source_path=source_path, local_path=local_path,
            overwrite=overwrite, create_parent_dirs=create_parent_dirs,
            preserve_permissions=preserve_permissions, chunk_size=chunk_size, options=options
        ):
            yield DownloadFileStreamResult(**item) if isinstance(item, dict) else item

    async def list_files(
            self, path: str, *, recursive: bool = False, max_depth: Optional[int] = None,
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False, file_types: Optional[List[str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ListFilesResult:
        raw = await self.invoke(
            "list_files", path=path, recursive=recursive, max_depth=max_depth,
            sort_by=sort_by, sort_descending=sort_descending,
            file_types=file_types, options=options
        )
        return raw if isinstance(raw, ListFilesResult) else ListFilesResult(**raw)

    async def list_directories(
            self, path: str, *, recursive: bool = False, max_depth: Optional[int] = None,
            sort_by: Literal['name', 'modified_time', 'size'] = "name",
            sort_descending: bool = False, options: Optional[Dict[str, Any]] = None
    ) -> ListDirsResult:
        raw = await self.invoke(
            "list_directories", path=path, recursive=recursive, max_depth=max_depth,
            sort_by=sort_by, sort_descending=sort_descending, options=options
        )
        return raw if isinstance(raw, ListDirsResult) else ListDirsResult(**raw)

    async def search_files(
            self, path: str, pattern: str, exclude_patterns: Optional[List[str]] = None
    ) -> SearchFilesResult:
        raw = await self.invoke(
            "search_files", path=path, pattern=pattern, exclude_patterns=exclude_patterns
        )
        return raw if isinstance(raw, SearchFilesResult) else SearchFilesResult(**raw)
