# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WecomChannel - 企业微信 AI 机器人通道（WebSocket 长连接）。"""

from __future__ import annotations

import logging
import asyncio
import os
import re
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod, EventType

logger = logging.getLogger(__name__)

try:
    from wecom_aibot_sdk import WSClient
    from wecom_aibot_sdk.utils import generate_req_id

    WECOM_AVAILABLE = True
except ImportError:
    WECOM_AVAILABLE = False
    WSClient = None
    generate_req_id = None


class WecomConfig(BaseModel):
    """企业微信通道配置（WebSocket 长连接）。"""

    enabled: bool = False
    bot_id: str = ""
    secret: str = ""
    ws_url: str = "wss://openws.work.weixin.qq.com"
    allow_from: list[str] = Field(default_factory=list)
    enable_streaming: bool = True
    send_thinking_message: bool = True
    # 文件处理配置
    max_download_size: int = 100 * 1024 * 1024  # 最大下载文件大小（默认 100MB）
    download_timeout: int = 60  # 下载超时时间（秒）
    send_file_allowed: bool = True  # 是否启用文件上传功能
    enable_file_download: bool = True  # 是否启用文件下载功能
    workspace_dir: str = ""  # 工作空间目录


class WecomChannel(BaseChannel):
    """
    企业微信 AI 机器人通道，基于 WebSocket 长连接。

    依赖：
    - 企业微信后台创建 AI 机器人，获取 bot_id 和 secret
    """

    name = "wecom"

    def __init__(self, config: WecomConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: WecomConfig = config
        self._ws_client: Any = None
        self._message_callback: Callable[[Message], None] | None = None
        self._connect_task: asyncio.Task | None = None
        # 流式回复：wecom_req_id -> {frame, stream_id, accumulated_content}
        self._pending_streams: dict[str, dict[str, Any]] = {}
        # 文件服务
        self._file_service: Any = None
        # 按 request_id 记录已发送文件路径，避免重复发送
        self._sent_file_paths_by_req: dict[str, set[str]] = {}

    @property
    def channel_id(self) -> str:
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """注册消息回调，供 ChannelManager 使用。"""
        self._message_callback = callback

    @staticmethod
    def _looks_like_msgid(val: str) -> bool:
        """过滤 msgid（长数字），避免误作 chatid 导致 93006。"""
        if not val or not isinstance(val, str):
            return True
        s = val.strip()
        if len(s) < 10:
            return False
        return s.isdigit()

    def _extract_frame_info(self, frame: dict) -> tuple[str, str, str]:
        """从 SDK frame 提取 chatid、req_id、content。

        企业微信消息结构（wecom-aibot-sdk）：
        - frame 含 cmd/headers/body；body 含 msgtype、from、text 等
        - 单聊 chattype=single 时 chatid 为 from.userid
        - 群聊时为 body.chatid 或 from.chatid
        - 兼容 frame 直接为 body 的扁平结构（部分回调场景）
        """
        body = frame.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        # 若 frame 无 body 但含 msgtype，则 frame 本身即消息体（扁平结构）
        if not body and frame.get("msgtype"):
            body = frame

        text = body.get("text") or {}
        content = (
            (text.get("content", "") if isinstance(text, dict) else str(text) if text else "")
            or body.get("content", "")
            or ""
        )
        headers = frame.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        req_id = (
            headers.get("req_id")
            or headers.get("reqId")
            or frame.get("req_id")
            or body.get("msgid")
            or body.get("req_id")
            or ""
        )

        # chatid: 单聊用 from.userid，群聊用 chatid 或 from.chatid；过滤 msgid（长数字）
        from_obj = body.get("from") or frame.get("from") or {}
        if not isinstance(from_obj, dict):
            from_obj = {}

        def _pick_chatid(*candidates: str) -> str:
            for c in candidates:
                s = str(c or "").strip()
                if s and not self._looks_like_msgid(s):
                    return s
            return ""

        chatid = _pick_chatid(
            body.get("chatid"),
            body.get("chat_id"),
            from_obj.get("chatid"),
            from_obj.get("chat_id"),
            from_obj.get("userid"),
            from_obj.get("user_id"),
            frame.get("chatid"),
            frame.get("chat_id"),
        )
        return chatid, req_id, str(content or "").strip()

    async def _handle_incoming_message(self, frame: dict) -> None:
        """处理 SDK 收到的消息，转换为 jiuwenclaw Message 并分发。"""
        chatid, req_id, content = self._extract_frame_info(frame)
        if not content:
            logger.debug("WecomChannel 收到空内容，跳过")
            return
        if not chatid:
            # 调试：打印 frame 结构以便排查
            body_preview = frame.get("body") or frame
            if isinstance(body_preview, dict):
                keys = ("msgtype", "chatid", "chat_id", "from", "msgid")
                preview = {k: body_preview.get(k) for k in keys if k in body_preview}
            else:
                preview = str(body_preview)[:200]
            logger.warning(
                "WecomChannel 无法从 frame 提取 chatid，跳过消息。frame.body 预览: %s",
                preview,
            )
            return

        # allow_from 校验：chatid 可为 userid 或 groupid
        if not self.is_allowed(chatid):
            logger.warning("WecomChannel 发送者 %s 未被允许", chatid)
            return

        logger.info("WecomChannel 收到消息: chatid=%s content=%s", chatid, content[:50], extra={'user_visible': 'critical'})

        # 写入 last_chat_id 供 cron/心跳推送使用
        if chatid and not self._looks_like_msgid(chatid):
            try:
                from jiuwenclaw.config import update_channel_in_config

                update_channel_in_config("wecom", {"last_chat_id": chatid or ""})
            except Exception as e:
                logger.warning("WecomChannel 写入 last_chat_id 失败: %s", e)

        req_id_final = req_id or f"wecom_{int(time.time() * 1000)}"
        # 流式回复：存储 frame，可选首帧发送「...」
        if (
            self.config.enable_streaming
            and self._ws_client
            and getattr(self._ws_client, "is_connected", False)
        ):
            stream_id = (
                generate_req_id("stream")
                if generate_req_id
                else f"stream_{int(time.time()*1000)}_{req_id_final}"
            )
            self._pending_streams[req_id_final] = {
                "frame": frame,
                "stream_id": stream_id,
                "accumulated": "",
            }
            # 无论流式/思考开关如何，始终发送等待占位
            await self._send_stream_placeholder(req_id_final)

        msg = Message(
            id=req_id_final,
            type="req",
            channel_id=self.name,
            session_id=chatid,
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
            metadata={
                "wecom_chat_id": chatid,
                "wecom_req_id": req_id_final,
            },
        )

        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    def _extract_chatid(self, msg: Message) -> str | None:
        """从出站消息提取 chatid；心跳/定时等系统消息从 config 取 last_chat_id 或 default_chat_id。"""
        meta = getattr(msg, "metadata", None) or {}
        chatid = (meta.get("wecom_chat_id") or "").strip()
        if chatid:
            return chatid
        sid = getattr(msg, "session_id", None) or msg.id
        sid_str = str(sid) if sid else ""
        # 系统会话（心跳、cron 等）无有效 chatid，使用 config 中的 last_chat_id
        system_session_prefixes = ("__", "heartbeat_", "cron")
        if sid_str and not any(sid_str.startswith(p) for p in system_session_prefixes):
            if not self._looks_like_msgid(sid_str):
                return sid_str
        try:
            from jiuwenclaw.config import get_config
            ch_cfg = (get_config().get("channels") or {}).get("wecom") or {}
            # last_chat_id：用户聊天时自动写入；default_chat_id：可手动配置，用于心跳/定时推送
            last = str(ch_cfg.get("last_chat_id") or ch_cfg.get("default_chat_id") or "").strip()
            if last and not self._looks_like_msgid(last):
                return last
            return None
        except Exception:
            return None

    def _extract_content(self, msg: Message) -> str:
        """从出站消息中提取文本内容。"""
        payload = getattr(msg, "payload", None) or {}
        params = getattr(msg, "params", None) or {}
        content = (
            params.get("content")
            or payload.get("content")
            or ""
        )
        if isinstance(content, dict):
            content = content.get("output", str(content))
        return str(content or "").strip()

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """移除 <think>...</think> 块及未闭合的 <think>...，避免将 Agent 的思考过程展示给用户。"""
        if not text or not isinstance(text, str):
            return text or ""
        # 1. 移除完整的 <think>...</think> 块
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
        # 2. 移除未闭合的 <think>...（流式场景下可能先收到 <think> 后收到 </think>）
        text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE | re.DOTALL)
        return text

    @staticmethod
    def _is_thinking_only_content(text: str) -> bool:
        """判断内容是否为空或仅为占位符（纯省略号等），不应视为有效回复。
        思考过程（thinking）会在 interface 转为 chat.processing_status；
        llm_reasoning 若以 content 到达，则在 WecomChannel 内基于 source_chunk_type 再次过滤。"""
        if not text or not isinstance(text, str):
            return True
        t = text.strip()
        if not t:
            return True
        # 纯省略号、纯点
        if re.match(r"^[.．。…\s]+$", t):
            return True
        return False

    def _extract_content_from_payload(self, msg: Message) -> str | None:
        """从 chat.delta / chat.final 的 payload 提取 content，无则返回 None。"""
        payload = getattr(msg, "payload", None) or {}
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if content is None:
            return None
        return str(content)

    @staticmethod
    def _is_reasoning_chunk(msg: Message) -> bool:
        """判断当前消息是否为不应展示给企业微信用户的 reasoning chunk。"""
        payload = getattr(msg, "payload", None) or {}
        if not isinstance(payload, dict):
            return False
        source_chunk_type = str(payload.get("source_chunk_type") or "").strip().lower()
        return source_chunk_type == "llm_reasoning"

    async def _send_stream_placeholder(self, req_id: str) -> None:
        """发送流式首帧占位。PHP SDK 用 <think></think> 显示加载动画，企业微信 Markdown 可能支持。"""
        entry = self._pending_streams.get(req_id)
        if not entry or not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            return
        try:
            # 尝试 <think></think>（PHP SDK 用法，可能渲染为加载动画）；若不支持则显示为 ...
            placeholder = "<think></think>"
            await self._ws_client.reply_stream(
                entry["frame"],
                entry["stream_id"],
                placeholder,
                finish=False,
            )
            logger.debug("WecomChannel 已发送流式占位: req_id=%s", req_id)
        except Exception as e:
            logger.debug("WecomChannel 发送流式占位失败: %s", e)

    def _get_req_id_for_stream(self, msg: Message) -> str | None:
        """从出站消息中提取 wecom_req_id，用于查找 pending stream。"""
        meta = getattr(msg, "metadata", None) or {}
        return (meta.get("wecom_req_id") or "").strip() or None

    async def send(self, msg: Message) -> None:
        """通过企业微信发送消息。支持流式（reply_stream）与非流式（send_message）。"""
        if not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            logger.warning("WecomChannel 未连接，跳过发送")
            return

        # 不向企业微信发送 chat.processing_status（思考状态事件），避免展示思考过程
        if msg.event_type == EventType.CHAT_PROCESSING_STATUS:
            return

        # 提取事件类型
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_type = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        # 处理文件发送事件（chat.media 与 chat.file 统一走文件发送路径）
        if event_type in ("chat.file", "chat.media"):
            await self._send_file_message(msg)
            return

        # 心跳/系统事件
        if msg.event_type == EventType.HEARTBEAT_RELAY:
            chatid = self._extract_chatid(msg)
            if chatid:
                payload = getattr(msg, "payload", None) or {}
                if isinstance(payload, dict) and payload.get("heartbeat"):
                    try:
                        body = {"msgtype": "markdown", "markdown": {"content": str(payload.get("heartbeat"))}}
                        await self._ws_client.send_message(chatid, body)
                        logger.debug("WecomChannel 心跳已发送至 chatid=%s", chatid)
                    except Exception as e:
                        logger.warning("WecomChannel 心跳发送失败: %s", e)
            else:
                logger.warning(
                    "WecomChannel 心跳未发送：无有效 chatid。请先在企业微信中与机器人对话一次，以写入 last_chat_id"
                )
            return

        # 流式回复：CHAT_DELTA / CHAT_FINAL 通过 reply_stream 发送，替换首帧「...」
        req_id = self._get_req_id_for_stream(msg)
        if req_id and self.config.enable_streaming:
            entry = self._pending_streams.get(req_id)
            if entry:
                if self._is_reasoning_chunk(msg):
                    logger.debug("WecomChannel 跳过 reasoning chunk: req_id=%s", req_id)
                    return
                content = self._extract_content_from_payload(msg)
                if content is not None:
                    entry["accumulated"] = (entry.get("accumulated") or "") + content
                    # 移除 <think>...</think> 块，不将 Agent 思考过程展示给用户
                    to_send = self._strip_think_tags(entry["accumulated"]).strip()
                    if not to_send or self._is_thinking_only_content(to_send):
                        if msg.event_type == EventType.CHAT_FINAL:
                            self._pending_streams.pop(req_id, None)
                        return
                    try:
                        is_final = msg.event_type == EventType.CHAT_FINAL
                        await self._ws_client.reply_stream(
                            entry["frame"],
                            entry["stream_id"],
                            to_send,
                            finish=is_final,
                        )
                        logger.debug(
                            "WecomChannel 流式发送: req_id=%s finish=%s len=%d",
                            req_id,
                            is_final,
                            len(entry["accumulated"]),
                        )
                        if is_final:
                            self._pending_streams.pop(req_id, None)
                    except Exception as e:
                        logger.error("WecomChannel 流式发送失败: %s", e, extra={'user_visible': 'critical'})
                        if msg.event_type == EventType.CHAT_FINAL:
                            self._pending_streams.pop(req_id, None)
                return

        # chat.error：清理 pending，发送错误提示
        if msg.event_type == EventType.CHAT_ERROR:
            if req_id:
                self._pending_streams.pop(req_id, None)
            payload = getattr(msg, "payload", None) or {}
            err_text = payload.get("error", "处理出错") if isinstance(payload, dict) else "处理出错"
            chatid = self._extract_chatid(msg)
            if chatid:
                try:
                    await self._ws_client.send_message(
                        chatid,
                        {"msgtype": "markdown", "markdown": {"content": f"⚠️ {err_text}"}},
                    )
                except Exception as e:
                    logger.debug("WecomChannel 发送错误消息失败: %s", e)
            return

        # 非流式或无 pending：仅 CHAT_FINAL 用 send_message
        if msg.event_type != EventType.CHAT_FINAL:
            return
        if req_id:
            self._pending_streams.pop(req_id, None)
        chatid = self._extract_chatid(msg)
        if not chatid:
            logger.warning("WecomChannel 无法确定回发目标 chatid")
            return
        if self._is_reasoning_chunk(msg):
            logger.debug("WecomChannel 跳过 final reasoning chunk")
            return
        content = self._strip_think_tags(self._extract_content(msg)).strip()
        if not content or self._is_thinking_only_content(content):
            logger.debug("WecomChannel 消息内容为空或仅为思考占位，跳过发送")
            return
        try:
            body = {"msgtype": "markdown", "markdown": {"content": content}}
            await self._ws_client.send_message(chatid, body)
            logger.debug("WecomChannel 已发送: chatid=%s len=%d", chatid, len(content))
        except Exception as e:
            logger.error("WecomChannel 发送失败: %s", e)

    async def _run_client(self) -> None:
        """在后台运行 WebSocket 客户端。"""
        if not WECOM_AVAILABLE or not WSClient:
            logger.error("WecomChannel 依赖未安装，请运行: pip install wecom-aibot-sdk")
            return

        opts: dict[str, Any] = {
            "bot_id": self.config.bot_id,
            "secret": self.config.secret,
        }
        if self.config.ws_url:
            opts["ws_url"] = self.config.ws_url
        client = WSClient(**opts)

        async def on_text(frame: dict) -> None:
            await self._handle_incoming_message(frame)

        async def on_image(frame: dict) -> None:
            await self._handle_image_message(frame)

        async def on_file(frame: dict) -> None:
            await self._handle_file_message(frame)

        async def on_voice(frame: dict) -> None:
            await self._handle_voice_message(frame)

        async def on_video(frame: dict) -> None:
            await self._handle_video_message(frame)

        async def on_mixed(frame: dict) -> None:
            await self._handle_mixed_message(frame)

        client.on("message.text", on_text)
        client.on("message.image", on_image)
        client.on("message.file", on_file)
        client.on("message.voice", on_voice)
        client.on("message.video", on_video)
        client.on("message.mixed", on_mixed)

        self._ws_client = client

        # 初始化文件服务
        try:
            from jiuwenclaw.channel.wecom_file_service import WecomFileService
            workspace_dir = self.config.workspace_dir or os.path.expanduser("~/.jiuwenclaw/agent/workspace")
            self._file_service = WecomFileService(
                ws_client=client,
                max_download_size=self.config.max_download_size,
                download_timeout=self.config.download_timeout,
                workspace_dir=workspace_dir,
            )
            logger.info("WecomChannel 文件服务已初始化")
        except Exception as e:
            logger.warning(f"WecomChannel 文件服务初始化失败: {e}")
            self._file_service = None

        try:
            await client.connect()
            logger.info("WecomChannel WebSocket 已连接")
            logger.info("WecomChannel 保活循环已启动（不因短暂断线退出，不打断 SDK 重连）")

            # 不要把 is_connected 作为退出条件。
            # wecom-aibot-sdk 在网络抖动/机器休眠唤醒后会先进入 disconnected，
            # 然后在内部自动重连；若这里因短暂 disconnected 直接退出，会触发 finally
            # 主动 disconnect，打断 SDK 的重连流程，导致通道长期停在 disconnected。
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WecomChannel WebSocket 异常: %s", e)
        finally:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning("WecomChannel 断开连接时异常: %s", e)
            self._ws_client = None

    async def start(self) -> None:
        """启动企业微信通道。"""
        if not WECOM_AVAILABLE:
            logger.error("WecomChannel 依赖未安装，请运行: pip install wecom-aibot-sdk")
            return

        if not self.config.bot_id or not self.config.secret:
            logger.error("WecomChannel 未配置 bot_id 或 secret")
            return

        if self._running:
            logger.warning("WecomChannel 已在运行")
            return

        self._running = True
        self._connect_task = asyncio.create_task(self._run_client(), name="wecom-channel")
        logger.info("WecomChannel 已启动（WebSocket 长连接）")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止企业微信通道。"""
        self._running = False

        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            self._connect_task = None

        if self._ws_client:
            try:
                await self._ws_client.disconnect()
            except Exception as e:
                logger.warning("WecomChannel 停止时断开异常: %s", e)
            self._ws_client = None

        logger.info("WecomChannel 已停止")

    # ==================== 文件消息处理方法 ====================

    async def _handle_image_message(self, frame: dict) -> None:
        """处理图片消息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 文件下载功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 文件服务未初始化")
            return

        body = frame.get("body", {})
        image_data = body.get("image", {})
        url = image_data.get("url", "")
        aes_key = image_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 图片消息缺少 url 或 aeskey")
            return

        # 提取消息信息
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"img_{int(time.time() * 1000)}"

        # 下载图片
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="images",
        )

        if not file_info:
            content = "[图片: 下载失败]"
        else:
            content = "[图片]"
            logger.info(f"WecomChannel 图片下载成功: {file_info['path']}")

        # 构建消息并发送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_file_message(self, frame: dict) -> None:
        """处理文件消息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 文件下载功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 文件服务未初始化")
            return

        body = frame.get("body", {})
        file_data = body.get("file", {})
        url = file_data.get("url", "")
        aes_key = file_data.get("aeskey", "")
        filename = file_data.get("filename", "unknown_file")

        if not url or not aes_key:
            logger.warning("WecomChannel 文件消息缺少 url 或 aeskey")
            return

        # 提取消息信息
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"file_{int(time.time() * 1000)}"

        # 下载文件
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="files",
            filename=filename,
        )

        if not file_info:
            content = f"[文件: {filename} 下载失败]"
        else:
            content = f"[文件: {filename}]"
            logger.info(f"WecomChannel 文件下载成功: {file_info['path']}")

        # 构建消息并发送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_voice_message(self, frame: dict) -> None:
        """处理语音消息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 文件下载功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 文件服务未初始化")
            return

        body = frame.get("body", {})
        voice_data = body.get("voice", {})
        url = voice_data.get("url", "")
        aes_key = voice_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 语音消息缺少 url 或 aeskey")
            return

        # 提取消息信息
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"voice_{int(time.time() * 1000)}"

        # 下载语音
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="voice",
        )

        if not file_info:
            content = "[语音: 下载失败]"
        else:
            content = "[语音]"
            logger.info(f"WecomChannel 语音下载成功: {file_info['path']}")

        # 构建消息并发送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_video_message(self, frame: dict) -> None:
        """处理视频消息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 文件下载功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 文件服务未初始化")
            return

        body = frame.get("body", {})
        video_data = body.get("video", {})
        url = video_data.get("url", "")
        aes_key = video_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 视频消息缺少 url 或 aeskey")
            return

        # 提取消息信息
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"video_{int(time.time() * 1000)}"

        # 下载视频
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="video",
        )

        if not file_info:
            content = "[视频: 下载失败]"
        else:
            content = "[视频]"
            logger.info(f"WecomChannel 视频下载成功: {file_info['path']}")

        # 构建消息并发送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_mixed_message(self, frame: dict) -> None:
        """处理图文混排消息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 文件下载功能已禁用")
            # 仍然处理文本部分
            await self._handle_incoming_message(frame)
            return

        if not self._file_service:
            logger.warning("WecomChannel 文件服务未初始化")
            await self._handle_incoming_message(frame)
            return

        body = frame.get("body", {})
        mixed_data = body.get("mixed", {})
        msg_items = mixed_data.get("msgitem", [])

        if not msg_items:
            await self._handle_incoming_message(frame)
            return

        # 提取文本和图片
        text_parts = []
        file_infos = []

        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"mixed_{int(time.time() * 1000)}"

        for idx, item in enumerate(msg_items):
            item_type = item.get("msgtype", "")
            
            if item_type == "text":
                text_content = item.get("text", {}).get("content", "")
                if text_content:
                    text_parts.append(text_content)
            
            elif item_type == "image":
                image_data = item.get("image", {})
                url = image_data.get("url", "")
                aes_key = image_data.get("aeskey", "")
                
                if url and aes_key:
                    file_info = await self._file_service.download_file(
                        url=url,
                        aes_key=aes_key,
                        message_id=f"{message_id}_{idx}",
                        file_category="images",
                    )
                    if file_info:
                        file_infos.append(file_info)

        # 合并文本
        content = " ".join(text_parts) if text_parts else "[图文混排]"
        
        # 构建消息并发送
        await self._send_file_message_to_handler(frame, content, file_infos if file_infos else None)

    async def _send_file_message_to_handler(
        self, frame: dict, content: str, files: list[dict] | None
    ) -> None:
        """将文件消息发送到消息处理器"""
        chatid, req_id, _ = self._extract_frame_info(frame)
        
        if not chatid:
            logger.warning("WecomChannel 无法从 frame 提取 chatid，跳过文件消息")
            return

        # 权限检查
        if not self.is_allowed(chatid):
            logger.warning("WecomChannel 发送者 %s 未被允许", chatid)
            return

        logger.info("WecomChannel 收到文件消息: chatid=%s content=%s", chatid, content[:50])

        req_id_final = req_id or f"wecom_{int(time.time() * 1000)}"

        # 构建消息
        params = {"content": content, "query": content}
        if files:
            params["files"] = files

        msg = Message(
            id=req_id_final,
            type="req",
            channel_id=self.name,
            session_id=chatid,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
            metadata={
                "wecom_chat_id": chatid,
                "wecom_req_id": req_id_final,
            },
        )

        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    # ==================== 文件发送方法 ====================

    async def _send_file_message(self, msg: Message) -> None:
        """发送文件消息"""
        if not self._file_service or not self.config.send_file_allowed:
            logger.warning("WecomChannel 文件发送功能未启用")
            return

        if not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            logger.warning("WecomChannel 未连接，跳过文件发送")
            return

        payload = msg.payload if isinstance(msg.payload, dict) else {}
        files = payload.get("files", [])
        if not files:
            return

        # 提取 chatid
        metadata = msg.metadata or {}
        chatid = metadata.get("wecom_chat_id") or ""
        if not chatid:
            chatid = getattr(msg, "session_id", None) or msg.id or ""
        
        if not chatid:
            logger.warning("WecomChannel 文件发送: 未找到接收者")
            return

        # 获取当前 request_id 用于去重
        request_id = getattr(msg, "id", "") or ""
        if request_id not in self._sent_file_paths_by_req:
            self._sent_file_paths_by_req[request_id] = set()

        for file_info in files:
            file_path = file_info if isinstance(file_info, str) else file_info.get("path", "")
            if not file_path or not os.path.isfile(file_path):
                logger.warning(f"WecomChannel 文件发送: 文件不存在 {file_path}")
                continue

            # 检查是否已发送
            if file_path in self._sent_file_paths_by_req[request_id]:
                continue

            # 确定媒体类型
            media_type = self._file_service.get_media_type_for_file(file_path)

            try:
                # 上传文件
                media_id = await self._file_service.upload_file(file_path, media_type)
                if not media_id:
                    logger.error(f"WecomChannel 文件上传失败: {file_path}")
                    continue

                # 发送媒体消息
                await self._ws_client.send_media_message(
                    chatid=chatid,
                    media_type=media_type,
                    media_id=media_id,
                )

                # 记录已发送
                self._sent_file_paths_by_req[request_id].add(file_path)
                logger.info(f"WecomChannel 文件发送成功: {file_path} -> {chatid}")

            except Exception as e:
                logger.error(f"WecomChannel 文件发送失败: {file_path}, error: {e}")

        # 清理过期的去重记录
        if len(self._sent_file_paths_by_req) > 100:
            # 删除最早的 50 个
            keys_to_remove = list(self._sent_file_paths_by_req.keys())[:50]
            for key in keys_to_remove:
                del self._sent_file_paths_by_req[key]

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "ws_url": self.config.ws_url,
                "bot_id": self.config.bot_id[:8] + "..." if len(self.config.bot_id) > 8 else self.config.bot_id,
            },
        )
