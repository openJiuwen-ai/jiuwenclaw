from __future__ import annotations

import json
from typing import Any, Dict

from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.device_command_manager import (
    get_device_command_manager,
)
from jiuwenswarm.common.utils import logger
from jiuwenswarm.server.request_context import get_device_context


def _is_data_event_status_success(status: Any) -> bool:
    if status is True:
        return True
    if status is None or status is False:
        return False
    return str(status).strip().lower() in ("success", "succeed", "successful", "ok")


def _outputs_top_level_code_ok(code: Any) -> bool:
    if code is None:
        return True
    if isinstance(code, bool):
        return bool(code)
    try:
        if isinstance(code, (int, float)) and int(code) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return str(code).strip() == "0"


class ToolInputError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.status = 400


async def execute_device_command(
    intent_name: str,
    command: Dict[str, Any],
    timeout: float = 60.0,
) -> Dict[str, Any]:
    logger.info("[%s_TOOL] Starting execution", intent_name)
    context = get_device_context()
    if context is None:
        raise RuntimeError("No active Xiaoyi request context")
    if context.channel_id != "xiaoyi":
        raise RuntimeError("Xiaoyi device tools require Xiaoyi channel")

    response = await get_device_command_manager().call(
        intent_name=intent_name,
        command=command,
        context=context,
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"{response.error_code}: {response.error_message}")
    return response.result or {}


def raise_if_device_error(outputs: Any, what_failed: str) -> None:
    if not isinstance(outputs, dict):
        return
    code = outputs.get("code")
    if not _outputs_top_level_code_ok(code):
        error_msg = outputs.get("errorMsg") or outputs.get("errMsg") or "unknown error"
        raise RuntimeError(f"{what_failed}: {error_msg} (code: {code})") from None
    ret = outputs.get("retErrCode")
    if ret is not None and str(ret) != "0":
        err_msg = outputs.get("errMsg", "unknown error")
        raise RuntimeError(f"{what_failed}: {err_msg} (retErrCode: {ret})") from None


def validate_required_params(params: Dict[str, Any], required: list[str]) -> None:
    for param_name in required:
        value = params.get(param_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ToolInputError(f"Missing required parameter: {param_name}")


def format_success_response(data: Dict[str, Any], message: str = "") -> Dict[str, Any]:
    response = {"success": True, **data}
    if message:
        response["message"] = message
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(response, ensure_ascii=False),
            }
        ]
    }


def format_error_response(error: str) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"success": False, "error": error}, ensure_ascii=False),
            }
        ]
    }
