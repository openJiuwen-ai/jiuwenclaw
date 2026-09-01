# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for resolving project-scoped coding memory paths."""

from __future__ import annotations

import ntpath
import os
from os import PathLike

DEFAULT_CODING_MEMORY_PROJECT = "default"


def resolve_coding_memory_project_name(project_dir: str | PathLike[str] | None) -> str:
    """Return the project-scoped directory name used under coding_memory/."""
    if project_dir is None:
        return DEFAULT_CODING_MEMORY_PROJECT

    raw_project_dir = str(project_dir).strip()
    if not raw_project_dir:
        return DEFAULT_CODING_MEMORY_PROJECT

    project_name = ntpath.basename(raw_project_dir.rstrip("/\\"))
    project_name = project_name.replace("/", "_").replace("\\", "_").strip()
    if not project_name or project_name in {".", ".."}:
        return DEFAULT_CODING_MEMORY_PROJECT
    return project_name


def resolve_project_coding_memory_dir(
    *,
    agent_workspace_dir: str | PathLike[str],
    project_dir: str | PathLike[str] | None,
) -> str:
    """Resolve ``<agent_workspace>/coding_memory/<project_name>``."""
    return os.path.join(
        os.path.abspath(str(agent_workspace_dir)),
        "coding_memory",
        resolve_coding_memory_project_name(project_dir),
    )


def resolve_project_coding_memory_workspace_path(
    *,
    project_dir: str | PathLike[str] | None,
) -> str:
    """Resolve the workspace-relative ``coding_memory/<project_name>`` path."""
    return os.path.join(
        "coding_memory",
        resolve_coding_memory_project_name(project_dir),
    )
