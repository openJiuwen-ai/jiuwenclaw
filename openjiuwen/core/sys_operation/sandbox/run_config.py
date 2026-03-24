from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.sys_operation.config import SandboxGatewayConfig


@dataclass
class SandboxRunConfig:
    """Runtime configuration passed to sandbox operations

    Encapsulates the original configuration for sandbox operations.
    All sandbox operations (fs/shell/code) created by the same SysOperation instance
    share the same SandboxRunConfig object.

    Attributes:
        config: Original SandboxGatewayConfig containing scope, sandbox_params, etc.
        isolation_key_template: Isolation key template with {session_id} placeholder.
            Use resolve_isolation_key() to get the actual key at invoke time.
    """
    config: SandboxGatewayConfig
    isolation_key_template: str
