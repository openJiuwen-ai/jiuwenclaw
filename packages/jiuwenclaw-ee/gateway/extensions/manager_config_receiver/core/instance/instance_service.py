# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 实例身份绑定（无主动心跳）。

``JIUWENCLAW_ID`` 优先取自 env；未设置时启动时自动生成 UUID。
存活由 Manager 周期探活本机 ``/api/v1/health`` 确认。
"""

from __future__ import annotations

import logging
import os

from ...infrastructure.config import Settings, get_settings
from ...infrastructure.utils import get_jiuwenclaw_id

logger = logging.getLogger(__name__)


def resolve_public_endpoint(cfg: Settings | None = None) -> str:
    """Manager 回调用的 Gateway Config Receiver Base URL（无尾斜杠）。

    由 ``GATEWAY_CONFIG_PUBLIC_HOST`` + ``GATEWAY_CONFIG_HTTP_PORT`` 拼接；
    host 未配置时默认 ``127.0.0.1``。
    """
    cfg = cfg or get_settings()
    port = int(cfg.gateway_config_http_port or 8775)
    host = (cfg.gateway_config_public_host or "").strip() or "127.0.0.1"
    return f"http://{host}:{port}"


def resolve_manager_http_base(cfg: Settings | None = None) -> str:
    cfg = cfg or get_settings()
    return (cfg.gateway_manager_http_url or "").strip().rstrip("/")


class InstanceService:
    """绑定 ``JIUWENCLAW_ID`` / GatewayDb；不再向 Manager 主动心跳。"""

    def __init__(self, cfg: Settings | None = None) -> None:
        self._cfg = cfg or get_settings()

    async def start(self) -> None:
        from_env = bool(os.getenv("JIUWENCLAW_ID", "").strip())
        jid = get_jiuwenclaw_id()
        if not from_env:
            logger.info(
                "[InstanceService] JIUWENCLAW_ID unset; generated jiuwenclaw_id=%s",
                jid,
            )
        try:
            from ..enterprise_config.gateway_db import GatewayDb

            GatewayDb.bind(jid)
        except Exception:  # noqa: BLE001
            logger.debug("[InstanceService] GatewayDb.bind failed", exc_info=True)
            return
        logger.info(
            "[InstanceService] bound jiuwenclaw_id=%s endpoint=%s "
            "(Manager health-probes this Gateway)",
            jid,
            resolve_public_endpoint(self._cfg),
        )

    async def stop(self) -> None:
        return
