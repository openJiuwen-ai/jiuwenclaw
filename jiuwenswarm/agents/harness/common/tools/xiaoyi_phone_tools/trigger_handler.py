# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Trigger 事件处理器 - Common/Trigger 点击推送触发.

与 xy_channel trigger-handler.ts + bot.ts 中 Trigger 分支对齐。

设备端场景：用户在手机侧点击定时任务的推送消息时，会通过 WebSocket
发来一个报文，顶层 events 数组里含一个 ``Common/Trigger`` 事件，
其 ``payload.dataMap`` 含 ``pushDataId``。

本模块负责：
1. 从入站 A2A 消息中识别 ``Common/Trigger`` 事件并解析出 pushDataId
2. 通过 ``pushdata_manager.get_push_data_by_id`` 查询预存的推送内容
3. 构造标准 a2a 文本消息响应（``kind: "text"`` 的 artifact-update），
   通过 WebSocket 回发给设备（直接返回推送内容，不走 agent 流程）

与 ``cron_trigger_handler``（System/UnfinishedTask，设备 cron 到时间自动触发、
执行 cron job 后回发结果）的区别：
- ``cron_trigger_handler``：设备 cron 定时器到点 → 触发 cron job 执行 → 回发结果
- ``trigger_handler``（本模块）：用户点击推送 → 直接读取已缓存的 pushData → 回发
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .pushdata_manager import get_push_data_by_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerEventContext:
    """从 Common/Trigger 事件解析出的上下文.

    与 xy_channel parser.ts extractTriggerData + parseA2AMessage 返回值对齐：
    - pushDataId 来自 event.payload.dataMap.pushDataId（extractTriggerData）
    - sessionId / taskId / rpc_id 来自 A2A JSON-RPC 信封（parseA2AMessage）
    """
    push_data_id: str       # payload.dataMap.pushDataId（核心字段）
    cron_id: str            # payload.dataMap.cronId（可选）
    cron_title: str         # payload.dataMap.cronTitle（可选）
    # session 相关（来自 A2A 信封 params.sessionId / session 字段，用于回发）
    session_id: str          # params.sessionId 或 顶层 session.sessionId
    conversation_id: str     # session.conversationId = dialogPageId
    dialog_page_id: str      # 同 conversation_id
    agent_id: str            # session.receiverAgentId 或 session.agentId
    # JSON-RPC 关联
    rpc_id: str              # message.id（JSON-RPC request id，对应 TS messageId）
    task_id: str             # params.id（A2A task id，对应 TS taskId）


def _resolve_session_fields(message: dict[str, Any]) -> dict[str, Any]:
    """从 A2A 消息信封解析 session 相关信息.

    与 xy_channel parser.ts parseA2AMessage + websocket.ts sessionId 提取对齐：
    sessionId 优先级（参考 TS websocket.ts line 630 + xiaoyi_connect.py
    ``_gui_response_session_id`` line 50-67）：
      1. message.sessionId（顶层，报文4可能直接带）
      2. params.sessionId（标准 A2A JSON-RPC 位置，TS parseA2AMessage 走这条）
      3. session.sessionId（报文1/2 顶层 session 字段）
      4. content.session.sessionId（报文1/2 变体）

    注意：agentId 不从此处提取——TS parser.ts line 7 明确注释
    ``agentId is not extracted from message - it should come from config``，
    wrapper 的 agentId 由调用方传 ``config.agent_id``（见 handle_trigger_event）。
    本函数仍提取 session.receiverAgentId/agentId 仅用于日志诊断，不用于回发。
    """
    # 1. 顶层 message.sessionId（报文4可能直接带，TS websocket.ts line 630 走这条）
    top_sid = str(message.get("sessionId") or "").strip()
    if top_sid:
        # conversationId / dialogPageId 从 session 字段补充（若存在）
        session = message.get("session") if isinstance(message.get("session"), dict) else {}
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        params_session = params.get("session") if isinstance(params.get("session"), dict) else {}
        merged_session = {**session, **params_session}
        return {
            "session_id": top_sid,
            "conversation_id": str(merged_session.get("conversationId") or ""),
            "dialog_page_id": str(
                merged_session.get("dialogPageId")
                or merged_session.get("conversationId")
                or ""
            ),
            "agent_id": str(
                merged_session.get("receiverAgentId")
                or merged_session.get("agentId")
                or ""
            ),
        }

    # 2. params.sessionId（标准 A2A JSON-RPC 位置，TS parseA2AMessage �走这条）
    params = message.get("params")
    if isinstance(params, dict):
        sid = str(params.get("sessionId") or "").strip()
        if sid:
            session = params.get("session") if isinstance(params.get("session"), dict) else {}
            return {
                "session_id": sid,
                "conversation_id": str(session.get("conversationId") or ""),
                "dialog_page_id": str(
                    session.get("dialogPageId") or session.get("conversationId") or ""
                ),
                "agent_id": str(
                    session.get("receiverAgentId")
                    or session.get("agentId")
                    or ""
                ),
            }

    # 3. 顶层 message.session（报文1/2 格式）
    session = message.get("session")
    if not isinstance(session, dict):
        # content.session（报文1/2 的一种变体：顶层 content 含 session）
        content = message.get("content")
        if isinstance(content, dict):
            session = content.get("session")
    if not isinstance(session, dict):
        session = {}

    return {
        "session_id": str(session.get("sessionId") or ""),
        "conversation_id": str(session.get("conversationId") or ""),
        "dialog_page_id": str(
            session.get("dialogPageId") or session.get("conversationId") or ""
        ),
        "agent_id": str(
            session.get("receiverAgentId")
            or session.get("agentId")
            or ""
        ),
    }

    # 2. 顶层 message.session（报文1/2 格式）
    session = message.get("session")
    if not isinstance(session, dict):
        # content.session（报文1/2 的一种变体：顶层 content 含 session）
        content = message.get("content")
        if isinstance(content, dict):
            session = content.get("session")
    if not isinstance(session, dict):
        session = {}

    return {
        "session_id": str(session.get("sessionId") or ""),
        "conversation_id": str(session.get("conversationId") or ""),
        "dialog_page_id": str(
            session.get("dialogPageId") or session.get("conversationId") or ""
        ),
        "agent_id": str(
            session.get("receiverAgentId")
            or session.get("agentId")
            or ""
        ),
    }


def _resolve_rpc_ids(message: dict[str, Any]) -> dict[str, str]:
    """解析 JSON-RPC 关联 id 和 A2A task id.

    与 xy_channel parser.ts parseA2AMessage + websocket.ts line 631 对齐：
    - rpc_id = message.id（顶层，对应 TS messageId = parseA2AMessage 的 id）
    - task_id 优先级：params.id（TS parseA2AMessage taskId）→ 顶层 taskId
      （TS websocket.ts line 631 ``parsed.params?.id || parsed.taskId``）
    """
    rpc_id = str(message.get("id") or "")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    # 优先 params.id（标准 A2A 位置），回退顶层 taskId（报文4可能直接带）
    task_id = str(params.get("id") or "").strip() or str(message.get("taskId") or "").strip()
    return {"rpc_id": rpc_id, "task_id": task_id}


def _iter_events(message: dict[str, Any]) -> list[dict[str, Any]]:
    """从消息中提取所有 events（兼容多种包装位置）.

    Common/Trigger 事件可能出现在以下位置：
    - message.params.message.parts[].data.events（A2A 包装格式，xy_channel bot.ts 走这条）
    - message.content.events（报文2变体，顶层 content 含 events）
    - message.events（直接顶层）
    """
    events: list[dict[str, Any]] = []

    # 1. A2A 包装格式：params.message.parts[].data.events（TS bot.ts 走这条）
    params = message.get("params")
    if isinstance(params, dict):
        msg = params.get("message")
        if isinstance(msg, dict):
            parts = msg.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict) or part.get("kind") != "data":
                        continue
                    data = part.get("data")
                    if not isinstance(data, dict):
                        continue
                    ev = data.get("events")
                    if isinstance(ev, list):
                        events.extend(
                            e for e in ev if isinstance(e, dict)
                        )

    # 2. message.content.events
    if not events:
        content = message.get("content")
        if isinstance(content, dict):
            ev = content.get("events")
            if isinstance(ev, list):
                events.extend(ev for ev in ev if isinstance(ev, dict))

    # 3. message.events
    if not events:
        ev = message.get("events")
        if isinstance(ev, list):
            events.extend(ev for ev in ev if isinstance(ev, dict))

    return events


def extract_trigger_event(message: dict[str, Any]) -> TriggerEventContext | None:
    """从入站消息中识别 Common/Trigger 事件，返回上下文.

    与 xy_channel parser.ts extractTriggerData + parseA2AMessage 对齐：
    1. 从 parts/events 中查找 event，header.namespace == "Common"，header.name == "Trigger"
    2. event.payload.dataMap.pushDataId 存在且非空（TS extractTriggerData 只提取 pushDataId）
    3. sessionId / taskId / rpc_id 从 A2A 信封提取（TS parseA2AMessage 而非 extractTriggerData）

    返回 None 表示不是 Common/Trigger 事件（可能是 System/UnfinishedTask
    或普通消息，交给其他 handler 处理）。
    """
    events = _iter_events(message)
    if not events:
        return None

    trigger_event: dict[str, Any] | None = None
    for event in events:
        header = event.get("header")
        if not isinstance(header, dict):
            continue
        if header.get("namespace") != "Common" or header.get("name") != "Trigger":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        data_map = payload.get("dataMap")
        if not isinstance(data_map, dict):
            continue
        push_data_id = str(data_map.get("pushDataId") or "").strip()
        if push_data_id:
            trigger_event = event
            break

    if trigger_event is None:
        return None

    payload = trigger_event.get("payload") or {}
    data_map = payload.get("dataMap") or {}
    push_data_id = str(data_map.get("pushDataId") or "").strip()
    cron_id = str(data_map.get("cronId") or "").strip()
    cron_title = str(data_map.get("cronTitle") or "").strip()

    session_fields = _resolve_session_fields(message)
    rpc_ids = _resolve_rpc_ids(message)

    return TriggerEventContext(
        push_data_id=push_data_id,
        cron_id=cron_id,
        cron_title=cron_title,
        session_id=session_fields["session_id"],
        conversation_id=session_fields["conversation_id"],
        dialog_page_id=session_fields["dialog_page_id"],
        agent_id=session_fields["agent_id"],
        rpc_id=rpc_ids["rpc_id"],
        task_id=rpc_ids["task_id"],
    )


def build_trigger_response_envelope(
    ctx: TriggerEventContext,
    result_text: str,
    *,
    message_id: str | None = None,
) -> dict[str, Any]:
    """构造标准 a2a 文本消息响应信封（与 xy_channel formatter.ts sendA2AResponse 对齐）.

    返回 JSON-RPC ``{jsonrpc, id, result}``，其中 ``result`` 是
    ``artifact-update`` 事件，``artifact.parts`` 含单个 ``kind: "text"`` part。

    Args:
        ctx: Trigger 上下文（含 session/agent/push 信息）
        result_text: 回发的推送内容正文（pushData.dataDetail）
        message_id: 可选，JSON-RPC id（对应入站 message.id），不提供则用 ctx.rpc_id
    """
    return {
        "jsonrpc": "2.0",
        "id": message_id or ctx.rpc_id or f"trigger-{int(time.time() * 1000)}",
        "result": {
            "taskId": ctx.task_id,
            "kind": "artifact-update",
            "append": False,
            "lastChunk": True,
            "final": True,
            "artifact": {
                "artifactId": str(uuid.uuid4()),
                "parts": [
                    {
                        "kind": "text",
                        "text": result_text,
                    }
                ],
            },
        },
    }


async def handle_trigger_event(
    message: dict[str, Any],
    send_ws_callback: Any,
    *,
    agent_id: str = "",
) -> bool:
    """处理 Common/Trigger 事件的主入口.

    与 xy_channel bot.ts Trigger 分支 + formatter.ts sendA2AResponse +
    outbound-gateway.ts sendWsFrame 对齐：
    1. extract_trigger_event 识别 Common/Trigger
    2. get_push_data_by_id 查询预存推送内容
    3. build_trigger_response_envelope 构造标准 a2a 文本消息
    4. 通过 send_ws_callback 回发设备

    Args:
        message: 入站 WS 消息（已解析为 dict）
        send_ws_callback: 回发回调，签名 async (wrapper: dict) -> None，
                          wrapper 格式为 {msgType, agentId, sessionId, taskId, msgDetail}
        agent_id: 回发 wrapper 的 agentId，由调用方传 ``config.agent_id``。
                  TS parser.ts line 7 明确注释 ``agentId is not extracted from
                  message - it should come from config``，与 ``_send_agent_response``
                  （xiaoyi_connect.py line 2217 ``agentId: self.config.agent_id``）一致。

    Returns:
        True 表示已识别并处理（调用方应 return，不再走正常消息流）；
        False 表示不是 Common/Trigger 事件（调用方继续其他处理）。
    """
    ctx = extract_trigger_event(message)
    if ctx is None:
        return False

    # agentId 用调用方传入的 config.agent_id（与 _send_agent_response line 2217 一致），
    # 不用 ctx.agent_id（入站消息可能不带 agentId）。
    wrapper_agent_id = agent_id or ctx.agent_id
    if not wrapper_agent_id:
        logger.warning(
            "[XiaoyiChannel][Trigger_NO_AGENT_ID] pushDataId=%s "
            "调用方未传 agent_id 且入站消息无 agentId，wrapper agentId 将为空",
            ctx.push_data_id,
        )

    if not ctx.session_id:
        logger.warning(
            "[XiaoyiChannel][Trigger_NO_SESSION_ID] pushDataId=%s "
            "入站消息无 sessionId（顶层/params/session 均未提取到）",
            ctx.push_data_id,
        )
    if not ctx.task_id:
        logger.warning(
            "[XiaoyiChannel][Trigger_NO_TASK_ID] pushDataId=%s "
            "入站消息无 taskId（params.id/顶层 taskId 均未提取到）",
            ctx.push_data_id,
        )

    logger.info(
        "[XiaoyiChannel][Trigger_IN] pushDataId=%s cronId=%s cronTitle=%s "
        "session=%s agent(config=%s, ctx=%s) rpc_id=%s task_id=%s",
        ctx.push_data_id,
        ctx.cron_id,
        ctx.cron_title,
        ctx.session_id,
        agent_id,
        ctx.agent_id,
        ctx.rpc_id,
        ctx.task_id,
    )

    # 查询预存的推送内容
    push_item = get_push_data_by_id(ctx.push_data_id)
    if push_item is None:
        logger.warning(
            "[XiaoyiChannel][Trigger_NOT_FOUND] pushDataId=%s 未找到推送记录",
            ctx.push_data_id,
        )
        result_text = "[trigger] 未找到该推送对应的记录，可能已过期。"
    else:
        result_text = str(push_item.get("dataDetail") or "")
        logger.info(
            "[XiaoyiChannel][Trigger_FOUND] pushDataId=%s data_len=%s",
            ctx.push_data_id[:8],
            len(result_text),
        )

    if not result_text:
        result_text = "[trigger] 推送内容为空"

    # 构造标准 a2a 文本消息响应信封
    response_envelope = build_trigger_response_envelope(ctx, result_text)

    # 与 xy_channel outbound-gateway.ts sendWsFrame line 36-42 对齐：
    # wrapper 的 agentId 用 config.agent_id（传入），sessionId/taskId 从入站消息提取，
    # msgDetail 内追加 hostname。
    response_envelope["hostname"] = (
        os.uname().nodename if hasattr(os, "uname") else os.gethostname()
    )
    wrapper = {
        "msgType": "agent_response",
        "agentId": wrapper_agent_id,
        "sessionId": ctx.session_id,
        "taskId": ctx.task_id,
        "msgDetail": json.dumps(response_envelope, ensure_ascii=False),
    }
    try:
        result = send_ws_callback(wrapper)
        if hasattr(result, "__await__"):
            await result
        logger.info(
            "[XiaoyiChannel][Trigger_SENT] pushDataId=%s session=%s "
            "agent=%s task=%s result_len=%d",
            ctx.push_data_id[:8],
            ctx.session_id,
            wrapper_agent_id,
            ctx.task_id,
            len(result_text),
        )
    except Exception as send_err:
        logger.warning(
            "[XiaoyiChannel][Trigger_SEND_ERROR] pushDataId=%s error=%s",
            ctx.push_data_id,
            send_err,
        )

    return True

