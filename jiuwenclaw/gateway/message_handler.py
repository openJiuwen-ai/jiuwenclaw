# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MessageHandler - 消息处理抽象与双队列实现（入队经 AgentServerClient 发往 AgentServer）."""

from __future__ import annotations

import hashlib
import logging
import asyncio
import os
import re
import secrets
import time

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict
from jiuwenclaw.channel.base import ChannelType
from jiuwenclaw.e2a.constants import E2A_WIRE_INTERNAL_METADATA_KEYS, FILE_TRANSFER_EVENT_TYPES
from jiuwenclaw.gateway.session_map import SessionMap
from jiuwenclaw.gateway.slash_command import (
    ParsedControlAction,
    parse_channel_control_text,
)
from jiuwenclaw.schema.hook_event import GatewayHookEvents
from jiuwenclaw.schema.hooks_context import GatewayChatHookContext
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.utils import FileTransferStartParams

logger = logging.getLogger(__name__)

_ACP_CHANNEL_ID = "acp"
_ACP_ORIGINAL_SESSION_ID_KEY = "acp_original_session_id"
_DEFAULT_INLINE_FILE_SIZE_LIMIT = 128 * 1024
_VIBESKILL_CHANNEL_ID = "vibeskill"
_VIBESKILL_ORIGINAL_SESSION_ID_KEY = "vibeskill_original_session_id"
_KNOWN_JIUWENCLAW_SESSION_PREFIXES = (
    "sess_",
    "acp_",
    "cron_",
    "feishu_",
    "wechat_",
    "xiaoyi_",
    "dingtalk_",
    "wecom_",
    "telegram_",
    "discord_",
    "whatsapp_",
    "vibeskill_",
)


class ChannelMode(str, Enum):
    AGENT_PLAN = "agent.plan"
    AGENT_FAST = "agent.fast"
    CODE_PLAN = "code.plan"
    CODE_NORMAL = "code.normal"
    TEAM = "team"


@dataclass
class ChannelControlState:
    session_id: str | None = None
    mode: ChannelMode = ChannelMode.AGENT_PLAN
    # SessionMap + Yuanrong: filled when channel uses SessionMap stable identity
    service_id: str | None = None
    agent_id: str | None = None


@dataclass
class NewSessionCancelParams:
    """\\new_session 时取消旧会话并发通知所需的具名参数（避免过长形参列表）。"""

    user_infos: dict[str, Any]
    channel_id: str
    reply_session_id: str | None
    new_sid: str
    old_sid: str | None


@dataclass
class ModeChangeCancelParams:
    """\\mode 切换时取消旧会话并发通知所需的具名参数。"""

    user_infos: dict[str, Any]
    channel_id: str
    reply_session_id: str | None
    old_sid: str | None
    new_mode_label: str

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
        self._stream_metadata: dict[str, dict[str, Any] | None] = {}  # request_id -> request metadata
        self._stream_modes: dict[str, str] = {}  # request_id -> mode
        self._requests_started_total = 0
        self._requests_finished_total = 0
        self._pending_evolution_approval: dict[str, str] = {}  # session_id -> approval_request_id
        self._queued_supplement_input: dict[str, dict[str, Any]] = {}  # session_id -> queued supplement payload
        self._session_evolution_in_progress: set[str] = set()
        self._acp_session_aliases: dict[str, str] = {}  # external_session_id -> internal_session_id
        self._acp_session_alias_lock = asyncio.Lock()
        self._vibeskill_session_aliases: dict[str, str] = {}  # external_session_id -> internal_session_id
        self._vibeskill_session_alias_lock = asyncio.Lock()

        # per-channel 控制状态：支持 \new_session / \mode 指令。
        # 使用 ChannelType 的 value 作为标准键，避免散落的硬编码字符串。
        self._control_channel_types = {
            ChannelType.FEISHU.value,
            ChannelType.XIAOYI.value,
            ChannelType.DINGTALK.value,
            ChannelType.WHATSAPP.value,
            ChannelType.WECOM.value,
            ChannelType.WECHAT.value,
        }
        # 使用 SessionMap 的 channel 族（由 config 中 gateway.session_map_scope 决定是否在 key 中含 user）
        self._session_map_channel_types = frozenset({
            "feishu",
        })
        self._channel_states: Dict[str, ChannelControlState] = {}
        self._session_map = SessionMap()
        self._cron_controller = None

        # IM Pipeline（数字分身）— None 时不执行，不影响原有逻辑
        self._inbound_pipeline = None   # type: Any  # IMInboundPipeline | None
        self._outbound_pipeline = None  # type: Any  # IMOutboundPipeline | None

        # 直接使用 jiuwenclaw.config 的 get_config_raw/set_config/update_channel_in_config
        # 避免在此处重复实现 config 模块加载逻辑。
        from jiuwenclaw.config import get_config_raw, update_channel_in_config

        self._get_config_raw = get_config_raw
        self._update_channel_in_config = update_channel_in_config

        set_push_handler = getattr(self._agent_client, "set_server_push_handler", None)
        if callable(set_push_handler):
            set_push_handler(self._handle_agent_server_push)

        # 文件传输处理器（延迟初始化）
        self._file_transfer_handler = None

    def set_inbound_pipeline(self, pipeline: Any) -> None:
        self._inbound_pipeline = pipeline

    def set_outbound_pipeline(self, pipeline: Any) -> None:
        self._outbound_pipeline = pipeline

    def reload_session_map(self) -> None:
        """Reload Redis-backed SessionMap cache after leader switchover (active-standby)."""
        self._session_map.reload()

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
        mode_raw = str(ch_cfg.get("default_mode") or "agent.plan").strip().lower()
        mode_map = {
            "agent.plan": ChannelMode.AGENT_PLAN,
            "agent.fast": ChannelMode.AGENT_FAST,
            "code.plan": ChannelMode.CODE_PLAN,
            "code.normal": ChannelMode.CODE_NORMAL,
            "team": ChannelMode.TEAM,
        }
        mode = mode_map.get(mode_raw, ChannelMode.AGENT_PLAN)
        return ChannelControlState(session_id=sid, mode=mode)

    def _get_channel_state_key(self, channel_id: str, conversation_id: str | None) -> str:
        """生成 channel 状态的复合键：channel_id:conversation_id."""
        if conversation_id:
            return f"{channel_id}:{conversation_id}"
        return channel_id

    def _resolve_channel_state_key(self, msg: "Message") -> str:
        """Resolve the state bucket key for a message.

        SessionMap-backed channels use the stable identity key so `/mode` and
        `/new_session` stay bound to the same identity even after session_id rotation.
        Other channels continue to scope state by inbound conversation/session id.
        """
        channel_id = str(getattr(msg, "channel_id", "") or "")
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(channel_id):
            stable_identity_key = self._session_map.get_identity_key(*identity_key)
            return self._get_channel_state_key(channel_id, stable_identity_key)
        return self._get_channel_state_key(channel_id, msg.session_id)

    def _get_or_create_channel_state(self, msg: "Message") -> ChannelControlState:
        """获取或创建消息对应 channel 状态（使用复合键）。

        conversation_id 从 msg.metadata 获取，如 feishu 的 feishu_chat_id，
        xiaoyi的xiaoyi_session_id，其他用 session_id
        """
        ch = msg.channel_id
        key = self._resolve_channel_state_key(msg)

        # 如果状态已存在，直接返回
        state = self._channel_states.get(key)
        if state is not None:
            return state

        # 否则从 config 加载默认值，并缓存
        state = self._get_channel_default_state(ch)
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(str(ch or "")):
            sess = self._session_map.get_session(*identity_key)
            state.session_id = sess.session_id
            state.service_id = sess.service_id
            state.agent_id = sess.agent_id
        self._channel_states[key] = state
        return state

    def _save_channel_state_to_config(self, channel_id: str) -> None:
        """将指定 Channel 的默认 session_id / mode 写回 config.yaml."""
        state = self._channel_states.get(channel_id)
        if not state:
            return
        self._update_channel_in_config(
            channel_id,
            {
                "default_session_id": state.session_id or "",
                "default_mode": state.mode.value if hasattr(state.mode, 'value') else str(state.mode),
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

    async def _send_channel_notice(
        self,
        user_infos: dict,
        channel_id: str,
        session_id: str | None,
        text_or_payload: str | dict[str, Any],
    ) -> None:
        """向指定 channel 发送一条系统提示消息.

        - str: 兼容历史行为，封装为 {"content": text, "is_complete": True}
        - dict: 透传给 channel（仅确保 is_complete=True）
        """
        from jiuwenclaw.schema.message import Message, EventType

        if isinstance(text_or_payload, dict):
            payload = dict(text_or_payload)
            payload.setdefault("is_complete", True)
        else:
            payload = {"content": text_or_payload, "is_complete": True}

        # 如果 payload 包含 error 字段，设置 ok=False 并将错误信息放到 content
        msg_ok = True
        if isinstance(payload, dict) and "error" in payload:
            msg_ok = False
            if "content" not in payload:
                payload["content"] = str(payload.get("error", ""))

        msg = Message(
            id=user_infos['id'],
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=msg_ok,
            payload=payload,
            event_type=EventType.CHAT_FINAL,
            metadata=user_infos['meta_data']
        )
        await self.publish_robot_messages(msg)

        # 只对 web channel 发送 processing_status，避免 feishu 等渠道显示多余的 "[状态]已完成" 消息框
        if channel_id == "web":
            status_msg = Message(
                id=user_infos['id'],
                type="event",
                channel_id=channel_id,
                session_id=session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={
                    "event_type": "chat.processing_status",
                    "session_id": session_id,
                    "is_processing": False,
                    "is_complete": True,
                },
                event_type=EventType.CHAT_PROCESSING_STATUS,
                metadata=None,
            )
            await self.publish_robot_messages(status_msg)

    async def _cancel_agent_work_for_session(self, msg: "Message", old_sid: str | None) -> None:
        """取消指定 session 的网关流式任务，并向 AgentServer 发送 CHAT_CANCEL（与 Web chat.interrupt intent=cancel 对齐）。

        网关侧仅取消 ``_stream_sessions[rid] == old_sid`` 的流式任务。AgentServer 对 ``intent=cancel`` 仍可能
        ``cancel_all_session_tasks``（与现网 unary 中断一致）；若需仅撤销单 session 需在 interface 层扩展协议。
        """
        from jiuwenclaw.schema.message import Message, ReqMethod

        self._clear_session_evolution_states(old_sid)

        tasks_to_cancel: list[asyncio.Task] = []
        rids_cancelled: list[str] = []

        for rid, task in list(self._stream_tasks.items()):
            if self._stream_sessions.get(rid) != old_sid:
                continue
            if not task.done():
                logger.info(
                    "[MessageHandler] 取消流式任务: request_id=%s session_id=%s",
                    rid,
                    old_sid,
                )
                task.cancel()
                tasks_to_cancel.append(task)
                rids_cancelled.append(rid)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        for rid in rids_cancelled:
            await self._send_interrupt_result_notification(
                rid, msg.channel_id, old_sid, "cancel",
            )

        if old_sid is None and not rids_cancelled:
            return

        sid_for_agent = (old_sid or "").strip()
        if not sid_for_agent:
            return

        # 即使网关侧已无活跃流式拉取任务（例如 Agent 正在执行 shell/工具），也必须通知 AgentServer，
        # 否则仅断开 CLI WebSocket 无法停止已派发的工作。

        cancel_req = Message(
            id=f"interrupt_{int(time.time() * 1000):x}_{secrets.token_hex(3)}",
            type="req",
            channel_id=msg.channel_id,
            session_id=sid_for_agent,
            params={
                "intent": "cancel",
                "session_id": sid_for_agent,
            },
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_CANCEL,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        agent_msg = await self._prepare_agent_dispatch_message(cancel_req)
        env_interrupt = self.message_to_e2a(agent_msg)
        await self._send_interrupt_to_agent(env_interrupt)

    async def cancel_agent_sessions_on_disconnect(
        self,
        session_keys: list[tuple[str, str]],
    ) -> None:
        """TUI/WebSocket 异常断开时，取消仍绑定在该连接上的会话（与显式 chat.interrupt 对齐）。"""
        from jiuwenclaw.schema.message import Message, ReqMethod

        seen: set[str] = set()
        for _channel_id, session_id in session_keys:
            sid = (session_id or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            stub = Message(
                id=f"ws_drop_{int(time.time() * 1000):x}_{secrets.token_hex(4)}",
                type="req",
                channel_id=_channel_id,
                session_id=sid,
                params={"intent": "cancel", "session_id": sid},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_CANCEL,
                is_stream=False,
            )
            try:
                await self._cancel_agent_work_for_session(stub, sid)
            except Exception:
                logger.warning(
                    "[MessageHandler] disconnect cancel failed: channel_id=%s session_id=%s",
                    _channel_id,
                    sid,
                    exc_info=True,
                )

    async def _new_session_cancel_and_notice(
        self,
        params: NewSessionCancelParams,
        msg: "Message",
    ) -> None:
        """先完成旧会话取消与 AgentServer 中断，再下发 session 已变更提示。"""
        await self._cancel_agent_work_for_session(msg, params.old_sid)
        await self._send_channel_notice(
            params.user_infos,
            params.channel_id,
            params.reply_session_id,
            f"[收到 CLI 指令], session_id 已变更为 {params.new_sid}",
        )

    async def _mode_change_cancel_and_notice(
        self,
        params: ModeChangeCancelParams,
        msg: "Message",
    ) -> None:
        """与 /new_session 一致：先取消当前会话在网关与 Agent 侧的任务，再下发 mode 已变更提示。"""
        await self._cancel_agent_work_for_session(msg, params.old_sid)
        await self._send_channel_notice(
            params.user_infos,
            params.channel_id,
            params.reply_session_id,
            self._build_mode_change_notice_text(params.new_mode_label),
        )

    @staticmethod
    def _build_mode_change_notice_text(mode_label: str) -> str:
        return f"[收到 CLI 指令], mode 已变更为 {mode_label}"

    def _handle_channel_control(self, msg: "Message") -> bool:
        r"""处理 \new_session / \mode / \skills 指令.

        Returns:
            True: 该消息是控制指令，已处理完毕，不需要转发给 Agent。
            False: 非控制指令，继续正常处理。
        """
        user_infos = {"id": msg.id, "meta_data": msg.metadata}

        ch = msg.channel_id
        channel_type = self._resolve_control_channel_type(msg)
        if channel_type not in self._control_channel_types:
            return False

        params = msg.params or {}
        text = str(params.get("query") or params.get("content") or "").strip()
        if not text:
            return False

        parsed = parse_channel_control_text(text)
        if parsed.action is ParsedControlAction.NONE:
            return False

        logger.info(
            "[MessageHandler] _handle_channel_control channel=%s text=%s action=%s",
            channel_type,
            text,
            parsed.action.value,
        )

        if parsed.action is ParsedControlAction.SKILLS_OK:
            asyncio.create_task(
                self._skills_slash_notice(user_infos, ch, msg.session_id, msg)
            )
            return True

        # 获取当前会话的状态（使用复合键）
        state = self._get_or_create_channel_state(msg)

        if parsed.action is ParsedControlAction.NEW_SESSION_OK:
            old_sid = state.session_id
            cid = str(getattr(msg, "channel_id", "") or "")
            identity_key = self._extract_identity_tuple(msg)
            if identity_key and self._channel_id_matches_session_map_types(cid):
                sess = self._session_map.get_session(*identity_key, rotate=True)
                new_sid = sess.session_id
                state.service_id = sess.service_id
                state.agent_id = sess.agent_id
            else:
                new_sid = self._generate_channel_session_id(channel_type)
                state.service_id = None
                state.agent_id = None
            state.session_id = new_sid
            asyncio.create_task(
                self._new_session_cancel_and_notice(
                    NewSessionCancelParams(
                        user_infos=user_infos,
                        channel_id=ch,
                        reply_session_id=msg.session_id,
                        new_sid=new_sid,
                        old_sid=old_sid,
                    ),
                    msg,
                )
            )
            return True
        if parsed.action is ParsedControlAction.NEW_SESSION_BAD:
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos,
                    ch,
                    msg.session_id,
                    "非法指令",
                )
            )
            return True

        if parsed.action is ParsedControlAction.MODE_OK:
            mode_str = parsed.mode_subcommand or ""
            if mode_str not in (
                "agent",
                "code",
                "team",
                "agent.plan",
                "agent.fast",
                "code.plan",
                "code.normal",
            ):
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        "非法指令",
                    )
                )
                return True
            old_mode = state.mode
            old_sid = state.session_id
            if mode_str == "agent":
                state.mode = ChannelMode.AGENT_PLAN
            elif mode_str == "code":
                state.mode = ChannelMode.CODE_NORMAL
            elif mode_str == "team":
                state.mode = ChannelMode.TEAM
            elif mode_str == "agent.plan":
                state.mode = ChannelMode.AGENT_PLAN
            elif mode_str == "agent.fast":
                state.mode = ChannelMode.AGENT_FAST
            elif mode_str == "code.plan":
                state.mode = ChannelMode.CODE_PLAN
            elif mode_str == "code.normal":
                state.mode = ChannelMode.CODE_NORMAL
            new_label = state.mode.value
            if old_mode != state.mode:
                asyncio.create_task(
                    self._mode_change_cancel_and_notice(
                        ModeChangeCancelParams(
                            user_infos=user_infos,
                            channel_id=ch,
                            reply_session_id=msg.session_id,
                            old_sid=old_sid,
                            new_mode_label=new_label,
                        ),
                        msg,
                    )
                )
            else:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        self._build_mode_change_notice_text(new_label),
                    )
                )
            return True
        if parsed.action is ParsedControlAction.SWITCH_OK:
            switch_str = parsed.switch_subcommand or ""
            target_mode: ChannelMode | None = None
            if switch_str == "plan":
                if state.mode in (ChannelMode.AGENT_PLAN, ChannelMode.AGENT_FAST):
                    target_mode = ChannelMode.AGENT_PLAN
                elif state.mode in (ChannelMode.CODE_PLAN, ChannelMode.CODE_NORMAL):
                    target_mode = ChannelMode.CODE_PLAN
            elif switch_str == "fast":
                if state.mode in (ChannelMode.AGENT_PLAN, ChannelMode.AGENT_FAST):
                    target_mode = ChannelMode.AGENT_FAST
            elif switch_str == "normal":
                if state.mode in (ChannelMode.CODE_PLAN, ChannelMode.CODE_NORMAL):
                    target_mode = ChannelMode.CODE_NORMAL
            if target_mode is None:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        "非法指令",
                    )
                )
                return True
            old_mode = state.mode
            old_sid = state.session_id
            state.mode = target_mode
            new_label = state.mode.value
            if old_mode != state.mode:
                asyncio.create_task(
                    self._mode_change_cancel_and_notice(
                        ModeChangeCancelParams(
                            user_infos=user_infos,
                            channel_id=ch,
                            reply_session_id=msg.session_id,
                            old_sid=old_sid,
                            new_mode_label=new_label,
                        ),
                        msg,
                    )
                )
            else:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        self._build_mode_change_notice_text(new_label),
                    )
                )
            return True
        if parsed.action in (ParsedControlAction.MODE_BAD, ParsedControlAction.SWITCH_BAD):
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos,
                    ch,
                    msg.session_id,
                    "非法指令",
                )
            )
            return True

        return False

    def _handle_all_channel_control(self, msg: "Message") -> bool:
        """处理所有通道的 Channel 控制指令（如 /ls、/view）
        Returns:
            True: 该消息是控制指令，已处理完毕，不需要转发给 Agent。
            False: 非控制指令，继续正常处理。
        """
        user_infos = {"id": msg.id, "meta_data": msg.metadata}

        ch = msg.channel_id
        channel_type = self._resolve_control_channel_type(msg)

        params = msg.params or {}
        metadata = msg.metadata or {}
        text = str(
            params.get("query") or
            params.get("content") or
            metadata.get("query") or
            ""
        ).strip()
        if not text:
            return False

        parsed = parse_channel_control_text(text)
        if parsed.action is ParsedControlAction.NONE:
            return False

        logger.info(
            "[MessageHandler] _handle_all_channel_control channel=%s text=%s action=%s",
            channel_type,
            text,
            parsed.action.value,
        )
        if parsed.action is ParsedControlAction.LS_OK:
            ls_path = text[len("/ls"):].strip() or "."
            asyncio.create_task(
                self._ls_slash_notice(user_infos, ch, msg.session_id, msg, ls_path)
            )
            return True

        if parsed.action is ParsedControlAction.VIEW_OK:
            asyncio.create_task(
                self._view_slash_notice(
                    user_infos, ch, msg.session_id, msg,
                    text,
                )
            )
            return True

        return False

    async def _skills_slash_notice(
        self,
        user_infos: dict[str, Any],
        channel_id: str,
        reply_session_id: str | None,
        msg: "Message",
    ) -> None:
        """受控通道整行 /skills list：请求 skills.list 并以 CHAT_FINAL 通知透传。"""
        from jiuwenclaw.schema.message import Message, ReqMethod

        req_id = f"skills_slash_{int(time.time() * 1000):x}_{secrets.token_hex(3)}"
        skills_req = Message(
            id=req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.SKILLS_LIST,
            is_stream=False,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        try:
            env = self.message_to_e2a(skills_req)
            resp = await self._agent_client.send_request(env)
            if resp.ok:
                if isinstance(resp.payload, dict):
                    notice_payload: dict[str, Any] = dict(resp.payload)
                else:
                    notice_payload = {"data": resp.payload}
            else:
                err = ""
                if isinstance(resp.payload, dict):
                    err = str(resp.payload.get("error") or "").strip()
                notice_payload = {
                    "error": f"获取技能列表失败{(': ' + err) if err else ''}",
                }
            await self._send_channel_notice(
                user_infos, channel_id, reply_session_id, notice_payload
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[MessageHandler] /skills list 请求失败: %s", exc)
            await self._send_channel_notice(
                user_infos,
                channel_id,
                reply_session_id,
                {"error": f"获取技能列表失败：{exc}"},
            )

    async def _ls_slash_notice(
        self,
        user_infos: dict[str, Any],
        channel_id: str,
        reply_session_id: str | None,
        msg: "Message",
        path: str,
    ) -> None:
        """/ls [path]：请求 command.ls 并以 CHAT_FINAL 通知透传。"""
        from jiuwenclaw.schema.message import Message, ReqMethod

        req_id = f"ls_slash_{int(time.time() * 1000):x}_{secrets.token_hex(3)}"
        ls_req = Message(
            id=req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={"path": path},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.COMMAND_LS,
            is_stream=False,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        try:
            env = self.message_to_e2a(ls_req)
            resp = await self._agent_client.send_request(env)
            if resp.ok:
                if isinstance(resp.payload, dict):
                    entries = resp.payload.get("entries", [])
                    if entries:
                        lines = [f"📁 {resp.payload.get('path', path)}:"]
                        for entry in entries:
                            name = entry.get("name", "?")
                            is_dir = entry.get("is_dir", False)
                            icon = "📁" if is_dir else "📄"
                            lines.append(f"  {icon} {name}")
                        notice_payload: dict[str, Any] = {"content": "\n".join(lines)}
                    else:
                        notice_payload = {"error": resp.payload.get("error", "目录为空或不存在")}
                else:
                    notice_payload = {"data": str(resp.payload)}
            else:
                err = ""
                if isinstance(resp.payload, dict):
                    err = str(resp.payload.get("error") or resp.payload.get("message") or "").strip()
                notice_payload = {
                    "error": f"列出目录失败{(': ' + err) if err else ''}",
                }
            await self._send_channel_notice(
                user_infos, channel_id, reply_session_id, notice_payload
            )
        except Exception as exc:
            logger.exception("[MessageHandler] /ls 请求失败: %s", exc)
            await self._send_channel_notice(
                user_infos,
                channel_id,
                reply_session_id,
                {"error": f"列出目录失败：{exc}"},
            )

    async def _view_slash_notice(
        self,
        user_infos: dict[str, Any],
        channel_id: str,
        reply_session_id: str | None,
        msg: "Message",
        text: str,
    ) -> None:
        """/view <path>：请求 command.view 并以 CHAT_FINAL 通知透传。"""
        import re
        from jiuwenclaw.schema.message import Message, ReqMethod

        view_match = re.match(
            r'^/(?:view|cat)\s+(.+?)(?:\s+-f\s+(\d+))?(?:\s+-l\s+(\d+))?(?:\s+-n\s+(\d+))?$',
            text.strip()
        )
        if not view_match:
            await self._send_channel_notice(
                user_infos,
                channel_id,
                reply_session_id,
                {"error": "用法: /view <path> [-f N] [-l N]"},
            )
            return

        path = view_match.group(1).strip()
        from_line = int(view_match.group(2) or 1)
        lines = int(view_match.group(3) or view_match.group(4) or 0) or None

        req_id = f"view_slash_{int(time.time() * 1000):x}_{secrets.token_hex(3)}"
        view_req = Message(
            id=req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={"path": path, "from_line": from_line, "lines": lines},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.COMMAND_VIEW,
            is_stream=False,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        try:
            env = self.message_to_e2a(view_req)
            resp = await self._agent_client.send_request(env)
            if resp.ok:
                if isinstance(resp.payload, dict):
                    notice_payload: dict[str, Any] = {
                        "content": resp.payload.get("content", "")
                    }
                else:
                    notice_payload = {"data": str(resp.payload)}
            else:
                err = ""
                if isinstance(resp.payload, dict):
                    err = str(resp.payload.get("error") or resp.payload.get("message") or "").strip()
                notice_payload = {
                    "error": f"查看文件失败{(': ' + err) if err else ''}",
                }
            await self._send_channel_notice(
                user_infos, channel_id, reply_session_id, notice_payload
            )
        except Exception as exc:
            logger.exception("[MessageHandler] /view 请求失败: %s", exc)
            await self._send_channel_notice(
                user_infos,
                channel_id,
                reply_session_id,
                {"error": f"查看文件失败：{exc}"},
            )

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
            sess = self._session_map.get_session(*identity_key)
            state.session_id = sess.session_id
            state.service_id = sess.service_id
            state.agent_id = sess.agent_id
            msg.session_id = sess.session_id
            if msg.params is None:
                msg.params = {}
            if isinstance(msg.params, dict):
                msg.params["service_id"] = sess.service_id
                if sess.agent_id:
                    msg.params["agent_id"] = sess.agent_id
                else:
                    msg.params.pop("agent_id", None)
        elif state.session_id:
            msg.session_id = state.session_id
            if isinstance(msg.params, dict):
                msg.params.pop("service_id", None)
                msg.params.pop("agent_id", None)

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
        # Outbound Pipeline（数字分身出站路由）— 在入队前运行
        if self._outbound_pipeline is not None:
            try:
                await self._outbound_pipeline.apply(msg)
            except Exception:
                logger.exception("Outbound pipeline error, message queued without routing")
        # remote 模式：chat.final 事件写入网关会话索引（助手预览；同步 JSON 读写，高流量时可能成为热点）
        self._maybe_update_session_index_on_robot_msg(msg)
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
    def _is_session_map_style_session_id(session_id: str) -> bool:
        parts = [part.strip() for part in str(session_id or "").split("::")]
        if len(parts) not in (5, 6):
            return False
        return all(parts)

    @classmethod
    def _is_known_jiuwenclaw_session_id(cls, session_id: str | None) -> bool:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        if sid.startswith(_KNOWN_JIUWENCLAW_SESSION_PREFIXES):
            return True
        return cls._is_session_map_style_session_id(sid)

    async def _ensure_acp_agent_session(self, session_id: str) -> str:
        from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenclaw.schema.message import ReqMethod

        env = e2a_from_agent_fields(
            request_id=f"acp-session-create-{int(time.time() * 1000):x}-{secrets.token_hex(3)}",
            channel_id=_ACP_CHANNEL_ID,
            session_id=session_id,
            req_method=ReqMethod.SESSION_CREATE,
            params={"session_id": session_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._agent_client.send_request(env)
        if not resp.ok:
            payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
            raise RuntimeError(str(payload.get("error") or "acp session.create failed"))
        payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
        resolved = payload.get("sessionId") or payload.get("session_id") or session_id
        resolved_str = str(resolved or "").strip()
        if not resolved_str:
            raise RuntimeError("acp session.create returned empty session_id")
        return resolved_str

    async def _resolve_acp_internal_session_id(
        self,
        external_session_id: str | None,
    ) -> tuple[str | None, bool]:
        external = str(external_session_id or "").strip()
        if not external:
            return None, False

        cached = self._acp_session_aliases.get(external)
        if cached:
            return cached, cached != external

        async with self._acp_session_alias_lock:
            cached = self._acp_session_aliases.get(external)
            if cached:
                return cached, cached != external

            desired = (
                external
                if self._is_known_jiuwenclaw_session_id(external)
                else self._generate_channel_session_id(_ACP_CHANNEL_ID)
            )
            ensured = await self._ensure_acp_agent_session(desired)
            self._acp_session_aliases[external] = ensured
            return ensured, ensured != external

    async def _prepare_agent_dispatch_message(self, msg: "Message") -> "Message":
        from jiuwenclaw.schema.message import ReqMethod

        if msg.channel_id == _ACP_CHANNEL_ID:
            if msg.req_method in (ReqMethod.INITIALIZE, ReqMethod.SESSION_CREATE):
                return msg
            internal_session_id, aliased = await self._resolve_acp_internal_session_id(msg.session_id)
            if not internal_session_id:
                return msg
            params = dict(msg.params or {})
            params["session_id"] = internal_session_id
            metadata = dict(msg.metadata or {})
            if aliased:
                metadata.setdefault(_ACP_ORIGINAL_SESSION_ID_KEY, str(msg.session_id or ""))
            return replace(
                msg,
                session_id=internal_session_id,
                params=params,
                metadata=metadata or None,
            )

        if msg.channel_id == _VIBESKILL_CHANNEL_ID:
            if msg.req_method in (ReqMethod.INITIALIZE, ReqMethod.SESSION_CREATE):
                return msg
            internal_session_id, aliased = await self._resolve_vibeskill_internal_session_id(msg.session_id)
            if not internal_session_id:
                return msg
            params = dict(msg.params or {})
            params["session_id"] = internal_session_id
            metadata = dict(msg.metadata or {})
            if aliased:
                metadata.setdefault(_VIBESKILL_ORIGINAL_SESSION_ID_KEY, str(msg.session_id or ""))
            return replace(
                msg,
                session_id=internal_session_id,
                params=params,
                metadata=metadata or None,
            )

        return msg

    def _resolve_acp_external_session_id(
        self,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None

        original = ""
        if isinstance(metadata, dict):
            original = str(metadata.get(_ACP_ORIGINAL_SESSION_ID_KEY) or "").strip()
        if original:
            return original

        for external, internal in self._acp_session_aliases.items():
            if internal == sid:
                return external
        return sid

    @staticmethod
    def _resolve_at_file_references(
        content: str,
        cwd: str | None = None,
        max_file_size: int | None = _DEFAULT_INLINE_FILE_SIZE_LIMIT,
    ) -> str:
        """Parse ``@path`` references in *content* and inline the file text.

        Supported forms:
        - ``@relative/path`` / ``@/absolute/path`` — resolved against *cwd*
        - ``@"path with spaces"`` — quoted paths
        - ``@path#L10-20`` — line-range suffix (ignored for now, whole file read)

        Returns content with ``@path`` replaced by a ``<file-content>`` block
        containing the actual text.  If a file cannot be read the original
        ``@path`` is kept unchanged.
        """
        if not content:
            return content

        working_dir = cwd or os.getcwd()

        # Match @path or @"quoted path", optionally followed by #L... line range
        pattern = re.compile(
            r'(?P<prefix>(?:^|(?<=\s)))@(?:"(?P<quoted>[^"]+)"|(?P<plain>[^\s#]+))(?:#[^#\s]*)?'
        )

        def _replacer(m: re.Match[str]) -> str:
            raw = m.group("quoted") or m.group("plain") or ""
            if not raw:
                return m.group(0)

            # Resolve path
            if raw.startswith("~/"):
                home = os.path.expanduser("~")
                resolved = os.path.join(home, raw[2:])
            elif MessageHandler._is_absolute_reference_path(raw):
                resolved = raw
            else:
                resolved = os.path.join(working_dir, raw)

            try:
                path = Path(resolved)
                if not path.is_file():
                    return m.group(0)
                size = path.stat().st_size
                truncated = False
                if max_file_size is None:
                    text = path.read_text(encoding="utf-8", errors="replace")
                else:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read(max_file_size + 1)
                    if size > max_file_size or len(text) > max_file_size:
                        truncated = True
                    if len(text) > max_file_size:
                        text = text[:max_file_size]
                    if truncated:
                        suffix = f"\n... (truncated, original_size={size} bytes)"
                        text = f"{text}{suffix}"
                return (
                    f'\n<file-content path="{raw}">\n{text}\n</file-content>\n'
                )
            except (OSError, UnicodeDecodeError):
                return m.group(0)

        return pattern.sub(_replacer, content)

    @staticmethod
    def _is_absolute_reference_path(raw: str) -> bool:
        return raw.startswith("/") or (len(raw) >= 3 and raw[1] == ":" and raw[2] == "\\")

    @staticmethod
    def _resolve_reference_path(raw: str, cwd: str | None = None) -> str:
        working_dir = cwd or os.getcwd()
        if raw.startswith("~/"):
            return os.path.join(os.path.expanduser("~"), raw[2:])
        if MessageHandler._is_absolute_reference_path(raw):
            return raw
        return os.path.join(working_dir, raw)

    @classmethod
    def _normalize_structured_attachments(
        cls,
        attachments: Any,
        cwd: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(attachments, list):
            return []

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in attachments:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            resolved_path = cls._resolve_reference_path(raw_path, cwd)
            if resolved_path in seen:
                continue
            seen.add(resolved_path)
            normalized.append(
                {
                    "path": resolved_path,
                    "type": str(item.get("type") or "file").strip() or "file",
                    "filename": str(item.get("filename") or Path(resolved_path).name).strip(),
                }
            )
        return normalized

    @classmethod
    def _strip_attached_mentions(
        cls,
        content: str,
        attachments: list[dict[str, Any]],
        cwd: str | None = None,
    ) -> str:
        if not content or not attachments:
            return content

        attached_paths = {
            cls._resolve_reference_path(str(item.get("path") or ""), cwd)
            for item in attachments
            if str(item.get("path") or "").strip()
        }
        if not attached_paths:
            return content

        pattern = re.compile(
            r'(?P<prefix>(?:^|(?<=\s)))@(?:"(?P<quoted>[^"]+)"|(?P<plain>[^\s#]+))(?:#[^#\s]*)?'
        )

        def _replacer(match: re.Match[str]) -> str:
            raw = match.group("quoted") or match.group("plain") or ""
            if not raw:
                return match.group(0)
            resolved = cls._resolve_reference_path(raw, cwd)
            if resolved not in attached_paths:
                return match.group(0)
            return f"{match.group('prefix')}{raw}"

        return pattern.sub(_replacer, content)

    @classmethod
    def _resolve_structured_attachments(
        cls,
        content: str,
        attachments: Any,
        cwd: str | None = None,
    ) -> str:
        normalized = cls._normalize_structured_attachments(attachments, cwd)
        if not normalized:
            return content

        prefix = " ".join(f'@"{item["path"]}"' for item in normalized)
        cleaned_content = cls._strip_attached_mentions(content, normalized, cwd)
        merged_content = f"{prefix} {cleaned_content}".strip()
        return cls._resolve_at_file_references(merged_content, cwd=cwd)

    async def create_agent_session(self, session_id: str) -> str:
        from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenclaw.schema.message import ReqMethod

        env = e2a_from_agent_fields(
            request_id=f"vibeskill-session-create-{int(time.time() * 1000):x}-{secrets.token_hex(3)}",
            channel_id=_VIBESKILL_CHANNEL_ID,
            session_id=session_id,
            req_method=ReqMethod.SESSION_CREATE,
            params={"session_id": session_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._agent_client.send_request(env)
        if not resp.ok:
            payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
            raise RuntimeError(str(payload.get("error") or "vibeskill session.create failed"))
        payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
        resolved = payload.get("sessionId") or payload.get("session_id") or session_id
        resolved_str = str(resolved or "").strip()
        if not resolved_str:
            raise RuntimeError("vibeskill session.create returned empty session_id")
        return resolved_str

    async def register_skill(self, session_id: str, skill_url: str) -> dict[str, Any]:
        """通过 AgentServer 将远程 skill 包注册到当前 session 的 workspace。

        仅支持 Standard mode session。
        """
        from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenclaw.schema.message import ReqMethod

        env = e2a_from_agent_fields(
            request_id=f"vibeskill-register-skill-{int(time.time() * 1000):x}-{secrets.token_hex(3)}",
            channel_id=_VIBESKILL_CHANNEL_ID,
            session_id=session_id,
            req_method=ReqMethod.SKILLS_IMPORT_LOCAL,
            params={
                "path": skill_url,
                "force": True,
                # 与 Standard chat 一致，按 session 维度路由到对应租户工作区
                "service_id": str(session_id or "").strip(),
            },
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._agent_client.send_request(env)
        if not resp.ok:
            payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
            raise RuntimeError(str(payload.get("error") or payload.get("detail") or "register skill failed"))
        return dict(resp.payload or {}) if isinstance(resp.payload, dict) else {"success": True}

    async def _resolve_vibeskill_internal_session_id(
        self,
        external_session_id: str | None,
    ) -> tuple[str | None, bool]:
        external = str(external_session_id or "").strip()
        if not external:
            return None, False

        cached = self._vibeskill_session_aliases.get(external)
        if cached:
            return cached, cached != external

        async with self._vibeskill_session_alias_lock:
            cached = self._vibeskill_session_aliases.get(external)
            if cached:
                return cached, cached != external

            desired = (
                external
                if self._is_known_jiuwenclaw_session_id(external)
                else self._generate_channel_session_id(_VIBESKILL_CHANNEL_ID)
            )
            ensured = await self.create_agent_session(desired)
            self._vibeskill_session_aliases[external] = ensured
            return ensured, ensured != external

    def _resolve_vibeskill_external_session_id(
        self,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None

        original = ""
        if isinstance(metadata, dict):
            original = str(metadata.get(_VIBESKILL_ORIGINAL_SESSION_ID_KEY) or "").strip()
        if original:
            return original

        for external, internal in self._vibeskill_session_aliases.items():
            if internal == sid:
                return external
        return sid

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
        resp: "AgentResponse | AgentResponseChunk",
        session_id: str | None,
        *,
        request_metadata: dict[str, Any] | None = None,
    ) -> "Message":
        from jiuwenclaw.schema.message import Message, EventType

        resp_metadata = getattr(resp, "metadata", None)
        metadata = MessageHandler._merge_agent_metadata(request_metadata, resp_metadata)
        request_id = str(getattr(resp, "request_id", ""))
        channel_id = str(getattr(resp, "channel_id", ""))
        payload = getattr(resp, "payload", None)
        ok = bool(getattr(resp, "ok", True))

        # 从 metadata 中提取 group_digital_avatar 和 enable_memory 字段
        # 这些字段在 message_to_e2a 中被放入 metadata，需要在这里提取出来
        group_digital_avatar = bool(metadata.get("group_digital_avatar", False)) if metadata else False
        enable_memory = bool(metadata.get("enable_memory", True)) if metadata else True

        # 检查 payload 中是否包含 event_type，如果包含则创建事件消息
        event_type = None
        if payload and isinstance(payload, dict):
            event_type_str = payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                    # 如果是事件类型，创建事件消息而不是响应消息
                    return Message(
                        id=request_id,
                        type="event",
                        channel_id=channel_id,
                        session_id=session_id,
                        params={},
                        timestamp=time.time(),
                        ok=True,
                        payload=payload,
                        event_type=event_type,
                        metadata=metadata,
                        group_digital_avatar=group_digital_avatar,
                        enable_memory=enable_memory,
                    )
                except ValueError:
                    # 不是有效的 EventType，继续作为普通响应处理
                    pass

        # 普通响应消息
        return Message(
            id=request_id,
            type="res",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=ok,
            payload=payload,
            event_type=EventType.CHAT_FINAL,
            metadata=metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
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
        
        # 获取原始请求的 metadata，用于合并
        request_metadata = self._stream_metadata.get(rid)
        
        # 获取 AgentServer 返回的 metadata
        wmd = wire.get("metadata")
        if isinstance(wmd, dict):
            resp_md = {
                k: v
                for k, v in wmd.items()
                if k not in E2A_WIRE_INTERNAL_METADATA_KEYS
            }
        else:
            resp_md = None

        # 合并 metadata：请求 metadata 在前，响应 metadata 在后（响应优先）
        bus_metadata = MessageHandler._merge_agent_metadata(request_metadata, resp_md)

        if chunk.channel_id == _ACP_CHANNEL_ID:
            session_id = self._resolve_acp_external_session_id(session_id, bus_metadata)

        # 检查是否是文件下载事件（AgentServer -> Gateway 的文件传输）
        payload = chunk.payload or {}
        if isinstance(payload, dict):
            event_type = payload.get("event_type", "")
            if event_type in FILE_TRANSFER_EVENT_TYPES:
                await self._handle_file_transfer_event(
                    event_type, payload, session_id, chunk.channel_id, bus_metadata
                )
                logger.info(
                    "[MessageHandler] server_push 文件下载事件已处理: request_id=%s event_type=%s",
                    rid,
                    event_type,
                )
                return
            if event_type == "cron.response":
                await self._handle_cron_push_payload(
                    payload=dict(chunk.payload),
                    request_id=rid,
                    channel_id=chunk.channel_id,
                    session_id=session_id,
                    metadata=bus_metadata,
                )
                return

        if self._is_terminal_stream_chunk(chunk):
            logger.debug(
                "[MessageHandler] 忽略 server_push 终止 chunk: request_id=%s",
                chunk.request_id,
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
                # 从原始请求中获取 mode，覆盖 LLM 工具调用的默认值
                request_mode = self._stream_modes.get(request_id)
                if request_mode:
                    params["mode"] = request_mode
                if (
                    str(params.get("targets") or "").strip() == "web"
                    and session_id
                    and not params.get("session_id")
                ):
                    params = dict(params)
                    params["session_id"] = session_id
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
            enable_streaming=False,  # 工具结果不开启流式，避免被发送到群聊
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

        # 从 metadata 中提取 group_digital_avatar 和 enable_memory 字段
        # 这些字段在 message_to_e2a 中被放入 metadata，需要在这里提取出来
        group_digital_avatar = bool(metadata.get("group_digital_avatar", False)) if metadata else False
        enable_memory = bool(metadata.get("enable_memory", True)) if metadata else True

        # 从 payload 中提取 event_type（如果存在）
        event_type = None
        payload = dict(chunk.payload) if chunk.payload and isinstance(chunk.payload, dict) else {}
        if payload:
            event_type_str = payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                except ValueError:
                    logger.debug("未知的 event_type: %s", event_type_str)
            elif payload.get("error"):
                event_type = EventType.CHAT_ERROR
                payload = {
                    "event_type": EventType.CHAT_ERROR.value,
                    "error": str(payload.get("error")),
                    **{k: v for k, v in payload.items() if k not in ("event_type", "error")},
                }

        return Message(
            id=chunk.request_id,
            type="event",
            channel_id=chunk.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=False if event_type == EventType.CHAT_ERROR else True,
            payload=payload or chunk.payload,
            event_type=event_type,
            metadata=metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
        )

    @staticmethod
    def _is_terminal_stream_chunk(chunk: AgentResponseChunk) -> bool:
        """识别仅用于结束流的哨兵 chunk，避免被当作业务事件继续下发。"""
        if not bool(getattr(chunk, "is_complete", False)):
            return False
        payload = getattr(chunk, "payload", None)
        if not payload:
            return True
        if not isinstance(payload, dict):
            return False
        if payload.get("event_type"):
            return False
        if payload.get("content") not in (None, ""):
            return False
        if payload.get("error") not in (None, ""):
            return False
        return payload.get("is_complete") is True and set(payload.keys()) <= {"is_complete"}

    async def _publish_stream_cancelled_final(
        self,
        request_id: str,
        channel_id: str,
        session_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """流式任务被网关取消时补发 chat.final，带 is_complete（供飞书等通道合并缓冲）。"""
        from jiuwenclaw.schema.message import Message, EventType

        group_digital_avatar = bool(request_metadata.get("group_digital_avatar", False)) if request_metadata else False
        enable_memory = bool(request_metadata.get("enable_memory", True)) if request_metadata else True

        out = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": EventType.CHAT_FINAL.value,
                "content": "",
                "is_complete": True,
            },
            event_type=EventType.CHAT_FINAL,
            metadata=request_metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
        )
        await self.publish_robot_messages(out)
        logger.info(
            "[MessageHandler] 已发送流式取消结束帧: request_id=%s session_id=%s",
            request_id,
            session_id,
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
            # session.create/delete 需 await，避免与后续入队消息（含 remote 下 session.list）竞态
            ReqMethod.SESSION_CREATE.value,
            ReqMethod.SESSION_DELETE.value,
        )

    @staticmethod
    async def _trigger_before_chat_request_hook(msg: "Message") -> None:
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
        await ExtensionRegistry.get_instance().trigger(GatewayHookEvents.BEFORE_CHAT_REQUEST, ctx)

    @staticmethod
    def _schedule_session_index_upsert(sid: str, role: str, content: str, timestamp: float) -> None:
        """在运行中的事件循环下将 ``upsert`` 放到线程池，避免同步 JSON 读写阻塞主循环。"""
        from jiuwenclaw.gateway.session_index import upsert

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            upsert(sid, role, content, timestamp)
            return
        loop.create_task(asyncio.to_thread(upsert, sid, role, content, timestamp))

    @staticmethod
    def _schedule_session_index_remove(sid: str) -> None:
        """在运行中的事件循环下将 ``remove`` 放到线程池，避免同步 IO 阻塞主循环。"""
        from jiuwenclaw.gateway.session_index import remove

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            remove(sid)
            return
        loop.create_task(asyncio.to_thread(remove, sid))

    @staticmethod
    def _maybe_update_session_index_on_user_msg(msg: "Message") -> None:
        """remote 模式下，用户 chat.send 时将用户消息预览写入网关会话索引。"""
        try:
            import time as _time
            from jiuwenclaw.gateway.session_index import is_remote_storage
            if not is_remote_storage():
                return
            if str(msg.channel_id or "").strip() != "web":
                return
            from jiuwenclaw.schema.message import ReqMethod
            if msg.req_method not in (ReqMethod.CHAT_SEND, ReqMethod.CHAT_RESUME):
                return
            sid = str(msg.session_id or "").strip()
            if not sid:
                return
            params = msg.params or {}
            content = str(params.get("query") or params.get("content") or "").strip()
            MessageHandler._schedule_session_index_upsert(
                sid, role="user", content=content, timestamp=msg.timestamp or _time.time()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session_index] _maybe_update_session_index_on_user_msg 异常: %s", exc)

    @staticmethod
    def _maybe_update_session_index_on_robot_msg(msg: "Message") -> None:
        """remote 模式下，chat.final 事件时将助手回复预览写入网关会话索引。"""
        try:
            import time as _time
            from jiuwenclaw.schema.message import EventType
            from jiuwenclaw.gateway.session_index import is_remote_storage
            if not is_remote_storage():
                return
            if str(msg.channel_id or "").strip() != "web":
                return
            if msg.event_type != EventType.CHAT_FINAL:
                return
            sid = str(msg.session_id or "").strip()
            if not sid:
                return
            payload = msg.payload or {}
            content = str(payload.get("content") or "").strip()
            MessageHandler._schedule_session_index_upsert(
                sid, role="assistant", content=content, timestamp=msg.timestamp or _time.time()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session_index] _maybe_update_session_index_on_robot_msg 异常: %s", exc)

    @staticmethod
    def _maybe_sync_session_index_on_response(msg: "Message", resp: "AgentResponse") -> None:
        """remote 模式下，根据 session.create/delete 的成功响应同步更新网关会话索引。"""
        try:
            import time as _time
            from jiuwenclaw.gateway.session_index import is_remote_storage
            if not is_remote_storage():
                return
            if str(msg.channel_id or "").strip() != "web":
                return
            req_method = msg.req_method
            if req_method is None:
                return
            if not resp.ok:
                return
            from jiuwenclaw.schema.message import ReqMethod
            payload = resp.payload or {}
            if req_method == ReqMethod.SESSION_CREATE:
                sid = str(payload.get("session_id") or msg.session_id or "").strip()
                if sid:
                    MessageHandler._schedule_session_index_upsert(
                        sid, role="", content="", timestamp=_time.time()
                    )
                    logger.debug("[session_index] session.create 已加入索引: %s", sid)
            elif req_method == ReqMethod.SESSION_DELETE:
                sid = str(payload.get("session_id") or "").strip()
                if not sid and isinstance(msg.params, dict):
                    sid = str(msg.params.get("session_id") or "").strip()
                if sid:
                    MessageHandler._schedule_session_index_remove(sid)
                    logger.debug("[session_index] session.delete 已从索引移除: %s", sid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session_index] _maybe_sync_session_index_on_response 异常: %s", exc)

    async def _process_remote_session_list_request(self, msg: "Message") -> None:
        """remote 模式：在网关读会话索引并响应，不转发给 AgentServer。"""
        from jiuwenclaw.gateway.session_index import list_sessions_page
        from jiuwenclaw.schema.agent import AgentResponse

        params = msg.params if isinstance(msg.params, dict) else {}
        sessions, total, limit, offset = list_sessions_page(params)
        resp = AgentResponse(
            request_id=msg.id,
            channel_id=msg.channel_id,
            ok=True,
            payload={
                "sessions": sessions,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
            metadata=msg.metadata,
        )
        out = self._response_to_message(
            resp, session_id=msg.session_id, request_metadata=msg.metadata
        )
        await self.publish_robot_messages(out)
        logger.info(
            "[MessageHandler] remote session.list 已响应: id=%s total=%s limit=%s offset=%s",
            msg.id, total, limit, offset,
        )

    @staticmethod
    def _is_evolution_approval_request_id(request_id: Any) -> bool:
        # Support skill evolution (skill_evolve_*) and new skill creation (skill_create*)
        return isinstance(request_id, str) and (
            request_id.startswith("skill_evolve_") or
            request_id.startswith("skill_create_")
        )

    def _queue_supplement_input(
        self,
        session_id: str | None,
        new_input: str,
        attachments: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        if not session_id:
            return
        payload: dict[str, Any] = {"new_input": new_input}
        if files:
            payload["files"] = files
        elif attachments:
            payload["attachments"] = attachments
        self._queued_supplement_input[session_id] = payload

    def _pop_queued_supplement_input(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        return self._queued_supplement_input.pop(session_id, None)

    def _mark_pending_evolution_approval(self, session_id: str | None, request_id: Any) -> None:
        if not session_id:
            return
        if self._is_evolution_approval_request_id(request_id):
            self._pending_evolution_approval[session_id] = str(request_id)

    def _clear_pending_evolution_approval(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._pending_evolution_approval.pop(session_id, None)

    def _mark_session_evolution_in_progress(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._session_evolution_in_progress.add(session_id)

    def _clear_session_evolution_in_progress(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._session_evolution_in_progress.discard(session_id)

    def _is_session_evolution_in_progress(self, session_id: str | None) -> bool:
        return isinstance(session_id, str) and session_id in self._session_evolution_in_progress

    def _clear_session_evolution_states(self, session_id: str | None) -> None:
        self._clear_session_evolution_in_progress(session_id)
        self._clear_pending_evolution_approval(session_id)
        self._pop_queued_supplement_input(session_id)

    @staticmethod
    def _is_nonempty_list(value: Any) -> bool:
        return isinstance(value, list) and bool(value)

    @staticmethod
    def _extract_supplement_files(params: Any) -> list[dict[str, Any]] | None:
        if not isinstance(params, dict):
            return None
        raw_files = params.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            return None
        files = [item for item in raw_files if isinstance(item, dict)]
        return files or None

    @classmethod
    def _collect_supplement_files_from_params(cls, params: Any) -> list[dict[str, Any]] | None:
        """从 supplement/interrupt 参数中收集附件（files 优先，其次 attachments）。"""
        files = cls._extract_supplement_files(params)
        if files:
            return files
        if not isinstance(params, dict):
            return None
        raw_attachments = params.get("attachments")
        if not isinstance(raw_attachments, list) or not raw_attachments:
            return None
        converted: list[dict[str, Any]] = []
        for item in raw_attachments:
            if not isinstance(item, dict):
                continue
            file_url = str(item.get("url") or item.get("uri") or "").strip()
            file_path = str(item.get("path") or "").strip()
            if not file_url and not file_path:
                continue
            file_name = (
                str(item.get("name") or item.get("filename") or Path(file_path).name or "unknown_file").strip()
                or "unknown_file"
            )
            converted.append(
                {
                    **item,
                    "url": file_url or item.get("url"),
                    "name": file_name,
                    "filename": file_name,
                }
            )
        return converted or None

    @classmethod
    def _normalize_files_for_agent_dispatch(
        cls,
        files: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """保留 url/name 供 Agent 自行拉取。

        企业版（``AGENT_RUNTIME`` 非空）且有 url 时，不向 Agent 传递 Gateway 本地 path。
        """
        if not files:
            return files

        strip_path_for_url = bool(os.getenv("AGENT_RUNTIME", "").strip())
        normalized: list[dict[str, Any]] = []
        for file_info in files:
            if not isinstance(file_info, dict):
                normalized.append(file_info)
                continue

            updated = dict(file_info)

            file_url = str(updated.get("url") or updated.get("uri") or "").strip()
            file_name = (
                str(updated.get("name") or updated.get("filename") or "unknown_file").strip()
                or "unknown_file"
            )
            updated["name"] = file_name
            updated.setdefault("filename", file_name)
            if file_url:
                updated["url"] = file_url

            local_path = str(updated.get("path") or "").strip()
            if strip_path_for_url:
                if file_url:
                    # Web/MinIO 附件只传 url，避免 Agent 看到 Gateway path 后先 read_file
                    updated.pop("path", None)
                elif local_path:
                    updated["path"] = local_path
                else:
                    updated.pop("path", None)
            else:
                transferred = bool(updated.get("_transferred"))
                if local_path and (transferred or Path(local_path).exists()):
                    updated["path"] = local_path
                elif file_url:
                    # Gateway 侧无效 path 对 Agent 无意义，改由 Agent 通过 url 下载
                    updated.pop("path", None)
                elif local_path:
                    updated["path"] = local_path

            normalized.append(updated)

        return normalized or None

    def _prepare_message_files_for_dispatch(self, msg: "Message") -> "Message":
        """chat.send 出队前规范化 files，确保 url 传给 Agent（含 supplement 重建消息）。"""
        if not isinstance(msg.params, dict):
            return msg
        raw_files = msg.params.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            return msg

        file_dicts = [item for item in raw_files if isinstance(item, dict)]
        if not file_dicts:
            return msg

        normalized = self._normalize_files_for_agent_dispatch(file_dicts)
        if not normalized:
            return msg

        url_count = sum(
            1 for item in normalized
            if isinstance(item, dict) and str(item.get("url") or item.get("uri") or "").strip()
        )
        if url_count:
            logger.info(
                "[MessageHandler] 附件将以 url 传递给 Agent 自行下载: request_id=%s files=%d url=%d",
                msg.id,
                len(normalized),
                url_count,
            )

        params = dict(msg.params)
        params["files"] = normalized
        return replace(msg, params=params)

    @staticmethod
    def _build_supplement_chat_send_params(
        new_input: str,
        session_id: str,
        *,
        files: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        trimmed = new_input.strip()
        params: dict[str, Any] = {
            "query": trimmed,
            "content": trimmed,
            "session_id": session_id,
            "is_supplement": True,
        }
        if files:
            params["files"] = files
        elif attachments:
            params["attachments"] = attachments
        return params

    @staticmethod
    def _build_queued_chat_send_message(
        msg: "Message",
        new_input: str,
        attachments: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> "Message":
        from jiuwenclaw.schema.message import Message, ReqMethod

        new_req_id = f"req_{int(time.time() * 1000):x}_{msg.id}"
        params = MessageHandler._build_supplement_chat_send_params(
            new_input,
            msg.session_id or "",
            files=files,
            attachments=attachments,
        )
        return Message(
            id=new_req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
        )

    async def _process_non_stream_request(self, msg: "Message", env: "E2AEnvelope") -> None:
        """执行单次非流式 Agent 请求并将结果写入 robot_messages（供串行或后台任务复用）。"""
        self._requests_started_total += 1
        try:
            resp = await self._agent_client.send_request(env)
            out = self._response_to_message(
                resp,
                session_id=msg.session_id,
                request_metadata=msg.metadata,
            )
            # remote 模式：session.create/delete 成功后同步更新网关会话索引
            self._maybe_sync_session_index_on_response(msg, resp)
            await self.publish_robot_messages(out)
            logger.info(
                "[MessageHandler] Agent 响应已写入 robot_messages: request_id=%s channel_id=%s",
                resp.request_id,
                resp.channel_id,
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
        finally:
            self._requests_finished_total += 1

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
                # 处理斜杠命令
                # 处理所有通道的 Channel 控制指令（如 /ls、/view）
                if self._handle_all_channel_control(msg):
                    continue
                # 处理受控通道的 Channel 控制指令（如 /new_session、/mode、/skills list）
                if self._handle_channel_control(msg):
                    # 该消息仅用于修改 session/mode，已给 Channel 回复提示，不再转发给 Agent
                    continue

                # 将当前 Channel 的控制状态应用到消息上. 包括 session_id 和 mode
                self._apply_channel_state(msg)

                # remote：session.list 在网关读索引并响应，纳入本队列以保证与 session.create/delete 顺序一致
                # 此处仅特殊处理 remote_storage + web_channel 的情况
                if msg.req_method == ReqMethod.SESSION_LIST:
                    from jiuwenclaw.gateway.session_index import is_remote_storage
                    if is_remote_storage() and str(msg.channel_id or "").strip() == "web":
                        await self._process_remote_session_list_request(msg)
                        continue

                # 检查是否是中断请求
                # 用户回答 Agent 的审批/确认请求
                if msg.req_method == ReqMethod.CHAT_ANSWER:
                    # 先正常转发用户审批答案，再按会话自动派发排队的新输入
                    agent_msg = await self._prepare_agent_dispatch_message(msg)
                    env = self.message_to_e2a(agent_msg)
                    await self._process_non_stream_request(msg, env)
                    answer_request_id = (msg.params or {}).get("request_id")
                    if self._is_evolution_approval_request_id(answer_request_id):
                        self._clear_pending_evolution_approval(msg.session_id)
                        self._clear_session_evolution_in_progress(msg.session_id)
                        queued_payload = self._pop_queued_supplement_input(msg.session_id)
                        queued_input = str((queued_payload or {}).get("new_input") or "").strip()
                        queued_attachments = (queued_payload or {}).get("attachments")
                        queued_files = (queued_payload or {}).get("files")
                        if (
                            queued_input
                            or self._is_nonempty_list(queued_files)
                            or self._is_nonempty_list(queued_attachments)
                        ):
                            queued_msg = self._build_queued_chat_send_message(
                                msg,
                                queued_input,
                                queued_attachments if isinstance(queued_attachments, list) else None,
                                queued_files if isinstance(queued_files, list) else None,
                            )
                            self._user_messages.put_nowait(queued_msg)
                            logger.info(
                                "[MessageHandler] evolution approval answered, queued supplement dispatched: id=%s session_id=%s",
                                queued_msg.id,
                                msg.session_id,
                            )
                    continue

                if msg.req_method == ReqMethod.CHAT_CANCEL:
                    logger.info(
                        "[MessageHandler] 收到中断请求: id=%s channel_id=%s",
                        msg.id, msg.channel_id,
                    )
                    new_input = (msg.params or {}).get("new_input")
                    has_new_input = isinstance(new_input, str) and new_input.strip()
                    raw_attachments = (msg.params or {}).get("attachments")
                    supplement_attachments = (
                        raw_attachments if isinstance(raw_attachments, list) else None
                    )
                    supplement_files = self._collect_supplement_files_from_params(msg.params)
                    has_supplement_payload = bool(has_new_input or supplement_files or supplement_attachments)
                    intent = (msg.params or {}).get("intent", "cancel")

                    if has_supplement_payload:
                        if (
                            self._is_session_evolution_in_progress(msg.session_id)
                            or (
                                isinstance(msg.session_id, str)
                                and msg.session_id in self._pending_evolution_approval
                            )
                        ):
                            queued_input = new_input.strip() if isinstance(new_input, str) else ""
                            self._queue_supplement_input(
                                msg.session_id,
                                queued_input,
                                supplement_attachments,
                                supplement_files,
                            )
                            logger.info(
                                "[MessageHandler] evolution phase pending, queue supplement input: session_id=%s",
                                msg.session_id,
                            )
                            await self._send_interrupt_result_notification(
                                msg.id,
                                msg.channel_id,
                                msg.session_id,
                                "supplement",
                                message="已加入队列，等待演进完成",
                            )
                            continue

                        # 有新输入：取消旧任务 → 保留 todo → 启动新任务（非并发）

                        # 1. 取消 gateway 侧当前 session 相关的流式任务（而非所有任务）
                        tasks_to_cancel = []
                        rids_cancelled = []
                        current_sid = msg.session_id
                        for rid, task in list(self._stream_tasks.items()):
                            # 只取消与当前 session_id 关联的任务
                            if self._stream_sessions.get(rid) != current_sid:
                                continue
                            if not task.done():
                                logger.info(
                                    "[MessageHandler] supplement: 取消流式任务 request_id=%s session_id=%s",
                                    rid, current_sid,
                                )
                                task.cancel()
                                tasks_to_cancel.append(task)
                                rids_cancelled.append(rid)
                        if tasks_to_cancel:
                            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

                        # 2. 通知前端 supplement（前端据此判断 is_processing 状态）
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, "supplement",
                        )

                        # 3. 发送 supplement intent 到 AgentServer（取消任务但保留 todo）
                        #    用 await 确保 agent 侧先完成取消再启动新任务
                        from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields

                        agent_msg = await self._prepare_agent_dispatch_message(msg)
                        supplement_env = e2a_from_agent_fields(
                            request_id=f"supplement_{int(time.time() * 1000):x}",
                            channel_id=msg.channel_id,
                            session_id=agent_msg.session_id,
                            req_method=ReqMethod.CHAT_CANCEL,
                            params={"intent": "supplement", "session_id": agent_msg.session_id},
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
                        supplement_query = new_input.strip() if isinstance(new_input, str) else ""
                        prepared_supplement_files = self._normalize_files_for_agent_dispatch(
                            supplement_files,
                        )
                        if prepared_supplement_files:
                            logger.info(
                                "[MessageHandler] supplement: 附件将以 url 传递给 Agent session_id=%s files=%d",
                                msg.session_id,
                                len(prepared_supplement_files),
                            )
                        new_msg = Message(
                            id=new_req_id,
                            type="req",
                            channel_id=msg.channel_id,
                            session_id=msg.session_id,
                            params=self._build_supplement_chat_send_params(
                                supplement_query,
                                msg.session_id or "",
                                files=prepared_supplement_files,
                                attachments=supplement_attachments if not prepared_supplement_files else None,
                            ),
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
                        )

                    elif intent == "cancel":
                        await self._cancel_agent_work_for_session(msg, msg.session_id)

                    elif intent in ("pause", "resume"):
                        # 暂停/恢复：不取消流式任务，转发给 AgentServer 处理 ReAct 循环
                        agent_msg = await self._prepare_agent_dispatch_message(msg)
                        env_interrupt = self.message_to_e2a(agent_msg)
                        asyncio.create_task(self._send_interrupt_to_agent(env_interrupt))
                        # 通知前端状态变更
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, intent,
                        )

                    continue

                # ---- Inbound Pipeline（数字分身入站过滤）----
                if self._inbound_pipeline is not None and msg.req_method == ReqMethod.CHAT_SEND:
                    try:
                        should_forward = await self._inbound_pipeline.apply(msg)
                    except Exception:
                        logger.exception("Inbound pipeline error, fallback to forwarding")
                    else:
                        if not should_forward:
                            continue  # 不相关消息，跳过

                # ---- Resolve @file references in chat.send content ----
                if msg.req_method == ReqMethod.CHAT_SEND and msg.params:
                    content = msg.params.get("query") or msg.params.get("content") or ""
                    attachments = msg.params.get("attachments")
                    cwd = None
                    if isinstance(msg.metadata, dict):
                        cwd = msg.metadata.get("cwd")
                    enriched = content
                    if attachments:
                        enriched = self._resolve_structured_attachments(
                            content,
                            attachments,
                            cwd=cwd,
                        )
                    elif content and "@" in content:
                        enriched = self._resolve_at_file_references(content, cwd=cwd)
                    if enriched != content:
                        msg.params = dict(msg.params)
                        msg.params["query"] = enriched
                        if "content" in msg.params:
                            msg.params["content"] = enriched
                        logger.info(
                            "[MessageHandler] attachments resolved in chat.send: id=%s",
                            msg.id,
                        )

                logger.info(
                    "[MessageHandler] 从 user_messages 取出，发往 AgentServer: id=%s channel_id=%s is_stream=%s",
                    msg.id, msg.channel_id, msg.is_stream,
                )
                # remote 模式：用户 chat.send 时在网关索引记录 role=user 预览
                self._maybe_update_session_index_on_user_msg(msg)
                msg = self._prepare_message_files_for_dispatch(msg)
                agent_msg = await self._prepare_agent_dispatch_message(msg)
                await MessageHandler._trigger_before_chat_request_hook(agent_msg)
                env = self.message_to_e2a(agent_msg)

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
                        self._stream_metadata[stream_rid] = msg.metadata
                        self._stream_modes[stream_rid] = (
                            msg.params.get("mode", "plan") if isinstance(msg.params, dict) else "plan"
                        )
                        logger.info(
                            "[MessageHandler] Stream 任务已启动（后台运行）: request_id=%s channel_id=%s 当前并发=%d",
                            stream_rid, msg.channel_id, len(self._stream_tasks),
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
        self._requests_started_total += 1
        try:
            async for chunk in self._agent_client.send_request_stream(env):
                # 跳过终止 chunk（仅作为流结束信号，不含实际数据）
                if self._is_terminal_stream_chunk(chunk):
                    logger.debug(
                        "[MessageHandler] 跳过终止 chunk: request_id=%s",
                        chunk.request_id,
                    )
                    continue

                # 修复A：过滤 keepalive 心跳 chunk，不下发到前端
                if isinstance(chunk.payload, dict) and chunk.payload.get("event_type") == "keepalive":
                    logger.debug(
                        "[MessageHandler] 过滤 keepalive chunk: request_id=%s",
                        chunk.request_id,
                    )
                    continue

                if isinstance(chunk.payload, dict):
                    event_type = chunk.payload.get("event_type")
                    if event_type == "chat.evolution_status":
                        status = str(chunk.payload.get("status", "")).strip().lower()
                        if status == "start":
                            self._mark_session_evolution_in_progress(session_id)
                            logger.info(
                                "[MessageHandler] evolution status start: session_id=%s request_id=%s",
                                session_id,
                                rid,
                            )
                        elif status == "end":
                            self._clear_session_evolution_in_progress(session_id)
                            logger.info(
                                "[MessageHandler] evolution status end: session_id=%s request_id=%s",
                                session_id,
                                rid,
                            )
                    approval_request_id = chunk.payload.get("request_id")
                    if (
                        event_type == "chat.ask_user_question"
                        and self._is_evolution_approval_request_id(approval_request_id)
                    ):
                        self._mark_pending_evolution_approval(session_id, approval_request_id)
                        logger.info(
                            "[MessageHandler] evolution approval detected: session_id=%s request_id=%s",
                            session_id,
                            approval_request_id,
                        )

                    # 处理文件下载事件（分布式模式）
                    if event_type in FILE_TRANSFER_EVENT_TYPES:
                        await self._handle_file_transfer_event(
                            event_type, chunk.payload, session_id, channel_id, request_metadata
                        )
                        continue

                    if event_type == "cron.response":
                        await self._handle_cron_push_payload(
                            payload=dict(chunk.payload),
                            request_id=rid,
                            channel_id=channel_id,
                            session_id=session_id,
                            metadata=request_metadata,
                        )
                        continue

                    # 检查是否是 processing_status=false 事件
                    if event_type == "chat.processing_status":
                        if chunk.payload.get("is_processing") is False:
                            has_processing_status_false = True

                # 携带 request metadata，供 Feishu/Xiaoyi 用平台身份回发
                out = self._chunk_to_message(
                    chunk,
                    session_id=session_id,
                    metadata=request_metadata,
                )
                await self.publish_robot_messages(out)
                logger.debug(
                    "[MessageHandler] Stream chunk 已写入 robot_messages: request_id=%s event_type=%s",
                    chunk.request_id, out.event_type,
                )
            logger.info(
                "[MessageHandler] Stream 正常完成: request_id=%s",
                rid,
            )
        except asyncio.CancelledError:
            cancelled = True
            logger.info(
                "[MessageHandler] Stream 被取消: request_id=%s",
                rid,
            )
            await self._publish_stream_cancelled_final(
                rid, channel_id, session_id, request_metadata,
            )
            raise  # 重新抛出，让调用者知道任务被取消
        finally:
            # 清理状态
            self._stream_tasks.pop(rid, None)
            self._stream_sessions.pop(rid, None)
            self._stream_metadata.pop(rid, None)
            self._stream_modes.pop(rid, None)
            if session_id is not None and session_id not in self._stream_sessions.values():
                # Fallback cleanup when stream exits unexpectedly without evolution end signal.
                self._clear_session_evolution_in_progress(session_id)
            logger.debug(
                "[MessageHandler] Stream 任务状态已清理: request_id=%s",
                rid,
            )
            self._requests_finished_total += 1
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
                    # 文件接收成功
                    file_path = result.get("file_path", "")
                    filename = Path(file_path).name
                    logger.info(
                        "[MessageHandler] 文件下载完成: transfer_id=%s path=%s channel_id=%s",
                        payload.get("transfer_id"),
                        file_path,
                        channel_id,
                    )

                    # 检查是否应该推送到 Web Server：仅当 channel_id 为 "web" 且设置了 AGENT_RUNTIME 时
                    agent_runtime = os.getenv("AGENT_RUNTIME", "").strip()
                    should_push_to_web = (channel_id == "web") and (agent_runtime != "")

                    if should_push_to_web:
                        # 推送文件到 Web Server 并获取 Web Server 的下载信息
                        download_info = await self._push_file_to_web_and_get_token(
                            file_path, filename, session_id or ""
                        )
                        if not download_info:
                            logger.error(
                                "[MessageHandler] AGENT_RUNTIME=%s，推送文件到 Web Server 失败，跳过发送 chat.file 事件: %s",
                                agent_runtime,
                                filename,
                            )
                            return
                        logger.info(
                            "[MessageHandler] AGENT_RUNTIME=%s，已推送文件到 Web Server, filename: %s",
                            agent_runtime,
                            filename,
                        )
                    else:
                        # 未设置 AGENT_RUNTIME，使用 Gateway 本地的 Token
                        from jiuwenclaw.agentserver.tools.web_file_download import build_file_download_info
                        download_info = build_file_download_info(
                            file_path=file_path,
                            file_name=filename,
                            session_id=session_id or "",
                        )

                    # 发送 chat.file 事件到 Channel（使用 Web Server 或 Gateway 的 Token）
                    from jiuwenclaw.schema.message import Message, EventType

                    files_payload = [
                        {
                            "path": file_path,
                            "name": download_info["name"],
                            "size": download_info["size"],
                            "mime_type": download_info["mime_type"],
                            "download_url": download_info["download_url"],
                            "download_token": download_info["download_token"],
                            "expires_at": download_info.get("expires_at"),
                        }
                    ]

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
                        "[MessageHandler] 已发送 chat.file 事件: channel_id=%s file=%s download_url=%s",
                        channel_id,
                        file_path,
                        download_info["download_url"],
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

    async def _push_file_to_web_and_get_token(
        self,
        file_path: str,
        filename: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """将文件推送到 Web Server 并获取 Web Server 的下载 Token.

        Args:
            file_path: Gateway 本地文件路径
            filename: 文件名
            session_id: 会话ID

        Returns:
            Web Server 返回的下载信息，包含 download_url、download_token 等
            如果推送失败，返回 None
        """
        import aiohttp

        # 在 K8s 同一集群中，Service 名称可直接通过 DNS 解析
        # 格式：<service-name>（同 namespace）
        web_server_url = "http://jiuwenclaw-web-nodeport:5173"
        logger.warning(
            "[MessageHandler] 使用默认 K8s Service URL: %s",
            web_server_url,
        )
        
        push_endpoint = f"{web_server_url}/file-api/push"
        
        try:
            # 读取文件内容
            file_size = os.path.getsize(file_path)
            with open(file_path, "rb") as f:
                file_content = f.read()

            # 构建 multipart 表单数据
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file",
                file_content,
                filename=filename,
                content_type="application/octet-stream"
            )
            form_data.add_field("session_id", session_id)
            form_data.add_field("filename", filename)
            form_data.add_field("file_size", str(file_size))

            # 发送 HTTP POST 请求
            timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(push_endpoint, data=form_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("success"):
                            logger.info(
                                "[MessageHandler] 成功推送文件到 Web Server 并获取 Token: %s, download_url=%s",
                                filename,
                                result.get("download_url"),
                            )
                            return {
                                "name": filename,
                                "size": file_size,
                                "mime_type": "application/octet-stream",
                                "download_url": result.get("download_url"),
                                "download_token": result.get("download_token"),
                                "expires_at": result.get("expires_at"),
                            }
                        else:
                            logger.error(
                                "[MessageHandler] Web Server 返回错误: %s",
                                result.get("error"),
                            )
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(
                            "[MessageHandler] 推送文件到 Web Server 失败: %s, status=%d, error=%s",
                            filename,
                            response.status,
                            error_text,
                        )
                        return None
        except Exception as e:
            logger.error(
                "[MessageHandler] 推送文件到 Web Server 异常: %s, error: %s",
                filename,
                e,
                exc_info=True,
            )
            return None

    def _should_transfer_files(self, env: "E2AEnvelope") -> bool:
        """判断是否需要进行分布式文件传输.

        Args:
            env: E2AEnvelope 信封

        Returns:
            True 如果需要传输文件
        """
        # 企业级部署（AGENT_RUNTIME）：附件经 MinIO URL 传递，不做 Gateway→AgentServer 本地文件传输
        if os.getenv("AGENT_RUNTIME", "").strip():
            return False

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

        _ft_svc = str(env.service_id or "").strip()
        _ft_ag = str(env.agent_id or "").strip()

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
                    service_id=str(ft_params.get("service_id") or _ft_svc or ""),
                    agent_id=str(ft_params.get("agent_id") or _ft_ag or ""),
                )
                return await self._agent_client.file_transfer_start(start_params)
            elif method == FILE_TRANSFER_CHUNK:
                return await self._agent_client.file_transfer_chunk(
                    transfer_id=ft_params.get("transfer_id", ""),
                    chunk_index=ft_params.get("chunk_index", 0),
                    base64_data=ft_params.get("base64_data", ""),
                    chunk_size=ft_params.get("chunk_size", 0),
                    channel_id=env.channel or "",
                    service_id=str(ft_params.get("service_id") or _ft_svc or ""),
                    agent_id=str(ft_params.get("agent_id") or _ft_ag or ""),
                    session_id=str(ft_params.get("session_id") or env.session_id or ""),
                )
            elif method == FILE_TRANSFER_COMPLETE:
                return await self._agent_client.file_transfer_complete(
                    transfer_id=ft_params.get("transfer_id", ""),
                    sha256=ft_params.get("sha256", ""),
                    channel_id=env.channel or "",
                    service_id=str(ft_params.get("service_id") or _ft_svc or ""),
                    agent_id=str(ft_params.get("agent_id") or _ft_ag or ""),
                    session_id=str(ft_params.get("session_id") or env.session_id or ""),
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
                        service_id=_ft_svc,
                        agent_id=_ft_ag,
                    )

                    if result.get("success"):
                        # 传输成功，更新路径为 AgentServer 端路径
                        new_path = result.get("file_path", "")
                        logger.info(
                            "[MessageHandler] 文件传输成功: local=%s -> remote=%s",
                            local_path,
                            new_path,
                        )
                        # 保留其他元数据，更新 path、name 和 size
                        # 关键修复：name 必须与 AgentServer 端实际存储的文件名一致，确保 Agent 能精确访问文件
                        updated_info = dict(file_info)
                        updated_info["path"] = new_path
                        updated_info["name"] = os.path.basename(new_path)
                        updated_info["size"] = result.get("file_size", file_info.get("size", 0))
                        updated_info["_transferred"] = True  # 标记已传输
                        updated_info["_original_path"] = local_path  # 保留原始路径
                        updated_info["_original_name"] = file_info.get("name", "")  # 保留飞书原始文件名（用于展示）
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

            # 更新 params.files；未成功传输的条目保留 url，去掉 Gateway 无效 path
            params["files"] = self._normalize_files_for_agent_dispatch(updated_files) or updated_files

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
                "intent": "cancel",
                "success": True,
                "message": "任务已取消",
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
        self,
        request_id: str,
        channel_id: str,
        session_id: str | None,
        intent: str,
        message: str | None = None,
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
                "message": message or messages_map.get(intent, "任务已中断"),
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
                "is_complete": not is_processing
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
        self._stream_metadata.clear()
        self._stream_modes.clear()
        self._session_evolution_in_progress.clear()
        self._pending_evolution_approval.clear()
        self._queued_supplement_input.clear()

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

    @property
    def stream_tasks_size(self) -> int:
        return len(self._stream_tasks)

    @property
    def requests_started_total(self) -> int:
        return self._requests_started_total

    @property
    def requests_finished_total(self) -> int:
        return self._requests_finished_total
