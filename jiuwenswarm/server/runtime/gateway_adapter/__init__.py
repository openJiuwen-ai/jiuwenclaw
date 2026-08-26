"""Gateway 用户业务适配器层（AgentServer 侧）。

把 Gateway 转发的 E2A 用户业务请求（session / config / workspace / project /
memory / harmonyos / cron 项目解析等）转换为 AgentServer 中用户态业务门面
可消费的形式，在当前 AgentServer 外部注入的 ``.jiuwenswarm`` 中执行。

注：Team 域请求（``team.*``）不经适配器层，由 ``agent_ws_server.py``
if/elif 链直接处理（已有 E2A handler）。

约束（方案第 6 章）：
- 适配器只负责 Gateway 请求与用户业务门面之间的兼容，不负责认证、鉴权、
  用户路由、平台连接或长期调度；
- 适配器不得反向依赖或导入 ``gateway.*``；可复用逻辑必须来自
  ``server/runtime`` 等中立模块；
- 适配器不得根据请求中的 ``user_id`` 选择、切换或推导用户目录。
"""

from __future__ import annotations

from jiuwenswarm.server.runtime.gateway_adapter.base import (
    AdapterRegistry,
    GatewayAdapter,
)
from jiuwenswarm.server.runtime.gateway_adapter.session_adapter import SessionAdapter
from jiuwenswarm.server.runtime.gateway_adapter.memory_adapter import MemoryAdapter
from jiuwenswarm.server.runtime.gateway_adapter.project_adapter import ProjectAdapter
from jiuwenswarm.server.runtime.gateway_adapter.workspace_file_adapter import (
    WorkspaceFileAdapter,
)
from jiuwenswarm.server.runtime.gateway_adapter.harmonyos_adapter import HarmonyOSAdapter
from jiuwenswarm.server.runtime.gateway_adapter.config_adapter import ConfigAdapter

__all__ = [
    "AdapterRegistry",
    "GatewayAdapter",
    "SessionAdapter",
    "MemoryAdapter",
    "ProjectAdapter",
    "WorkspaceFileAdapter",
    "HarmonyOSAdapter",
    "ConfigAdapter",
]
