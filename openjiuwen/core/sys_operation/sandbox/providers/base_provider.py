# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List, Tuple, Literal, AsyncIterator

from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.protocal.fs_protocal import (
    BaseFsProtocal,
    DEFAULT_READ_CHUNK_SIZE,
    DEFAULT_READ_STREAM_CHUNK_SIZE,
    DEFAULT_UPLOAD_CHUNK_SIZE,
    DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
    DEFAULT_DOWNLOAD_CHUNK_SIZE,
    DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE,
)
from openjiuwen.core.sys_operation.protocal.shell_protocal import BaseShellProtocal
from openjiuwen.core.sys_operation.protocal.code_protocal import BaseCodeProtocal
from openjiuwen.core.sys_operation.result import (
    ReadFileResult, WriteFileResult, ExecuteCmdResult, ExecuteCodeResult,
    ListFilesResult, ListDirsResult, SearchFilesResult,
    ReadFileStreamResult, ExecuteCmdStreamResult, ExecuteCodeStreamResult,
    UploadFileResult, UploadFileStreamResult, DownloadFileResult, DownloadFileStreamResult
)
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint


class BaseFSProvider(BaseFsProtocal, ABC):
    """Abstract interface for File System capabilities of a sandbox."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        self.endpoint = endpoint
        self.config = config

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
        raise NotImplementedError(f"{self.__class__.__name__}.read_file is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.read_file_stream is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.write_file is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.upload_file is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.upload_file_stream is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.download_file is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.download_file_stream is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.list_files is not implemented")

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
        raise NotImplementedError(f"{self.__class__.__name__}.list_directories is not implemented")

    async def search_files(
            self,
            path: str,
            pattern: str,
            exclude_patterns: Optional[List[str]] = None
    ) -> SearchFilesResult:
        raise NotImplementedError(f"{self.__class__.__name__}.search_files is not implemented")


class BaseShellProvider(BaseShellProtocal, ABC):
    """Abstract interface for Shell execution capabilities of a sandbox."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        self.endpoint = endpoint
        self.config = config

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        raise NotImplementedError(f"{self.__class__.__name__}.execute_cmd is not implemented")

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        raise NotImplementedError(f"{self.__class__.__name__}.execute_cmd_stream is not implemented")


class BaseCodeProvider(BaseCodeProtocal, ABC):
    """Abstract interface for Code execution capabilities of a sandbox."""

    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        self.endpoint = endpoint
        self.config = config

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        raise NotImplementedError(f"{self.__class__.__name__}.execute_code is not implemented")

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        raise NotImplementedError(f"{self.__class__.__name__}.execute_code_stream is not implemented")
