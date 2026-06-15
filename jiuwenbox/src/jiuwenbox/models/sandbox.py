# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Sandbox data models."""

from __future__ import annotations

import enum
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

SANDBOX_ID_MIN_LEN = 4
SANDBOX_ID_MAX_LEN = 16
CUSTOM_SANDBOX_ID_RE = re.compile(r"^[0-9a-z_-]{4,16}$")

SANDBOX_ID_FORMAT_MESSAGE = (
    "sandbox_id must be 4-16 characters and contain only lowercase letters, "
    "digits, hyphens, and underscores (e.g. my-sb_01)"
)


class InvalidSandboxIdError(Exception):
    """Raised when a user-supplied sandbox_id fails format validation."""


def generate_sandbox_id() -> str:
    """Generate a sandbox id using the existing uuid4[:12] scheme."""
    return str(uuid.uuid4())[:12]


def validate_custom_sandbox_id(value: str) -> str:
    """Validate a user-supplied sandbox_id; return it unchanged on success."""
    if not CUSTOM_SANDBOX_ID_RE.fullmatch(value):
        raise InvalidSandboxIdError(SANDBOX_ID_FORMAT_MESSAGE)
    return value


class SandboxPhase(str, enum.Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    STOPPED = "stopped"
    ERROR = "error"
    DELETING = "deleting"


class PolicyMode(str, enum.Enum):
    OVERRIDE = "override"
    APPEND = "append"


class SandboxSpec(BaseModel):
    """Specification for creating a sandbox."""

    env: dict[str, str] = Field(default_factory=dict)
    sandbox_id: str | None = None


class SandboxRef(BaseModel):
    """Reference to an existing sandbox."""

    id: str
    phase: SandboxPhase = SandboxPhase.PROVISIONING
    runtime: str = "process"
    pid: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    # 最后一次 sandbox API 调用 (exec / file IO / lifecycle 切换) 的时间戳, 仅
    # 供 jiuwenbox 服务端 reaper 用于判定空闲淘汰; 不持久化 (重启时跟整个
    # sandbox 注册表一起被清空) 也不下传到 daemon。``None`` 表示尚未发生过
    # 任何交互, reaper 会用 ``started_at`` 兜底。
    last_active_at: datetime | None = None
    error_message: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class ExecResult(BaseModel):
    """Result of executing a command in a sandbox."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class BackgroundExecResult(BaseModel):
    """Result of starting a background command in a sandbox."""

    started: bool
    pid: int | None = None
    command: list[str] = Field(default_factory=list)
    error_message: str | None = None
