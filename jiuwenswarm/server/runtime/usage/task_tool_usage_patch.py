# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Monkeypatch TaskTool so subagent LLM usage reaches the parent stream.

``TaskTool`` drives subagents via ``invoke()``, which returns only the final
output string. Without this patch the parent's ``llm_usage`` accumulator never
sees those calls, so ``/usage`` under-reports whenever ``task_tool`` runs.

Pattern matches :mod:`jiuwenswarm.server.runtime.debug_trace.task_tool_patch`
and the team ``SwarmflowBudgetRail``: attach a rail that bills each model call,
then forward the captured metadata onto the parent session stream tagged with
``subagent_type`` so ``by_agent`` can attribute the dollars.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)
_PATCH_APPLIED = False


def _usage_meta_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        meta = usage.model_dump()
        return meta if isinstance(meta, dict) else None
    if isinstance(usage, dict):
        return dict(usage)
    return {
        "model_name": getattr(usage, "model_name", "") or "",
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cache_tokens": getattr(usage, "cache_tokens", 0) or 0,
    }


def _build_capture_rail() -> Any:
    """Build an AgentRail that records usage_metadata after each model call."""
    from openjiuwen.core.single_agent.rail.base import AgentRail, ModelCallInputs

    class UsageCaptureRail(AgentRail):
        priority: int = 100

        def __init__(self) -> None:
            super().__init__()
            self.events: list[dict[str, Any]] = []

        async def after_model_call(self, ctx: Any) -> None:
            inputs = ctx.inputs
            if not isinstance(inputs, ModelCallInputs):
                return
            meta = _usage_meta_dict(getattr(inputs.response, "usage_metadata", None))
            if meta is not None:
                self.events.append(meta)

    return UsageCaptureRail()


async def _emit_usage_to_parent(
    parent_session: Any,
    *,
    subagent_type: str,
    events: list[dict[str, Any]],
) -> None:
    """Write captured subagent usage onto the parent session stream."""
    if not events or parent_session is None:
        return
    try:
        from openjiuwen.core.session.stream.base import OutputSchema
    except Exception:
        _logger.debug("[TaskTool usage] OutputSchema import failed", exc_info=True)
        return

    write = getattr(parent_session, "write_stream", None)
    if write is None:
        return

    for meta in events:
        tagged = dict(meta)
        tagged["subagent_type"] = subagent_type
        tagged["agent_id"] = subagent_type
        try:
            await write(
                OutputSchema(
                    type="llm_usage",
                    index=0,
                    payload={
                        "usage_metadata": tagged,
                        "result_type": "answer",
                    },
                )
            )
        except Exception:
            _logger.debug(
                "[TaskTool usage] write_stream failed for subagent=%s",
                subagent_type,
                exc_info=True,
            )


def apply_task_tool_usage_patch() -> None:
    """Patch ``TaskTool.invoke`` to forward subagent usage to the parent stream.

    Idempotent. Apply *after* the debug-trace patch so this wrapper sits on the
    outside: debug may re-route to ``stream()``, but the capture rail still
    fires on every model call either way.
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    from openjiuwen.harness.tools.subagent.task_tool import TaskTool

    if getattr(TaskTool, "usage_cost_patch_applied", False):
        _PATCH_APPLIED = True
        return

    _inner_invoke = TaskTool.invoke

    async def _invoke_with_usage(self, inputs, **kwargs):  # type: ignore[no-untyped-def]
        from openjiuwen.core.session.agent import Session

        parent_session = kwargs.get("session", None)
        subagent_type = ""
        if isinstance(inputs, dict):
            subagent_type = str(inputs.get("subagent_type") or "").strip()

        parent_agent = getattr(self, "parent_agent", None)
        orig_create = getattr(parent_agent, "create_subagent", None) if parent_agent else None
        rails: list[Any] = []

        if callable(orig_create):
            def _create_with_rail(*args, **create_kwargs):  # type: ignore[no-untyped-def]
                subagent = orig_create(*args, **create_kwargs)
                try:
                    rail = _build_capture_rail()
                    if hasattr(subagent, "add_rail"):
                        subagent.add_rail(rail)
                        rails.append(rail)
                except Exception:
                    _logger.debug(
                        "[TaskTool usage] add_rail failed for subagent_type=%s",
                        subagent_type,
                        exc_info=True,
                    )
                return subagent

            parent_agent.create_subagent = _create_with_rail  # type: ignore[assignment]

        try:
            result = await _inner_invoke(self, inputs, **kwargs)
        finally:
            if parent_agent is not None and callable(orig_create):
                parent_agent.create_subagent = orig_create  # type: ignore[assignment]

        if isinstance(parent_session, Session) and subagent_type and rails:
            events: list[dict[str, Any]] = []
            for rail in rails:
                events.extend(getattr(rail, "events", []) or [])
            await _emit_usage_to_parent(
                parent_session, subagent_type=subagent_type, events=events
            )
        return result

    TaskTool.invoke = _invoke_with_usage  # type: ignore[assignment]
    TaskTool.usage_cost_patch_applied = True
    _PATCH_APPLIED = True


__all__ = ["apply_task_tool_usage_patch"]
