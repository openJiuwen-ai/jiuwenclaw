# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Startup compatibility patch for Agent-Core file operation history.

The current Agent-Core version does not expose a switch for its private file
operation history helpers.  JiuwenSwarm therefore keeps the product-facing
configuration and applies a process-wide patch at AgentServer startup when
history is explicitly disabled.

This module deliberately patches only history side effects.  File reads,
writes, edits, and shell commands continue to use Agent-Core's original
execution paths.  The patch is startup-only: changing the configuration while
the process is running requires an AgentServer restart.
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.common.config import is_file_operation_history_enabled

logger = logging.getLogger(__name__)

_PATCHED = False

_FILESYSTEM_HISTORY_HELPERS = (
    "_append_op_history",
    "_record_rm_targets_before_deletion",
    "_detect_and_record_deletions",
)
_SHELL_HISTORY_HELPERS = (
    "_record_rm_targets_before_deletion",
    "_detect_and_record_deletions",
)


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    """No-op replacement for Agent-Core's async history helpers."""
    del args, kwargs
    return None


def _patch_module_helpers(module: Any, helper_names: tuple[str, ...]) -> bool:
    """Replace helpers that still exist and warn for changed Agent-Core APIs."""
    patched_any = False
    module_name = getattr(module, "__name__", repr(module))
    for name in helper_names:
        if not hasattr(module, name):
            logger.warning(
                "Agent-Core 模块 %s 中未找到 %s，可能版本已变更；跳过该补丁",
                module_name,
                name,
            )
            continue
        setattr(module, name, _noop_async)
        patched_any = True
    return patched_any


def disable_file_operation_history() -> None:
    """Disable Agent-Core file operation history for this process.

    The filesystem module owns the original helper functions.  Bash and
    PowerShell import two of those helpers directly, so their module-local
    bindings must be replaced as well.

    The operation is idempotent and intentionally has no inverse operation.
    """
    global _PATCHED

    if _PATCHED:
        return

    try:
        import openjiuwen.harness.tools.filesystem as filesystem
        import openjiuwen.harness.tools.shell.bash._tool as bash_tool
        import openjiuwen.harness.tools.shell.powershell._tool as powershell_tool
    except ImportError as exc:
        # ModuleNotFoundError is an ImportError subclass.  Keep AgentServer
        # startup fail-open when Agent-Core changes or is not installed.
        logger.warning(
            "无法导入 Agent-Core 模块，跳过文件操作历史补丁，保持原行为: %s",
            exc,
        )
        return

    # write_file / edit_file history persistence and filesystem-side deletion
    # history processing.
    patched_any = _patch_module_helpers(filesystem, _FILESYSTEM_HISTORY_HELPERS)

    # These shell modules import the deletion helpers directly, which creates
    # independent module-local bindings that are not changed by patching
    # filesystem.* alone.
    for shell_module in (bash_tool, powershell_tool):
        patched_any = (
            _patch_module_helpers(shell_module, _SHELL_HISTORY_HELPERS)
            or patched_any
        )

    _PATCHED = patched_any
    if not _PATCHED:
        logger.warning(
            "Agent-Core 模块中未找到可替换的文件操作历史辅助函数，保持原行为"
        )
        return

    logger.info(
        "file_operation_history.enabled=false; Agent-Core file operation "
        "history disabled for this AgentServer process"
    )


def configure_file_operation_history(config: dict[str, Any] | None) -> None:
    """Apply the startup file-history policy from an already loaded config."""
    if is_file_operation_history_enabled(config):
        if _PATCHED:
            logger.warning(
                "file_operation_history is enabled in the reloaded config, "
                "but history remains disabled until AgentServer restart"
            )
        else:
            logger.info("file_operation_history.enabled=true; Agent-Core file operation history enabled")
        return

    disable_file_operation_history()
