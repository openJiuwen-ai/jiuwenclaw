# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-global, idempotent tool registration helper.

Concurrent session agents under the same service/agent scope share tool resource
ids. Use ``ensure_tool_registered`` (double-checked locking) instead of bare
``add_tool`` so check-then-add races do not spam ``resource already exist``
ERROR logs from ``Runner.resource_mgr``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from openjiuwen.core.runner import Runner

logger = logging.getLogger(__name__)

_TOOL_REGISTER_LOCK = threading.Lock()


def ensure_tool_registered(tool: Any) -> Any:
    """Register ``tool`` in ``Runner.resource_mgr`` if missing (thread-safe).

    Returns the existing registered tool when the id is already present, otherwise
    the newly registered ``tool``. ``already exist`` from a lost race is treated as
    success so callers can still attach the tool card to ability_manager.
    """
    tool_id = getattr(getattr(tool, "card", None), "id", None)
    if not tool_id:
        Runner.resource_mgr.add_tool(tool)
        return tool

    existing = Runner.resource_mgr.get_tool(tool_id)
    if existing is not None:
        return existing

    with _TOOL_REGISTER_LOCK:
        existing = Runner.resource_mgr.get_tool(tool_id)
        if existing is not None:
            return existing
        result = Runner.resource_mgr.add_tool(tool)
        if result is not None and hasattr(result, "is_ok") and not result.is_ok():
            err_text = str(getattr(result, "value", result) or "")
            if "already exist" in err_text.lower():
                existing = Runner.resource_mgr.get_tool(tool_id)
                return existing if existing is not None else tool
            logger.warning(
                "[tool_registration] ensure_tool_registered failed: id=%s err=%s",
                tool_id,
                err_text,
            )
        return tool
