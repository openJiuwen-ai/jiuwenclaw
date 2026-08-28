# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin invoke transport: direct mcp/run (businessCredential handshake)."""

from __future__ import annotations

import os
import uuid
from typing import Any

# Direct plugin WS: full URL, do not concatenate.
# Example: wss://host:18449/agent-runtime-service-ws/v1/mcp/run
_MCP_RUN_ENV = "AGENT_RUNTIME_MCP_RUN"
_AGENT_BASE_ENV = "AGENT_RUNTIME_BASEURL"
_UID_ENV = "AGENT_RUNTIME_UID"
_CLAW_UID_ENV = "CLAW_XIAOYI_UID"
_CREDENTIAL_ENV = "CLAW_BUSINESS_CREDENTIAL"
_DEVICE_ID_ENVS = ("AGENT_RUNTIME_DEVICE_ID", "X_DEVICE_ID")


def is_mcp_run_url(url: str) -> bool:
    """True when URL is the Runtime mcp/run WS (including agent-runtime-service-ws)."""
    return "/mcp/run" in (url or "").rstrip("/")


def resolve_plugin_runtime_url() -> str:
    """Return AGENT_RUNTIME_MCP_RUN (full URL, no path concat). Empty if unset."""
    return (os.environ.get(_MCP_RUN_ENV) or "").strip()


def resolve_agent_runtime_baseurl() -> str:
    """Return Agent Runtime base URL if set (agent_as_a_tool path)."""
    return (os.environ.get(_AGENT_BASE_ENV) or "").strip()


def _xiaoyi_channel() -> dict[str, Any]:
    try:
        from jiuwenswarm.common.config import get_config

        channels = get_config().get("channels") or {}
        xiaoyi = channels.get("xiaoyi") or {}
        return xiaoyi if isinstance(xiaoyi, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def resolve_runtime_uid() -> str:
    for key in (_CLAW_UID_ENV, _UID_ENV):
        env_uid = (os.environ.get(key) or "").strip()
        if env_uid:
            return env_uid
    return str(_xiaoyi_channel().get("uid") or "").strip()


def resolve_business_credential() -> str:
    """Product mcp/run handshake credential (desktop spawn, not .env)."""
    return (os.environ.get(_CREDENTIAL_ENV) or "").strip()


def resolve_runtime_device_id() -> str:
    for key in _DEVICE_ID_ENVS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        from jiuwenswarm.common.invocation_context.runtime import get_current_invocation_context
        from jiuwenswarm.server.xiaoyi_invocation import get_xiaoyi_invocation_extension

        invocation = get_current_invocation_context()
        if invocation is None:
            return ""
        extension = get_xiaoyi_invocation_extension(invocation)
        return str((extension.device_id if extension else None) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def resolve_device_hostname() -> str:
    """PC hostname from desktop getDeviceInfo (CLAW_DEVICE_HOSTNAME)."""
    return (os.environ.get("CLAW_DEVICE_HOSTNAME") or "").strip()


def resolve_device_sandbox_system() -> str:
    """OS label from desktop getDeviceInfo (windows/macos/…)."""
    return (os.environ.get("CLAW_DEVICE_SANDBOX_SYSTEM") or "").strip()


def build_product_mcp_headers(*, plugin_session_id: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
    """Handshake headers for mcp/run: businessCredential, trace, optional uid/device."""
    uid = resolve_runtime_uid()
    device_id = resolve_runtime_device_id()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "businessCredential": resolve_business_credential(),
        "x-hag-trace-id": uuid.uuid4().hex,
    }
    if uid:
        headers["x-uid"] = uid
    if device_id:
        headers["x-device-id"] = device_id
    if plugin_session_id:
        headers["x-plugin-session-id"] = plugin_session_id
    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


def build_runtime_headers(*, extra: dict[str, str] | None = None, url: str | None = None) -> dict[str, str]:
    """Handshake headers for mcp/run: businessCredential + uid/device/trace."""
    _ = url
    plugin_session_id = ""
    if extra:
        plugin_session_id = str(extra.get("x-plugin-session-id") or "")
    return build_product_mcp_headers(plugin_session_id=plugin_session_id, extra=extra)


def build_plugin_skill_extra_info(
    *,
    session_id: str | None = None,
    interaction_id: int = 0,
) -> dict[str, Any]:
    """Build extraInfo for PluginSkillExec mcp/run.

    uid 与握手 x-uid 同源（CLAW_XIAOYI_UID / AGENT_RUNTIME_UID / 渠道 uid）。
    有桌面 CLAW_DEVICE_* / device id 时用桌面设备信息；否则用 PC 缺省（sandbox_pc）。
    """
    uid = resolve_runtime_uid()
    device_id = resolve_runtime_device_id()
    hostname = resolve_device_hostname()
    sandbox_system = resolve_device_sandbox_system()

    resolved_session = session_id or ""
    try:
        from jiuwenswarm.common.invocation_context.runtime import get_current_invocation_context
        from jiuwenswarm.server.xiaoyi_invocation import get_xiaoyi_invocation_extension

        invocation = get_current_invocation_context()
        if invocation is not None:
            extension = get_xiaoyi_invocation_extension(invocation)
            resolved_session = (
                (extension.root_session_id if extension else None)
                or (extension.params_session_id if extension else None)
                or session_id
                or invocation.session_id
                or ""
            )
            if not device_id:
                device_id = str((extension.device_id if extension else None) or "")
            if not interaction_id and invocation.trace and invocation.trace.interaction_id:
                try:
                    interaction_id = int(str(invocation.trace.interaction_id).strip() or "0")
                except ValueError:
                    interaction_id = 0
    except Exception:  # noqa: BLE001
        pass

    device_info = {
        "deviceName": hostname or "sandbox_pc",
        "ohosApiVersion": 0,
        "romVersion": "",
        "sysVersion": sandbox_system or "",
        "x-device-id": device_id,
        "x-device-type": sandbox_system or "pc",
    }
    session_device_id = device_id

    return {
        "context": {
            "deviceInfo": device_info,
            "userInfo": {
                "uid": uid,
            },
        },
        "session": {
            "sessionId": str(resolved_session),
            "interactionId": int(interaction_id or 0),
            "deviceId": session_device_id,
        },
    }


def build_cloud_plugin_context(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> Any:
    """Compatibility wrapper: return CloudPluginContext when possible."""
    _ = agent_id
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
        CloudPluginContext,
    )

    extra = build_plugin_skill_extra_info(session_id=session_id)
    session = extra.get("session") or {}
    device = (extra.get("context") or {}).get("deviceInfo") or {}
    return CloudPluginContext(
        session_id=str(session.get("sessionId") or ""),
        interaction_id=int(session.get("interactionId") or 0),
        device_id=str(session.get("deviceId") or ""),
        device_name=str(device.get("deviceName") or ""),
        device_type=str(device.get("x-device-type") or ""),
        sys_version=str(device.get("sysVersion") or ""),
    )


def missing_plugin_url_error(*, plugin_id: str = "", tool_name: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            "缺少插件 WS 地址：需 AGENT_RUNTIME_MCP_RUN（桌面 spawn 注入，或实验室写入环境）"
        ),
        "pluginId": plugin_id,
        "toolName": tool_name,
    }


def missing_credential_error(*, plugin_id: str = "", tool_name: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            "缺少插件握手凭证：需 CLAW_BUSINESS_CREDENTIAL（桌面登录后 spawn 注入，"
            "或实验室写入环境）"
        ),
        "pluginId": plugin_id,
        "toolName": tool_name,
    }


def missing_agent_baseurl_error() -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            "缺少 Agent Runtime 地址：请配置环境变量 AGENT_RUNTIME_BASEURL"
        ),
    }


__all__ = [
    "build_cloud_plugin_context",
    "build_product_mcp_headers",
    "build_plugin_skill_extra_info",
    "build_runtime_headers",
    "is_mcp_run_url",
    "missing_agent_baseurl_error",
    "missing_credential_error",
    "missing_plugin_url_error",
    "resolve_agent_runtime_baseurl",
    "resolve_business_credential",
    "resolve_device_hostname",
    "resolve_device_sandbox_system",
    "resolve_plugin_runtime_url",
    "resolve_runtime_device_id",
    "resolve_runtime_uid",
]
