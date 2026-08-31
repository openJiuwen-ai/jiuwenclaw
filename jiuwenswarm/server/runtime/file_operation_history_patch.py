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


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    """No-op replacement for Agent-Core's async history helpers."""
    del args, kwargs
    return None


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

    import openjiuwen.harness.tools.filesystem as filesystem
    import openjiuwen.harness.tools.shell.bash._tool as bash_tool
    import openjiuwen.harness.tools.shell.powershell._tool as powershell_tool

    # write_file / edit_file history persistence and filesystem-side deletion
    # history processing.
    for name in (
        "_append_op_history",
        "_record_rm_targets_before_deletion",
        "_detect_and_record_deletions",
    ):
        setattr(filesystem, name, _noop_async)

    # These shell modules import the deletion helpers directly, which creates
    # independent module-local bindings that are not changed by patching
    # filesystem.* alone.
    for shell_module in (bash_tool, powershell_tool):
        for name in (
            "_record_rm_targets_before_deletion",
            "_detect_and_record_deletions",
        ):
            setattr(shell_module, name, _noop_async)

    _PATCHED = True
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
