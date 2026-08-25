# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session id helpers for invoke meta-tools."""

from __future__ import annotations

from typing import Any


def resolve_session_id(inputs: dict[str, Any] | None = None, **kwargs: Any) -> str | None:
    """Resolve session id from kwargs/inputs/session object."""
    merged: dict[str, Any] = {}
    if isinstance(inputs, dict):
        merged.update(inputs)
    merged.update(kwargs)

    for key in ("sessionId", "session_id"):
        value = merged.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    session = merged.get("session")
    if session is not None:
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            try:
                sid = getter()
                if sid is not None and str(sid).strip():
                    return str(sid).strip()
            except Exception:  # noqa: BLE001
                pass
        sid = getattr(session, "session_id", None) or getattr(session, "id", None)
        if sid is not None and str(sid).strip():
            return str(sid).strip()
    return None
