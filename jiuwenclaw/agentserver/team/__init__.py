# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent Team public API.

Team runtime depends on optional agent-core APIs. Keep this package light so a
normal AgentServer request does not import the full Team runtime at startup.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "load_team_spec_dict": ("config_loader", "load_team_spec_dict"),
    "TeamManager": ("team_manager", "TeamManager"),
    "cancel_all_team_stream_tasks_across_managers": (
        "team_manager",
        "cancel_all_team_stream_tasks_across_managers",
    ),
    "pause_all_team_session_runtimes_across_managers": (
        "team_manager",
        "pause_all_team_session_runtimes_across_managers",
    ),
    "find_team_skill_rail_across_managers": (
        "team_manager",
        "find_team_skill_rail_across_managers",
    ),
    "get_all_team_managers": ("team_manager", "get_all_team_managers"),
    "get_team_manager": ("team_manager", "get_team_manager"),
    "refresh_team_shared_skill_links_across_managers": (
        "team_manager",
        "refresh_team_shared_skill_links_across_managers",
    ),
    "reset_team_manager": ("team_manager", "reset_team_manager"),
    "stop_team_session_runtime_across_managers": (
        "team_manager",
        "stop_team_session_runtime_across_managers",
    ),
    "TeamMonitorHandler": ("handlers.team_monitor_handler", "TeamMonitorHandler"),
    "WorkflowMonitorHandler": (
        "handlers.workflow_monitor_handler",
        "WorkflowMonitorHandler",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attr_name)
    globals()[name] = value
    return value
