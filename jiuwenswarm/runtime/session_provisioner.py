# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Transport-neutral product Session lifecycle orchestration.

The provisioner owns the business transaction behind ``session.delete``.
AgentServer remains responsible only for request/wire translation, while any
in-process Runtime client can use the same operation without constructing a
Server or a transport.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from jiuwenswarm.runtime.plan import PlanModeController
    from jiuwenswarm.server.runtime.agent_manager import AgentManager

logger = logging.getLogger(__name__)


class SessionDeleteLifecycle(Protocol):
    """Optional Runtime capability that participates in Session deletion."""

    async def begin_session_delete(self, session_id: str) -> None:
        """Quiesce dependent work before destructive deletion."""
        ...

    async def abort_session_delete(
        self,
        session_id: str,
        *,
        channel_id: str = "",
    ) -> None:
        """Restore dependent work after deletion fails."""
        ...

    async def commit_session_delete(self, session_id: str) -> None:
        """Apply dependent deletion policy after the Session is gone."""
        ...


@dataclass(frozen=True, slots=True)
class SessionDeleteResult:
    """Transport-independent result of one product Session deletion."""

    ok: bool
    session_id: str
    channel_id: str | None = None
    is_team: bool = False
    team_name: str = ""
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def failure(
        cls,
        session_id: str,
        *,
        code: str,
        message: str,
    ) -> SessionDeleteResult:
        return cls(
            ok=False,
            session_id=session_id,
            error_code=code,
            error_message=message,
        )


class RuntimeSessionProvisioner:
    """Coordinate persistent Session deletion for one shared Runtime."""

    def __init__(
        self,
        *,
        agent_manager: AgentManager,
        plan_controller: PlanModeController,
        delete_lifecycle: SessionDeleteLifecycle | None = None,
    ) -> None:
        self._agent_manager = agent_manager
        self._plan_controller = plan_controller
        self._delete_lifecycle = delete_lifecycle

    def set_delete_lifecycle(
        self,
        lifecycle: SessionDeleteLifecycle | None,
    ) -> None:
        """Replace the optional lifecycle participant owned by the host."""
        self._delete_lifecycle = lifecycle

    async def delete_session(
        self,
        *,
        channel_id: str,
        session_id: str,
        cleanup_session: Callable[..., Awaitable[bool]],
    ) -> SessionDeleteResult:
        """Delete one Session while preserving the established transaction."""
        target = str(session_id or "").strip()
        if not target:
            return SessionDeleteResult.failure(
                target,
                code="BAD_REQUEST",
                message="session_id is required",
            )

        from jiuwenswarm.common.utils import get_agent_sessions_dir
        from jiuwenswarm.server.runtime.session.session_history import (
            resolve_session_dir,
        )

        session_dir, invalid_reason = resolve_session_dir(
            target,
            sessions_root=get_agent_sessions_dir(),
        )
        if session_dir is None:
            return SessionDeleteResult.failure(
                target,
                code="BAD_REQUEST",
                message=invalid_reason or "invalid session_id",
            )
        if not session_dir.exists():
            return SessionDeleteResult.failure(
                target,
                code="NOT_FOUND",
                message="session not found",
            )
        if not session_dir.is_dir():
            return SessionDeleteResult.failure(
                target,
                code="BAD_REQUEST",
                message="session is not a directory",
            )

        # Keep one participant for the whole transaction.  AgentServer only
        # replaces this dependency while constructing/rebuilding Runtime, but
        # taking a snapshot also prevents a concurrent host reconfiguration
        # from pairing one lifecycle's begin with another one's abort/commit.
        delete_lifecycle = self._delete_lifecycle
        checkpoint_error = await self._ensure_delete_dependencies(
            target,
            delete_lifecycle=delete_lifecycle,
        )
        if checkpoint_error is not None:
            return checkpoint_error

        from jiuwenswarm.common.mode_matrix import is_team_mode
        from jiuwenswarm.server.runtime.session.session_metadata import (
            get_session_metadata,
        )

        metadata = get_session_metadata(target)
        is_team_session = is_team_mode(metadata.get("mode"))
        team_name = str(metadata.get("team_name") or "").strip()
        resolved_channel_id = (
            str(metadata.get("channel_id") or channel_id or "").strip() or None
        )
        result = SessionDeleteResult(
            ok=True,
            session_id=target,
            channel_id=resolved_channel_id,
            is_team=is_team_session,
            team_name=team_name,
        )

        self._mark_kvc_session_deleted(result)
        trajectory_prepared = False
        lifecycle_prepared = False
        try:
            if not is_team_session:
                from jiuwenswarm.observability.session_delete import (
                    begin_trajectory_session_delete,
                )

                begin_trajectory_session_delete(target)
                trajectory_prepared = True
            if delete_lifecycle is not None:
                await delete_lifecycle.begin_session_delete(target)
                lifecycle_prepared = True

            if is_team_session:
                deleted = await self._delete_team_session(result)
            else:
                await self._delete_agent_session(
                    result,
                    cleanup_session=cleanup_session,
                )
                deleted = True
            if deleted:
                shutil.rmtree(session_dir)
        except BaseException as exc:
            await self._abort_delete(
                result,
                trajectory_prepared=trajectory_prepared,
                lifecycle_prepared=lifecycle_prepared,
                delete_lifecycle=delete_lifecycle,
            )
            if not isinstance(exc, Exception):
                raise
            logger.warning(
                "Runtime session.delete cleanup failed: session_id=%s error=%s",
                target,
                exc,
            )
            return self._cleanup_failed(target)

        if not deleted:
            await self._abort_delete(
                result,
                trajectory_prepared=trajectory_prepared,
                lifecycle_prepared=lifecycle_prepared,
                delete_lifecycle=delete_lifecycle,
            )
            return self._cleanup_failed(target)

        await self._commit_delete_observers(
            result,
            trajectory_prepared=trajectory_prepared,
            lifecycle_prepared=lifecycle_prepared,
            delete_lifecycle=delete_lifecycle,
        )
        return result

    def commit_session_delete(self, result: SessionDeleteResult) -> None:
        """Commit Runtime-owned Plan/cache/binding state after disk deletion."""
        if not result.ok:
            raise ValueError("cannot commit a failed session delete")
        self._plan_controller.reset_session(result.session_id)

        from jiuwenswarm.server.runtime.session.session_metadata import (
            remove_session_metadata_cache,
        )

        remove_session_metadata_cache(result.session_id)
        if not result.is_team:
            return
        try:
            from jiuwenswarm.server.runtime.team_binding_store import (
                get_team_binding_store,
            )

            get_team_binding_store().unbind_session(
                team_name=result.team_name or None,
                session_id=result.session_id,
            )
        except Exception as exc:  # noqa: BLE001 - deletion already committed
            logger.warning(
                "Runtime failed to unbind deleted team session: "
                "session_id=%s team_name=%s error=%s",
                result.session_id,
                result.team_name,
                exc,
            )

    async def _ensure_delete_dependencies(
        self,
        session_id: str,
        *,
        delete_lifecycle: SessionDeleteLifecycle | None,
    ) -> SessionDeleteResult | None:
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
                ensure_persistent_checkpointer,
            )

            await ensure_persistent_checkpointer()
        except Exception as exc:  # noqa: BLE001 - public result compatibility
            logger.exception(
                "Runtime persistent checkpointer unavailable: session_id=%s error=%s",
                session_id,
                exc,
            )
            return SessionDeleteResult.failure(
                session_id,
                code="CHECKPOINT_UNAVAILABLE",
                message="persistent checkpointer is unavailable",
            )

        if delete_lifecycle is None or bool(
            getattr(delete_lifecycle, "is_available", True)
        ):
            return None
        start = getattr(delete_lifecycle, "start", None)
        if not callable(start):
            return None
        try:
            await start()
        except Exception as exc:  # noqa: BLE001 - established best effort
            logger.warning(
                "Runtime Session delete lifecycle is not ready yet: %s",
                exc,
            )
        return None

    @staticmethod
    def _cleanup_failed(session_id: str) -> SessionDeleteResult:
        return SessionDeleteResult.failure(
            session_id,
            code="DELETE_FAILED",
            message="session runtime cleanup failed",
        )

    @staticmethod
    def _mark_kvc_session_deleted(result: SessionDeleteResult) -> None:
        try:
            from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_product_hooks import (
                mark_session_deleted,
            )

            mark_session_deleted(
                session_id=result.session_id,
                channel_id=result.channel_id or "default",
                is_team=result.is_team,
            )
        except Exception as exc:  # noqa: BLE001 - established best effort
            logger.warning(
                "Runtime KVC delete tombstone failed; preserving product delete: "
                "session_id=%s error=%s",
                result.session_id,
                exc,
            )

    async def _delete_team_session(self, result: SessionDeleteResult) -> bool:
        from jiuwenswarm.agents.harness.team import get_team_manager

        return await get_team_manager(result.channel_id).delete_session_runtime(
            result.session_id,
            reason="session.delete: ",
        )

    async def _delete_agent_session(
        self,
        result: SessionDeleteResult,
        *,
        cleanup_session: Callable[..., Awaitable[bool]],
    ) -> None:
        await self._agent_manager.release_subagent_runtime_for_session(
            channel_id=result.channel_id,
            session_id=result.session_id,
            reason="session_deleted",
        )
        await cleanup_session(
            channel_id=result.channel_id or "",
            session_id=result.session_id,
            reset_plan_state=False,
        )

        from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_product_hooks import (
            evict_plan_session,
        )

        await evict_plan_session(
            session_id=result.session_id,
            agent_manager=self._agent_manager,
            channel_id=result.channel_id,
        )
        from openjiuwen.core.runner import Runner

        await Runner.release(result.session_id)

    async def _abort_delete(
        self,
        result: SessionDeleteResult,
        *,
        trajectory_prepared: bool,
        lifecycle_prepared: bool,
        delete_lifecycle: SessionDeleteLifecycle | None,
    ) -> None:
        if trajectory_prepared:
            try:
                from jiuwenswarm.observability.session_delete import (
                    abort_trajectory_session_delete,
                )

                abort_trajectory_session_delete(result.session_id)
            except Exception as exc:  # noqa: BLE001 - preserve primary failure
                logger.warning(
                    "Runtime trajectory delete rollback failed: session_id=%s error=%s",
                    result.session_id,
                    exc,
                )
        if lifecycle_prepared and delete_lifecycle is not None:
            try:
                await delete_lifecycle.abort_session_delete(
                    result.session_id,
                    channel_id=result.channel_id or "",
                )
            except Exception as exc:  # noqa: BLE001 - preserve primary failure
                logger.warning(
                    "Runtime Session delete lifecycle rollback failed: "
                    "session_id=%s error=%s",
                    result.session_id,
                    exc,
                )
        try:
            from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_product_hooks import (
                restore_session_after_failed_delete,
            )

            restore_session_after_failed_delete(result.session_id)
        except Exception as exc:  # noqa: BLE001 - preserve primary failure
            logger.warning(
                "Runtime KVC failed-delete rollback failed: session_id=%s error=%s",
                result.session_id,
                exc,
            )

    async def _commit_delete_observers(
        self,
        result: SessionDeleteResult,
        *,
        trajectory_prepared: bool,
        lifecycle_prepared: bool,
        delete_lifecycle: SessionDeleteLifecycle | None,
    ) -> None:
        if trajectory_prepared:
            try:
                from jiuwenswarm.observability.session_delete import (
                    commit_trajectory_session_delete,
                )

                commit_trajectory_session_delete(result.session_id)
            except Exception as exc:  # noqa: BLE001 - deletion already committed
                logger.warning(
                    "Runtime trajectory delete commit failed: session_id=%s error=%s",
                    result.session_id,
                    exc,
                )
        if lifecycle_prepared and delete_lifecycle is not None:
            try:
                await delete_lifecycle.commit_session_delete(
                    result.session_id,
                )
            except Exception as exc:  # noqa: BLE001 - deletion already committed
                logger.warning(
                    "Runtime Session delete lifecycle commit failed: "
                    "session_id=%s error=%s",
                    result.session_id,
                    exc,
                )


__all__ = [
    "RuntimeSessionProvisioner",
    "SessionDeleteLifecycle",
    "SessionDeleteResult",
]
