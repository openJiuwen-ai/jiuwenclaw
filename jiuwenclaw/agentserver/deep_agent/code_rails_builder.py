"""Build the small set of enterprise Code-mode rails.

The enterprise branch keeps one deep adapter for all modes.  This module keeps
Code-specific construction outside that adapter while allowing each optional
rail to fail independently.

This migration intentionally does not register Worktree, CodeAgent, UserHook,
Observability, or SkillRetrieval rails; those are outside the minimal Code-mode
scope.
"""

from __future__ import annotations

import os
from typing import Any

from jiuwenclaw.agentserver.deep_agent.rails.project_memory_rail import (
    ProjectMemoryRail,
)
from jiuwenclaw.utils import logger


def _code_memory_enabled(config: dict[str, Any]) -> bool:
    """Return the Code-mode memory switch with a safe default."""
    modes = config.get("modes", {}) if isinstance(config, dict) else {}
    code = modes.get("code", {}) if isinstance(modes, dict) else {}
    memory = code.get("memory", {}) if isinstance(code, dict) else {}
    return bool(memory.get("enabled", True)) if isinstance(memory, dict) else True


def _build_project_memory(
    *,
    config: dict[str, Any],
    project_dir: str | None,
    workspace_dir: str,
    language: str,
) -> ProjectMemoryRail | None:
    """Build ProjectMemoryRail when Code-mode memory is enabled."""
    if not _code_memory_enabled(config):
        logger.info(
            "[CodeRails] ProjectMemoryRail disabled by modes.code.memory.enabled"
        )
        return None

    try:
        modes = config.get("modes", {})
        code_mode = modes.get("code", {}) if isinstance(modes, dict) else {}
        memory_config = (
            code_mode.get("memory", {}) if isinstance(code_mode, dict) else {}
        )
        project_memory_config = config.get("project_memory", {})
        if not isinstance(project_memory_config, dict):
            project_memory_config = {}
        raw_dirs = memory_config.get(
            "additional_directories",
            project_memory_config.get("additional_directories"),
        )
        if isinstance(raw_dirs, str):
            additional_dirs = tuple(
                item.strip() for item in raw_dirs.split(os.pathsep) if item.strip()
            )
        elif isinstance(raw_dirs, (list, tuple, set)):
            additional_dirs = tuple(
                str(item).strip() for item in raw_dirs if str(item).strip()
            )
        else:
            additional_dirs = None
        raw_max_chars = memory_config.get(
            "max_chars",
            project_memory_config.get("max_chars", 60_000),
        )
        try:
            max_chars = max(1, int(raw_max_chars))
        except (TypeError, ValueError):
            logger.warning(
                "[CodeRails] invalid ProjectMemory max_chars=%r; using 60000",
                raw_max_chars,
            )
            max_chars = 60_000
        return ProjectMemoryRail(
            workspace=project_dir or workspace_dir or "./",
            language=language,
            max_chars=max_chars,
            additional_directories=additional_dirs,
        )
    except Exception as exc:  # noqa: BLE001 - optional rail boundary
        logger.warning("[CodeRails] ProjectMemoryRail build failed: %s", exc)
        return None


def _build_plan_rails() -> list[Any]:
    """Build the plan-mode rail group, or return an empty list if unsupported."""
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.code.code_agent_mode_rail import (
            CodeAgentModeRail,
        )
    except ImportError as exc:
        logger.error(
            "[CodeRails] AgentModeRail is unavailable; Code plan rails disabled: %s",
            exc,
        )
        return []

    rails: list[Any] = []
    builders = (
        (
            "CodeAgentModeRail",
            lambda: CodeAgentModeRail(allowed_tools=None),
        ),
        (
            "CodeConfirmInterruptRail",
            lambda: _build_code_confirm_rail(),
        ),
        (
            "PlanApprovalInterruptRail",
            lambda: _build_plan_approval_rail(),
        ),
    )
    for rail_name, builder in builders:
        try:
            rail = builder()
        except ImportError as exc:
            logger.error("[CodeRails] %s unavailable: %s", rail_name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - optional rail boundary
            logger.warning("[CodeRails] %s build failed: %s", rail_name, exc)
            continue
        rails.append(rail)
    return rails


def _build_code_confirm_rail() -> Any:
    """Build the optional generic confirmation rail independently."""
    from jiuwenclaw.agentserver.deep_agent.rails.code.code_confirm_interrupt_rail import (
        CodeConfirmInterruptRail,
    )

    return CodeConfirmInterruptRail(tool_names=["switch_mode"])


def _build_plan_approval_rail() -> Any:
    """Build the optional plan approval rail independently."""
    from jiuwenclaw.agentserver.deep_agent.rails.code.code_plan_approval_interrupt_rail import (
        PlanApprovalInterruptRail,
    )

    return PlanApprovalInterruptRail()


def build_code_mode_extra_rails(
    adapter: Any,
    config_base: dict[str, Any],
    *,
    project_dir: str | None,
    workspace_dir: str,
    language: str = "cn",
) -> list[Any]:
    """Build enterprise Code-mode rails in stable priority order.

    The adapter is used only for the existing CodingMemoryRail factory.  This
    avoids duplicating embedding configuration parsing in the new builder.
    """
    rails: list[Any] = []

    project_memory = _build_project_memory(
        config=config_base,
        project_dir=project_dir,
        workspace_dir=workspace_dir,
        language=language,
    )
    if project_memory is not None:
        rails.append(project_memory)

    if _code_memory_enabled(config_base):
        try:
            coding_memory = adapter.build_coding_memory_rail()
        except Exception as exc:  # noqa: BLE001 - optional rail boundary
            logger.warning("[CodeRails] CodingMemoryRail build failed: %s", exc)
            coding_memory = None
        if coding_memory is not None:
            rails.append(coding_memory)
    else:
        logger.info(
            "[CodeRails] CodingMemoryRail disabled by modes.code.memory.enabled"
        )

    rails.extend(_build_plan_rails())
    logger.info(
        "[CodeRails] Built Code-mode rails: %s",
        [type(rail).__name__ for rail in rails],
    )
    return rails


__all__ = ["build_code_mode_extra_rails"]
