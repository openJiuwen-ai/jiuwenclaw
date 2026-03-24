# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from enum import Enum
from typing import Optional, List, Dict, Any, Union, Literal

from pydantic import Field, BaseModel


class LocalWorkConfig(BaseModel):
    """Local working configuration"""
    shell_allowlist: Optional[List[str]] = Field(
        default=["echo", "rg", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3", "npm", "node", "git",
                 "cat", "type", "mkdir", "md", "rm", "rd", "cp", "copy", "mv", "move", "grep", "find", "curl", "wget",
                 "ps", "df", "ping"],
        description="List of allowed command prefixes. If None, all commands are allowed (warning: insecure).")

    work_dir: Optional[str] = Field(
        default=None,
        description="Local working directory path")


class ContainerScope(str, Enum):
    """Container creation granularity

    Determines how the ContainerManager routes requests to container instances.
    """
    SYSTEM = "system"  # System-wide: all requests share one container
    SESSION = "session"  # Session-level: requests with same session_id share one container
    CUSTOM = "custom"  # Custom-level: uses context.id directly as the key


class SandboxLauncherConfig(BaseModel):
    launcher_type: str
    gateway_url: str = Field(default="", description="Remote sandbox gateway service endpoint")
    sandbox_type: str = Field(default="mock", description="Sandbox provider type, e.g. aio/e2b/mock")
    on_stop: Literal["delete", "pause", "keep"] = Field(
        default="delete",
        description=(
            "Behaviour when the sandbox is stopped (remove_sys_operation or Runner.stop). "
            "'delete' destroys the sandbox; 'pause' suspends it so it can be resumed on next launch; "
            "'keep' leaves it running."
        )
    )
    idle_ttl_seconds: Optional[int] = Field(
        default=None,
        description="Evict idle sandbox after this TTL (seconds). Idle eviction always deletes, regardless of on_stop.",
    )
    extra_params: Dict[str, Any] = Field(default_factory=dict,
                                         description="Arbitrary parameters to pass to the launcher.")


class PreDeployLauncherConfig(SandboxLauncherConfig):
    """Configuration for a pre-existing sandbox reachable via HTTP/WS.

    Use this when the sandbox process is managed externally (e.g. already
    running on a server or started by a sidecar).  The launcher simply
    returns the provided ``base_url`` without spinning up any process.
    """
    launcher_type: Literal["pre_deploy"] = "pre_deploy"
    sandbox_type: str = Field(default="aio", description="Sandbox provider type")
    base_url: str = Field(..., description="Sandbox service base URL (http:// or ws://)")


class SandboxIsolationConfig(BaseModel):
    """Configuration for container isolation and naming granularity."""
    custom_id: Optional[str] = Field(
        default=None,
        description="Core identity override. If set, this replaces the automatic session_id or context_id."
    )
    container_scope: ContainerScope = Field(
        default=ContainerScope.SESSION,
        description="Container granularity template: SYSTEM / SESSION / CUSTOM.",
    )
    prefix: Optional[str] = Field(
        default=None,
        description="Namespace prefix to isolate multiple roles/tasks in the same scope."
    )


class SandboxGatewayConfig(BaseModel):
    """Sandbox gateway configuration."""
    model_config = {"arbitrary_types_allowed": True}

    isolation: SandboxIsolationConfig = Field(
        default_factory=SandboxIsolationConfig,
        description="Isolation and naming strategy for the sandbox container."
    )
    launcher_config: Optional[Union[PreDeployLauncherConfig, SandboxLauncherConfig]] = Field(
        default=None,
        description="How to obtain/connect sandbox runtime. Phase 1 only supports PreDeployLauncherConfig + aio.",
    )
    timeout_seconds: int = Field(default=30, description="Unified timeout in seconds (request + readiness)")
    auth_headers: Dict[str, str] = Field(default_factory=dict, description="Authentication HTTP headers")
    auth_query_params: Dict[str, str] = Field(default_factory=dict, description="Authentication query parameters")


class GatewayStoreConfig(BaseModel):
    type: str = Field(default="memory",
                      description="Store type, phase 1 only supports memory")
    redis_url: Optional[str] = None


class GatewayConfig(BaseModel):
    store: GatewayStoreConfig = Field(default_factory=GatewayStoreConfig)


class SandboxCreateRequest(BaseModel):
    isolation_key: Optional[str] = None
    config: SandboxGatewayConfig


class GatewayInvokeRequest(BaseModel):
    """Request model for Gateway full-chain routing."""
    op_type: str = Field(description="Operation type: fs / shell / code")
    method: str = Field(description="Method name, e.g. read_file, execute_cmd")
    params: Dict[str, Any] = Field(default_factory=dict, description="Method parameters")
    isolation_key: Optional[str] = Field(default=None, description="Sandbox isolation key")
