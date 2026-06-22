# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-chat binding for registered skill dirs (reload session isolation)."""

from __future__ import annotations

import contextvars
from contextvars import Token

_SESSION_REGISTERED_SKILL_DIRS: contextvars.ContextVar[tuple[str, ...] | None] = (
    contextvars.ContextVar("jiuwenclaw_session_registered_skill_dirs", default=None)
)


def bind_session_registered_skill_dirs(dirs: list[str] | tuple[str, ...] | None) -> Token:
    """Bind adapter snapshot for the current async task (chat scope)."""
    value = tuple(str(d) for d in dirs) if dirs else None
    return _SESSION_REGISTERED_SKILL_DIRS.set(value)


def reset_session_registered_skill_dirs(token: Token) -> None:
    _SESSION_REGISTERED_SKILL_DIRS.reset(token)


def get_bound_session_registered_skill_dirs() -> list[str] | None:
    bound = _SESSION_REGISTERED_SKILL_DIRS.get()
    if bound is None:
        return None
    return list(bound)
