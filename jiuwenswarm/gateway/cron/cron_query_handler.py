"""A2A CronQuery envelope dispatcher.

Implements the ``AgentEvent.CronQuery`` protocol defined in
``定时任务 RPC 接口协议文档.md``. Provides a single ``dispatch_cron_query``
entry point that:

1. Accepts an A2A-style payload ``{action, params/jobId}``
2. Routes to the appropriate ``CronController`` method
3. Returns a response payload ``{action, status, ans}`` matching the doc

Used by Web / TUI / Xiaoyi channels to unify cron RPC under one protocol.
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.gateway.cron.controller import CronController

logger = logging.getLogger(__name__)

VALID_ACTIONS = frozenset(
    {"list", "status", "runs", "add", "update", "remove", "run", "queryTimeList"}
)


async def dispatch_cron_query(
    payload: dict[str, Any],
    *,
    cron_controller: CronController | None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch an A2A CronQuery payload and return the response payload.

    Per the protocol doc:
    - Success: ``{action, status: True, ans}``
    - Error: ``{action, ans: {error: "..."}}`` (no ``status`` field)
    - queryTimeList: ``ans`` is a list (array of date-grouped entries).

    Args:
        payload: The ``payload`` field from the A2A envelope, containing
            ``action`` and either ``params`` or ``jobId``.
        cron_controller: The singleton ``CronController`` instance.
        session_id: Optional session ID for channel routing context.

    Returns:
        A dict with ``action`` and ``ans``; ``status`` only on success.
    """
    if not isinstance(payload, dict):
        return {
            "action": "unknown",
            "ans": {"error": "payload must be an object"},
        }

    action = str(payload.get("action") or "").strip()
    if action not in VALID_ACTIONS:
        return {
            "action": action,
            "ans": {"error": f"Unknown action: {action}"},
        }

    if cron_controller is None:
        return {
            "action": action,
            "ans": {"error": "cron controller not available"},
        }

    try:
        ans = await _route_action(action, payload, cron_controller, session_id)
        return {
            "action": action,
            "status": True,
            "ans": ans,
        }
    except KeyError as exc:
        return {
            "action": action,
            "ans": {"error": str(exc) or "job not found"},
        }
    except Exception as exc:
        logger.warning("[cron.query] action=%s error: %s", action, exc)
        return {
            "action": action,
            "ans": {"error": str(exc)},
        }


async def _route_action(
    action: str,
    payload: dict[str, Any],
    cc: CronController,
    session_id: str | None,
) -> dict[str, Any]:
    """Route to the appropriate controller method based on action."""

    if action == "list":
        params = payload.get("params") or {}
        include_disabled = True
        if isinstance(params, dict):
            include_disabled = bool(params.get("includeDisabled", True))
        return await cc.list_jobs_a2a(include_disabled=include_disabled)

    if action == "status":
        return await cc.status()

    if action == "runs":
        job_id = str(payload.get("jobId") or "").strip()
        params = payload.get("params") or {}
        limit = 10
        if isinstance(params, dict) and params.get("limit") is not None:
            try:
                limit = int(params["limit"])
            except (TypeError, ValueError):
                limit = 10
        return await cc.runs(job_id, limit=limit)

    if action == "add":
        params = payload.get("params") or {}
        # 支持两种格式：
        # 1. 前端嵌套格式: {params: {job: {name, schedule, payload, ...}}}
        # 2. 设备扁平格式: {params: {name, schedule, payload, delivery, ...}}
        #    (Xiaoyi 设备把 job 字段直接放在 params 里)
        job_params = params.get("job") if isinstance(params, dict) else None
        if not isinstance(job_params, dict):
            # 设备扁平格式：检查 params 是否含 job 相关字段（name/schedule/payload）
            if isinstance(params, dict) and any(
                k in params for k in ("name", "schedule", "payload", "delivery", "cron_expr")
            ):
                job_params = params
        if not isinstance(job_params, dict):
            raise ValueError("params.job (or flat job fields in params) is required for add action")
        # session_id fallback: use context session_id, or params.sessionId if provided
        effective_sid = session_id
        if not effective_sid and isinstance(params, dict):
            sid_val = params.get("sessionId")
            if isinstance(sid_val, str) and sid_val.strip():
                effective_sid = sid_val.strip()
        # 设备可能在 params.agentId 中指定目标 agent（当前忽略，使用默认）
        return await cc.create_job_a2a(job_params, session_id=effective_sid)

    if action == "update":
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required for update action")
        params = payload.get("params") or {}
        patch_params = params.get("patch") if isinstance(params, dict) else params
        if not isinstance(patch_params, dict):
            patch_params = params if isinstance(params, dict) else {}
        return await cc.update_job_a2a(job_id, patch_params)

    if action == "remove":
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required for remove action")
        return await cc.delete_job_a2a(job_id)

    if action == "run":
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required for run action")
        return await cc.run_now_a2a(job_id)

    if action == "queryTimeList":
        return await cc.query_time_list()

    # Should not reach here due to VALID_ACTIONS check
    raise ValueError(f"Unknown action: {action}")


def build_a2a_response_envelope(
    action: str,
    ans: dict[str, Any] | list[Any],
    status: bool = True,
) -> dict[str, Any]:
    """Build a complete A2A CronQuery response envelope.

    Returns the full ``{header, payload}`` structure suitable for sending
    back via WebSocket (Xiaoyi channel) or extracting payload for Web/TUI.

    Per the protocol doc:
    - Success: ``payload`` includes ``status: true`` and ``ans``.
    - Error: ``payload`` omits ``status`` and ``ans`` contains ``{error: "..."}``.
    - queryTimeList: ``ans`` is a list (array of date-grouped entries).
    """
    payload: dict[str, Any] = {
        "action": action,
    }
    if status:
        payload["status"] = True
    payload["ans"] = ans
    return {
        "header": {
            "namespace": "AgentEvent",
            "name": "CronQuery",
        },
        "payload": payload,
    }


def _scan_events_or_directives(container: dict[str, Any]) -> dict[str, Any] | None:
    """Scan ``data.events[]`` and ``data.directives[]`` for a CronQuery entry.

    Xiaoyi devices send CronQuery inside ``data.events[]`` (as an A2A event),
    while other channels use ``data.directives[]``. This helper checks both.

    Args:
        container: A ``data`` dict that may contain ``events`` or ``directives``.

    Returns:
        The CronQuery ``payload`` dict if found, otherwise ``None``.
    """
    for key in ("events", "directives"):
        items = container.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_header = item.get("header")
            if isinstance(item_header, dict):
                if item_header.get("namespace") == "AgentEvent" and item_header.get("name") == "CronQuery":
                    item_payload = item.get("payload")
                    if isinstance(item_payload, dict):
                        return item_payload
    return None


def extract_cron_query_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a CronQuery payload from an A2A message if present.

    Handles four locations:
    1. Root-level ``{header: {namespace: "AgentEvent", name: "CronQuery"}, payload}``
    2. Inside ``params.message.parts[].data.events[]`` or ``data.directives[]``
       (Xiaoyi devices use ``events``, push channels use ``directives``)
    3. Xiaoyi ``msgType: "data"`` format where ``msgDetail`` is a JSON string
       containing the A2A envelope (root-level or parts events/directives)
    4. JSON-RPC method call ``{jsonrpc: "2.0", method: "cron.query",
       params: {action, ...}}`` — used by some Xiaoyi device firmware

    Returns the ``payload`` dict (containing ``action`` and params/jobId) if
    found, otherwise ``None``.
    """
    # Path 1: Root-level A2A envelope
    header = message.get("header")
    if isinstance(header, dict):
        if header.get("namespace") == "AgentEvent" and header.get("name") == "CronQuery":
            payload = message.get("payload")
            if isinstance(payload, dict):
                return payload

    # Path 4: JSON-RPC method call {jsonrpc: "2.0", method: "cron.query", params: {...}}
    if message.get("jsonrpc") == "2.0" and message.get("method") == "cron.query":
        params = message.get("params")
        if isinstance(params, dict) and "action" in params:
            return params

    # Path 2: Inside params.message.parts[].data.events[] or data.directives[]
    params = message.get("params")
    if isinstance(params, dict):
        msg = params.get("message")
        if isinstance(msg, dict):
            parts = msg.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    data = part.get("data")
                    if not isinstance(data, dict):
                        continue
                    found = _scan_events_or_directives(data)
                    if found is not None:
                        return found

    # Path 3: Xiaoyi msgType="data" format: msgDetail is a JSON string containing A2A content
    msg_type = message.get("msgType")
    if msg_type == "data":
        import json as _json
        msg_detail = message.get("msgDetail")
        if isinstance(msg_detail, str) and msg_detail.strip():
            try:
                inner = _json.loads(msg_detail)
                if isinstance(inner, dict):
                    # Try root-level envelope inside msgDetail
                    inner_header = inner.get("header")
                    if isinstance(inner_header, dict):
                        if inner_header.get("namespace") == "AgentEvent" and inner_header.get("name") == "CronQuery":
                            inner_payload = inner.get("payload")
                            if isinstance(inner_payload, dict):
                                return inner_payload
                    # Try JSON-RPC method call inside msgDetail
                    if inner.get("jsonrpc") == "2.0" and inner.get("method") == "cron.query":
                        inner_params = inner.get("params")
                        if isinstance(inner_params, dict) and "action" in inner_params:
                            return inner_params
                    # Try params.message.parts[].data.events[]/directives[] inside msgDetail
                    inner_params = inner.get("params")
                    if isinstance(inner_params, dict):
                        inner_msg = inner_params.get("message")
                        if isinstance(inner_msg, dict):
                            inner_parts = inner_msg.get("parts")
                            if isinstance(inner_parts, list):
                                for part in inner_parts:
                                    if not isinstance(part, dict):
                                        continue
                                    part_data = part.get("data")
                                    if not isinstance(part_data, dict):
                                        continue
                                    found = _scan_events_or_directives(part_data)
                                    if found is not None:
                                        return found
            except (ValueError, TypeError):
                pass  # msgDetail is not valid JSON, skip

    return None

