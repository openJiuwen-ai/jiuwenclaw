# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""拦截沙箱 execute_cmd：仅白名单连接器 CLI 改在宿主执行.

普通 powershell / cmd / bash（如 Get-ChildItem）必须留在沙箱内, 否则会以
宿主用户跑, 绕过 list_files 已经拦住的 NTFS 隔离.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from jiuwenswarm.common.connectors import command_uses_connector_cli
from jiuwenswarm.common.host_shell import (
    connector_wrap_posix,
    host_environ,
    host_shell_argv,
    host_shell_wrap,
    run_host_subprocess,
)

logger = logging.getLogger(__name__)

_installed = False


def command_should_host_exec(command: str) -> bool:
    """只有连接器 CLI 才出沙箱; Get-ChildItem 等普通命令走沙箱."""
    return command_uses_connector_cli(command)


def _host_workdir(cwd: Optional[str]) -> Optional[str]:
    workdir = None if not cwd or cwd == "." else cwd
    if workdir and not os.path.isdir(workdir):
        logger.info("[connector] skip sandbox cwd %r; using process cwd", workdir)
        return None
    return workdir


def install_connector_host_exec_hooks() -> None:
    global _installed
    if _installed:
        return

    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.sys_operation.result import (
        ExecuteCmdChunkData,
        ExecuteCmdStreamResult,
    )
    from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
        JiuwenBoxShellProvider,
        _build_shell_error_result,
    )

    original = JiuwenBoxShellProvider.execute_cmd

    async def _run_host(self: Any, command: str, argv: list[str], cwd: Optional[str], timeout: Optional[int]) -> Any:
        logger.info("[connector] host-exec %s (%s)", argv[0], argv[1] if len(argv) > 1 else "")
        local_result = await asyncio.to_thread(
            run_host_subprocess,
            argv,
            cwd=_host_workdir(cwd),
            env=host_environ(),
            timeout=timeout if (timeout and timeout > 0) else None,
        )
        return self._wrap_shell_local_result(command, cwd, timeout, local_result)

    async def execute_cmd(
        self: Any,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        if command_should_host_exec(command):
            argv = host_shell_argv(command, shell_type=kwargs.get("shell_type"))
            if not argv:
                argv = host_shell_wrap(
                    command.strip(),
                    posix=connector_wrap_posix(command, kwargs.get("shell_type")),
                )
            return await _run_host(self, command, argv, cwd, timeout)
        return await original(
            self, command, cwd=cwd, timeout=timeout, environment=environment, **kwargs
        )

    async def execute_cmd_stream(
        self: Any,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = 300,
        environment: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        result = await execute_cmd(
            self, command, cwd=cwd, timeout=timeout, environment=environment, **kwargs
        )
        if result.code != StatusCode.SUCCESS.code:
            yield _build_shell_error_result(
                "execute_cmd_stream",
                result.message,
                ExecuteCmdStreamResult,
                data=ExecuteCmdChunkData(chunk_index=0, exit_code=-1),
            )
            return
        chunks: list[tuple[str, str]] = []
        for line in (result.data.stdout or "").splitlines(keepends=True):
            chunks.append((line, "stdout"))
        for line in (result.data.stderr or "").splitlines(keepends=True):
            chunks.append((line, "stderr"))
        for index, (text, kind) in enumerate(chunks):
            yield ExecuteCmdStreamResult(
                code=StatusCode.SUCCESS.code,
                message=f"Get {kind} stream successfully",
                data=ExecuteCmdChunkData(text=text, type=kind, chunk_index=index),
            )
        yield ExecuteCmdStreamResult(
            code=StatusCode.SUCCESS.code,
            message="Command executed successfully",
            data=ExecuteCmdChunkData(chunk_index=len(chunks), exit_code=result.data.exit_code),
        )

    JiuwenBoxShellProvider.execute_cmd = execute_cmd  # type: ignore[method-assign]
    JiuwenBoxShellProvider.execute_cmd_stream = execute_cmd_stream  # type: ignore[method-assign]
    _installed = True
