"""Model tool for applying third-party channel configuration in the Gateway."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any
from urllib.parse import urljoin

from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.agents.harness.common.channel_runtime_context import (
    CURRENT_CHANNEL_ID,
    CURRENT_SESSION_ID,
)
from jiuwenswarm.common.channel_config_registry import (
    CONFIGURABLE_THIRD_PARTY_CHANNEL_ID_TEXT,
    normalize_configurable_channel_id,
)


def _gateway_control_url() -> str:
    configured = str(os.getenv("JIUWENSWARM_GATEWAY_CONTROL_URL") or "").strip()
    if configured:
        configured = configured.rstrip("/")
        if configured.endswith("/channel-config"):
            return configured
        return urljoin(configured + "/", "channel-config")

    host = str(os.getenv("GATEWAY_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(os.getenv("GATEWAY_PORT", "19001"))
    return f"ws://{host}:{port}/channel-config"


async def _request_gateway_control(method: str, params: dict[str, Any]) -> dict[str, Any]:
    import websockets

    request_id = f"channel-control-{uuid.uuid4().hex}"
    async with websockets.connect(_gateway_control_url(), open_timeout=10, close_timeout=5) as ws:
        # Gateway sends a connection acknowledgement before it accepts requests.
        await asyncio.wait_for(ws.recv(), timeout=10)
        request_params = dict(params)
        requester_channel_id = CURRENT_CHANNEL_ID.get().strip()
        requester_session_id = CURRENT_SESSION_ID.get().strip()
        if requester_channel_id or requester_session_id:
            request_params["requester"] = {
                "channel_id": requester_channel_id,
                "session_id": requester_session_id,
            }
        await ws.send(json.dumps({
            "type": "req",
            "id": request_id,
            "method": method,
            "params": request_params,
        }, ensure_ascii=False))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            response = json.loads(raw)
            if response.get("type") != "res" or response.get("id") != request_id:
                continue
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "channel configuration failed"))
            payload = response.get("payload")
            return payload if isinstance(payload, dict) else {}


@tool(
    name="configure_channel",
    description=(
        "配置并立即重连 JiuwenSwarm 的第三方消息频道。仅当用户明确要连接、修改或断开三方"
        "频道时使用；不要把普通聊天误当成配置请求。channel_id 只能为 "
        f"{CONFIGURABLE_THIRD_PARTY_CHANNEL_ID_TEXT}。settings 是需要合并的配置对象；常见"
        "字段包括 enabled=true、app_id、app_secret、bot_token、client_id、client_secret、"
        "corp_id、agent_id、secret、webhook_url 等。微信扫码登录使用 enabled=true、"
        "auto_login=true，并可继续调用 get_wechat_login_status 获取二维码。查询微信扫码状态时"
        "不要调用本工具；只有用户明确要求刷新/重新生成二维码时才传 refresh_qr=true。工具成功后"
        "目标频道已在 Gateway 中应用。不要使用 bash/python 自行生成微信二维码图片。"
    ),
)
async def configure_channel(channel_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Apply a channel configuration through the running Gateway."""
    return await _request_gateway_control(
        "channel.configure",
        {"channel_id": normalize_configurable_channel_id(channel_id), "settings": settings},
    )


@tool(
    name="get_wechat_login_status",
    description=(
        "查询微信扫码登录状态和当前二维码。用户要求绑定微信、查看二维码、重新扫码或确认微信登录状态时使用。"
        "返回的 qr 字段可能是 url、data_url、encode 或 text；若 kind=encode，直接把 value 作为二维码内容展示/"
        "转述给用户扫码。三方频道会自动基于当前状态下发二维码图片或链接，不要使用 bash/python 自行生成"
        "二维码图片。本工具只查询状态，不会刷新二维码。若二维码过期或用户明确要求重新生成二维码，"
        "再调用 configure_channel(channel_id='wechat', settings={'enabled': true, 'auto_login': true, "
        "'refresh_qr': true}) 重新触发登录。"
    ),
)
async def get_wechat_login_status() -> dict[str, Any]:
    """Return the current WeChat QR login state from the Gateway."""
    return await _request_gateway_control("wechat.login_status", {})
