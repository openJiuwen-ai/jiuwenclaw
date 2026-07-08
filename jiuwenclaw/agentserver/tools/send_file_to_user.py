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
from dataclasses import dataclass
from typing import Any, List, Union

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.config import get_file_transfer_config
from jiuwenclaw.agentserver.file_transfer_manager import get_file_transfer_manager
from jiuwenclaw.agentserver.tools.cron_tool_context import (
    get_cron_tool_channel_id,
    get_cron_tool_metadata,
    get_cron_tool_session_id,
)

logger = logging.getLogger(__name__)


@dataclass
class SendFileRoute:
    """Per-request routing context for send_file (resolved from contextvars).

    Encapsulates the 4 related routing fields so they are passed as a single
    argument instead of 4 separate ones (G.FNM.03).
    """
    request_id: str
    session_id: str
    channel_id: str
    metadata: dict[str, Any] | None


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

    def _resolve_route(self) -> SendFileRoute:
        """Resolve per-request route from contextvars (set by _bind_runtime_cron_context),
        falling back to instance attrs for callers that don't set the contextvars.

        Reading from contextvars at execution time makes the toolkit safe to share
        across concurrent requests: each async task sees its own request's values.
        """
        cv_session_id = get_cron_tool_session_id()
        cv_channel_id = get_cron_tool_channel_id()
        cv_metadata = get_cron_tool_metadata()
        session_id = cv_session_id or self.session_id
        channel_id = cv_channel_id or self.channel_id
        if cv_metadata:
            metadata = dict(cv_metadata)
            request_id = metadata.get("request_id") or self.request_id
        else:
            metadata = self._request_metadata
            request_id = self.request_id
        return SendFileRoute(
            request_id=request_id,
            session_id=session_id,
            channel_id=channel_id,
            metadata=metadata,
        )

    async def send_file(self, abs_file_path_list: Union[List[str], str]) -> str:
        """Send files to user."""
        route = self._resolve_route()
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

        if not valid_files:
            msg_parts = ["发送文件失败：所有文件均不存在"]
            for mf in missing_files:
                msg_parts.append(f"  - {mf}")
            return "\n".join(msg_parts)

        logger.info(
            "[SendFileToolkit] send_file 开始 session_id=%s 有效文件=%d 缺失=%d",
            route.session_id, len(valid_files), len(missing_files),
        )

        # 检查是否启用分布式文件传输
        ft_config = get_file_transfer_config()
        if ft_config.enabled:
            return await self._send_file_distributed(valid_files, missing_files, route)
        else:
            return await self._send_file_local(valid_files, missing_files, route)

    async def _send_file_local(
        self,
        valid_files: List[str],
        missing_files: List[str],
        route: SendFileRoute,
    ) -> str:
        """本地模式：直接传递文件路径。"""
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
                    file_path, base_name, route.session_id
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

        import time
        from jiuwenclaw.agentserver.session_history import (
            append_history_record,
        )
        append_history_record(
            session_id=route.session_id,
            request_id=route.request_id,
            channel_id=route.channel_id,
            role="assistant",
            event_type="chat.file",
            content="",
            timestamp=time.time(),
            extra={"files": files_payload},
        )

        msg = {
            "request_id": route.request_id,
            "channel_id": route.channel_id,
            "session_id": route.session_id,
            "payload": {
                "event_type": "chat.file",
                "files": files_payload,
            },
            "is_complete": False,
        }
        if route.metadata:
            msg["metadata"] = dict(route.metadata)
        await server.send_push(msg)
        result_parts = [f"成功发送 {len(valid_files)} 个文件"]
        if missing_files:
            result_parts.append("以下文件不存在，未发送：")
            for mf in missing_files:
                result_parts.append(f"  - {mf}")
        return "\n".join(result_parts)

    async def _send_file_distributed(
        self,
        valid_files: List[str],
        missing_files: List[str],
        route: SendFileRoute,
    ) -> str:
        """分布式模式：通过分片传输发送文件到 Gateway。"""
        ft_manager = get_file_transfer_manager()
        success_count = 0
        failed_files = []

        for file_path in valid_files:
            try:
                async def send_callback(event_type: str, params: dict) -> None:
                    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
                    server = AgentWebSocketServer.get_instance()
                    msg = {
                        "request_id": route.request_id,
                        "channel_id": route.channel_id,
                        "session_id": route.session_id,
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
                    session_id=route.session_id,
                    channel_id=route.channel_id,
                    request_id=route.request_id,
                )

                if result.get("success"):
                    success_count += 1
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
        if failed_files:
            result_parts.append(f"发送失败 {len(failed_files)} 个文件：")
            for ff in failed_files:
                result_parts.append(f"  - {ff['file']}: {ff['error']}")
        if missing_files:
            result_parts.append("以下文件不存在，未发送：")
            for mf in missing_files:
                result_parts.append(f"  - {mf}")

        return "\n".join(result_parts) if result_parts else "发送完成"

    def get_tools(self, *, tool_id: str | None = None) -> List[Tool]:
        """Return tools for registration in Runner.

        Args:
            tool_id: Optional stable id for the tool card. When provided, the tool
                is registered under this id (enabling safe single registration and
                name-based fallback lookup in resource_mgr). When omitted, a random
                uuid is used (per-instance isolation, e.g. team members).
        """
        def make_tool(name: str, description: str, input_params: dict, func) -> Tool:
            card_kwargs: dict[str, Any] = {
                "name": name,
                "description": description,
                "input_params": input_params,
            }
            if tool_id:
                card_kwargs["id"] = tool_id
            card = ToolCard(**card_kwargs)
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="send_file_to_user",
                description=(
                    "【文件发送工具】当需要将生成的文件、导出的数据、创建的文档等发送给用户时使用此工具。"
                    "使用场景包括：用户请求导出/下载文件、任务完成后需要交付文件、生成报告/文档后发送给用户。"
                    "参数格式：接受路径数组，路径必须是绝对路径。"
                    "示例：['/tmp/file1.csv', '/tmp/file2.xlsx']"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "abs_file_path_list": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "要发送的文件绝对路径。"
                                "必须是数组格式，例如 ['/path/file1.csv', '/path/file2.xlsx']。"
                                "支持任意文件类型（pdf、xlsx、docx、png、zip等）。"
                            ),
                        }
                    },
                    "required": ["abs_file_path_list"],
                },
                func=self.send_file,
            ),
        ]
