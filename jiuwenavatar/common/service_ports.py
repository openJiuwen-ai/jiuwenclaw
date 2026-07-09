# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JiuwenAvatar 默认服务端口 — 与父项目 openjiuwen/jiuwenswarm 错开，避免同机部署冲突."""

from __future__ import annotations

# 默认实例 (index=0) 基线端口
DEFAULT_AGENT_SERVER_PORT = 28092
DEFAULT_WEB_PORT = 29000
DEFAULT_GATEWAY_PORT = 29001
DEFAULT_WEBHOOK_PORT = 29002
DEFAULT_FRONTEND_PORT = 29173

# 便于文档与脚本引用
DEFAULT_PORTS_DOC = (
    f"agent_server={DEFAULT_AGENT_SERVER_PORT}, "
    f"web={DEFAULT_WEB_PORT}, "
    f"gateway={DEFAULT_GATEWAY_PORT}, "
    f"webhook={DEFAULT_WEBHOOK_PORT}, "
    f"frontend={DEFAULT_FRONTEND_PORT}"
)
