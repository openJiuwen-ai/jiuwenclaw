# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Send File Toolkit

提供发送文件到用户的工具。支持发送一个或多个文件。

使用方式：
1. 创建 SendFileToolkit 实例
2. 调用 get_tools() 获取工具列表
3. 工具会自动注册到 Runner 中
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any, List, Union

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard


logger = logging.getLogger(__name__)


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
            request_id,
            session_id,
            channel_id,
            bool(self._request_metadata),
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
            request_id,
            session_id,
            channel_id,
            bool(self._request_metadata),
        )

    @staticmethod
    def _normalize_target_channels(target_channels: Any) -> list[str]:
        """Normalize target_channels into a list of non-empty strings.

        Accepts a single string, a JSON array string, or a native list.
        Returns [] when absent/empty.
        """
        if target_channels is None:
            return []
        if isinstance(target_channels, str):
            stripped = target_channels.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
                if isinstance(parsed, str):
                    return [parsed.strip()] if parsed.strip() else []
                return [stripped]
            except (TypeError, ValueError):
                return [stripped]
        if isinstance(target_channels, (list, tuple)):
            return [str(x).strip() for x in target_channels if str(x).strip()]
        return [str(target_channels).strip()]

    async def send_file(
        self,
        abs_file_path_list: Union[List[str], str],
        target_channels: Union[List[str], str, None] = None,
    ) -> str:
        """Send files to user.

        Args:
            abs_file_path_list: List of absolute file paths to send.
            target_channels: Optional explicit delivery targets. Each item is
                a channel id (e.g. "feishu", "web") or a team human-agent
                seat name (the member_name used in /join). When omitted the
                Gateway auto-routes the file to all channels joined to the
                session (team mode). When provided, the file is delivered
                only to the specified targets.

        Returns:
            Success message or error description.
        """
        target_channel_list = SendFileToolkit._normalize_target_channels(target_channels)
        if target_channel_list:
            logger.info(
                "[SendFileToolkit] send_file target_channels=%s session_id=%s",
                target_channel_list, self.session_id,
            )
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
            self.session_id,
            len(valid_files),
            len(missing_files),
        )

        try:
            from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

            server = AgentWebSocketServer.get_instance()

            files_payload = []
            try:
                from jiuwenswarm.agents.harness.common.tools.web_file_download import (
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
            from jiuwenswarm.server.runtime.session.session_history import (
                append_history_record,
            )
            append_history_record(
                session_id=self.session_id,
                request_id=self.request_id,
                channel_id=self.channel_id,
                role="assistant",
                event_type="chat.file",
                content="",
                timestamp=time.time(),
                extra={"files": files_payload},
            )

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
            # 合并 metadata：原始 request metadata + 文件投递目标提示。
            # send_file_targets 由 Gateway 的 dispatch 层解析为 fan_out_targets，
            # 使文件可跨 channel 投递到 team 会话已接入的 channel（如飞书）。
            merged_meta: dict[str, Any] = {}
            if self._request_metadata:
                merged_meta.update(self._request_metadata)
            if target_channel_list:
                merged_meta["send_file_targets"] = list(target_channel_list)
            if merged_meta:
                msg["metadata"] = merged_meta
            await server.send_push(msg)
            result_parts = [f"成功发送 {len(valid_files)} 个文件"]
            if missing_files:
                result_parts.append("以下文件不存在，未发送：")
                for mf in missing_files:
                    result_parts.append(f"  - {mf}")
            return "\n".join(result_parts)
        except Exception as e:
            logger.exception(
                "[SendFileToolkit] send_file 失败 session_id=%s error=%s",
                self.session_id,
                str(e),
            )
            return f"提交文件失败: {str(e)}"

    def get_tools(self) -> List[Tool]:
        """Return tools for registration in Runner.

        Returns:
            List of tools for sending files.
        """

        def make_tool(
            name: str,
            description: str,
            input_params: dict,
            func,
        ) -> Tool:
            card = ToolCard(
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="send_file_to_user",
                description=(
                    "【文件发送工具】当需要将生成的文件、导出的数据、创建的文档等发送给用户时使用此工具。"
                    "使用场景包括：用户请求导出/下载文件、任务完成后需要交付文件、生成报告/文档后发送给用户。"
                    "参数格式：abs_file_path_list 接受单个路径字符串或路径数组，路径必须是绝对路径。"
                    "示例：'/tmp/report.pdf' 或 ['/tmp/file1.csv', '/tmp/file2.xlsx']。"
                    "target_channels 可选：指定文件投递的目标 channel（如 ['feishu', 'xiaoyi', 'web']）。"
                    "省略时自动投递给当前会话已接入的所有用户 channel（team 模式下含飞书等 IM 端）。"
                    "当用户在某个 channel（如飞书）请求文件、或要求把文件发给指定 channel 时，应显式传入 target_channels。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "abs_file_path_list": {
                            "type": "string",
                            "description": (
                                "要发送的文件绝对路径。"
                                "可以是单个路径字符串如 '/path/to/file.pdf'，"
                                "或 JSON 数组字符串如 '[\"/path/file1.csv\", \"/path/file2.xlsx\"]'。"
                                "支持任意文件类型（pdf、xlsx、docx、png、zip等）。"
                            ),
                        },
                        "target_channels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "可选：文件投递目标 channel 或 team 成员席位名列表，"
                                "如 [\"feishu\"] 或 [\"xiaoyi\"]。"
                                "省略时自动投递给当前会话已接入的所有用户 channel。"
                                "当需跨 channel 投递（如 web 会话中把文件发给飞书用户）时传入此项。"
                            ),
                        },
                    },
                    "required": ["abs_file_path_list"],
                },
                func=self.send_file,
            ),
        ]
