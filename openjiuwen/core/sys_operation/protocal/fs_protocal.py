# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Literal, Optional, Tuple, Dict, Any, AsyncIterator, List

from openjiuwen.core.sys_operation.result import (
    ReadFileResult,
    ReadFileStreamResult,
    WriteFileResult,
    UploadFileResult,
    UploadFileStreamResult,
    DownloadFileResult,
    DownloadFileStreamResult,
    ListFilesResult,
    ListDirsResult,
    SearchFilesResult,
)

# Default chunk sizes
DEFAULT_READ_CHUNK_SIZE = 0
DEFAULT_UPLOAD_CHUNK_SIZE = 0
DEFAULT_DOWNLOAD_CHUNK_SIZE = 0
DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE = 1024 * 1024
DEFAULT_UPLOAD_STREAM_CHUNK_SIZE = 1024 * 1024
DEFAULT_READ_STREAM_CHUNK_SIZE = 8192  # 8KB


class BaseFsProtocal(ABC):
    """Unified FS method signatures shared by Operation and Provider layers."""

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def write_file(self, path: str, content: str | bytes, *,
                         mode: Literal['text', 'bytes'] = "text",
                         prepend_newline: bool = True,
                         append_newline: bool = False,
                         create_if_not_exist: bool = True,
                         permissions: str = "644",
                         encoding: str = "utf-8",
                         options: Optional[Dict[str, Any]] = None
                         ) -> WriteFileResult:
        pass

    @abstractmethod
    async def upload_file(self, local_path: str, target_path: str, *,
                          overwrite: bool = False,
                          create_parent_dirs: bool = True,
                          preserve_permissions: bool = True,
                          chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
                          options: Optional[Dict[str, Any]] = None
                          ) -> UploadFileResult:
        pass

    @abstractmethod
    async def upload_file_stream(self, local_path: str, target_path: str, *,
                                 overwrite: bool = False,
                                 create_parent_dirs: bool = True,
                                 preserve_permissions: bool = True,
                                 chunk_size: int = DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
                                 options: Optional[Dict[str, Any]] = None
                                 ) -> AsyncIterator[UploadFileStreamResult]:
        pass

    @abstractmethod
    async def download_file(self, source_path: str, local_path: str, *,
                            overwrite: bool = False,
                            create_parent_dirs: bool = True,
                            preserve_permissions: bool = True,
                            chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
                            options: Optional[Dict[str, Any]] = None
                            ) -> DownloadFileResult:
        pass

    @abstractmethod
    async def download_file_stream(self, source_path: str, local_path: str, *,
                                   overwrite: bool = False,
                                   create_parent_dirs: bool = True,
                                   preserve_permissions: bool = True,
                                   chunk_size: int = DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE,
                                   options: Optional[Dict[str, Any]] = None
                                   ) -> AsyncIterator[DownloadFileStreamResult]:
        pass

    @abstractmethod
    async def list_files(self, path: str, *,
                         recursive: bool = False,
                         max_depth: Optional[int] = None,
                         sort_by: Literal['name', 'modified_time', 'size'] = "name",
                         sort_descending: bool = False,
                         file_types: Optional[List[str]] = None,
                         options: Optional[Dict[str, Any]] = None
                         ) -> ListFilesResult:
        pass

    @abstractmethod
    async def list_directories(self, path: str, *,
                               recursive: bool = False,
                               max_depth: Optional[int] = None,
                               sort_by: Literal['name', 'modified_time', 'size'] = "name",
                               sort_descending: bool = False,
                               options: Optional[Dict[str, Any]] = None
                               ) -> ListDirsResult:
        pass

    @abstractmethod
    async def search_files(self, path: str,
                           pattern: str,
                           exclude_patterns: Optional[List[str]] = None
                           ) -> SearchFilesResult:
        pass
