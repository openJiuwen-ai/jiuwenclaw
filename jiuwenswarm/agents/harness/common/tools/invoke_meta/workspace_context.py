# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-request workspace context for invoke meta-tools."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from jiuwenswarm.common.utils import get_agent_workspace_dir

_effective_request_workspace_dir: ContextVar[Optional[str]] = ContextVar(
    "invoke_meta_effective_request_workspace_dir",
    default=None,
)


def set_effective_request_workspace_dir(workspace_dir: Optional[str]) -> None:
    _effective_request_workspace_dir.set(workspace_dir)


def get_effective_request_workspace_dir() -> Optional[str]:
    """Return request workspace, falling back to agent workspace root."""
    value = _effective_request_workspace_dir.get()
    if value and str(value).strip():
        return str(value).strip()
    try:
        return str(get_agent_workspace_dir())
    except Exception:  # noqa: BLE001
        return None
