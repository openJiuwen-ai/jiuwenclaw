# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager 写库后通知 agent-runtime 回收 AgentServer Pod（与 RuntimeRoutedAgentClient 无关）。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from jiuwenswarm.common.local_env_config import read_env

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 2.0
_config_update_handle: asyncio.TimerHandle | None = None


def trigger_runtime_config_update() -> None:
    """企业配置变更：防抖后请求 agent-runtime 批删 AgentServer Pod，由 autoscale 重建。"""
    base = read_env("GATEWAY_RUNTIME_MANAGER_URL", "").strip()
    if not base:
        logger.debug(
            "[ManagerConfigReceiver] GATEWAY_RUNTIME_MANAGER_URL unset, skip runtime notify"
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "[ManagerConfigReceiver] no running event loop, runtime config update skipped"
        )
        return

    global _config_update_handle
    if _config_update_handle is not None:
        _config_update_handle.cancel()
    _config_update_handle = loop.call_later(
        _DEBOUNCE_SECONDS,
        _schedule_agentserver_cleanup,
    )
    logger.info(
        "[ManagerConfigReceiver] agentserver cleanup debounced %.1fs",
        _DEBOUNCE_SECONDS,
    )


def _schedule_agentserver_cleanup() -> None:
    global _config_update_handle
    _config_update_handle = None
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _request_agentserver_cleanup(),
            name="runtime-agentserver-cleanup",
        )
    except RuntimeError:
        logger.warning(
            "[ManagerConfigReceiver] no running event loop, agentserver cleanup skipped"
        )


async def _request_agentserver_cleanup() -> None:
    base = read_env("GATEWAY_RUNTIME_MANAGER_URL", "").strip().rstrip("/")
    if not base:
        return
    namespace = read_env("NAMESPACE", "default").strip() or "default"
    label_selector = read_env(
        "GATEWAY_RUNTIME_AGENTSERVER_LABEL",
        "jiuwenclaw-component=agentserver",
    ).strip()
    try:
        timeout = float(read_env("GATEWAY_RUNTIME_MANAGER_TIMEOUT", "40"))
    except (TypeError, ValueError):
        timeout = 40.0
    if timeout <= 0:
        timeout = 40.0

    url = f"{base}/api/session/cleanup"
    body: dict[str, Any] = {
        "type": "cleanup",
        "metadata": {"request_id": f"cfg-{uuid.uuid4().hex[:12]}"},
        "rawdata": {
            "namespace": namespace,
            "label_selector": label_selector,
        },
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.post(url, json=body)
        if resp.status_code != 200:
            logger.warning(
                "[ManagerConfigReceiver] agentserver cleanup failed status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return
        payload = resp.json()
        raw = payload.get("rawdata") if isinstance(payload.get("rawdata"), dict) else payload
        cleaned = raw.get("cleaned") if isinstance(raw, dict) else None
        logger.info(
            "[ManagerConfigReceiver] agentserver cleanup ok namespace=%s cleaned=%s",
            namespace,
            cleaned,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ManagerConfigReceiver] agentserver cleanup request failed: %s",
            exc,
        )
