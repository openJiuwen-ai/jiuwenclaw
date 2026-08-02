# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Adapt jiuwenclaw PermissionEngine results for harness TeamPermissionRail.

``TeamPermissionRail`` subclasses harness ``PermissionInterruptRail`` and
compares against harness ``PermissionLevel``. Teammate ASK must still use the
hosted leader path, but evaluation should match plan/leader (jiuwenclaw)
semantics — especially ``tools.<name>=allow`` must not be re-escalated by the
harness shell-subcommand fallback.
"""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.harness.security.models import (
    PermissionLevel as HarnessPermissionLevel,
    PermissionResult as HarnessPermissionResult,
)


class JiuwenclawPermissionEngineAdapter:
    """Duck-typed engine for harness rails; delegates checks to jiuwenclaw."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.config: dict[str, Any] = dict(getattr(engine, "config", None) or {})

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._engine, "enabled", True))

    @property
    def llm(self) -> Any:
        return getattr(self._engine, "llm", None)

    @property
    def model_name(self) -> str | None:
        return getattr(self._engine, "model_name", None)

    def update_config(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})
        self._engine.update_config(self.config)

    def update_llm(self, llm: Any, model_name: str | None) -> None:
        updater = getattr(self._engine, "update_llm", None)
        if callable(updater):
            updater(llm, model_name)

    def set_permission_checks_active(self, fn: Callable[[], bool] | None) -> None:
        setter = getattr(self._engine, "set_permission_checks_active", None)
        if callable(setter):
            setter(fn)

    def update_trusted_dirs(self, trusted_dirs: list[Any]) -> None:
        updater = getattr(self._engine, "update_trusted_dirs", None)
        if callable(updater):
            updater(trusted_dirs)

    async def check_permission(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> HarnessPermissionResult:
        result = await self._engine.check_permission(
            tool_name=tool_name,
            tool_args=tool_args if isinstance(tool_args, dict) else {},
            channel_id="web",
        )
        level_raw = getattr(getattr(result, "permission", None), "value", None) or str(
            getattr(result, "permission", "ask")
        )
        try:
            level = HarnessPermissionLevel(str(level_raw).strip().lower())
        except ValueError:
            level = HarnessPermissionLevel.ASK
        return HarnessPermissionResult(
            permission=level,
            matched_rule=getattr(result, "matched_rule", None),
            reason=getattr(result, "reason", None),
            external_paths=getattr(result, "external_paths", None),
        )


__all__ = ["JiuwenclawPermissionEngineAdapter"]
