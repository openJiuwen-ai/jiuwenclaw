# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Send File Toolkit

提供发送文件到用户的工具。支持发送一个或多个文件。

使用方式：
1. 创建 SendFileToolkit 实例
2. 调用 get_tools() 获取工具列表
3. 工具会自动注册到 Runner 中

分布式模式：
- 当 file_transfer.enabled=true 时，使用分片传输将文件发送到 Gateway
- Gateway 接收后调用 Channel API 发送给用户
"""

from __future__ import annotations

import json
import os
import logging
import time
from typing import Any, List, Union

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.agentserver.file_transfer_manager import get_file_transfer_manager
from jiuwenclaw.agentserver.session_history import append_history_record
from jiuwenclaw.config import get_file_transfer_config

logger = logging.getLogger(__name__)


def _append_sent_file_paths(result_parts: list[str], sent_paths: list[str]) -> None:
    """Append absolute paths for artifact detection (plain str tool_result)."""
    if not sent_paths:
        return
    result_parts.append("已发送文件路径：")
    for file_path in sent_paths:
        result_parts.append(f"  - {file_path}")


def _build_files_payload(valid_files: list[str]) -> list[dict[str, str]]:
    return [
        {
            "path": file_path,
            "name": os.path.basename(file_path),
        }
        for file_path in valid_files
    ]


class SendFileToolkit:
    """Toolkit for sending files to users."""

    def __init__(
        self,
        request_id: str,
        session_id: str,
        channel_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Initialize SendFileToolkit.

        Args:
            request_id: Request identifier for message routing.
            session_id: Session identifier for message routing.
            channel_id: Channel identifier for message routing.
            metadata: 与 AgentRequest.metadata 一致（E2A channel_context 映射结果），用于 send_push。
        """
        self.request_id = request_id
        self.session_id = session_id
        self.channel_id = channel_id
        self._request_metadata = dict(metadata) if metadata else None
        logger.debug(
            "[SendFileToolkit] 初始化 request_id=%s session_id=%s channel_id=%s has_metadata=%s",
            request_id, session_id, channel_id, bool(self._request_metadata),
        )

    def update_runtime_context(
        self,
        *,
        request_id: str,
        session_id: str,
        channel_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update per-request runtime context without recreating the toolkit/tool.
        """
        self.request_id = request_id
        self.session_id = session_id
        self.channel_id = channel_id
        self._request_metadata = dict(metadata) if metadata else None
        logger.debug(
            "[SendFileToolkit] update_runtime_context request_id=%s session_id=%s channel_id=%s has_metadata=%s",
            request_id, session_id, channel_id, bool(self._request_metadata),
        )

    async def send_file(self, abs_file_path_list: Union[List[str], str]) -> str:
        """Send files to user."""
        if isinstance(abs_file_path_list, str):
            try:
                parsed = json.loads(abs_file_path_list)
                if isinstance(parsed, list):
                    abs_file_path_list = parsed
                elif isinstance(parsed, str):
                    abs_file_path_list = [parsed]
                else:
                    abs_file_path_list = [abs_file_path_list]
            except (TypeError, ValueError):
                abs_file_path_list = [abs_file_path_list]

        if not isinstance(abs_file_path_list, list):
            abs_file_path_list = [str(abs_file_path_list)]

        valid_files = []
        missing_files = []
        for fp in abs_file_path_list:
            fp = str(fp).strip()
            if not fp:
                continue
            if os.path.isfile(fp):
                valid_files.append(fp)
            else:
                missing_files.append(fp)
                logger.warning("[SendFileToolkit] 文件不存在: %s", fp)

        #  诊断日志：观察文件路径和 output_dir ContextVar
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import get_effective_request_output_dir
        actual_output_dir = get_effective_request_output_dir()
        logger.info(
            "[SendFileToolkit]  DIAGNOSTIC: 文件发送检查 "
            "| session_id=%s "
            "| output_dir_from_ContextVar=%s "
            "| valid_files=[%s] "
            "| missing_files=[%s]",
            self.session_id,
            actual_output_dir,
            ", ".join(valid_files),
            ", ".join(missing_files),
        )

        if not valid_files:
            msg_parts = ["发送文件失败：所有文件均不存在"]
            for mf in missing_files:
                msg_parts.append(f"  - {mf}")
            return "\n".join(msg_parts)

        logger.info(
            "[SendFileToolkit] send_file 开始 session_id=%s 有效文件=%d 缺失=%d",
            self.session_id, len(valid_files), len(missing_files),
        )

        # 检查是否启用分布式文件传输
        ft_config = get_file_transfer_config()
        if ft_config.enabled:
            return await self._send_file_distributed(valid_files, missing_files)
        else:
            return await self._send_file_local(valid_files, missing_files)

    async def _emit_chat_file(self, valid_files: list[str]) -> None:
        """Push authoritative chat.file after paths are validated on disk."""
        if not valid_files:
            return
        files_payload = _build_files_payload(valid_files)
        from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer

        server = AgentWebSocketServer.get_instance()
        msg: dict[str, Any] = {
            "request_id": self.request_id,
            "channel_id": self.channel_id,
            "session_id": self.session_id,
            "payload": {
                "event_type": "chat.file",
                "files": files_payload,
            },
            "is_complete": False,
        }
        if self._request_metadata:
            msg["metadata"] = dict(self._request_metadata)
        await server.send_push(msg)
        logger.info(
            "[SendFileToolkit] chat.file 已推送 request_id=%s file_count=%d paths=%s",
            self.request_id,
            len(valid_files),
            valid_files,
        )
        append_history_record(
            session_id=self.session_id,
            request_id=self.request_id,
            channel_id=self.channel_id,
            role="assistant",
            content="",
            timestamp=time.time(),
            event_type="chat.file",
            extra={"files": files_payload},
        )

    async def _send_file_local(
        self,
        valid_files: List[str],
        missing_files: List[str],
    ) -> str:
        """本地模式：直接传递文件路径。"""
        try:
            from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
            server = AgentWebSocketServer.get_instance()

            files_payload = []
            try:
                from jiuwenclaw.agentserver.tools.web_file_download import (
                    build_file_download_info,
                )

                for file_path in valid_files:
                    base_name = os.path.basename(file_path)
                    download_info = build_file_download_info(
                        file_path, base_name, self.session_id
                    )
                    files_payload.append({
                        "path": file_path,
                        "name": base_name,
                        "size": download_info["size"],
                        "mime_type": download_info["mime_type"],
                        "download_url": download_info["download_url"],
                        "download_token": download_info["download_token"],
                        "expires_at": download_info.get("expires_at"),
                    })
            except Exception as download_err:
                logger.warning(
                    "[SendFileToolkit] 生成下载信息失败，回退到基础模式: %s",
                    download_err,
                )
                files_payload = [
                    {
                        "path": file_path,
                        "name": os.path.basename(file_path),
                    }
                    for file_path in valid_files
                ]

            # 推送 chat.file 消息
            msg = {
                "request_id": self.request_id,
                "channel_id": self.channel_id,
                "session_id": self.session_id,
                "payload": {
                    "event_type": "chat.file",
                    "files": files_payload,
                },
                "is_complete": False,
            }
            if self._request_metadata:
                msg["metadata"] = dict(self._request_metadata)
            await server.send_push(msg)

            # 记录历史
            from jiuwenclaw.agentserver.session_history import (
                append_history_record as _append_history_record,
            )
            _append_history_record(
                session_id=self.session_id,
                request_id=self.request_id,
                channel_id=self.channel_id,
                role="assistant",
                event_type="chat.file",
                content="",
                timestamp=time.time(),
                extra={"files": files_payload},
            )
            logger.info(
                "[SendFileToolkit] chat.file 已推送 request_id=%s file_count=%d paths=%s",
                self.request_id,
                len(valid_files),
                valid_files,
            )

            # 构建返回结果（补充路径列表输出）
            result_parts = [f"成功发送 {len(valid_files)} 个文件"]
            _append_sent_file_paths(result_parts, valid_files)
            if missing_files:
                result_parts.append("以下文件不存在，未发送：")
                for mf in missing_files:
                    result_parts.append(f"  - {mf}")
            return "\n".join(result_parts)
        except Exception as e:
            logger.exception(
                "[SendFileToolkit] _send_file_local 失败 session_id=%s error=%s",
                self.session_id,
                str(e),
            )
            return f"提交文件失败: {str(e)}"

    async def _send_file_distributed(
        self,
        valid_files: List[str],
        missing_files: List[str],
    ) -> str:
        """分布式模式：通过分片传输发送文件到 Gateway。"""
        ft_manager = get_file_transfer_manager()
        success_count = 0
        failed_files = []
        sent_paths: list[str] = []

        for file_path in valid_files:
            try:
                async def send_callback(event_type: str, params: dict) -> None:
                    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
                    server = AgentWebSocketServer.get_instance()
                    msg = {
                        "request_id": self.request_id,
                        "channel_id": self.channel_id,
                        "session_id": self.session_id,
                        "payload": {
                            "event_type": event_type,
                            **params,
                        },
                        "is_complete": False,
                    }
                    await server.send_push(msg)

                result = await ft_manager.send_file(
                    file_path=file_path,
                    send_callback=send_callback,
                    session_id=self.session_id,
                    channel_id=self.channel_id,
                    request_id=self.request_id,
                )

                if result.get("success"):
                    success_count += 1
                    sent_paths.append(file_path)
                    logger.info(
                        "[SendFileToolkit] 分布式发送成功 file=%s transfer_id=%s",
                        file_path, result.get("transfer_id"),
                    )
                else:
                    failed_files.append({
                        "file": file_path,
                        "error": result.get("error", "unknown error"),
                    })
                    logger.warning(
                        "[SendFileToolkit] 分布式发送失败 file=%s error=%s",
                        file_path, result.get("error"),
                    )
            except Exception as e:
                failed_files.append({"file": file_path, "error": str(e)})
                logger.exception(
                    "[SendFileToolkit] 分布式发送异常 file=%s", file_path,
                )

        result_parts = []
        if success_count > 0:
            result_parts.append(f"成功发送 {success_count} 个文件")
            _append_sent_file_paths(result_parts, sent_paths)
        if failed_files:
            result_parts.append(f"发送失败 {len(failed_files)} 个文件：")
            for ff in failed_files:
                result_parts.append(f"  - {ff['file']}: {ff['error']}")
        if missing_files:
            result_parts.append("以下文件不存在，未发送：")
            for mf in missing_files:
                result_parts.append(f"  - {mf}")

        return "\n".join(result_parts) if result_parts else "发送完成"

    def get_tools(self) -> List[Tool]:
        """Return tools for registration in Runner."""
        def make_tool(name: str, description: str, input_params: dict, func) -> Tool:
            card = ToolCard(name=name, description=description, input_params=input_params)
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="send_file_to_user",
                description=(
                    "【文件发送工具】当需要将生成的文件、导出的数据、创建的文档等发送给用户时使用此工具。\n"
                    "\n使用场景：\n"
                    "- 用户请求导出/下载文件\n"
                    "- 任务完成后需要交付文件\n"
                    "- 生成报告/文档后发送给用户\n"
                    "\n文件保存位置说明：\n"
                    "- Agent 的当前工作目录 (cwd) 是 effective_project_dir，Agent 可以在其中访问和编辑项目文件\n"
                    "- 但 Agent 生成的输出文件应保存至 output_dir 目录\n"
                    "- output_dir 可通过 get_effective_request_output_dir() API 获取，是为每个会话创建的隔离输出目录\n"
                    "- 建议优先将输出文件保存到 output_dir，确保文件隔离和易于管理\n"
                    "\n参数格式：\n"
                    "- 接受路径数组，路径必须是绝对路径\n"
                    "- 可以接受来自任何位置的文件路径，但建议优先使用 output_dir 中的文件\n"
                    "- 示例：['{output_dir}/file1.csv', '{output_dir}/file2.xlsx'] 或 ['/project/result.md']"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "abs_file_path_list": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "要发送的文件绝对路径。\n"
                                "必须是数组格式，例如 ['/path/file1.csv', '/path/file2.xlsx']。\n"
                                "建议使用 get_effective_request_output_dir() 获取的 output_dir 作为文件保存位置。\n"
                                "支持任意文件类型（pdf、xlsx、docx、png、zip等）。"
                            ),
                        }
                    },
                    "required": ["abs_file_path_list"],
                },
                func=self.send_file,
            ),
        ]
