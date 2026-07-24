# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenSwarm-side Coding Memory rail adjustments."""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails import CodingMemoryRail as _BaseCodingMemoryRail


class CodingMemoryRail(_BaseCodingMemoryRail):
    """Keep Coding Memory cold-start indexing out of the request path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._manager_init_task: asyncio.Task[None] | None = None

    async def before_invoke(self, ctx: Any) -> None:
        """Start manager initialization without delaying the user request."""
        if not self._manager_initialized and self._manager_init_task is None:
            self._manager_init_task = asyncio.create_task(
                self._initialize_manager_in_background(ctx),
                name="coding-memory-init",
            )

        self._recalled_content = None
        self._prefetch_task = None

        is_read_only = self._read_only_tools or self._is_read_only(ctx.inputs)
        if not is_read_only and self._manager:
            query = self._extract_last_user_query(ctx)
            if query:
                self._prefetch_task = asyncio.create_task(self._auto_recall(query))

    async def _initialize_manager_in_background(self, ctx: Any) -> None:
        """Run the base initializer and record success or degraded state."""
        cancelled = False
        try:
            await self._init_coding_memory_manager(ctx)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[CodingMemoryRail] background initialization failed: %s",
                exc,
            )
        finally:
            # A cancelled task may finish after uninit() and a new lifecycle
            # has started. Only the current task may update this rail state.
            if not cancelled and self._manager_init_task is asyncio.current_task():
                self._manager_initialized = True

    @staticmethod
    def _is_read_only(inputs: Any) -> bool:
        """Support callback inputs and lightweight test doubles."""
        values = []
        for name in ("is_cron", "is_heartbeat"):
            value = getattr(inputs, name, False)
            values.append(value() if callable(value) else value)
        return any(values)

    def uninit(self, agent: Any) -> None:
        """Cancel pending initialization before the rail is torn down."""
        task = self._manager_init_task
        self._manager_init_task = None
        if task is not None and not task.done():
            task.cancel()
        super().uninit(agent)


__all__ = ["CodingMemoryRail"]
