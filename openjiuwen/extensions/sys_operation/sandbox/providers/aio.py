# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, List, Tuple, AsyncIterator
import asyncio
import os
import time

from openjiuwen.core.sys_operation.result import (
    ExecuteCmdData,
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
    ExecuteCmdChunkData,
    ExecuteCodeData,
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
    ExecuteCodeChunkData,
    ReadFileResult,
    ReadFileData,
    ReadFileStreamResult,
    ReadFileChunkData,
    WriteFileResult,
    WriteFileData,
    ListFilesResult,
    ListDirsResult,
    FileSystemData,
    FileSystemItem,
    SearchFilesResult,
    SearchFilesData,
    UploadFileResult,
    UploadFileData,
    UploadFileStreamResult,
    UploadFileChunkData,
    DownloadFileResult,
    DownloadFileData,
    DownloadFileStreamResult,
    DownloadFileChunkData,
)
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.core.sys_operation.config import SandboxGatewayConfig
from openjiuwen.core.sys_operation.sandbox.providers.base_provider import (
    BaseFSProvider,
    BaseShellProvider,
    BaseCodeProvider,
)
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
from openjiuwen.core.common.exception.codes import StatusCode


def _is_retryable_error(msg: str) -> bool:
    """Check if the error message indicates a retryable 502/503 status code."""
    return "502" in msg or "503" in msg


@SandboxRegistry.provider("aio", "fs")
class AIOFSProvider(BaseFSProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            if not self.endpoint.base_url:
                raise ValueError("AIO provider requires endpoint.base_url")
            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

    async def read_file(self, path: str, mode: str = "text", **kwargs) -> ReadFileResult:
        import base64
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _read():
                    res = client.file.read_file(file=path)
                    content = res.data.content
                    if mode == "bytes":
                        if isinstance(content, str):
                            decoded = base64.b64decode(content)
                            content = decoded
                    data = ReadFileData(path=path, content=content, mode=mode or "text")
                    return ReadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_read)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio read_file timeout")

    async def write_file(self, path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult:
        import base64
        if isinstance(content, bytes):
            if mode == "bytes":
                content_str = base64.b64encode(content).decode('ascii')
                encoding = "base64"
            else:
                content_str = content.decode('utf-8')
                encoding = "utf-8"
        else:
            content_str = content
            encoding = "utf-8"
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _write():
                    res = client.file.write_file(file=path, content=content_str, encoding=encoding)
                    data = WriteFileData(path=path, size=len(content_str), mode=mode or "text")
                    return WriteFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_write)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio write_file timeout")

    async def list_files(self, path: str, *, recursive: bool = False,
                         max_depth: Optional[int] = None,
                         sort_by: str = "name", sort_descending: bool = False,
                         file_types: Optional[List[str]] = None, **kwargs) -> ListFilesResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _list():
                    list_kwargs = dict(
                        path=path,
                        recursive=recursive,
                        include_size=True,
                    )
                    if max_depth is not None:
                        list_kwargs["max_depth"] = max_depth
                    if file_types is not None:
                        list_kwargs["file_types"] = file_types
                    if sort_by:
                        list_kwargs["sort_by"] = sort_by
                    if sort_descending:
                        list_kwargs["sort_desc"] = sort_descending
                    res = client.file.list_path(**list_kwargs)
                    files = [
                        FileSystemItem(
                            name=item.name,
                            path=item.path,
                            size=item.size if item.size is not None else 0,
                            is_directory=False,
                            modified_time=item.modified_time or "0"
                        )
                        for item in (res.data.files or [])
                        if not item.is_directory
                    ]
                    data = FileSystemData(
                        total_count=len(files),
                        list_items=files,
                        root_path=path,
                        recursive=recursive,
                        max_depth=max_depth
                    )
                    return ListFilesResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_list)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio list_files timeout")

    async def list_directories(self, path: str, *, recursive: bool = False,
                               max_depth: Optional[int] = None,
                               sort_by: str = "name", sort_descending: bool = False,
                               **kwargs) -> ListDirsResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _list():
                    list_kwargs = dict(
                        path=path,
                        recursive=recursive,
                    )
                    if max_depth is not None:
                        list_kwargs["max_depth"] = max_depth
                    if sort_by:
                        list_kwargs["sort_by"] = sort_by
                    if sort_descending:
                        list_kwargs["sort_desc"] = sort_descending
                    res = client.file.list_path(**list_kwargs)
                    dirs = [
                        FileSystemItem(
                            name=item.name,
                            path=item.path,
                            size=0,
                            is_directory=True,
                            modified_time=item.modified_time or "0"
                        )
                        for item in (res.data.files or [])
                        if item.is_directory
                    ]
                    data = FileSystemData(
                        total_count=len(dirs),
                        list_items=dirs,
                        root_path=path,
                        recursive=recursive,
                        max_depth=max_depth
                    )
                    return ListDirsResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_list)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio list_directories timeout")

    async def read_file_stream(self, path: str, *, mode: str = "text",
                               head: Optional[int] = None, tail: Optional[int] = None,
                               line_range: Optional[Tuple[int, int]] = None,
                               encoding: str = "utf-8", chunk_size: int = 8192,
                               **kwargs) -> AsyncIterator[ReadFileStreamResult]:
        result = await self.read_file(path, mode=mode)
        content = result.data.content
        if isinstance(content, bytes):
            text = content.decode(encoding)
        else:
            text = content
        lines = text.splitlines(keepends=True)
        if line_range is not None:
            start, end = line_range
            lines = lines[start:end]
        elif head is not None:
            lines = lines[:head]
        elif tail is not None:
            lines = lines[-tail:]
        filtered = "".join(lines)
        if mode == "bytes":
            data_bytes = filtered.encode(encoding)
            total = len(data_bytes)
            chunk_idx = 0
            for i in range(0, max(total, 1), chunk_size):
                chunk = data_bytes[i:i + chunk_size]
                is_last = (i + chunk_size >= total)
                yield ReadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                           data=ReadFileChunkData(
                                               path=path, chunk_content=chunk, mode="bytes",
                                               chunk_size=chunk_size, chunk_index=chunk_idx, is_last_chunk=is_last
                                           ))
                chunk_idx += 1
        else:
            data_str = filtered
            total = len(data_str)
            chunk_idx = 0
            for i in range(0, max(total, 1), chunk_size):
                chunk = data_str[i:i + chunk_size]
                is_last = (i + chunk_size >= total)
                yield ReadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                           data=ReadFileChunkData(
                                               path=path, chunk_content=chunk, mode="text",
                                               chunk_size=chunk_size, chunk_index=chunk_idx, is_last_chunk=is_last
                                           ))
                chunk_idx += 1

    async def upload_file(self, local_path: str, target_path: str, *,
                          overwrite: bool = False, create_parent_dirs: bool = True,
                          preserve_permissions: bool = True,
                          chunk_size: int = 0, **kwargs) -> UploadFileResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _upload():
                    filename = os.path.basename(local_path)
                    with open(local_path, "rb") as fh:
                        res = client.file.upload_file(
                            file=(filename, fh, "application/octet-stream"),
                            path=target_path,
                        )
                    data = UploadFileData(
                        local_path=local_path,
                        target_path=target_path,
                        size=res.data.file_size,
                    )
                    return UploadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_upload)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio upload_file timeout")

    async def upload_file_stream(self, local_path: str, target_path: str, *,
                                 overwrite: bool = False, create_parent_dirs: bool = True,
                                 preserve_permissions: bool = True,
                                 chunk_size: int = 1048576,
                                 **kwargs) -> AsyncIterator[UploadFileStreamResult]:
        client = await asyncio.to_thread(self._get_client)
        file_size = os.path.getsize(local_path)
        chunk_idx = 0
        with open(local_path, "rb") as fh:
            while True:
                raw = fh.read(chunk_size)
                if not raw:
                    break
                is_first = (chunk_idx == 0)
                content_str = raw.decode("utf-8", errors="replace")
                is_last = (fh.tell() >= file_size)

                def _write_chunk(c=content_str, first=is_first):
                    client.file.write_file(file=target_path, content=c, append=not first)

                await asyncio.to_thread(_write_chunk)
                yield UploadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                             data=UploadFileChunkData(
                                                 local_path=local_path, target_path=target_path,
                                                 chunk_size=chunk_size, chunk_index=chunk_idx, is_last_chunk=is_last
                                             ))
                chunk_idx += 1

    async def download_file(self, source_path: str, local_path: str, *,
                            overwrite: bool = False, create_parent_dirs: bool = True,
                            preserve_permissions: bool = True,
                            chunk_size: int = 0, **kwargs) -> DownloadFileResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _download():
                    chunks = []
                    for chunk in client.file.download_file(path=source_path):
                        chunks.append(chunk)
                    return b"".join(chunks)

                content = await asyncio.to_thread(_download)
                if create_parent_dirs:
                    parent = os.path.dirname(local_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                if not overwrite and os.path.exists(local_path):
                    raise FileExistsError(f"File already exists: {local_path}")
                with open(local_path, "wb") as fh:
                    fh.write(content)
                data = DownloadFileData(
                    source_path=source_path,
                    local_path=local_path,
                    size=len(content),
                )
                return DownloadFileResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)
            except FileExistsError:
                raise
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio download_file timeout")

    async def download_file_stream(self, source_path: str, local_path: str, *,
                                   overwrite: bool = False, create_parent_dirs: bool = True,
                                   preserve_permissions: bool = True,
                                   chunk_size: int = 1048576,
                                   **kwargs) -> AsyncIterator[DownloadFileStreamResult]:
        client = await asyncio.to_thread(self._get_client)
        if create_parent_dirs:
            parent = os.path.dirname(local_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not overwrite and os.path.exists(local_path):
            raise FileExistsError(f"File already exists: {local_path}")

        def _iter_download():
            return list(client.file.download_file(path=source_path))

        raw_chunks = await asyncio.to_thread(_iter_download)
        buffer = b""
        chunk_idx = 0
        total_raw = len(raw_chunks)
        with open(local_path, "wb") as fh:
            for i, raw in enumerate(raw_chunks):
                buffer += raw
                is_last_raw = (i == total_raw - 1)
                while len(buffer) >= chunk_size:
                    piece = buffer[:chunk_size]
                    buffer = buffer[chunk_size:]
                    fh.write(piece)
                    yield DownloadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                                   data=DownloadFileChunkData(
                                                       source_path=source_path, local_path=local_path,
                                                       chunk_size=chunk_size, chunk_index=chunk_idx,
                                                       is_last_chunk=(is_last_raw and len(buffer) == 0)
                                                   ))
                    chunk_idx += 1
                if is_last_raw and buffer:
                    fh.write(buffer)
                    yield DownloadFileStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                                   data=DownloadFileChunkData(
                                                       source_path=source_path, local_path=local_path,
                                                       chunk_size=chunk_size, chunk_index=chunk_idx,
                                                       is_last_chunk=True
                                                   ))

    async def search_files(self, path: str, pattern: str,
                           exclude_patterns: Optional[List[str]] = None) -> SearchFilesResult:
        import fnmatch
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _search():
                    # Try glob_files first; fall back to list_path + fnmatch
                    try:
                        glob_kwargs = dict(
                            path=path,
                            pattern=pattern,
                            files_only=True,
                            include_metadata=True,
                        )
                        if exclude_patterns:
                            glob_kwargs["exclude"] = exclude_patterns
                        res = client.file.glob_files(**glob_kwargs)
                        files = res.data.files or []
                    except Exception as glob_err:
                        if "404" in str(glob_err):
                            # glob_files not supported, fall back to list_path
                            res = client.file.list_path(path=path, recursive=True, include_size=True)
                            files = [
                                f for f in (res.data.files or [])
                                if not f.is_directory and fnmatch.fnmatch(f.name, pattern)
                            ]
                            if exclude_patterns:
                                for ep in exclude_patterns:
                                    files = [f for f in files if not fnmatch.fnmatch(f.name, ep)]
                        else:
                            raise
                    items = [
                        FileSystemItem(
                            name=f.name,
                            path=f.path,
                            size=f.size if f.size is not None else 0,
                            is_directory=f.is_directory or False,
                            modified_time=f.modified_time or "0",
                        )
                        for f in files
                    ]
                    data = SearchFilesData(
                        total_matches=len(items),
                        matching_files=items,
                        search_path=path,
                        search_pattern=pattern,
                        exclude_patterns=exclude_patterns,
                    )
                    return SearchFilesResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_search)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio search_files timeout")


@SandboxRegistry.provider("aio", "shell")
class AIOShellProvider(BaseShellProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

    async def execute_cmd(self, command: str, cwd: str = ".", **kwargs) -> ExecuteCmdResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _exec():
                    res = client.shell.exec_command(command=command)
                    raw_exit = res.data.exit_code if hasattr(res.data, "exit_code") else 0
                    data = ExecuteCmdData(
                        command=command,
                        cwd=cwd or ".",
                        stdout=res.data.output,
                        stderr="",
                        exit_code=raw_exit if raw_exit is not None else 0,
                    )
                    return ExecuteCmdResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_exec)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio execute_cmd timeout")

    async def execute_cmd_stream(self, command: str, *, cwd: Optional[str] = None,
                                 timeout: Optional[int] = 300,
                                 **kwargs) -> AsyncIterator[ExecuteCmdStreamResult]:
        result = await self.execute_cmd(command, cwd=cwd or ".")
        chunk_idx = 0
        stdout = result.data.stdout or ""
        stderr = result.data.stderr or ""
        exit_code = result.data.exit_code
        all_parts = []
        if stdout:
            for line in stdout.splitlines(keepends=True):
                all_parts.append((line, "stdout"))
        if stderr:
            for line in stderr.splitlines(keepends=True):
                all_parts.append((line, "stderr"))
        if not all_parts:
            all_parts.append(("", "stdout"))
        for i, (text, stype) in enumerate(all_parts):
            is_last = (i == len(all_parts) - 1)
            yield ExecuteCmdStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                         data=ExecuteCmdChunkData(
                                             text=text, type=stype, chunk_index=i,
                                             exit_code=exit_code if is_last else None,
                                         ))


@SandboxRegistry.provider("aio", "code")
class AIOCodeProvider(BaseCodeProvider):
    def __init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None):
        super().__init__(endpoint, config)
        self._client = None
        self._timeout_seconds = 30  # Default

    def _get_client(self):
        if self._client is None:
            from agent_sandbox import Sandbox

            if not self.endpoint.base_url:
                raise ValueError("AIO provider requires endpoint.base_url")
            self._client = Sandbox(base_url=self.endpoint.base_url)
        return self._client

    async def execute_code(self, code: str, *, language: str = "python", **kwargs) -> ExecuteCodeResult:
        client = await asyncio.to_thread(self._get_client)
        deadline = time.time() + max(1, int(self._timeout_seconds))
        delay = 0.5
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                def _exec():
                    res = client.code.execute_code(code=code, language=language)
                    raw_exit = res.data.exit_code if hasattr(res.data, "exit_code") else 0
                    data = ExecuteCodeData(
                        code_content=code,
                        language=language,
                        stdout=res.data.stdout or "",
                        stderr=res.data.stderr or "",
                        exit_code=raw_exit if raw_exit is not None else 0,
                    )
                    return ExecuteCodeResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=data)

                return await asyncio.to_thread(_exec)
            except Exception as e:
                last = e
                msg = str(e)
                if _is_retryable_error(msg):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2)
                    continue
                raise
        raise last or TimeoutError("aio execute_code timeout")

    async def execute_code_stream(self, code: str, *, language: str = "python",
                                  timeout: int = 300,
                                  **kwargs) -> AsyncIterator[ExecuteCodeStreamResult]:
        result = await self.execute_code(code, language=language)
        chunk_idx = 0
        stdout = result.data.stdout or ""
        stderr = result.data.stderr or ""
        exit_code = result.data.exit_code
        all_parts = []
        if stdout:
            for line in stdout.splitlines(keepends=True):
                all_parts.append((line, "stdout"))
        if stderr:
            for line in stderr.splitlines(keepends=True):
                all_parts.append((line, "stderr"))
        if not all_parts:
            all_parts.append(("", "stdout"))
        for i, (text, stype) in enumerate(all_parts):
            is_last = (i == len(all_parts) - 1)
            yield ExecuteCodeStreamResult(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg,
                                          data=ExecuteCodeChunkData(
                                              text=text, type=stype, chunk_index=i,
                                              exit_code=exit_code if is_last else None,
                                          ))
