# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent-facing tools for Skill-driven DeepResearch report rewrites."""
from __future__ import annotations

import json
import logging
import os

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.agentserver.tools.deepresearch_plugin.document_rewrite import (
    RewriteError,
    commit_rewrite,
    prepare_rewrite,
)
from jiuwenclaw.agentserver.tools.deepresearch_tools import _get_route
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_effective_request_output_dir,
)

logger = logging.getLogger(__name__)


def _error(exc: RewriteError) -> str:
    return json.dumps(
        {"status": "error", "error_code": exc.code, "error": str(exc)},
        ensure_ascii=False,
    )


@tool(
    name="deepresearch_prepare_rewrite",
    description=(
        "准备 DeepResearch Markdown 局部改写。必须在生成任何改写正文前调用；"
        "校验报告 revision、选区和引用白名单，返回一次性 context_token。"
    ),
)
async def deepresearch_prepare_rewrite(
    report_path: str,
    action: str,
    block_id: str,
    start: int,
    end: int,
    selected_text: str,
    instruction: str = "",
) -> str:
    route = _get_route()
    output_dir = get_effective_request_output_dir()
    session_id = str(route.get("session_id") or "")
    if not output_dir or not session_id:
        return json.dumps({
            "status": "error",
            "error_code": "BAD_REQUEST",
            "error": "rewrite workspace or session is unavailable",
        })
    try:
        result = prepare_rewrite(
            workspace_root=output_dir,
            report_path=report_path,
            action=action,
            block_id=block_id,
            start=start,
            end=end,
            selected_text=selected_text,
            instruction=instruction,
            session_id=session_id,
        )
    except RewriteError as exc:
        logger.info("deepresearch prepare rewrite rejected: code=%s", exc.code)
        return _error(exc)
    return json.dumps({"status": "prepared", **result}, ensure_ascii=False)


async def _deliver_report(report_path: str, route: dict[str, object]) -> bool:
    if not route.get("session_id") or not route.get("channel_id"):
        return False
    from jiuwenclaw.agentserver.gateway_push.transport import (  # pylint: disable=import-outside-toplevel
        WebSocketGatewayPushTransport,
    )

    transport = WebSocketGatewayPushTransport()
    await transport.send_push({
        "request_id": route.get("request_id", ""),
        "channel_id": route["channel_id"],
        "session_id": route["session_id"],
        "payload": {
            "event_type": "chat.file",
            "files": [{"path": report_path, "name": os.path.basename(report_path)}],
        },
        "is_complete": False,
    })
    return True


@tool(
    name="deepresearch_commit_rewrite",
    description=(
        "提交 DeepResearch 局部改写结果并创建不可变 child revision。"
        "只能使用 deepresearch_prepare_rewrite 返回的 context_token，禁止直接写报告文件。"
    ),
)
async def deepresearch_commit_rewrite(
    context_token: str,
    structured_result: dict,
) -> str:
    route = _get_route()
    session_id = str(route.get("session_id") or "")
    if not session_id:
        return json.dumps({
            "status": "error",
            "error_code": "BAD_REQUEST",
            "error": "rewrite session is unavailable",
        })
    try:
        result = commit_rewrite(
            context_token=context_token,
            session_id=session_id,
            structured_result=structured_result,
        )
        delivered = await _deliver_report(result["report_path"], route)
    except RewriteError as exc:
        logger.info("deepresearch commit rewrite rejected: code=%s", exc.code)
        return _error(exc)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("deepresearch rewrite artifact delivery failed")
        return json.dumps({
            "status": "error",
            "error_code": "WRITE_FAILED",
            "error": "rewrite artifact delivery failed",
        })
    return json.dumps(
        {"status": "completed", "report_delivered": delivered, **result},
        ensure_ascii=False,
    )


__all__ = ["deepresearch_prepare_rewrite", "deepresearch_commit_rewrite"]
