# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""通知 Runtime Management 企业配置已变更（非业务分发）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def trigger_runtime_config_update() -> None:
    try:
        from jiuwenswarm.extensions.registry import ExtensionRegistry

        registry = ExtensionRegistry.get_instance()
        ext = registry.get_agent_server_client_extension()
        if ext is None or not hasattr(ext, "get_client"):
            return
        client = ext.get_client()
        if client is None or not hasattr(client, "set_or_update_server_config"):
            return
        client.set_or_update_server_config(config={"enterprise_config_update": True})
        logger.info("[ManagerConfigReceiver] triggered runtime management config update")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ManagerConfigReceiver] failed to trigger runtime management config update: %s",
            exc,
        )
