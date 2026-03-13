# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MessageHandler - 消息处理抽象与双队列实现（入队经 AgentServerClient 发往 AgentServer）."""

from __future__ import annotations

import asyncio
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict

from jiuwenclaw.utils import logger


class ChannelMode(str, Enum):
    PLAN = "plan"
    AGENT = "agent"


@dataclass
class ChannelControlState:
    session_id: str | None = None
    mode: ChannelMode = ChannelMode.PLAN
if TYPE_CHECKING:
    from jiuwenclaw.gateway.agent_client import AgentServerClient
    from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
    from jiuwenclaw.schema.message import Message


# ---------- 双队列实现：入队经 AgentServerClient 发往 AgentServer ----------
class MessageHandler(ABC):
    """
    维护两个异步消息队列，入队消息通过 AgentServerClient 发送给 AgentServer：

    - _user_messages：Channel 发来的消息，由内部转发循环消费并调用 agent_client.send_request
    - _robot_messages：AgentServer 的响应，由 ChannelManager 消费并派发到对应 Channel
    """

    def __init__(self, agent_client: "AgentServerClient") -> None:
        self._agent_client = agent_client
        self._user_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._robot_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._running = False
        self._forward_task: asyncio.Task | None = None
        self._stream_tasks: dict[str, asyncio.Task] = {}  # request_id -> task
        self._stream_sessions: dict[str, str | None] = {}  # request_id -> session_id

        # per-channel 控制状态：支持 \new_session / \mode 指令（feishu/xiaoyi/dingding）
        self._control_channels = {"feishu", "xiaoyi", "dingtalk"}
        self._channel_states: Dict[str, ChannelControlState] = {}

        # 直接使用 jiuwenclaw.config 的 get_config_raw/set_config/update_channel_in_config
        # 避免在此处重复实现 config 模块加载逻辑。
        from jiuwenclaw.config import get_config_raw, update_channel_in_config

        self._get_config_raw = get_config_raw
        self._update_channel_in_config = update_channel_in_config

        self._load_channel_states_from_config()

    def handle_message(self, msg: "Message") -> None:
        """Channel 同步回调：将消息放入 user_messages 队列，由转发循环发给 AgentServer."""
        self._user_messages.put_nowait(msg)
        logger.info(
            "[MessageHandler] _user_messages 入队: id=%s channel_id=%s session_id=%s",
            msg.id, msg.channel_id, msg.session_id,
        )

    # ---------- Channel 控制状态：\new_session / \mode ----------

    def _load_channel_states_from_config(self) -> None:
        """从 config.yaml 初始化各 Channel 的默认 session_id / mode."""
        try:
            cfg: Dict[str, Any] = self._get_config_raw()
        except Exception:  # noqa: BLE001
            cfg = {}
        channels_cfg = cfg.get("channels") or {}
        for ch in self._control_channels:
            ch_cfg = channels_cfg.get(ch) or {}
            sid_raw = ch_cfg.get("default_session_id") or ""
            sid = str(sid_raw).strip() or None
            # 若未在 config 中指定默认 session_id，则为该 channel 生成一个带时间戳的新 session_id，
            # 这样 feishu/xiaoyi/dingtalk 在未执行 \new_session 之前，消息的 session_id 也是
            # `<channel>_<ts_hex>_<rand>` 这种可解析时间戳的格式。
            if not sid:
                sid = self._generate_channel_session_id(ch)
            mode_raw = str(ch_cfg.get("default_mode") or "plan").strip().lower()
            mode = ChannelMode.AGENT if mode_raw == "agent" else ChannelMode.PLAN
            self._channel_states[ch] = ChannelControlState(session_id=sid, mode=mode)

    def _save_channel_state_to_config(self, channel_id: str) -> None:
        """将指定 Channel 的 session_id / mode 写回 config.yaml."""
        state = self._channel_states.get(channel_id)
        if state is None:
            return
        try:
            self._update_channel_in_config(
                channel_id,
                {
                    "default_session_id": state.session_id or "",
                    "default_mode": state.mode.value,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存 Channel 控制状态到 config 失败: %s", exc)

    def _generate_channel_session_id(self, channel_id: str) -> str:
        """为指定 channel 生成新的 session_id."""
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"{channel_id}_{ts}_{suffix}"

    async def _send_channel_notice(self, channel_id: str, session_id: str | None, text: str) -> None:
        """向指定 channel 发送一条系统提示消息."""
        from jiuwenclaw.schema.message import Message, EventType

        msg = Message(
            id=f"sys-{int(time.time() * 1000)}",
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"content": text},
            event_type=EventType.CHAT_FINAL,
        )
        await self.publish_robot_messages(msg)

    def _handle_channel_control(self, msg: "Message") -> bool:
        """处理 \new_session / \mode 指令.

        Returns:
            True: 该消息是控制指令，已处理完毕，不需要转发给 Agent。
            False: 非控制指令，继续正常处理。
        """
        ch = msg.channel_id
        if ch not in self._control_channels:
            return False

        params = msg.params or {}
        text = str(params.get("query") or params.get("content") or "").strip()
        if not text:
            return False

        logger.info('this is in _handle_channel_control, channel id is %s, text is %s, "\\new_session" in text is %s', ch, text, str("\\new_session" in text))
        # \new_session：重置当前 Channel 的会话 ID
        if "/new_session" == text:
            state = self._channel_states.get(ch) or ChannelControlState()
            new_sid = self._generate_channel_session_id(ch)
            state.session_id = new_sid
            self._channel_states[ch] = state
            self._save_channel_state_to_config(ch)
            # 给当前会话回复提示（用原有 session_id）
            asyncio.create_task(
                self._send_channel_notice(ch, msg.session_id, f"[收到 CLI 指令], session_id 已变更为 {new_sid}")
            )
            return True
        elif "/new_session" in text:
            asyncio.create_task(
                self._send_channel_notice(ch, msg.session_id, f"非法指令")
            )
            return True

        # \mode plan / \mode agent
        if text == "/mode plan" or text == "/mode agent": 
            parts = text.split()
            if len(parts) >= 2 and parts[1] in ("plan", "agent"):
                state = self._channel_states.get(ch) or ChannelControlState()
                state.mode = ChannelMode.AGENT if parts[1] == "agent" else ChannelMode.PLAN
                self._channel_states[ch] = state
                self._save_channel_state_to_config(ch)
                asyncio.create_task(
                    self._send_channel_notice(ch, msg.session_id, f"[收到 CLI 指令], mode 已变更为 {state.mode.value}")
                )
                return True
        elif "/mode" in text:
            asyncio.create_task(
                self._send_channel_notice(ch, msg.session_id, f"非法指令")
            )
            return True

        return False

    def _apply_channel_state(self, msg: "Message") -> None:
        """将当前 Channel 的控制状态应用到消息上（session_id / mode）."""
        ch = msg.channel_id
        state = self._channel_states.get(ch)
        if not state:
            return

        # 对 feishu/xiaoyi/dingtalk 强制覆盖 session_id；web 等保持原有行为
        if ch in self._control_channels and state.session_id:
            msg.session_id = state.session_id

        # 将 mode 写入 params，后续 AgentRequest 会从 params["mode"] 里读取
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
    def _message_to_request(msg: "Message") -> "AgentRequest":
        from jiuwenclaw.schema.agent import AgentRequest

        return AgentRequest(
            request_id=msg.id,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            req_method=msg.req_method,
            params=msg.params,
            is_stream=msg.is_stream,
            timestamp=msg.timestamp,
            metadata=msg.metadata,
        )


    @staticmethod
    def _response_to_message(resp: "AgentResponse", session_id: str | None) -> "Message":
        from jiuwenclaw.schema.message import Message, EventType

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
                        metadata=resp.metadata,
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
            metadata=resp.metadata,
        )

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
                
         
                # 先处理 Channel 控制指令（仅 feishu/xiaoyi/dingtalk）
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
                        from jiuwenclaw.schema.agent import AgentRequest as _AgentReq
                        supplement_req = _AgentReq(
                            request_id=f"supplement_{int(time.time() * 1000):x}",
                            channel_id=msg.channel_id,
                            session_id=msg.session_id,
                            req_method=ReqMethod.CHAT_CANCEL,
                            params={"intent": "supplement"},
                            is_stream=False,
                            timestamp=time.time(),
                        )
                        try:
                            await self._send_interrupt_to_agent(supplement_req)
                        except Exception:
                            pass  # 即使失败也继续启动新任务

                        # 4. 入队新任务（单一任务，不并发）
                        from jiuwenclaw.schema.message import Message

                        new_req_id = f"req_{int(time.time() * 1000):x}_{msg.id}"
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
                        )
                        self._user_messages.put_nowait(new_msg)
                        logger.info(
                            "[MessageHandler] supplement: 旧任务已取消，新任务已入队: id=%s session_id=%s",
                            new_msg.id, msg.session_id,
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
                        req = self._message_to_request(msg)
                        asyncio.create_task(self._send_interrupt_to_agent(req))

                    elif intent in ("pause", "resume"):
                        # 暂停/恢复：不取消流式任务，转发给 AgentServer 处理 ReAct 循环
                        req = self._message_to_request(msg)
                        asyncio.create_task(self._send_interrupt_to_agent(req))
                        # 通知前端状态变更
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, intent,
                        )

                    continue

                logger.info(
                    "[MessageHandler] 从 user_messages 取出，发往 AgentServer: id=%s channel_id=%s is_stream=%s",
                    msg.id, msg.channel_id, msg.is_stream,
                )
                req = self._message_to_request(msg)
                try:
                    if req.is_stream:
                        # 流式处理：启动后台任务，支持多任务并发
                        # 通知前端新任务开始处理
                        await self._send_processing_status(
                            req.request_id, msg.session_id, msg.channel_id, is_processing=True,
                        )
                        task = asyncio.create_task(
                            self._process_stream(req, msg.session_id)
                        )
                        self._stream_tasks[req.request_id] = task
                        self._stream_sessions[req.request_id] = msg.session_id
                        logger.info(
                            "[MessageHandler] Stream 任务已启动（后台运行）: request_id=%s channel_id=%s 当前并发=%d",
                            req.request_id, req.channel_id, len(self._stream_tasks),
                        )
                        # 不 await，让流式任务在后台运行，_forward_loop 继续处理下一个消息
                    else:
                        # 非流式处理：单个响应写入 robot_messages
                        resp = await self._agent_client.send_request(req)
                        out = self._response_to_message(resp, session_id=msg.session_id)
                        await self.publish_robot_messages(out)
                        logger.info(
                            "[MessageHandler] Agent 响应已写入 robot_messages: request_id=%s channel_id=%s",
                            resp.request_id, resp.channel_id,
                        )
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

    async def _process_stream(self, req: "AgentRequest", session_id: str | None) -> None:
        """处理流式请求，逐个 chunk 写入 robot_messages.

        这个方法被包装为 Task，在后台运行，可以被随时取消。
        """
        cancelled = False
        try:
            async for chunk in self._agent_client.send_request_stream(req):
                # 跳过终止 chunk（仅作为流结束信号，不含实际数据）
                if chunk.is_complete and not chunk.payload:
                    logger.debug(
                        "[MessageHandler] 跳过终止 chunk: request_id=%s",
                        chunk.request_id,
                    )
                    continue
                # 携带 request metadata，供 Feishu/Xiaoyi 用平台身份回发
                out = self._chunk_to_message(
                    chunk, session_id=session_id, metadata=req.metadata
                )
                await self.publish_robot_messages(out)
                logger.debug(
                    "[MessageHandler] Stream chunk 已写入 robot_messages: request_id=%s event_type=%s",
                    chunk.request_id, out.event_type,
                )
            logger.info(
                "[MessageHandler] Stream 正常完成: request_id=%s",
                req.request_id,
            )
        except asyncio.CancelledError:
            cancelled = True
            logger.info(
                "[MessageHandler] Stream 被取消: request_id=%s",
                req.request_id,
            )
            raise  # 重新抛出，让调用者知道任务被取消
        finally:
            # 清理状态
            self._stream_tasks.pop(req.request_id, None)
            self._stream_sessions.pop(req.request_id, None)
            logger.debug(
                "[MessageHandler] Stream 任务状态已清理: request_id=%s",
                req.request_id,
            )
            # 所有流式任务正常结束后，通知前端全部处理完成
            if not cancelled and not self._stream_tasks:
                await self._send_processing_status(
                    req.request_id, session_id, req.channel_id, is_processing=False,
                )
                logger.info(
                    "[MessageHandler] 所有流式任务已完成，已发送 is_processing=false: session_id=%s",
                    session_id,
                )

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

    async def _send_interrupt_to_agent(self, req: "AgentRequest") -> None:
        """Fire-and-forget: 发送中断请求到 AgentServer，不阻塞转发循环."""
        try:
            resp = await self._agent_client.send_request(req)
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

    async def stop_forwarding(self) -> None:
        """停止转发任务."""
        self._running = False

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
