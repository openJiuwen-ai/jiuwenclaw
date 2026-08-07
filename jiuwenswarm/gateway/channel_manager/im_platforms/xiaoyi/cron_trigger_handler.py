"""Xiaoyi cron Trigger bridge for device-initiated cron runs.

当设备端 cron 到时间后，会通过 WebSocket 发来一个请求（报文1），
顶层 ``events`` 数组里含一个 ``System/UnfinishedTask`` 事件，
其 ``payload.agentIdList[].data``（JSON 字符串）里嵌套着
``{header:{name:"webhook...",namespace:"Common"},payload:{cronId,cronTitle,pushDataId}}``。
顶层 ``session`` 含 sessionId、conversationId（= dialogPageId）、agentId、uid 等。

本模块负责：
1. 从入站消息中识别 ``System/UnfinishedTask`` 事件并解析嵌套的 webhook trigger
2. 解析出 cronId / cronTitle / pushDataId / dialogPageId / session 等
3. 触发对应 cron job 执行（run_now_and_wait），等结果产出后
4. 构造报文5格式的 ``DisplayStreamingText`` + ``DisplayCachePushData`` directives
   通过 WebSocket 回发给设备

与 ``cron_query_handler`` 同层，在 ``_handle_raw_message`` 中优先拦截，
不落入普通 message/stream 分发。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CronTriggerContext:
    """从报文1解析出的 Trigger 事件上下文."""
    cron_id: str
    cron_title: str
    push_data_id: str
    # session 相关（来自报文1顶层 session 字段 + agentIdList 条目）
    session_id: str          # 顶层 session.sessionId（WS 连接 id）
    conversation_id: str     # session.conversationId = dialogPageId
    dialog_page_id: str      # 同 conversation_id
    device_id: str
    agent_id: str            # session.agentId 或 agentIdList[].agentId
    uid: str
    interaction_id: int      # session.interactionId 或 agentIdList[].interactionId
    request_id: str          # session.requestId（用于 streamingTextId）
    # JSON-RPC 关联
    rpc_id: str              # message.id（JSON-RPC request id，若有）
    task_id: str             # params.id（A2A task id，若有）
    # webhook trigger header name（如 webhook6f94791a554b4b64a45）
    webhook_name: str


def _walk(value: Any):
    """递归遍历 dict/list/JSON字符串，yield 所有 dict."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            yield from _walk(json.loads(value))
        except json.JSONDecodeError:
            return


def _find_cron_trigger_data(message: dict[str, Any]) -> dict[str, Any] | None:
    """在消息中查找嵌套的 cron webhook trigger 数据.

    报文1的 cron trigger 在 ``events[].payload.agentIdList[].data``（JSON 字符串）里，
    data 解析后含 ``{header:{name:"webhook...",namespace:"Common"},payload:{cronId,...}}``。
    本函数遍历所有 events，找到含 cronId 的 webhook trigger data 并返回其解析后的 dict。
    """
    events = message.get("events")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        agent_id_list = payload.get("agentIdList")
        if not isinstance(agent_id_list, list):
            continue
        for agent_info in agent_id_list:
            if not isinstance(agent_info, dict):
                continue
            data_str = agent_info.get("data")
            if not isinstance(data_str, str) or not data_str.strip():
                continue
            try:
                data = json.loads(data_str)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            header = data.get("header")
            data_payload = data.get("payload")
            if not isinstance(header, dict) or not isinstance(data_payload, dict):
                continue
            # 识别 webhook trigger：header.namespace == "Common"，payload 含 cronId
            if header.get("namespace") == "Common" and data_payload.get("cronId"):
                return data
    return None


def _resolve_session_fields(message: dict[str, Any]) -> dict[str, Any]:
    """从报文1顶层 session 字段解析 session 相关信息.

    报文1的 session 字段含 sessionId、conversationId、dialogPageId、
    deviceId、agentId、uid、interactionId、requestId 等。
    """
    session = message.get("session")
    if not isinstance(session, dict):
        params = message.get("params")
        if isinstance(params, dict):
            session = params.get("session")
    if not isinstance(session, dict):
        session = {}

    return {
        "session_id": str(session.get("sessionId") or ""),
        "conversation_id": str(session.get("conversationId") or ""),
        "dialog_page_id": str(
            session.get("dialogPageId") or session.get("conversationId") or ""
        ),
        "device_id": str(session.get("deviceId") or ""),
        "agent_id": str(
            session.get("receiverAgentId")
            or session.get("agentId")
            or ""
        ),
        "uid": str(session.get("uid") or ""),
        "interaction_id": int(session.get("interactionId") or 0),
        "request_id": str(session.get("requestId") or ""),
    }


def _resolve_rpc_ids(message: dict[str, Any]) -> dict[str, str]:
    """解析 JSON-RPC 关联 id 和 A2A task id."""
    rpc_id = str(message.get("id") or "")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    task_id = str(params.get("id") or "")
    return {"rpc_id": rpc_id, "task_id": task_id}


def _extract_agent_id_list_info(message: dict[str, Any]) -> dict[str, Any]:
    """从 agentIdList 条目提取 sessionId/dialogPageId/interactionId/agentId.

    这些字段可能与顶层 session 不同（agentIdList 是具体 agent 的会话信息），
    优先用 agentIdList 里的值补充。
    """
    events = message.get("events")
    if not isinstance(events, list):
        return {}
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        agent_id_list = payload.get("agentIdList")
        if not isinstance(agent_id_list, list):
            continue
        for agent_info in agent_id_list:
            if not isinstance(agent_info, dict):
                continue
            data_str = agent_info.get("data")
            if not isinstance(data_str, str):
                continue
            try:
                data = json.loads(data_str)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("payload"), dict):
                if data["payload"].get("cronId"):
                    return {
                        "session_id": str(agent_info.get("sessionId") or ""),
                        "dialog_page_id": str(agent_info.get("dialogPageId") or ""),
                        "interaction_id": int(agent_info.get("interactionId") or 0),
                        "agent_id": str(agent_info.get("agentId") or ""),
                    }
    return {}


def extract_cron_trigger(message: dict[str, Any]) -> CronTriggerContext | None:
    """从入站消息中识别 cron Trigger 事件，返回上下文.

    识别条件：
    1. 消息含 ``System/UnfinishedTask`` event，其 ``agentIdList[].data``
       是 JSON 字符串，含 ``{header:{namespace:"Common"},payload:{cronId,...}}``
    2. data.payload 含 cronId（我们的 job_id）

    返回 None 表示不是 cron trigger 事件。
    """
    trigger_data = _find_cron_trigger_data(message)
    if trigger_data is None:
        return None

    trigger_payload = trigger_data.get("payload")
    if not isinstance(trigger_payload, dict):
        return None

    cron_id = str(trigger_payload.get("cronId") or "").strip()
    if not cron_id:
        return None

    cron_title = str(trigger_payload.get("cronTitle") or "").strip()
    push_data_id = str(trigger_payload.get("pushDataId") or "").strip()

    webhook_header = trigger_data.get("header") or {}
    webhook_name = str(webhook_header.get("name") or "")

    session_fields = _resolve_session_fields(message)
    agent_info = _extract_agent_id_list_info(message)

    # 优先用 agentIdList 里的 session 信息（更具体），回退到顶层 session
    session_id = agent_info.get("session_id") or session_fields["session_id"]
    dialog_page_id = agent_info.get("dialog_page_id") or session_fields["dialog_page_id"]
    interaction_id = agent_info.get("interaction_id") or session_fields["interaction_id"]
    agent_id = agent_info.get("agent_id") or session_fields["agent_id"]
    conversation_id = session_fields["conversation_id"] or dialog_page_id

    rpc_ids = _resolve_rpc_ids(message)

    return CronTriggerContext(
        cron_id=cron_id,
        cron_title=cron_title,
        push_data_id=push_data_id,
        session_id=session_id,
        conversation_id=conversation_id,
        dialog_page_id=dialog_page_id,
        device_id=session_fields["device_id"],
        agent_id=agent_id,
        uid=session_fields["uid"],
        interaction_id=interaction_id,
        request_id=session_fields["request_id"],
        rpc_id=rpc_ids["rpc_id"],
        task_id=rpc_ids["task_id"],
        webhook_name=webhook_name,
    )


def build_display_streaming_text_directives(
    ctx: CronTriggerContext,
    result_text: str,
    *,
    streaming_text_id: str | None = None,
    notify_id: int | None = None,
) -> list[dict[str, Any]]:
    """构造报文5的 directives 数组（DisplayStreamingText + DisplayCachePushData）.

    Args:
        ctx: Trigger 上下文（含 session/agent/cron 信息）
        result_text: cron job 执行结果正文
        streaming_text_id: 可选，若不提供则用 ``<sessionId>&<interactionId>&<seq>&01`` 格式
        notify_id: 可选，DisplayCachePushData 的 notifyId，若不提供则随机生成

    Returns:
        directives 数组，含两个指令条目
    """
    # streamingTextId: 报文5格式为 "<sessionId中间段>&<interaction>&<seq>&01"
    # 从报文5样例 "b7d5f467-0ee4-44da-b795-daed9fdcccb7&0&e853&01" 看，
    # 是 sessionId 的 UUID 段 & interactionId & seq & 01
    if streaming_text_id is None:
        # 从 session_id 提取 UUID 段（可能是 jx-xxx 或纯 UUID）
        sid_core = ctx.session_id
        if sid_core.startswith("jx-"):
            sid_core = sid_core[3:]
        streaming_text_id = f"{sid_core}&{ctx.interaction_id}&{uuid.uuid4().hex[:4]}&01"

    display_streaming_text = {
        "header": {
            "name": "DisplayStreamingText",
            "namespace": "UserInteraction",
        },
        "payload": {
            "contextText": result_text,
            "isFinal": True,
            "isStart": True,
            "streamingText": result_text,
            "streamingTextId": streaming_text_id,
            "ttsMode": "",
        },
    }

    # notifyId: 报文5样例为 554704897（int），用随机正整数
    if notify_id is None:
        notify_id = abs(uuid.uuid4().int % 1_000_000_000)

    display_cache_push_data = {
        "header": {
            "name": "DisplayCachePushData",
            "namespace": "Common",
        },
        "payload": {
            "agentId": ctx.agent_id,
            "cronTitle": ctx.cron_title,
            "dialogPageId": ctx.dialog_page_id,
            "notifyId": notify_id,
            "timestamp": int(time.time() * 1000),
        },
    }

    return [display_streaming_text, display_cache_push_data]


def build_display_streaming_text_envelope(
    ctx: CronTriggerContext,
    result_text: str,
    *,
    streaming_text_id: str | None = None,
    notify_id: int | None = None,
) -> dict[str, Any]:
    """构造报文5完整响应信封（jsonrpc/result/artifact/parts 格式）.

    返回的 dict 可直接作为 ``msgDetail`` 内容，包装在 ``agent_response`` 里
    通过 WebSocket 发送给设备。格式与现有 ``_send_text_response`` 一致，
    但 parts 用 ``kind: "data"`` + ``directives``，而非 ``kind: "text"``。
    """
    directives = build_display_streaming_text_directives(
        ctx,
        result_text,
        streaming_text_id=streaming_text_id,
        notify_id=notify_id,
    )

    return {
        "jsonrpc": "2.0",
        "id": ctx.rpc_id or f"cron-trigger-{int(time.time() * 1000)}",
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
                        "kind": "data",
                        "data": {"directives": directives},
                    }
                ],
            },
        },
    }

