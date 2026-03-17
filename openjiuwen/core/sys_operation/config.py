# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, List, Dict, Any
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


class SandboxGatewayConfig(BaseModel):
    """Remote sandbox gateway connection configuration."""

    gateway_url: str = Field(default="", description="Remote sandbox gateway service endpoint")
    params: Dict[str, Any] = Field(default_factory=dict, description="Global request parameters")
    auth_headers: Dict[str, str] = Field(default_factory=dict, description="Authentication HTTP headers")
    auth_query_params: Dict[str, str] = Field(default_factory=dict, description="Authentication query parameters")
