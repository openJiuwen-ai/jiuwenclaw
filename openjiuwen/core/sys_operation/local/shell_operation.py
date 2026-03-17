# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import locale
import os
import platform
from typing import Optional, Dict, Any, AsyncIterator, Callable

from _pytest import pathlib

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import LogEventType, sys_operation_logger
from openjiuwen.core.sys_operation.local.utils import OperationUtils, StreamEvent, StreamEventType
from openjiuwen.core.sys_operation.result.base_result import build_operation_error_result
from openjiuwen.core.sys_operation.shell import BaseShellOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult, ExecuteCmdData, ExecuteCmdChunkData
)


@operation(name="shell", mode=OperationMode.LOCAL, description="local shell operation")
class ShellOperation(BaseShellOperation):
    """Shell operation"""

    _BUFFERING_WRAPPERS: Dict[str, Callable[[str], str]] = {
        "windows": lambda cmd: cmd,
        "linux": lambda cmd: f"stdbuf -oL -eL {cmd}",
        "darwin": lambda cmd: f"script -q /dev/null {cmd}",
    }

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        """
        Asynchronously execute a command(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            ExecuteCmdResult: Execution result.
        """

        method_name = self.execute_cmd.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute cmd", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_cmd_err(error_msg: str, data: Optional[ExecuteCmdData] = None) -> ExecuteCmdResult:
            """Create standard error result for cmd execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd", "error_msg": error_msg},
                result_cls=ExecuteCmdResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute cmd", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        if not command or not command.strip():
            return _create_exec_cmd_err(error_msg="command can not be empty")

        actual_cwd = None
        try:
            # Resolve CWD
            actual_cwd = self._resolve_cwd(cwd)
            if not self._check_allowlist(command):
                return _create_exec_cmd_err(error_msg="command not allowed by allowlist",
                                            data=ExecuteCmdData(command=command, cwd=str(actual_cwd)))

            exec_env = OperationUtils.prepare_environment(environment)
            wrap_command = self._wrap_command_with_buffering(command)
            proc = await asyncio.create_subprocess_shell(
                wrap_command,
                cwd=str(actual_cwd),
                env=exec_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            encoding = (options or {}).get("encoding", self._detect_shell_encoding())
            process_handler = OperationUtils.create_handler(process=proc, encoding=encoding, timeout=timeout)
            invoke_data = await process_handler.invoke()
            invoke_exception = getattr(invoke_data, "exception", None)
            if isinstance(invoke_exception, asyncio.TimeoutError):
                return _create_exec_cmd_err(f"execution timeout after {timeout} seconds",
                                            data=ExecuteCmdData(
                                                command=command,
                                                cwd=str(actual_cwd),
                                                exit_code=invoke_data.exit_code,
                                                stdout=invoke_data.stdout,
                                                stderr=invoke_data.stderr
                                            ))
            success_result = ExecuteCmdResult(
                code=StatusCode.SUCCESS.code,
                message="Command executed successfully",
                data=ExecuteCmdData(
                    command=command,
                    cwd=str(actual_cwd),
                    exit_code=invoke_data.exit_code,
                    stdout=invoke_data.stdout,
                    stderr=invoke_data.stderr
                )
            )
            sys_operation_logger.info("End to execute cmd", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_END,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(success_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
                metadata={"wrap_command": wrap_command}
            ))
            return success_result

        except Exception as e:
            return _create_exec_cmd_err(error_msg=f"unexpected error: {str(e)}",
                                        data=ExecuteCmdData(command=command, cwd=str(actual_cwd)))

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        """
        Asynchronously execute a command streaming(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCmdStreamResult]: Streaming structured results.
        """
        method_name = self.execute_cmd_stream.__name__
        method_params = locals().copy()
        method_params.pop('self', None)

        start_time = asyncio.get_event_loop().time()
        sys_operation_logger.info("Start to execute cmd streaming", event=self._create_sys_operation_event(
            event_type=LogEventType.SYS_OP_START,
            method_name=method_name,
            method_params=method_params
        ))

        def _create_exec_cmd_stream_err(error_msg: str,
                                        data: Optional[ExecuteCmdChunkData] = None) -> ExecuteCmdStreamResult:
            """Create standardized error result for streaming command execution"""
            if data and hasattr(data, "exit_code") and data.exit_code is None:
                data.exit_code = -1
            err_result = build_operation_error_result(
                error_type=StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR,
                msg_format_kwargs={"execution": "execute_cmd_stream", "error_msg": error_msg},
                result_cls=ExecuteCmdStreamResult,
                data=data
            )
            sys_operation_logger.error("Failed to execute cmd streaming", event=self._create_sys_operation_event(
                event_type=LogEventType.SYS_OP_ERROR,
                method_name=method_name,
                method_params=method_params,
                method_result=self._safe_model_dump(err_result),
                method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
            ))
            return err_result

        chunk_index = 0

        if not command or not command.strip():
            yield _create_exec_cmd_stream_err(
                error_msg="command can not be empty",
                data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1)
            )
            return

        try:
            actual_cwd = self._resolve_cwd(cwd)

            if not self._check_allowlist(command):
                yield _create_exec_cmd_stream_err(
                    error_msg="command not allowed by allowlist",
                    data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1))
                return

            exec_env = OperationUtils.prepare_environment(environment)
            wrap_command = self._wrap_command_with_buffering(command)
            process = await asyncio.create_subprocess_shell(
                wrap_command,
                cwd=str(actual_cwd),
                env=exec_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            chunk_size = (options or {}).get("chunk_size", 1024)
            encoding = (options or {}).get("encoding", self._detect_shell_encoding())
            process_handler = OperationUtils.create_handler(process=process, chunk_size=chunk_size, encoding=encoding,
                                                            timeout=timeout)

            def _stream_event_trans(stream_event_data: StreamEvent, data_idx: int) -> Optional[ExecuteCmdStreamResult]:
                """Transform raw StreamEvent to structured ExecuteCmdStreamResult"""

                def _handle_std_out_err(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle STDOUT/STDERR stream events"""
                    chunk_data = ExecuteCmdChunkData(text=event.data, type=event.type.value, chunk_index=idx)
                    stream_result = ExecuteCmdStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message=f"Get {chunk_data.type} stream successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.debug("Receive execute cmd stream", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_STREAM,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(stream_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000
                    ))
                    return stream_result

                def _handle_exec_error(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle execution error events"""
                    chunk_data = ExecuteCmdChunkData(chunk_index=idx, exit_code=-1)
                    error_msg = f"execution receive error: {event.data}"
                    return _create_exec_cmd_stream_err(error_msg, chunk_data)

                def _handle_process_exit(event: StreamEvent, idx: int) -> ExecuteCmdStreamResult:
                    """Handle process exit event (final chunk)"""
                    exit_code = event.data
                    chunk_data = ExecuteCmdChunkData(chunk_index=idx, exit_code=exit_code)
                    exit_result = ExecuteCmdStreamResult(
                        code=StatusCode.SUCCESS.code,
                        message="Command executed successfully",
                        data=chunk_data
                    )
                    sys_operation_logger.info("End to execute cmd streaming", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_END,
                        method_name=method_name,
                        method_params=method_params,
                        method_result=self._safe_model_dump(exit_result),
                        method_exec_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
                        metadata={"wrap_command": wrap_command}
                    ))
                    return exit_result

                event_handler_map: dict[StreamEventType, Any] = {
                    StreamEventType.STDOUT: _handle_std_out_err,
                    StreamEventType.STDERR: _handle_std_out_err,
                    StreamEventType.ERROR: _handle_exec_error,
                    StreamEventType.EXIT: _handle_process_exit
                }
                handler = event_handler_map.get(stream_event_data.type)
                if handler is None:
                    sys_operation_logger.warning("Failed to get event handler", event=self._create_sys_operation_event(
                        event_type=LogEventType.SYS_OP_ERROR,
                        method_name=method_name,
                        metadata={"stream_type": stream_event_data.type.value}
                    ))
                    return None
                else:
                    return handler(stream_event_data, data_idx)

            async for chunk in process_handler.stream():
                modify_data = _stream_event_trans(chunk, chunk_index)
                if modify_data:
                    yield modify_data
                    chunk_index += 1
                if chunk.type in (StreamEventType.ERROR, StreamEventType.EXIT):
                    return

        except Exception as e:
            yield _create_exec_cmd_stream_err(error_msg=f"unexpected error: {str(e)}",
                                              data=ExecuteCmdChunkData(chunk_index=chunk_index, exit_code=-1))
            return

    def _check_allowlist(self, command: str) -> bool:
        """Check if command is in allowlist."""
        if not hasattr(self._run_config, 'shell_allowlist') or not self._run_config.shell_allowlist:
            return True

        cmd_prefix = command.split()[0] if command.strip() else ""
        return any(cmd_prefix == allowed or cmd_prefix.endswith(os.sep + allowed)
                   for allowed in self._run_config.shell_allowlist)

    def _resolve_cwd(self, cwd: Optional[str]) -> pathlib.Path:
        """Resolve CWD against work_dir (if configured)."""
        work_dir_val = getattr(self._run_config, 'work_dir', None)

        if work_dir_val is None:
            if not cwd:
                return pathlib.Path.cwd()
            return pathlib.Path(cwd).expanduser().resolve()

        work_dir = pathlib.Path(work_dir_val).resolve()
        if not cwd:
            return work_dir

        target = pathlib.Path(cwd).expanduser()
        if not target.is_absolute():
            target = work_dir / target

        return target.resolve()

    def _wrap_command_with_buffering(self, command: str) -> str:
        """"Wraps a command string with OS-specific buffering wrapper if available."""
        os_name = platform.system().lower()
        wrapper = self._BUFFERING_WRAPPERS.get(os_name)
        return wrapper(command) if wrapper else command

    @staticmethod
    def _detect_shell_encoding() -> str:
        """Detect the shell output encoding for the current system.
        """

        try:
            encoding = locale.getpreferredencoding(False)
            return encoding if encoding else "utf-8"
        except Exception:
            return "utf-8"
