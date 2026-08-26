# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""主备角色：active-standby 下仅 PRIMARY 接受写流量 / Ready。"""

from __future__ import annotations

import os


def gateway_deployment_mode() -> str:
    return (os.getenv("DEPLOYMENT_MODE") or "standalone").strip().lower() or "standalone"


def is_gateway_primary() -> bool:
    """standalone / distributed：始终视为可写；active-standby：读 LeaderElection。"""
    mode = gateway_deployment_mode()
    if mode != "active-standby":
        return True
    try:
        from jiuwenswarm.gateway.leader_election import LeaderElection

        le = LeaderElection.get_instance()
        return bool(le.is_primary)
    except Exception:  # noqa: BLE001
        # 选主未就绪时保守：不 Ready，避免 STANDBY 抢流量
        return False
