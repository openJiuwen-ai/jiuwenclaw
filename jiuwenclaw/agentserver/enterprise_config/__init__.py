"""从 Gateway 本地库解析企业级配置生效策略与模板。"""

from jiuwenclaw.agentserver.enterprise_config.loader import (
    DEFAULT_AGENT_LOAD_SLOTS,
    load_effective_enterprise_config,
)

__all__ = [
    "DEFAULT_AGENT_LOAD_SLOTS",
    "load_effective_enterprise_config",
]
