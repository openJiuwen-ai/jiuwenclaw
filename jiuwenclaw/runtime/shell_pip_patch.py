# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Monkey-patch openjiuwen ShellOperation so bash/shell tools use the runtime venv."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, AsyncIterator, Callable, Dict, Optional

from jiuwenclaw.runtime.pip_env import rewrite_shell_command, runtime_subprocess_env

logger = logging.getLogger(__name__)

_ISOLATION_ENV_KEYS = ("PATH", "VIRTUAL_ENV", "PYTHONPATH")
_PATCHED_ATTR = "_jiuwenclaw_pip_isolation_patched"


def _merge_isolation_environment(
    environment: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    iso = runtime_subprocess_env()
    overrides = {key: iso[key] for key in _ISOLATION_ENV_KEYS if key in iso}
    if environment is None:
        return overrides or None
    merged = dict(environment)
    merged.update(overrides)
    return merged


def _apply_shell_isolation(
    command: str,
    environment: Optional[Dict[str, str]],
) -> tuple[str, Optional[Dict[str, str]]]:
    rewritten = rewrite_shell_command(command or "")
    merged_env = _merge_isolation_environment(environment)
    if rewritten != (command or ""):
        logger.debug("[shell_pip_patch] Rewrote shell command: %s -> %s", command, rewritten)
    return rewritten, merged_env


def _wrap_execute_cmd(
    orig: Callable[..., Any],
) -> Callable[..., Any]:
    @wraps(orig)
    async def patched(
        self,
        command: str,
        *,
        environment: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ):
        command, environment = _apply_shell_isolation(command, environment)
        return await orig(self, command, environment=environment, **kwargs)

    return patched


def _wrap_execute_cmd_stream(
    orig: Callable[..., AsyncIterator[Any]],
) -> Callable[..., AsyncIterator[Any]]:
    @wraps(orig)
    async def patched(
        self,
        command: str,
        *,
        environment: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        command, environment = _apply_shell_isolation(command, environment)
        async for item in orig(self, command, environment=environment, **kwargs):
            yield item

    return patched


def apply_shell_pip_isolation_patch() -> None:
    """Ensure all SysOperation shell executions use the isolated runtime venv."""
    try:
        from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
    except ImportError:
        logger.debug("[shell_pip_patch] ShellOperation not available; skip patch")
        return

    if getattr(ShellOperation, _PATCHED_ATTR, False):
        return

    ShellOperation.execute_cmd = _wrap_execute_cmd(ShellOperation.execute_cmd)
    ShellOperation.execute_cmd_stream = _wrap_execute_cmd_stream(
        ShellOperation.execute_cmd_stream,
    )
    ShellOperation.execute_cmd_background = _wrap_execute_cmd(
        ShellOperation.execute_cmd_background,
    )
    setattr(ShellOperation, _PATCHED_ATTR, True)
    logger.info("[shell_pip_patch] Applied ShellOperation pip isolation patch")
