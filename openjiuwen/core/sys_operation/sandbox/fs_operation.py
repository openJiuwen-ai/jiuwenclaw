# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Tuple, Dict, Any, Literal, List, AsyncIterator

from openjiuwen.core.sys_operation.fs import BaseFsOperation, DEFAULT_READ_STREAM_CHUNK_SIZE, DEFAULT_READ_CHUNK_SIZE, \
    DEFAULT_UPLOAD_CHUNK_SIZE, DEFAULT_UPLOAD_STREAM_CHUNK_SIZE, DEFAULT_DOWNLOAD_CHUNK_SIZE, \
    DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ReadFileResult, WriteFileResult, \
    UploadFileResult, DownloadFileResult, ListFilesResult, ListDirsResult, SearchFilesResult, \
    ReadFileStreamResult, DownloadFileStreamResult, UploadFileStreamResult
)


@operation(name="fs", mode=OperationMode.SANDBOX, description="sandbox fs operation")
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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
            head: Number of lines to read from the start (text mode only).
                  0 is equivalent to None.
            tail: Number of lines to read from the end (text mode only).
                  0 is equivalent to None.
            line_range: Specific line range to read (start, end) - 1-indexed, inclusive (text mode only).
                  If start <= 0 or end <= 0 or start > end, returns empty content.
            encoding: Character encoding for text mode (default: utf-8).
            chunk_size: Buffer size for bytes mode reading (default: 8192 bytes).
            options: Extended configuration options (dict, optional).

        Returns:
            AsyncIterator[ReadFileStreamResult]: Streaming structured results, line-by-line or chunk-by-chunk.
        """
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
            chunk_size: Maximum number of bytes to download at once (default: 0, unlimited)
            options: Extended configuration options (dict, optional).

        Returns:
            AsyncIterator[UploadFileStreamResult]: Streaming structured results, chunk-by-chunk.
        """
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Fs operation sandbox mode is not implemented yet.")
