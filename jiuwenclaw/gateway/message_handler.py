# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MessageHandler - 消息处理抽象与双队列实现（入队经 AgentServerClient 发往 AgentServer）."""

from __future__ import annotations

import logging
import asyncio
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict
from jiuwenclaw.channel.base import ChannelType
from jiuwenclaw.e2a.constants import E2A_WIRE_INTERNAL_METADATA_KEYS, FILE_TRANSFER_EVENT_TYPES
from jiuwenclaw.gateway.session_map import SessionMap
from jiuwenclaw.schema.hook_event import GatewayHookEvents
from jiuwenclaw.schema.hooks_context import GatewayChatHookContext
from jiuwenclaw.utils import FileTransferStartParams

logger = logging.getLogger(__name__)



class ChannelMode(str, Enum):
    PLAN = "plan"
    AGENT = "agent"


@dataclass
class ChannelControlState:
    session_id: str | None = None
    mode: ChannelMode = ChannelMode.PLAN
if TYPE_CHECKING:
    from jiuwenclaw.e2a.models import E2AEnvelope
    from jiuwenclaw.gateway.agent_client import AgentServerClient
    from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk
    from jiuwenclaw.schema.message import Message


# ---------- 双队列实现：入队经 AgentServerClient 发往 AgentServer ----------
class MessageHandler(ABC):
    """
    维护两个异步消息队列，入队消息通过 AgentServerClient 发送给 AgentServer：

    - _user_messages：Channel 发来的消息，由内部转发循环消费并调用 agent_client.send_request
    - _robot_messages：AgentServer 的响应，由 ChannelManager 消费并派发到对应 Channel

    AgentServer 经 WebSocket 下行 **E2AResponse** 线 JSON；``WebSocketAgentServerClient`` 内
    （``jiuwenclaw.e2a.wire_codec``）解析并还原为 ``AgentResponse`` / ``AgentResponseChunk``，
    本类仍通过 ``_response_to_message`` / ``_chunk_to_message`` 转为 ``Message`` 供 Channel 消费。

    单例模式：全局仅存在一个 MessageHandler 实例，可通过 MessageHandler(client) 或
    MessageHandler.get_instance(client) 获取。
    """

    _instance: "MessageHandler | None" = None

    def __new__(cls, agent_client: "AgentServerClient", *args: Any, **kwargs: Any) -> "MessageHandler":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, agent_client: "AgentServerClient") -> None:
        if getattr(self, "_singleton_initialized", False):
            return
        self._singleton_initialized = True
        self._agent_client = agent_client
        self._user_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._robot_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._running = False
        self._forward_task: asyncio.Task | None = None
        self._stream_tasks: dict[str, asyncio.Task] = {}  # request_id -> task
        self._stream_sessions: dict[str, str | None] = {}  # request_id -> session_id

        # per-channel 控制状态：支持 \new_session / \mode 指令。
        # 使用 ChannelType 的 value 作为标准键，避免散落的硬编码字符串。
        self._control_channel_types = {
            ChannelType.FEISHU.value,
            ChannelType.XIAOYI.value,
            ChannelType.DINGTALK.value,
            ChannelType.WHATSAPP.value,
            ChannelType.WECOM.value,
        }
        # 使用 SessionMap 的 channel 族（由 config 中 gateway.session_map_scope 决定是否在 key 中含 user）
        self._session_map_channel_types = frozenset({
            "feishu_enterprise",
        })
        self._channel_states: Dict[str, ChannelControlState] = {}
        self._session_map = SessionMap()
        self._cron_controller = None

        # 直接使用 jiuwenclaw.config 的 get_config_raw/set_config/update_channel_in_config
        # 避免在此处重复实现 config 模块加载逻辑。
        from jiuwenclaw.config import get_config_raw, update_channel_in_config

        self._get_config_raw = get_config_raw
        self._update_channel_in_config = update_channel_in_config

        from jiuwenclaw.gateway.agent_client import WebSocketAgentServerClient

        if isinstance(agent_client, WebSocketAgentServerClient):
            agent_client.set_server_push_handler(self._handle_agent_server_push)

        # 文件传输处理器（延迟初始化）
        self._file_transfer_handler = None

    @classmethod
    def get_instance(cls, agent_client: "AgentServerClient | None" = None) -> "MessageHandler":
        """获取单例实例。

        - 若实例已存在：可直接调用 get_instance() 或 get_instance(None)，无需传入 client。
        - 若尚未创建：需传入 agent_client，即 get_instance(client) 或 MessageHandler(client)。
        """
        if cls._instance is not None:
            return cls._instance
        if agent_client is None:
            raise RuntimeError(
                "MessageHandler 尚未初始化，请先使用 MessageHandler(client) 或 get_instance(client) 创建"
            )
        return cls(agent_client)

    def handle_message(self, msg: "Message") -> None:
        """Channel 同步回调：将消息放入 user_messages 队列，由转发循环发给 AgentServer."""
        self._user_messages.put_nowait(msg)
        logger.info(
            "[MessageHandler] _user_messages 入队: id=%s channel_id=%s session_id=%s",
            msg.id, msg.channel_id, msg.session_id,
            extra={'user_visible': 'critical'},
        )

    # ---------- Channel 控制状态：\new_session / \mode ----------

    def _get_channel_default_state(self, channel_id: str) -> ChannelControlState:
        """从 config.yaml 读取 Channel 的默认 session_id / mode."""
        try:
            cfg: Dict[str, Any] = self._get_config_raw()
        except Exception:  # noqa: BLE001
            cfg = {}
        channels_cfg = cfg.get("channels") or {}
        ch_cfg = channels_cfg.get(channel_id) or {}
        sid_raw = ch_cfg.get("default_session_id") or ""
        sid = str(sid_raw).strip() or None
        # 若未在 config 中指定默认 session_id，为该 channel 生成一个带时间戳的新 session_id
        if not sid:
            sid = self._generate_channel_session_id(channel_id)
        mode_raw = str(ch_cfg.get("default_mode") or "plan").strip().lower()
        mode = ChannelMode.AGENT if mode_raw == "agent" else ChannelMode.PLAN
        return ChannelControlState(session_id=sid, mode=mode)

    def _get_channel_state_key(self, channel_id: str, conversation_id: str | None) -> str:
        """生成 channel 状态的复合键：channel_id:conversation_id."""
        if conversation_id:
            return f"{channel_id}:{conversation_id}"
        return channel_id

    def _get_or_create_channel_state(self, msg: "Message") -> ChannelControlState:
        """获取或创建消息对应 channel 状态（使用复合键）。

        conversation_id 从 msg.metadata 获取，如 feishu 的 feishu_chat_id。
        """
        ch = msg.channel_id
        # 获取 conversation_id：从不同平台的 metadata 中提取会话标识
        # feishu: feishu_chat_id, xiaoyi: xiaoyi_session_id, 其他用 session_id
        key = self._get_channel_state_key(ch, msg.session_id)

        # 如果状态已存在，直接返回
        state = self._channel_states.get(key)
        if state is not None:
            return state

        # 否则从 config 加载默认值，并缓存
        state = self._get_channel_default_state(ch)
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(str(ch or "")):
            state.session_id = self._session_map.get_session_id(*identity_key)
        self._channel_states[key] = state
        return state

    def _save_channel_state_to_config(self, channel_id: str) -> None:
        """将指定 Channel 的默认 session_id / mode 写回 config.yaml.

        注意：仅更新默认值，不保存每个会话的状态。
        """
        try:
            cfg: Dict[str, Any] = self._get_config_raw()
        except Exception:  # noqa: BLE001
            cfg = {}
        channels_cfg = cfg.get("channels") or {}
        ch_cfg = channels_cfg.get(channel_id) or {}
        self._update_channel_in_config(
            channel_id,
            {
                "default_session_id": ch_cfg.get("default_session_id") or "",
                "default_mode": ch_cfg.get("default_mode") or "plan",
            },
        )

    def _generate_channel_session_id(self, channel_id: str) -> str:
        """为指定 channel 生成新的 session_id."""
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"{channel_id}_{ts}_{suffix}"

    @staticmethod
    def _extract_identity_tuple(msg: "Message") -> tuple[str, str, str, str] | None:
        provider = str(getattr(msg, "provider", None) or "").strip()
        chat_id = str(getattr(msg, "chat_id", None) or "").strip()
        bot_id = str(getattr(msg, "bot_id", None) or "").strip()
        user_id = str(getattr(msg, "user_id", None) or "").strip()
        identity_parts = (provider, chat_id, bot_id, user_id)
        if all(identity_parts):
            return (provider, chat_id, bot_id, user_id)
        return None

    def _channel_id_matches_session_map_types(self, channel_id: str) -> bool:
        """channel_id 是否属于 _session_map_channel_types 中某一族（精确匹配或 base: 前缀）."""
        cid = str(channel_id or "").strip()
        for base in self._session_map_channel_types:
            if cid == base or cid.startswith(f"{base}:"):
                return True
        return False

    def _resolve_control_channel_type(self, msg: "Message") -> str:
        """Resolve control channel type key: prefer provider, fallback to channel_id."""
        provider_raw = getattr(msg, "provider", None)
        provider = str(getattr(provider_raw, "value", provider_raw) or "").strip()
        if provider:
            return provider
        return str(getattr(msg, "channel_id", "") or "")

    async def _send_channel_notice(self, user_infos: dict, channel_id: str, session_id: str | None, text: str) -> None:
        """向指定 channel 发送一条系统提示消息."""
        from jiuwenclaw.schema.message import Message, EventType

        msg = Message(
            id=user_infos['id'],
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"content": text},
            event_type=EventType.CHAT_FINAL,
            metadata=user_infos['meta_data']
        )
        await self.publish_robot_messages(msg)

    def _handle_channel_control(self, msg: "Message") -> bool:
        r"""处理 \new_session / \mode 指令.

        Returns:
            True: 该消息是控制指令，已处理完毕，不需要转发给 Agent。
            False: 非控制指令，继续正常处理。
        """
        # print("this is in _handle_channel_control, msg is ", msg)
        user_infos = {}
        user_infos['id'] = msg.id
        user_infos['meta_data'] = msg.metadata

        ch = msg.channel_id
        channel_type = self._resolve_control_channel_type(msg)
        if channel_type not in self._control_channel_types:
            return False

        params = msg.params or {}
        text = str(params.get("query") or params.get("content") or "").strip()
        if not text:
            return False

        logger.info(
            'this is in _handle_channel_control, channel id is %s, text is %s, "\\new_session" in text is %s',
            channel_type,
            text,
            str("\\new_session" in text),
        )

        # 获取当前会话的状态（使用复合键）
        state = self._get_or_create_channel_state(msg)

        # \new_session：重置当前会话的 session_id
        if "/new_session" == text:
            cid = str(getattr(msg, "channel_id", "") or "")
            identity_key = self._extract_identity_tuple(msg)
            if identity_key and self._channel_id_matches_session_map_types(cid):
                new_sid = self._session_map.get_session_id(*identity_key, rotate=True)
            else:
                new_sid = self._generate_channel_session_id(channel_type)
            state.session_id = new_sid
            # 给当前会话回复提示（用原有 session_id）
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos, 
                    ch, 
                    msg.session_id, 
                    f"[收到 CLI 指令], session_id 已变更为 {new_sid}")
            )
            return True
        elif "/new_session" in text:
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos, 
                    ch, 
                    msg.session_id, 
                    f"非法指令")
            )
            return True

        # \mode plan / \mode agent
        if text == "/mode plan" or text == "/mode agent":
            parts = text.split()
            if len(parts) >= 2 and parts[1] in ("plan", "agent"):
                state.mode = ChannelMode.AGENT if parts[1] == "agent" else ChannelMode.PLAN
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos, 
                        ch, 
                        msg.session_id, 
                        f"[收到 CLI 指令], mode 已变更为 {state.mode.value}")
                )
                return True
        elif "/mode" in text:
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos, 
                    ch, 
                    msg.session_id, 
                    f"非法指令")
            )
            return True

        return False

    def _apply_channel_state(self, msg: "Message") -> None:
        """将当前 Channel 的控制状态应用到消息上（session_id / mode）."""
        channel_type = self._resolve_control_channel_type(msg)
        if channel_type not in self._control_channel_types:
            return
        state = self._get_or_create_channel_state(msg)

        # 仅 _session_map_channel_types 中的通道族使用 SessionMap；其它受控通道仍按 config/state 与入站 session_id。
        cid = str(getattr(msg, "channel_id", "") or "")
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(cid):
            sid = self._session_map.get_session_id(*identity_key)
            state.session_id = sid
            msg.session_id = sid
        elif state.session_id:
            msg.session_id = state.session_id

        # 将 mode 写入 params，后续 E2A / Agent 侧从 params["mode"] 读取
        if msg.params is None:
            msg.params = {}
        if isinstance(msg.params, dict):
            msg.params.setdefault("mode", state.mode.value)

    # ---------- user_messages ----------

    async def publish_user_messages(self, msg: "Message") -> None:
        """将消息放入 user_messages 队列（异步）."""
        await self._user_messages.put(msg)

    def publish_user_messages_nowait(self, msg: "Message") -> None:
        """将消息放入 user_messages 队列（同步）."""
        self._user_messages.put_nowait(msg)

    async def consume_user_messages(self, timeout: float | None = None) -> "Message | None":
        """消费一条 user_messages；timeout 为 None 则阻塞，否则超时返回 None."""
        if timeout is not None and timeout <= 0:
            try:
                return self._user_messages.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            if timeout is None:
                return await self._user_messages.get()
            return await asyncio.wait_for(self._user_messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ---------- robot_messages ----------

    async def publish_robot_messages(self, msg: "Message") -> None:
        """将 Agent 响应放入 robot_messages 队列."""
        await self._robot_messages.put(msg)

    def publish_robot_messages_nowait(self, msg: "Message") -> None:
        """将 Agent 响应放入 robot_messages 队列（同步）."""
        self._robot_messages.put_nowait(msg)

    async def consume_robot_messages(self, timeout: float | None = None) -> "Message | None":
        """消费一条 robot_messages；timeout 为 None 则阻塞，否则超时返回 None."""
        if timeout is not None and timeout <= 0:
            try:
                return self._robot_messages.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            if timeout is None:
                return await self._robot_messages.get()
            return await asyncio.wait_for(self._robot_messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    def message_to_e2a(msg: "Message") -> "E2AEnvelope":
        from jiuwenclaw.e2a.gateway_normalize import message_to_e2a_or_fallback

        return message_to_e2a_or_fallback(msg)


    @staticmethod
    def _merge_agent_metadata(
        request_metadata: dict[str, Any] | None,
        response_metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """合并 Agent 响应 metadata 与网关请求 metadata。

        send_push / 工具链返回的响应常不带 metadata，通道（如钉钉 batchSend）需要
        请求侧的 dingtalk_sender_id、conversation_type 等；响应中有同名字段时优先响应。
        """
        req_md = request_metadata or {}
        resp_md = response_metadata or {}
        if not req_md and not resp_md:
            return None
        merged: dict[str, Any] = {**req_md, **resp_md}
        return merged

    @staticmethod
    def _response_to_message(
        resp: "AgentResponse",
        session_id: str | None,
        *,
        request_metadata: dict[str, Any] | None = None,
    ) -> "Message":
        from jiuwenclaw.schema.message import Message, EventType

        metadata = MessageHandler._merge_agent_metadata(request_metadata, resp.metadata)

        # 检查 payload 中是否包含 event_type，如果包含则创建事件消息
        event_type = None
        if resp.payload and isinstance(resp.payload, dict):
            event_type_str = resp.payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                    # 如果是事件类型，创建事件消息而不是响应消息
                    return Message(
                        id=resp.request_id,
                        type="event",
                        channel_id=resp.channel_id,
                        session_id=session_id,
                        params={},
                        timestamp=time.time(),
                        ok=True,
                        payload=resp.payload,
                        event_type=event_type,
                        metadata=metadata,
                    )
                except ValueError:
                    # 不是有效的 EventType，继续作为普通响应处理
                    pass

        # 普通响应消息
        return Message(
            id=resp.request_id,
            type="res",
            channel_id=resp.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=resp.ok,
            payload=resp.payload,
            metadata=metadata,
        )

    async def _handle_agent_server_push(self, wire: dict[str, Any]) -> None:
        """AgentServer ``send_push`` 下行：与 RPC 共用连接但不得占用 unary/stream 等待队列。"""
        from jiuwenclaw.e2a.wire_codec import parse_agent_server_wire_chunk
        from jiuwenclaw.e2a.constants import (
            FILE_DOWNLOAD_START,
            FILE_DOWNLOAD_CHUNK,
            FILE_DOWNLOAD_COMPLETE,
        )

        try:
            chunk = parse_agent_server_wire_chunk(wire)
        except Exception as e:
            logger.exception("[MessageHandler] server_push 解析失败: %s", e)
            return
        rid = str(chunk.request_id or "")
        sid_raw = wire.get("session_id")
        if sid_raw is not None and str(sid_raw).strip():
            session_id: str | None = str(sid_raw)
        else:
            session_id = self._stream_sessions.get(rid)
        wmd = wire.get("metadata")
        if isinstance(wmd, dict):
            bus_md = {
                k: v
                for k, v in wmd.items()
                if k not in E2A_WIRE_INTERNAL_METADATA_KEYS
            }
            bus_metadata: dict[str, Any] | None = bus_md if bus_md else None
        else:
            bus_metadata = None
        if isinstance(chunk.payload, dict) and chunk.payload.get("event_type") == "cron.response":
            await self._handle_cron_push_payload(
                payload=dict(chunk.payload),
                request_id=rid,
                channel_id=chunk.channel_id,
                session_id=session_id,
                metadata=bus_metadata,
            )
            return

        # 检查是否是文件下载事件（AgentServer -> Gateway 的文件传输）
        payload = chunk.payload or {}
        if isinstance(payload, dict):
            event_type = payload.get("event_type", "")
            if event_type in (FILE_DOWNLOAD_START, FILE_DOWNLOAD_CHUNK, FILE_DOWNLOAD_COMPLETE):
                await self._handle_file_transfer_event(
                    event_type, payload, session_id, chunk.channel_id, bus_metadata
                )
                logger.info(
                    "[MessageHandler] server_push 文件下载事件已处理: request_id=%s event_type=%s",
                    rid,
                    event_type,
                )
                return

        out = self._chunk_to_message(
            chunk, session_id=session_id, metadata=bus_metadata
        )
        await self.publish_robot_messages(out)
        logger.info(
            "[MessageHandler] server_push 已写入 robot_messages: request_id=%s channel_id=%s",
            rid,
            chunk.channel_id,
        )

    def set_cron_controller(self, controller: Any) -> None:
        self._cron_controller = controller

    async def _handle_cron_push_payload(
        self,
        *,
        payload: dict[str, Any],
        request_id: str,
        channel_id: str,
        session_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        cc = self._cron_controller
        if cc is None:
            return
        action = str(payload.get("action") or "").strip()
        params = payload.get("data") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            if action == "list":
                data = await cc.list_jobs()
            elif action == "get":
                data = await cc.get_job(str(params.get("job_id") or ""))
            elif action == "create":
                data = await cc.create_job(params)
            elif action == "update":
                data = await cc.update_job(str(params.get("job_id") or ""), dict(params.get("patch") or {}))
            elif action == "delete":
                data = {"deleted": await cc.delete_job(str(params.get("job_id") or ""))}
            elif action == "toggle":
                data = await cc.toggle_job(str(params.get("job_id") or ""), bool(params.get("enabled")))
            elif action == "preview":
                data = await cc.preview_job(str(params.get("job_id") or ""), int(params.get("count", 5)))
            elif action == "run_now":
                data = {"run_id": await cc.run_now(str(params.get("job_id") or ""))}
            else:
                data = {"error": f"unknown cron action: {action}"}
        except Exception as exc:  # noqa: BLE001
            data = {"error": str(exc)}

        from jiuwenclaw.schema.message import EventType, Message
        out = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.tool_result",
                "tool_name": "cron",
                "result": data,
            },
            event_type=EventType.CHAT_TOOL_RESULT,
            metadata=metadata,
        )
        await self.publish_robot_messages(out)

    @staticmethod
    def _chunk_to_message(
        chunk: AgentResponseChunk,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """将 AgentResponseChunk 转换为 Message（用于流式处理）。
        metadata 传入 request 的 metadata，供 Feishu/Xiaoyi 等通道回发时使用平台身份。
        """
        from jiuwenclaw.schema.message import Message, EventType

        # 从 payload 中提取 event_type（如果存在）
        event_type = None
        if chunk.payload and isinstance(chunk.payload, dict):
            event_type_str = chunk.payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                except ValueError:
                    logger.debug("未知的 event_type: %s", event_type_str)

        return Message(
            id=chunk.request_id,
            type="event",
            channel_id=chunk.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload=chunk.payload,
            event_type=event_type,
            metadata=metadata,
        )

    @staticmethod
    def _non_stream_rpc_may_run_parallel(env: "E2AEnvelope") -> bool:
        """可与其它非流式 RPC 并发，不阻塞 _forward_loop。

        网关队列否则串行 await Agent，慢请求（如 SkillNet 搜索）会堵住后续的 skills.list 刷新。
        聊天相关必须按入队顺序与流式任务协调，不得后台并发。
        """
        from jiuwenclaw.schema.message import ReqMethod

        m = env.method
        if not m:
            return False
        return m not in (
            ReqMethod.CHAT_SEND.value,
            ReqMethod.CHAT_RESUME.value,
            ReqMethod.CHAT_CANCEL.value,
            ReqMethod.CHAT_ANSWER.value,
        )

    @staticmethod
    def _should_trigger_before_chat_request_hook(msg: "Message") -> bool:
        from jiuwenclaw.schema.message import ReqMethod

        return msg.req_method in (
            ReqMethod.CHAT_SEND,
            ReqMethod.CHAT_RESUME,
            ReqMethod.CHAT_ANSWER,
        )

    async def _trigger_before_chat_request_hook(self, msg: "Message") -> None:
        if not self._should_trigger_before_chat_request_hook(msg):
            return

        params = msg.params if isinstance(msg.params, dict) else {}
        if not isinstance(msg.params, dict):
            msg.params = params

        ctx = GatewayChatHookContext(
            request_id=msg.id,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            req_method=msg.req_method.value if msg.req_method is not None else None,
            params=params,
        )
        # 插件必须延迟引用
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        await ExtensionRegistry.get_instance().trigger(GatewayHookEvents.BEFORE_CHAT_REQUEST, ctx)

    async def _process_non_stream_request(self, msg: "Message", env: "E2AEnvelope") -> None:
        """执行单次非流式 Agent 请求并将结果写入 robot_messages（供串行或后台任务复用）。"""
        try:
            resp = await self._agent_client.send_request(env)
            out = self._response_to_message(
                resp, session_id=msg.session_id, request_metadata=msg.metadata
            )
            await self.publish_robot_messages(out)
            logger.info(
                "[MessageHandler] Agent 响应已写入 robot_messages: request_id=%s channel_id=%s",
                resp.request_id,
                resp.channel_id,
                extra={'user_visible': 'critical'},
            )
        except Exception as e:
            logger.exception("AgentServer send_request failed for %s: %s", msg.id, e)
            err_msg = self._build_error_out_message(msg, e)
            await self.publish_robot_messages(err_msg)
            logger.info(
                "[MessageHandler] 错误响应已写入 robot_messages: id=%s channel_id=%s",
                msg.id,
                msg.channel_id,
            )

    # ---------- 入队 -> AgentServer -> 出队 转发循环 ----------

    async def _forward_loop(self) -> None:
        """循环：从 user_messages 取消息，经 AgentServerClient 发往 AgentServer，将响应写入 robot_messages.
        支持流式和非流式两种模式。使用 timeout=None 阻塞等待，保证有消息时第一时间被唤醒处理；
        stop 时 task 被 cancel 会打断 get() 并退出。

        支持中断机制：当收到 CHAT_CANCEL 请求时，会立即取消正在执行的流式任务。
        """
        from jiuwenclaw.schema.message import ReqMethod

        while self._running:
            try:
                msg = await self.consume_user_messages(timeout=None)
                if msg is None:
                    continue
                
         
                # 先处理 Channel 控制指令（仅 feishu/xiaoyi/dingtalk/whatsapp）
                if self._handle_channel_control(msg):
                    # 该消息仅用于修改 session/mode，已给 Channel 回复提示，不再转发给 Agent
                    continue

                # 将当前 Channel 的控制状态应用到消息上
                self._apply_channel_state(msg)

                # 检查是否是中断请求
                if msg.req_method == ReqMethod.CHAT_CANCEL:
                    logger.info(
                        "[MessageHandler] 收到中断请求: id=%s channel_id=%s",
                        msg.id, msg.channel_id,
                        extra={'user_visible': 'critical'},
                    )
                    new_input = (msg.params or {}).get("new_input")
                    has_new_input = isinstance(new_input, str) and new_input.strip()
                    intent = (msg.params or {}).get("intent", "cancel")

                    if has_new_input:
                        # 有新输入：取消旧任务 → 保留 todo → 启动新任务（非并发）

                        # 1. 取消 gateway 侧所有运行中的流式任务
                        tasks_to_cancel = []
                        for rid, task in list(self._stream_tasks.items()):
                            if not task.done():
                                logger.info(
                                    "[MessageHandler] supplement: 取消流式任务 request_id=%s", rid,
                                )
                                task.cancel()
                                tasks_to_cancel.append(task)
                        if tasks_to_cancel:
                            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

                        # 2. 通知前端 supplement（前端据此判断 is_processing 状态）
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, "supplement",
                        )

                        # 3. 发送 supplement intent 到 AgentServer（取消任务但保留 todo）
                        #    用 await 确保 agent 侧先完成取消再启动新任务
                        from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields

                        supplement_env = e2a_from_agent_fields(
                            request_id=f"supplement_{int(time.time() * 1000):x}",
                            channel_id=msg.channel_id,
                            session_id=msg.session_id,
                            req_method=ReqMethod.CHAT_CANCEL,
                            params={"intent": "supplement"},
                            is_stream=False,
                            timestamp=time.time(),
                        )
                        try:
                            await self._send_interrupt_to_agent(supplement_env)
                        except Exception:
                            pass  # 即使失败也继续启动新任务

                        # 4. 入队新任务（单一任务，不并发）
                        from jiuwenclaw.schema.message import Message

                        new_req_id = f"req_{int(time.time() * 1000):x}_{msg.id}"
                        sup_meta = dict(msg.metadata) if msg.metadata else None
                        new_msg = Message(
                            id=new_req_id,
                            type="req",
                            channel_id=msg.channel_id,
                            session_id=msg.session_id,
                            params={
                                "query": new_input.strip(),
                                "session_id": msg.session_id,
                                "is_supplement": True,
                            },
                            timestamp=time.time(),
                            ok=True,
                            req_method=ReqMethod.CHAT_SEND,
                            is_stream=True,
                            provider=msg.provider,
                            chat_id=msg.chat_id,
                            user_id=msg.user_id,
                            bot_id=msg.bot_id,
                            metadata=sup_meta,
                        )
                        self._user_messages.put_nowait(new_msg)
                        logger.info(
                            "[MessageHandler] supplement: 旧任务已取消，新任务已入队: id=%s session_id=%s",
                            new_msg.id, msg.session_id,
                            extra={'user_visible': 'critical'},
                        )

                    elif intent == "cancel":
                        # 取消所有运行中的流式任务
                        for rid, task in list(self._stream_tasks.items()):
                            if not task.done():
                                logger.info(
                                    "[MessageHandler] 取消流式任务: request_id=%s", rid,
                                )
                                task.cancel()
                                sid = self._stream_sessions.get(rid)
                                await self._send_interrupt_result_notification(
                                    rid, msg.channel_id, sid, "cancel",
                                )
                        # Fire-and-forget: 发送取消请求到 AgentServer
                        env_interrupt = self.message_to_e2a(msg)
                        asyncio.create_task(self._send_interrupt_to_agent(env_interrupt))

                    elif intent in ("pause", "resume"):
                        # 暂停/恢复：不取消流式任务，转发给 AgentServer 处理 ReAct 循环
                        env_interrupt = self.message_to_e2a(msg)
                        asyncio.create_task(self._send_interrupt_to_agent(env_interrupt))
                        # 通知前端状态变更
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, intent,
                        )

                    continue

                logger.info(
                    "[MessageHandler] 从 user_messages 取出，发往 AgentServer: id=%s channel_id=%s is_stream=%s",
                    msg.id, msg.channel_id, msg.is_stream,
                    extra={'user_visible': 'critical'},
                )
                await self._trigger_before_chat_request_hook(msg)
                env = self.message_to_e2a(msg)

                # 分布式文件传输：将 Gateway 本地文件传输到 AgentServer
                try:
                    if self._should_transfer_files(env):
                        env = await self._transfer_files_to_agent_server(env, msg)
                except Exception as e:
                    logger.exception(
                        "[MessageHandler] 文件传输过程异常: request_id=%s error=%s, 继续使用原路径",
                        env.request_id,
                        e,
                    )

                stream_rid = env.request_id or msg.id
                try:
                    if env.is_stream:
                        # 流式处理：启动后台任务，支持多任务并发
                        # 通知前端新任务开始处理
                        await self._send_processing_status(
                            stream_rid, msg.session_id, msg.channel_id, is_processing=True,
                        )
                        task = asyncio.create_task(
                            self.process_stream(env, msg.session_id, msg.metadata)
                        )
                        self._stream_tasks[stream_rid] = task
                        self._stream_sessions[stream_rid] = msg.session_id
                        logger.info(
                            "[MessageHandler] Stream 任务已启动（后台运行）: request_id=%s channel_id=%s 当前并发=%d",
                            stream_rid, msg.channel_id, len(self._stream_tasks), extra={'user_visible': 'critical'}
                        )
                        # 不 await，让流式任务在后台运行，_forward_loop 继续处理下一个消息
                    elif self._non_stream_rpc_may_run_parallel(env):
                        # 非流式且非聊天：后台执行，避免慢 RPC（如 SkillNet）阻塞队列中的其它请求
                        method_label = env.method or "none"
                        asyncio.create_task(
                            self._process_non_stream_request(msg, env),
                            name=f"gw-nonstr-{method_label}-{stream_rid[:24]}",
                        )
                        logger.info(
                            "[MessageHandler] 非流式 RPC 已后台执行: id=%s method=%s",
                            msg.id,
                            method_label,
                        )
                    else:
                        await self._process_non_stream_request(msg, env)
                except Exception as e:
                    logger.exception("AgentServer send_request failed for %s: %s", msg.id, e)
                    err_msg = self._build_error_out_message(msg, e)
                    await self.publish_robot_messages(err_msg)
                    logger.info(
                            "[MessageHandler] 错误响应已写入 robot_messages: id=%s channel_id=%s",
                        msg.id, msg.channel_id,
                    )
            except asyncio.CancelledError:
                break

    async def process_stream(
        self,
        env: "E2AEnvelope",
        session_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """处理流式请求，逐个 chunk 写入 robot_messages.

        这个方法被包装为 Task，在后台运行，可以被随时取消。
        遥测可通过替换类上的 ``process_stream`` 进行打点。
        """
        rid = env.request_id or ""
        channel_id = env.channel or ""
        cancelled = False
        has_processing_status_false = False  # 追踪 AgentServer 是否已发送 processing_status=false
        try:
            async for chunk in self._agent_client.send_request_stream(env):
                # 跳过终止 chunk（仅作为流结束信号，不含实际数据）
                if chunk.is_complete and not chunk.payload:
                    logger.debug(
                        "[MessageHandler] 跳过终止 chunk: request_id=%s",
                        chunk.request_id,
                    )
                    continue

                payload = chunk.payload or {}
                if isinstance(payload, dict):
                    # 处理文件下载事件（分布式模式）
                    event_type = payload.get("event_type", "")
                    if event_type in FILE_TRANSFER_EVENT_TYPES:
                        await self._handle_file_transfer_event(
                            event_type, payload, session_id, channel_id, request_metadata
                        )
                        continue

                    # 检查是否是 processing_status=false 事件
                    if event_type == "chat.processing_status":
                        if payload.get("is_processing") is False:
                            has_processing_status_false = True

                # 携带 request metadata，供 Feishu/Xiaoyi 用平台身份回发
                out = self._chunk_to_message(
                    chunk, session_id=session_id, metadata=request_metadata
                )
                await self.publish_robot_messages(out)
                logger.debug(
                    "[MessageHandler] Stream chunk 已写入 robot_messages: request_id=%s event_type=%s",
                    chunk.request_id, out.event_type,
                )
            logger.info(
                "[MessageHandler] Stream 正常完成: request_id=%s",
                rid, extra={'user_visible': 'critical'}
            )
        except asyncio.CancelledError:
            cancelled = True
            logger.info(
                "[MessageHandler] Stream 被取消: request_id=%s",
                rid, extra={'user_visible': 'critical'}
            )
            raise  # 重新抛出，让调用者知道任务被取消
        finally:
            # 清理状态
            self._stream_tasks.pop(rid, None)
            self._stream_sessions.pop(rid, None)
            logger.debug(
                "[MessageHandler] Stream 任务状态已清理: request_id=%s",
                rid,
            )
            # 所有流式任务正常结束后，通知前端全部处理完成
            # 只有当 AgentServer 没有发送过 processing_status=false 时才发送
            if not cancelled and not self._stream_tasks and not has_processing_status_false:
                await self._send_processing_status(
                    rid, session_id, channel_id, is_processing=False,
                )
                logger.info(
                    "[MessageHandler] 所有流式任务已完成，已发送 is_processing=false: session_id=%s",
                    session_id,
                )

    async def _handle_file_transfer_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        session_id: str | None,
        channel_id: str,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """处理文件传输事件（AgentServer -> Gateway 的文件下载）.

        Args:
            event_type: 事件类型（file.download.start/chunk/complete）
            payload: 事件参数
            session_id: 会话ID
            channel_id: 频道ID
            request_metadata: 请求元数据
        """
        from jiuwenclaw.e2a.constants import (
            FILE_DOWNLOAD_START,
            FILE_DOWNLOAD_CHUNK,
            FILE_DOWNLOAD_COMPLETE,
        )
        from jiuwenclaw.gateway.file_transfer_handler import get_file_transfer_handler

        # 延迟初始化文件传输处理器
        if self._file_transfer_handler is None:
            self._file_transfer_handler = get_file_transfer_handler()

        ft_handler = self._file_transfer_handler

        # 检查是否启用分布式模式
        if not ft_handler.enabled:
            logger.warning(
                "[MessageHandler] 收到文件下载事件但未启用分布式模式: event_type=%s",
                event_type,
            )
            return

        try:
            if event_type == FILE_DOWNLOAD_START:
                dl_params = FileTransferStartParams(
                    transfer_id=payload.get("transfer_id", ""),
                    filename=payload.get("filename", "unnamed"),
                    file_size=payload.get("file_size", 0),
                    sha256=payload.get("sha256", ""),
                    total_chunks=payload.get("total_chunks", 0),
                    chunk_size=payload.get("chunk_size", 65536),
                    mime_type=payload.get("mime_type", ""),
                    session_id=session_id or "",
                    channel_id=channel_id,
                )
                result = await ft_handler.handle_download_start(dl_params)
                logger.info(
                    "[MessageHandler] 文件下载开始: transfer_id=%s accepted=%s",
                    payload.get("transfer_id"),
                    result.get("accepted"),
                )

            elif event_type == FILE_DOWNLOAD_CHUNK:
                result = await ft_handler.handle_download_chunk(
                    transfer_id=payload.get("transfer_id", ""),
                    chunk_index=payload.get("chunk_index", 0),
                    base64_data=payload.get("base64_data", ""),
                )
                logger.debug(
                    "[MessageHandler] 文件下载分片: transfer_id=%s chunk=%d",
                    payload.get("transfer_id"),
                    payload.get("chunk_index"),
                )

            elif event_type == FILE_DOWNLOAD_COMPLETE:
                result = await ft_handler.handle_download_complete(
                    transfer_id=payload.get("transfer_id", ""),
                    sha256=payload.get("sha256", ""),
                )

                if result.get("success"):
                    # 文件接收成功，发送 chat.file 事件到 Channel
                    file_path = result.get("file_path", "")
                    logger.info(
                        "[MessageHandler] 文件下载完成: transfer_id=%s path=%s",
                        payload.get("transfer_id"),
                        file_path,
                    )

                    # 发送 chat.file 事件到 Channel
                    from jiuwenclaw.schema.message import Message, EventType
                    from pathlib import Path

                    files_payload = [{
                        "path": file_path,
                        "name": Path(file_path).name,
                    }]

                    file_msg = Message(
                        id=f"file_{payload.get('transfer_id', '')}",
                        type="event",
                        channel_id=channel_id,
                        session_id=session_id,
                        params={},
                        timestamp=time.time(),
                        ok=True,
                        payload={
                            "event_type": EventType.CHAT_FILE.value,
                            "files": files_payload,
                        },
                        event_type=EventType.CHAT_FILE,
                        metadata=request_metadata,
                    )
                    await self.publish_robot_messages(file_msg)
                    logger.info(
                        "[MessageHandler] 已发送 chat.file 事件: channel_id=%s file=%s",
                        channel_id,
                        file_path,
                    )
                else:
                    logger.warning(
                        "[MessageHandler] 文件下载失败: transfer_id=%s error=%s",
                        payload.get("transfer_id"),
                        result.get("error"),
                    )

        except Exception as e:
            logger.exception(
                "[MessageHandler] 处理文件传输事件失败: event_type=%s error=%s",
                event_type,
                e,
            )

    def _should_transfer_files(self, env: "E2AEnvelope") -> bool:
        """判断是否需要进行分布式文件传输.

        Args:
            env: E2AEnvelope 信封

        Returns:
            True 如果需要传输文件
        """
        from pathlib import Path

        # 延迟初始化文件传输处理器
        if self._file_transfer_handler is None:
            from jiuwenclaw.gateway.file_transfer_handler import get_file_transfer_handler
            self._file_transfer_handler = get_file_transfer_handler()

        # 检查是否启用分布式模式
        if not self._file_transfer_handler.enabled:
            return False

        # 检查 params.files 是否存在且非空
        params = env.params or {}
        files = params.get("files")
        if not files or not isinstance(files, list):
            return False

        # 检查是否有需要传输的本地文件
        for file_info in files:
            if isinstance(file_info, dict):
                path = file_info.get("path", "")
                if path and Path(path).exists():
                    return True

        return False

    async def _transfer_files_to_agent_server(
        self,
        env: "E2AEnvelope",
        msg: "Message",
    ) -> "E2AEnvelope":
        """将 params.files 中的文件传输到 AgentServer（分布式模式）.

        Args:
            env: 已构建的 E2AEnvelope
            msg: 原始消息（用于提取 session_id, channel_id 等）

        Returns:
            更新后的 E2AEnvelope（params.files 中的 path 已替换为 AgentServer 端路径）
        """
        from pathlib import Path
        from jiuwenclaw.e2a.constants import (
            FILE_TRANSFER_START,
            FILE_TRANSFER_CHUNK,
            FILE_TRANSFER_COMPLETE,
        )
        from jiuwenclaw.e2a.models import E2AEnvelope

        # 确保文件传输处理器已初始化
        if self._file_transfer_handler is None:
            from jiuwenclaw.gateway.file_transfer_handler import get_file_transfer_handler
            self._file_transfer_handler = get_file_transfer_handler()

        ft_handler = self._file_transfer_handler
        params = dict(env.params or {})
        files = params.get("files", [])

        if not files:
            return env

        # 构建 send_callback：使用 agent_client 的 file_transfer 方法
        async def send_callback(method: str, ft_params: dict[str, Any]) -> dict[str, Any]:
            """文件传输回调函数，通过 agent_client 发送传输消息."""
            if method == FILE_TRANSFER_START:
                start_params = FileTransferStartParams(
                    transfer_id=ft_params.get("transfer_id", ""),
                    filename=ft_params.get("filename", "unnamed"),
                    file_size=ft_params.get("file_size", 0),
                    sha256=ft_params.get("sha256", ""),
                    total_chunks=ft_params.get("total_chunks", 0),
                    chunk_size=ft_params.get("chunk_size", 65536),
                    mime_type=ft_params.get("mime_type", ""),
                    session_id=ft_params.get("session_id", "") or env.session_id or "",
                    channel_id=env.channel or "",
                )
                return await self._agent_client.file_transfer_start(start_params)
            elif method == FILE_TRANSFER_CHUNK:
                return await self._agent_client.file_transfer_chunk(
                    transfer_id=ft_params.get("transfer_id", ""),
                    chunk_index=ft_params.get("chunk_index", 0),
                    base64_data=ft_params.get("base64_data", ""),
                    chunk_size=ft_params.get("chunk_size", 0),
                    channel_id=env.channel or "",
                )
            elif method == FILE_TRANSFER_COMPLETE:
                return await self._agent_client.file_transfer_complete(
                    transfer_id=ft_params.get("transfer_id", ""),
                    sha256=ft_params.get("sha256", ""),
                    channel_id=env.channel or "",
                )
            else:
                return {"accepted": False, "error": f"unknown method: {method}"}

        # 并行传输多个文件（使用信号量限制并发数）
        semaphore = asyncio.Semaphore(ft_handler.config.max_concurrent_transfers)

        async def transfer_single_file(file_info: dict[str, Any]) -> dict[str, Any]:
            """传输单个文件."""
            async with semaphore:
                local_path = file_info.get("path", "")
                if not local_path or not Path(local_path).exists():
                    logger.warning(
                        "[MessageHandler] 文件不存在或路径无效: %s",
                        local_path,
                    )
                    return file_info  # 返回原信息，不修改

                try:
                    # 调用 FileTransferHandler 进行传输
                    result = await ft_handler.send_file_to_agent_server(
                        file_path=local_path,
                        send_callback=send_callback,
                        session_id=env.session_id or "",
                        channel_id=env.channel or "",
                        request_id=env.request_id or "",
                    )

                    if result.get("success"):
                        # 传输成功，更新路径为 AgentServer 端路径
                        new_path = result.get("file_path", "")
                        logger.info(
                            "[MessageHandler] 文件传输成功: local=%s -> remote=%s",
                            local_path,
                            new_path,
                        )
                        # 保留其他元数据，更新 path 和 size
                        updated_info = dict(file_info)
                        updated_info["path"] = new_path
                        updated_info["size"] = result.get("file_size", file_info.get("size", 0))
                        updated_info["_transferred"] = True  # 标记已传输
                        updated_info["_original_path"] = local_path  # 保留原始路径
                        return updated_info
                    else:
                        # 传输失败，记录警告但保留原路径（回退到本地模式）
                        logger.warning(
                            "[MessageHandler] 文件传输失败: path=%s error=%s, 回退到本地模式",
                            local_path,
                            result.get("error", "unknown"),
                        )
                        return file_info

                except Exception as e:
                    logger.exception(
                        "[MessageHandler] 文件传输异常: path=%s error=%s",
                        local_path,
                        e,
                    )
                    return file_info  # 异常时保留原信息

        # 并行执行所有文件传输
        transfer_tasks = [transfer_single_file(f) for f in files if isinstance(f, dict)]

        if transfer_tasks:
            logger.info(
                "[MessageHandler] 开始分布式文件传输: request_id=%s files=%d",
                env.request_id,
                len(transfer_tasks),
            )
            updated_files = await asyncio.gather(*transfer_tasks)

            # 更新 params.files
            params["files"] = updated_files

            # 创建新的 E2AEnvelope（保持其他字段不变）
            updated_env = E2AEnvelope(
                protocol_version=env.protocol_version,
                provenance=env.provenance,
                request_id=env.request_id,
                jsonrpc_id=env.jsonrpc_id,
                correlation_id=env.correlation_id,
                task_id=env.task_id,
                context_id=env.context_id,
                session_id=env.session_id,
                message_id=env.message_id,
                timestamp=env.timestamp,
                identity_origin=env.identity_origin,
                channel=env.channel,
                user_id=env.user_id,
                source_agent_id=env.source_agent_id,
                method=env.method,
                params=params,  # 更新后的 params
                ext_method=env.ext_method,
                session_update_kind=env.session_update_kind,
                is_stream=env.is_stream,
                expected_output_modes=env.expected_output_modes,
                auth=env.auth,
                channel_context=env.channel_context,
                a2a_metadata=env.a2a_metadata,
                acp_meta=env.acp_meta,
            )

            transferred_count = sum(1 for f in updated_files if f.get("_transferred"))
            logger.info(
                "[MessageHandler] 文件传输完成: request_id=%s files=%d transferred=%d",
                env.request_id,
                len(files),
                transferred_count,
            )

            return updated_env

        return env

    async def _send_stream_cancelled_notification(
        self, request_id: str | None, channel_id: str, session_id: str | None
    ) -> None:
        """发送流式任务被取消的通知到客户端."""
        if not request_id:
            return

        from jiuwenclaw.schema.message import Message, EventType

        cancel_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.interrupt_result",
                "intent": "pause",
                "success": True,
                "message": "任务已暂停",
            },
            event_type=EventType.CHAT_INTERRUPT_RESULT,
            metadata=None,
        )
        await self.publish_robot_messages(cancel_msg)
        logger.info(
            "[MessageHandler] 已发送流式任务取消通知: request_id=%s",
            request_id,
        )

    async def _send_interrupt_to_agent(self, env: "E2AEnvelope") -> None:
        """Fire-and-forget: 发送中断请求到 AgentServer，不阻塞转发循环."""
        try:
            resp = await self._agent_client.send_request(env)
            logger.info(
                "[MessageHandler] AgentServer 中断响应(已丢弃): request_id=%s ok=%s",
                resp.request_id, resp.ok,
            )
        except Exception as e:
            logger.warning("[MessageHandler] AgentServer 中断请求失败(忽略): %s", e)

    async def _send_interrupt_result_notification(
        self, request_id: str, channel_id: str, session_id: str | None, intent: str,
    ) -> None:
        """发送 interrupt_result 事件到前端（pause / resume 等）."""
        from jiuwenclaw.schema.message import Message, EventType

        messages_map = {
            "pause": "任务已暂停",
            "resume": "任务已恢复",
            "cancel": "任务已取消",
            "supplement": "任务已切换",
        }
        notify_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.interrupt_result",
                "intent": intent,
                "success": True,
                "message": messages_map.get(intent, "任务已中断"),
            },
            event_type=EventType.CHAT_INTERRUPT_RESULT,
            metadata=None,
        )
        await self.publish_robot_messages(notify_msg)
        logger.info(
            "[MessageHandler] 已发送 interrupt_result 通知: intent=%s request_id=%s",
            intent, request_id,
        )

    async def _send_processing_status(
        self, request_id: str, session_id: str | None, channel_id: str, *, is_processing: bool,
    ) -> None:
        """发送 chat.processing_status 事件到客户端."""
        from jiuwenclaw.schema.message import Message, EventType

        status_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.processing_status",
                "session_id": session_id,
                "is_processing": is_processing,
            },
            event_type=EventType.CHAT_PROCESSING_STATUS,
            metadata=None,
        )
        await self.publish_robot_messages(status_msg)

    def _build_error_out_message(self, msg: "Message", error: Exception) -> "Message":
        from jiuwenclaw.schema.message import Message

        return Message(
            id=msg.id,
            type="res",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={},
            timestamp=time.time(),
            ok=False,
            payload={"error": str(error)},
            metadata=msg.metadata,
        )

    async def start_forwarding(self) -> None:
        """启动入队 -> AgentServer -> 出队 的转发任务."""
        if self._forward_task is not None:
            return
        self._running = True
        self._forward_task = asyncio.create_task(self._forward_loop())
        logger.info("[MessageHandler] 转发循环已启动 (_user_messages -> AgentServer -> _robot_messages)")

        # 启动文件传输处理器的清理任务（分布式模式）
        if self._file_transfer_handler is None:
            from jiuwenclaw.gateway.file_transfer_handler import get_file_transfer_handler
            self._file_transfer_handler = get_file_transfer_handler()
        if self._file_transfer_handler.enabled:
            await self._file_transfer_handler.start_cleanup_task()

    async def stop_forwarding(self) -> None:
        """停止转发任务."""
        self._running = False

        # 停止文件传输处理器的清理任务
        if self._file_transfer_handler is not None and self._file_transfer_handler.enabled:
            await self._file_transfer_handler.stop_cleanup_task()

        # 取消所有流式任务
        for rid, task in list(self._stream_tasks.items()):
            if not task.done():
                logger.info("[MessageHandler] 停止时取消流式任务: request_id=%s", rid)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stream_tasks.clear()
        self._stream_sessions.clear()

        # 取消转发循环
        if self._forward_task is not None:
            self._forward_task.cancel()
            try:
                await self._forward_task
            except asyncio.CancelledError:
                pass
            self._forward_task = None

        logger.info("[MessageHandler] 转发循环已停止")

    # ---------- 状态 ----------

    @property
    def user_messages_size(self) -> int:
        return self._user_messages.qsize()

    @property
    def robot_messages_size(self) -> int:
        return self._robot_messages.qsize()
