# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import concurrent.futures
import types
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from pydantic import BaseModel, Field

from jiuwenclaw.utils import logger
from jiuwenclaw.channel.base import RobotMessageRouter, BaseChannel
from jiuwenclaw.schema.message import Message, ReqMethod, EventType


class FeishuConfig(BaseModel):
    """飞书通道配置模型，使用WebSocket长连接接收消息。"""

    enabled: bool = False  # 是否启用飞书通道
    app_id: str = ""  # 飞书开放平台的应用ID
    app_secret: str = ""  # 飞书开放平台的应用密钥
    encrypt_key: str = ""  # 事件订阅的加密密钥（可选）
    verification_token: str = ""  # 事件订阅的验证令牌（可选）
    allow_from: list[str] = Field(default_factory=list)  # 允许的用户的open_id列表
    enable_streaming: bool = True  # 是否开启流式/过程消息下发
    chat_id: str = ""  # 可选：固定推送目标 chat_id（群聊 oc_xxx 或个人 open_id）


try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )

    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

# 非文本消息类型的显示占位符映射
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class FeishuChannel(BaseChannel):
    """
    飞书/飞书IM通道实现，基于WebSocket长连接。

    特性：
    - 使用WebSocket接收事件，无需公网IP或webhook
    - 支持群聊和私聊消息
    - 自动添加"已读"反应表情
    - 支持Markdown表格渲染为飞书表格元素

    依赖：
    - 飞书开放平台的应用ID和应用密钥
    - 机器人功能已启用
    - 事件订阅已启用（im.message.receive_v1）
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig, router: RobotMessageRouter):
        """
        初始化飞书通道实例。

        Args:
            config: 飞书配置对象
            router: 消息路由器实例
        """
        super().__init__(config, router)
        self.config: FeishuConfig = config
        self._api_client: Any = None  # 飞书API客户端（用于发送消息）
        self._websocket_client: Any = None  # WebSocket客户端（用于接收消息）
        self._websocket_thread: threading.Thread | None = None  # WebSocket运行线程
        self._message_dedup_cache: OrderedDict[str, None] = OrderedDict()  # 消息去重缓存
        self._main_loop: asyncio.AbstractEventLoop | None = None  # 主线程事件循环
        self._ws_thread_loop: asyncio.AbstractEventLoop | None = None  # WebSocket线程事件循环
        self._message_callback: Callable[[Message], None] | None = None  # 网关模式回调
        self._stopping = False
        # 按 request_id 聚合 chat.delta，避免同一任务被拆分成多条消息发送到飞书。
        self._stream_text_buffers: dict[str, str] = {}

    @property
    def channel_id(self) -> str:
        """返回通道唯一标识符，用于ChannelManager注册与消息派发。"""
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """
        注册消息回调函数，用于Gateway模式。

        当收到消息时调用此回调函数，而非通过router路由。

        Args:
            callback: 消息回调函数
        """
        self._message_callback = callback

    async def _handle_message(
        self,
        chat_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        处理接收到的消息并分发。

        若已通过on_message注册网关回调，则直接回调；否则通过router路由消息。

        Args:
            chat_id: 聊天ID
            content: 消息内容
            metadata: 额外的元数据
        """
        msg = Message(id=chat_id, type="req", channel_id=self.name, session_id=str(chat_id),
            provider="feishu",
            chat_id=str(chat_id),
            user_id=str((metadata or {}).get("feishu_open_id") or (metadata or {}).get("open_id") or ""),
            bot_id=str((metadata or {}).get("feishu_app_id") or ""),
            params={"content": content, "query": content}, timestamp=time.time(), ok=True,
            req_method=ReqMethod.CHAT_SEND, is_stream=True, metadata=metadata)
        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    async def start(self) -> None:
        """启动飞书机器人，使用WebSocket长连接接收消息。"""
        if not self._validate_start_conditions():
            return

        self._running = True
        self._main_loop = asyncio.get_running_loop()
        self._initialize_api_client()
        self._start_websocket_in_thread()

        logger.info("飞书机器人已启动，使用WebSocket长连接接收消息")
        logger.info("无需公网IP - 通过WebSocket接收事件")

        # 持续运行直到停止
        while self._running:
            await asyncio.sleep(1)

    def _validate_start_conditions(self) -> bool:
        """验证启动所需的条件是否满足。"""
        if not FEISHU_AVAILABLE:
            logger.error("飞书SDK未安装，请先安装 lark_oapi")
            return False

        if not self.config.app_id or not self.config.app_secret:
            logger.error("飞书应用ID或应用密钥未配置")
            return False

        return True

    def _initialize_api_client(self) -> None:
        """初始化飞书API客户端，用于发送消息。"""
        self._api_client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    def _start_websocket_in_thread(self) -> None:
        """在独立线程中启动WebSocket客户端，避免事件循环冲突。"""
        config = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
            "encrypt_key": self.config.encrypt_key or "",
            "verification_token": self.config.verification_token or "",
        }

        self._websocket_thread = threading.Thread(
            target=self._run_websocket_client,
            args=(config,),
            daemon=True,
        )
        self._websocket_thread.start()

        # 等待WebSocket客户端创建完成
        self._wait_for_websocket_client_ready()

    def _run_websocket_client(self, config: dict) -> None:
        """
        在子线程中运行WebSocket客户端。

        Args:
            config: WebSocket配置参数
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_thread_loop = loop

        # 临时替换lark_oapi.ws.client模块的事件循环，避免"already running"错误
        import lark_oapi.ws.client as _ws_client_mod

        _saved_loop = getattr(_ws_client_mod, "loop", None)
        _ws_client_mod.loop = loop

        ws_client = None
        try:
            event_handler = (
                lark.EventDispatcherHandler.builder(
                    config["encrypt_key"],
                    config["verification_token"],
                )
                .register_p2_im_message_receive_v1(self._on_message_sync)
                .build()
            )

            ws_client = lark.ws.Client(
                config["app_id"],
                config["app_secret"],
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            self._patch_ws_client_shutdown(ws_client)
            self._websocket_client = ws_client
            ws_client.start()
        except Exception as e:
            if self._stopping or not self._running:
                logger.info("飞书WebSocket线程退出: {}", e)
            else:
                logger.error("飞书WebSocket连接建立失败: {}", e)
        finally:
            self._cleanup_websocket_thread(_saved_loop, ws_client, loop)

    def _cleanup_websocket_thread(
        self,
        saved_loop: Any,
        ws_client: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """清理WebSocket线程资源。"""
        import lark_oapi.ws.client as _ws_client_mod

        if saved_loop is not None:
            _ws_client_mod.loop = saved_loop

        if ws_client is None:
            self._websocket_client = None

        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass

        try:
            loop.close()
        except Exception:
            pass

        self._ws_thread_loop = None

    def _wait_for_websocket_client_ready(self) -> None:
        """等待WebSocket客户端创建完成。"""
        for _ in range(50):
            if self._websocket_client is not None:
                break
            time.sleep(0.1)

    async def stop(self) -> None:
        """停止飞书机器人。"""
        self._running = False
        self._stopping = True
        self._stream_text_buffers.clear()

        if self._websocket_client and self._ws_thread_loop and self._ws_thread_loop.is_running():
            try:
                await self._shutdown_ws_client()
            except Exception as e:
                logger.warning("停止WebSocket客户端时发生异常: {}", e)

        if self._ws_thread_loop and self._ws_thread_loop.is_running():
            self._ws_thread_loop.call_soon_threadsafe(self._ws_thread_loop.stop)

        if self._websocket_thread and self._websocket_thread.is_alive():
            self._websocket_thread.join(timeout=2.0)

        logger.info("飞书机器人已停止")
        self._stopping = False

    async def _shutdown_ws_client(self) -> None:
        """在飞书 websocket 线程中执行断连与任务清理."""
        loop = self._ws_thread_loop
        ws_client = self._websocket_client
        if loop is None or ws_client is None or not loop.is_running():
            return

        async def _shutdown() -> None:
            try:
                setattr(ws_client, "_auto_reconnect", False)
            except Exception:
                pass

            conn = getattr(ws_client, "_conn", None)
            if conn is not None:
                try:
                    await conn.close(code=1000, reason="bye")
                except Exception as e:
                    logger.debug("飞书连接关闭时出现异常: {}", e)

            await asyncio.sleep(0.05)

        fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2.0)
        except concurrent.futures.CancelledError:
            pass
        except asyncio.TimeoutError:
            logger.debug("飞书客户端清理超时，继续停止事件循环")
        except Exception as e:
            logger.debug("飞书客户端清理任务异常: {}", e)

    @staticmethod
    def _patch_ws_client_shutdown(ws_client: Any) -> None:
        """修复 lark_oapi 在并发关闭时可能触发的 Lock release 异常."""
        original_disconnect = getattr(ws_client, "_disconnect", None)
        if not callable(original_disconnect):
            return
        if getattr(ws_client, "_disconnect_patched", False):
            return

        async def _safe_disconnect(self):
            try:
                return await original_disconnect()
            except RuntimeError as e:
                if "Lock is not acquired" in str(e):
                    logger.debug("忽略 lark_oapi 断连并发异常: {}", e)
                    return None
                raise

        ws_client._disconnect = types.MethodType(_safe_disconnect, ws_client)
        ws_client._disconnect_patched = True

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """
        添加消息反应的同步方法（在线程池中运行）。

        Args:
            message_id: 消息ID
            emoji_type: 表情类型
        """
        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )

            response = self._api_client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning(
                    f"添加消息反应失败: 错误码={response.code}, 消息={response.msg}"
                )
            else:
                logger.debug(f"已为消息 {message_id} 添加 {emoji_type} 表情")
        except Exception as e:
            logger.warning(f"添加消息反应时发生异常: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        为消息添加反应表情符号（非阻塞）。

        常见表情符号类型：
        - THUMBSUP: 点赞
        - OK: 确认
        - EYES: 查看
        - DONE: 完成
        - OnIt: 处理中
        - HEART: 爱心

        Args:
            message_id: 消息ID
            emoji_type: 表情类型
        """
        if not self._api_client or not Emoji:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    # Markdown表格正则表达式（标题行+分隔符行+数据行）
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    @staticmethod
    def _parse_markdown_table(table_text: str) -> dict | None:
        """
        将Markdown表格解析为飞书表格元素。

        Args:
            table_text: Markdown表格文本

        Returns:
            dict: 飞书表格元素，解析失败返回None
        """
        lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            return None

        split = lambda l: [c.strip() for c in l.strip("|").split("|")]
        headers = split(lines[0])
        rows = [split(l) for l in lines[2:]]

        columns = [
            {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
            for i, h in enumerate(headers)
        ]

        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [
                {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))}
                for r in rows
            ],
        }

    def _build_feishu_card_elements(self, content: str) -> list[dict]:
        """
        将内容分割为Markdown和表格元素，用于构建飞书卡片。

        Args:
            content: 要处理的内容

        Returns:
            list[dict]: 飞书卡片元素列表
        """
        elements, last_end = [], 0

        for m in self._TABLE_RE.finditer(content):
            before = content[last_end : m.start()].strip()
            if before:
                # 转换非表格内容为富文本 div 元素
                elements.extend(self._markdown_to_feishu_elements(before))

            elements.append(
                self._parse_markdown_table(m.group(1))
                or self._markdown_to_feishu_elements(m.group(1))
            )
            last_end = m.end()

        remaining = content[last_end:].strip()
        if remaining:
            elements.extend(self._markdown_to_feishu_elements(remaining))

        return elements or self._markdown_to_feishu_elements(content)

    def _markdown_to_feishu_elements(self, md_content: str) -> list[dict]:
        """
        将 Markdown 内容转换为飞书卡片元素列表。

        Args:
            md_content: Markdown 内容

        Returns:
            list[dict]: 飞书卡片元素列表
        """
        elements = []
        lines = md_content.split('\n')
        current_text = []

        for line in lines:
            stripped = line.strip()

            # 处理标题
            if stripped.startswith('## '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[3:]}**"
                    }
                })
            elif stripped.startswith('### '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[4:]}**"
                    }
                })
            elif stripped.startswith('# '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[2:]}**"
                    }
                })
            # 处理分隔线
            elif stripped == '---':
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({"tag": "hr"})
            # 处理引用块
            elif stripped.startswith('> '):
                current_text.append(stripped[2:])
            # 处理列表项
            elif stripped.startswith('- ') or stripped.startswith('* '):
                current_text.append(f"• {stripped[2:]}")
            elif re.match(r'^\d+\. ', stripped):
                current_text.append(stripped)
            else:
                current_text.append(line)

        if current_text:
            elements.append(self._create_div_element('\n'.join(current_text)))

        return elements if elements else [{"tag": "div", "text": {"tag": "lark_md", "content": md_content}}]

    def _create_div_element(self, content: str) -> dict:
        """
        创建飞书 div 元素。

        Args:
            content: 文本内容

        Returns:
            dict: 飞书 div 元素
        """
        # 处理内联格式：粗体、斜体、代码等
        formatted = content
        # 保留粗体和斜体
        # 处理行内代码
        formatted = re.sub(r'`([^`]+)`', r'`\1`', formatted)
        # 处理链接
        formatted = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[\1](\2)', formatted)

        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": formatted
            }
        }

    async def send(self, msg: Message) -> None:
        """
        通过飞书发送消息。

        Args:
            msg: 要发送的消息对象
        """
        if not self._api_client:
            logger.warning("飞书客户端未初始化")
            return

        try:
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""
            stream_key = str(getattr(msg, "id", "") or "")
            streaming_enabled = bool(self.config.enable_streaming)

            # 流式增量：先缓存，不立即发送。
            if event_name == "chat.delta":
                delta = self._extract_message_content(msg)
                if delta and stream_key:
                    self._stream_text_buffers[stream_key] = (
                        self._stream_text_buffers.get(stream_key, "") + delta
                    )
                return

            # 非 streaming 模式下仅下发最终结果，屏蔽执行过程类事件。
            if (not streaming_enabled) and event_name in {"chat.tool_call", "chat.tool_result", "todo.updated"}:
                return

            # 流式结束兜底：有些场景不会携带非空 chat.final，使用 processing_status=false 冲刷缓存。
            if event_name == "chat.processing_status":
                is_processing = payload.get("is_processing")
                if is_processing is not False:
                    if not streaming_enabled:
                        return
                    content_str = self._extract_message_content(msg)
                    if not content_str.strip():
                        return
                else:
                    content_str = self._stream_text_buffers.pop(stream_key, "")
                    if not content_str.strip():
                        if not streaming_enabled:
                            return
                        content_str = self._extract_message_content(msg)
                        if not content_str.strip():
                            return
            else:
                buffered_text = ""
                if event_name == "chat.final":
                    buffered_text = self._stream_text_buffers.pop(stream_key, "")
                elif event_name in {"chat.error", "chat.interrupt_result"}:
                    self._stream_text_buffers.pop(stream_key, None)
                content_str = self._extract_message_content(msg)
                if event_name == "chat.final":
                    content_str = self._merge_stream_and_final_content(
                        buffered_text,
                        content_str,
                    )

            receive_id, id_type = self._extract_receive_info(msg)
            payload = getattr(msg, "payload", None) or {}
            if (
                msg.event_type == EventType.HEARTBEAT_RELAY
                and isinstance(payload, dict)
                and payload.get("heartbeat")
            ):
                content_str = str(payload.get("heartbeat"))

            if not content_str.strip():
                logger.warning("飞书发送：消息内容为空，跳过发送")
                return

            card_content = self._build_card_content(content_str)
            await self._send_feishu_message(receive_id, id_type, card_content, msg.id)

        except Exception as e:
            logger.error(f"发送飞书消息时发生异常: {e}")

    @staticmethod
    def _merge_stream_and_final_content(stream_text: str, final_text: str) -> str:
        """合并流式累积文本和 final 文本，优先保留信息更完整的一侧。"""
        stream_text = stream_text or ""
        final_text = final_text or ""
        if not stream_text.strip():
            return final_text
        if not final_text.strip():
            return stream_text
        if stream_text == final_text:
            return final_text
        if final_text.startswith(stream_text):
            return final_text
        if stream_text.startswith(final_text):
            return stream_text
        return final_text if len(final_text) >= len(stream_text) else stream_text

    def _extract_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        从消息对象中提取接收者ID和ID类型。
        优先使用 metadata 中的平台身份（feishu_chat_id / feishu_open_id），
        避免 \new_session 覆盖 session_id 后导致 Invalid ids。

        Args:
            msg: 消息对象

        Returns:
            tuple: (接收者ID, ID类型)
        """
        meta = getattr(msg, "metadata", None) or {}
        receive_id = ""
        id_type = "open_id"

        # 1) 优先用 metadata 中的平台身份
        feishu_chat_id = (meta.get("feishu_chat_id") or "").strip()
        feishu_open_id = (meta.get("feishu_open_id") or "").strip()
        if feishu_chat_id:
            receive_id = feishu_chat_id
            id_type = "chat_id" if feishu_chat_id.startswith("oc_") else "open_id"
        elif feishu_open_id:
            receive_id = feishu_open_id
            id_type = "open_id"

        # 2) 若 metadata 中没有平台身份，则使用配置中的 chat_id 作为固定推送目标
        # print('this is in _extract_receive_info')
        logger.info('this is in _extract_receive_info, chat_id is %s', self.config.chat_id)
        if not receive_id:
            cfg_chat_id = getattr(self.config, "chat_id", "") or ""
            cfg_chat_id = cfg_chat_id.strip()
            if cfg_chat_id:
                receive_id = cfg_chat_id
                id_type = "chat_id" if cfg_chat_id.startswith("oc_") else "open_id"

        # 3) 仍然没有，则回退到 session_id / id（兼容旧逻辑）
        if not receive_id:
            receive_id = getattr(msg, "session_id", None) or msg.id or ""
            if receive_id.startswith("oc_"):
                id_type = "chat_id"
            else:
                id_type = "open_id"

        return receive_id, id_type

    def _extract_message_content(self, msg: Message) -> str:
        """
        从消息对象中提取内容字符串。

        Args:
            msg: 消息对象

        Returns:
            str: 消息内容字符串
        """
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        if event_name == "chat.tool_call":
            tool_info = payload.get("tool_call", payload)
            if isinstance(tool_info, dict):
                tool_name = tool_info.get("tool_name") or tool_info.get("name") or "unknown_tool"
                args = (
                    tool_info.get("arguments")
                    or tool_info.get("args")
                    or tool_info.get("input")
                    or tool_info.get("params")
                )
                args_text = self._truncate_text(self._extract_preferred_text(args), max_len=240)
                return f"[工具调用] {tool_name}" if not args_text else f"[工具调用] {tool_name}\n参数: {args_text}"
            tool_text = self._extract_preferred_text(tool_info)
            tool_text = self._truncate_text(tool_text, max_len=160)
            return f"[工具调用] {tool_text}" if tool_text else "[工具调用]"

        if event_name == "chat.tool_result":
            tool_name = payload.get("tool_name") or "unknown_tool"
            result_text = self._extract_tool_result_text(payload.get("result"))
            return f"[工具结果] {tool_name}" if not result_text else f"[工具结果] {tool_name}\n{result_text}"

        if event_name == "todo.updated":
            todos = payload.get("todos")
            if not isinstance(todos, list) or not todos:
                return "[待办更新]"
            total = len(todos)
            completed = 0
            running = 0
            pending = 0
            cancelled = 0
            for item in todos:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "")).strip().lower()
                if status == "completed":
                    completed += 1
                elif status == "running":
                    running += 1
                elif status in ("cancelled", "canceled"):
                    cancelled += 1
                else:
                    # waiting/pending/unknown 统一归为待处理
                    pending += 1
            return (
                f"[待办更新] 已完成 {completed}/{total}"
                f"｜进行中 {running}"
                f"｜待处理 {pending}"
                f"｜已取消 {cancelled}"
            )

        if event_name == "chat.error":
            error_text = self._extract_preferred_text(payload.get("error"))
            return f"[错误] {error_text}" if error_text else "[错误] 未知错误"

        if event_name == "chat.processing_status":
            is_processing = payload.get("is_processing")
            if is_processing is True:
                return "[状态] 处理中"
            if is_processing is False:
                return "[状态] 已完成"
            return ""

        if event_name == "chat.interrupt_result":
            return self._extract_preferred_text(payload.get("message")) or "[状态] 任务已中断"

        if event_name == "heartbeat.relay":
            return self._extract_preferred_text(payload.get("heartbeat"))

        # Gateway/Agent 响应在 payload.content，直接发送可能在 params.content
        content_str = (msg.params or {}).get("content") or payload.get("content") or ""
        if isinstance(content_str, dict):
            content_str = content_str.get("output", content_str)
        text = self._truncate_text(self._extract_preferred_text(content_str), max_len=4000)
        if text:
            return text

        # 最后仅尝试提取可读字段，不再整包透传 JSON，避免渠道侧出现原始结构化噪音。
        return self._extract_preferred_text(payload if payload else msg.payload)

    def _extract_tool_result_text(self, value: Any) -> str:
        """提取工具结果可读摘要，限制长度，避免飞书消息过载。"""
        if isinstance(value, dict):
            for key in ("summary", "message", "output", "result", "content", "text", "error"):
                if key in value:
                    text = self._extract_preferred_text(value.get(key))
                    if text:
                        return self._truncate_text(text, max_len=600)
        text = self._extract_preferred_text(value)
        return self._truncate_text(text, max_len=600)

    @staticmethod
    def _extract_preferred_text(value: Any) -> str:
        """从结构化数据中提取可读文本，避免直接发送 JSON."""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            if (
                (text.startswith("{") and text.endswith("}"))
                or (text.startswith("[") and text.endswith("]"))
            ):
                try:
                    parsed = json.loads(text)
                except Exception:
                    # 兼容 Python dict 字符串
                    match = re.search(
                        r"['\"](output|content|text|message|result|error|summary)['\"]\s*:\s*['\"](.+?)['\"]",
                        text,
                        flags=re.DOTALL,
                    )
                    return match.group(2).strip() if match else ""
                extracted = FeishuChannel._extract_preferred_text(parsed)
                return extracted or ""
            return text

        if isinstance(value, dict):
            for key in ("output", "content", "text", "message", "result", "error", "summary"):
                if key in value:
                    extracted = FeishuChannel._extract_preferred_text(value.get(key))
                    if extracted:
                        return extracted
            return ""

        if isinstance(value, list):
            parts: list[str] = []
            for item in value[:3]:
                extracted = FeishuChannel._extract_preferred_text(item)
                if extracted:
                    parts.append(extracted)
            return "\n".join(parts).strip()

        return str(value).strip()

    @staticmethod
    def _truncate_text(text: str, max_len: int = 240) -> str:
        text = (text or "").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "..."

    @staticmethod
    def _stringify_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    def _build_card_content(self, content_str: str) -> str:
        """
        构建飞书卡片内容。

        Args:
            content_str: 消息内容字符串

        Returns:
            str: JSON格式的卡片内容
        """
        elements = self._build_feishu_card_elements(content_str)
        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements,
        }
        return json.dumps(card, ensure_ascii=False)

    async def _send_feishu_message(
        self, receive_id: str, id_type: str, card_content: str, msg_id: str
    ) -> None:
        """
        发送飞书消息。

        Args:
            receive_id: 接收者ID
            id_type: ID类型
            card_content: 卡片内容
            msg_id: 发送消息ID（用于日志）
        """
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(card_content)
                .build()
            )
            .build()
        )

        response = self._api_client.im.v1.message.create(request)

        if not response.success():
            logger.error(
                f"发送飞书消息失败: 错误码={response.code}, "
                f"消息={response.msg}, 日志ID={response.get_log_id()}"
            )
        else:
            logger.debug(f"已向 {msg_id} 发送飞书消息")

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """
        传入消息的同步处理器（从WebSocket线程调用）。

        在主事件循环中调度异步处理。

        Args:
            data: 飞书消息事件数据
        """
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._main_loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """
        处理来自飞书的传入消息。

        Args:
            data: 飞书消息事件数据
        """
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # 消息去重检查
            if self._is_duplicate_message(message.message_id):
                return

            # 跳过机器人发送的消息
            if sender.sender_type == "bot":
                return

            # 后台添加"已读"反应，不阻塞消息处理
            asyncio.create_task(self._add_reaction(message.message_id, "THUMBSUP"))

            # 解析消息内容
            content = self._parse_message_content(message)
            if not content:
                return

            # 提取发送者open_id
            open_id = (
                getattr(getattr(sender, "sender_id", None), "open_id", None) or ""
            )

            # 将最近一次可回发的飞书身份写入 config.yaml，供 cron 推送时使用
            try:
                from jiuwenclaw.config import update_channel_in_config

                update_channel_in_config(
                    "feishu",
                    {
                        "last_chat_id": getattr(message, "chat_id", None) or "",
                        "last_open_id": open_id or "",
                        "last_message_id": getattr(message, "message_id", None) or "",
                    },
                )
            except Exception:
                # 不影响正常收消息
                pass

            # 处理消息：将平台身份写入 metadata，供回发时使用（与 session_id 解耦，\new_session 后仍可正确回发）
            await self._handle_message(
                chat_id=message.chat_id,
                content=content,
                metadata={
                    "message_id": message.message_id,
                    "chat_type": message.chat_type,
                    "msg_type": message.message_type,
                    "open_id": open_id,
                    "feishu_open_id": open_id,
                    "feishu_chat_id": getattr(message, "chat_id", None) or "",
                    "feishu_app_id": getattr(self.config, "app_id", "") or "",
                },
            )

        except Exception as e:
            logger.error(f"处理飞书消息时发生异常: {e}")

    def _is_duplicate_message(self, message_id: str) -> bool:
        """
        检查消息是否重复。

        Args:
            message_id: 消息ID

        Returns:
            bool: True表示消息重复，False表示新消息
        """
        if message_id in self._message_dedup_cache:
            return True

        self._message_dedup_cache[message_id] = None

        # 修剪缓存：当超过1000时保留最近的500条
        while len(self._message_dedup_cache) > 1000:
            self._message_dedup_cache.popitem(last=False)

        return False

    def _parse_message_content(self, message: Any) -> str:
        """
        解析消息内容。

        Args:
            message: 飞书消息对象

        Returns:
            str: 解析后的消息内容
        """
        msg_type = message.message_type

        if msg_type == "text":
            try:
                return json.loads(message.content).get("text", "")
            except json.JSONDecodeError:
                return message.content or ""
        else:
            return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
