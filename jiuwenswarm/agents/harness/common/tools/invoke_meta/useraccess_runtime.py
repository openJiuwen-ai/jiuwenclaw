# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local CloudWsRelay entry for invoke plugin skills (same /ws/link as xiaoyi)."""

from __future__ import annotations

import hashlib
import hmac
import base64
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Desktop CloudWsRelay local port (claw_desktop JIUWEN_XIAOYI_RELAY_PORT).
_DEFAULT_RELAY_WS = "ws://127.0.0.1:19690"
_RELAY_URL_ENVS = ("XIAOYI_RELAY_WS_URL", "CLAW_XIAOYI_RELAY_WS_URL", "USERACCESS_PLUGIN_WS_URL")
# Legacy direct UserAccess URL (optional override; prefer local relay).
_LEGACY_PLUGIN_URL_ENVS = ("AGENT_RUNTIME_MCP_RUN",)
_AGENT_BASE_ENV = "AGENT_RUNTIME_BASEURL"
_UID_ENV = "AGENT_RUNTIME_UID"
_DEVICE_ID_ENVS = ("AGENT_RUNTIME_DEVICE_ID", "X_DEVICE_ID")


def resolve_plugin_runtime_url() -> str:
    """Prefer local CloudWsRelay; fall back to legacy direct plugin WS URL."""
    for key in _RELAY_URL_ENVS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    for key in _LEGACY_PLUGIN_URL_ENVS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return _DEFAULT_RELAY_WS


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
    env_uid = (os.environ.get(_UID_ENV) or "").strip()
    if env_uid:
        return env_uid
    return str(_xiaoyi_channel().get("uid") or "").strip()


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


def build_local_relay_headers(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Headers for AgentServer → CloudWsRelay (localAuth + x-relay-role=plugin)."""
    xiaoyi = _xiaoyi_channel()
    # Prefer desktop-injected env (AgentServer); config may still be ${CLAW_XIAOYI_*} placeholders.
    ak = (
        (os.environ.get("CLAW_XIAOYI_AK") or "").strip()
        or str(xiaoyi.get("ak") or "").strip()
    )
    sk = (
        (os.environ.get("CLAW_XIAOYI_SK") or "").strip()
        or str(xiaoyi.get("sk") or "").strip()
    )
    agent_id = (
        (os.environ.get("CLAW_XIAOYI_AGENT_ID") or "").strip()
        or str(xiaoyi.get("agent_id") or xiaoyi.get("agentId") or "").strip()
    )

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "x-relay-role": "plugin",
    }
    if ak and sk and agent_id:
        ts = str(int(time.time() * 1000))
        signature = base64.b64encode(
            hmac.new(sk.encode("utf-8"), ts.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        headers["x-access-key"] = ak
        headers["x-sign"] = signature
        headers["x-ts"] = ts
        headers["x-agent-id"] = agent_id
    else:
        logger.warning(
            "[useraccess_runtime] 本地中转鉴权缺失：需 CLAW_XIAOYI_AK/SK/AGENT_ID "
            "（桌面 spawn AgentServer 注入）或 channels.xiaoyi.ak/sk/agent_id"
        )

    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


def build_runtime_headers(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Handshake headers for plugin invoke.

    Local relay path uses localAuth (not businessCredential — that stays in desktop relay).
    """
    return build_local_relay_headers(extra=extra)


def build_plugin_skill_extra_info(
    *,
    session_id: str | None = None,
    interaction_id: int = 0,
) -> dict[str, Any]:
    """Build extraInfo aligned with skills/request.txt.

    deviceInfo 优先用桌面 getDeviceInfo 注入的环境变量（与 CloudWsRelay / model-proxy 同源）：
    AGENT_RUNTIME_DEVICE_ID / CLAW_DEVICE_HOSTNAME / CLAW_DEVICE_SANDBOX_SYSTEM。
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

    # PC 端映射：hostname→deviceName，sandboxSystem→x-device-type/sysVersion（非鸿蒙字段，与样例同结构）
    return {
        "context": {
            "deviceInfo": {
                "deviceName": hostname or "sandbox_pc",
                "ohosApiVersion": 0,
                "romVersion": "",
                "sysVersion": sandbox_system or "",
                "x-device-id": device_id,
                "x-device-type": sandbox_system or "pc",
            },
            "userInfo": {
                "uid": uid,
            },
        },
        "session": {
            "sessionId": str(resolved_session),
            "interactionId": int(interaction_id or 0),
            "deviceId": device_id,
        },
    }


def build_cloud_plugin_context(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> Any:
    """Compatibility wrapper: return CloudPluginContext when possible."""
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
        CloudPluginContext,
    )

    extra = build_plugin_skill_extra_info(session_id=session_id)
    session = extra.get("session") or {}
    device = (extra.get("context") or {}).get("deviceInfo") or {}
    return CloudPluginContext(
        session_id=str(session.get("sessionId") or ""),
        interaction_id=int(session.get("interactionId") or 0),
        message_name="",
        device_id=str(session.get("deviceId") or ""),
        device_name=str(device.get("deviceName") or ""),
        device_type=str(device.get("x-device-type") or ""),
        sys_version=str(device.get("sysVersion") or ""),
        agent_id=str(agent_id or ""),
        agent_login_session_id="",
        current_agent_attachment=[],
        service_center_data=[],
    )


def missing_plugin_url_error(*, plugin_id: str = "", tool_name: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            "缺少本地 CloudWsRelay 地址：请确认桌面云端渠道已启用，"
            "或配置环境变量 XIAOYI_RELAY_WS_URL"
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
    "build_local_relay_headers",
    "build_plugin_skill_extra_info",
    "build_runtime_headers",
    "missing_agent_baseurl_error",
    "missing_plugin_url_error",
    "resolve_agent_runtime_baseurl",
    "resolve_device_hostname",
    "resolve_device_sandbox_system",
    "resolve_plugin_runtime_url",
    "resolve_runtime_device_id",
    "resolve_runtime_uid",
]
