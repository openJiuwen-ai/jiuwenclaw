# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.extensions.agentos.agentos_router.models import AgentInfo, AgentStatus


AgentCreator = Callable[[AgentInfo], Awaitable[AgentInfo | None]]
AgentKey = tuple[str, str]
SUPPORTED_AGENT_TYPES = frozenset({"jiuwenswarm", "opencode", "claude"})


class AgentCreatingTimeout(TimeoutError):
    """Timed out while waiting for another request to create an Agent."""


class AgentDeleted(RuntimeError):
    """Agent was deleted while creation was in flight."""


@dataclass
class AgentRuntime:
    """In-process agent record: business info plus create-wait signaling."""

    info: AgentInfo
    creating_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def key(self) -> AgentKey:
        return self.info.user_id, self.info.agent_type

    def is_ready(self) -> bool:
        return self.info.status is AgentStatus.READY

    def is_creating(self) -> bool:
        return self.info.status is AgentStatus.CREATING

    def is_failed(self) -> bool:
        return self.info.status is AgentStatus.FAILED

    def is_deleted(self) -> bool:
        return self.info.status is AgentStatus.DELETED

    def snapshot(self) -> AgentRuntime:
        """Return a detached runtime view for callers (info only)."""
        return AgentRuntime(info=self.info.copy())

    def attach_to_envelope(self, envelope: E2AEnvelope) -> None:
        envelope.channel_context["agent_id"] = self.info.agent_id
        envelope.channel_context["agent_type"] = self.info.agent_type
        if self.info.sandbox_id:
            envelope.channel_context["sandbox_id"] = self.info.sandbox_id

    def reset_for_retry(self) -> None:
        self.info.status = AgentStatus.CREATING
        self.info.error = None
        self.info.updated_at = time.time()
        self.creating_event = asyncio.Event()

    @staticmethod
    def apply_creator_result(created: AgentInfo | None, *, base: AgentInfo) -> AgentInfo:
        resolved = created.copy() if created is not None else base.copy()
        resolved.agent_id = base.agent_id
        resolved.user_id = base.user_id
        resolved.agent_type = base.agent_type
        resolved.status = AgentStatus.READY
        resolved.error = None
        resolved.updated_at = time.time()
        return resolved

    def mark_ready(self, resolved: AgentInfo) -> None:
        self.info = resolved
        self.creating_event.set()

    def mark_failed(self, exc: BaseException) -> None:
        failed = self.info.copy()
        failed.status = AgentStatus.FAILED
        failed.error = str(exc)
        failed.updated_at = time.time()
        self.info = failed
        self.creating_event.set()

    def mark_deleted(self) -> None:
        deleted = self.info.copy()
        deleted.status = AgentStatus.DELETED
        deleted.error = "agent deleted"
        deleted.updated_at = time.time()
        self.info = deleted
        self.creating_event.set()

    async def wait_until_settled(self, timeout: float) -> None:
        await asyncio.wait_for(self.creating_event.wait(), timeout=timeout)

    @staticmethod
    def normalize_key(user_id: str, agent_type: str) -> AgentKey:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("user_id is required")
        return normalized_user_id, AgentRuntime.normalize_agent_type(agent_type)

    @staticmethod
    def normalize_agent_type(raw: Any) -> str:
        agent_type = str(raw or "jiuwenswarm").strip().lower()
        if agent_type not in SUPPORTED_AGENT_TYPES:
            raise ValueError(f"unsupported agent_type: {agent_type}")
        return agent_type

    @classmethod
    def for_key(
        cls,
        key: AgentKey,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRuntime:
        return cls(
            info=AgentInfo(
                user_id=key[0],
                agent_type=key[1],
                metadata=dict(metadata or {}),
            )
        )


class AgentManager:
    """In-memory Agent store keyed by (user_id, agent_type) with single-flight creation."""

    def __init__(self, *, creating_timeout_seconds: float = 60.0) -> None:
        self._runtimes: dict[AgentKey, AgentRuntime] = {}
        self._runtimes_lock = asyncio.Lock()
        self._creating_timeout_seconds = max(0.1, float(creating_timeout_seconds))

    async def get_or_create_agent(
        self,
        user_id: str,
        agent_type: str,
        *,
        creator: AgentCreator | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRuntime:
        """Get a READY Agent runtime or create one, waiting for in-flight creation."""

        key = AgentRuntime.normalize_key(user_id, agent_type)
        wait_timeout = (
            self._creating_timeout_seconds
            if timeout_seconds is None
            else max(0.1, float(timeout_seconds))
        )

        while True:
            owner = False
            async with self._runtimes_lock:
                existing = self._runtimes.get(key)
                if existing is not None and existing.is_ready():
                    return existing.snapshot()

                if existing is None:
                    runtime = AgentRuntime.for_key(key, metadata=metadata)
                    self._runtimes[key] = runtime
                    owner = True
                else:
                    runtime = existing
                    if runtime.is_failed():
                        runtime.reset_for_retry()
                        owner = True
                creator_base = runtime.info.copy()

            if owner:
                return await self._run_creator(
                    key, creator_base, creator, owner_runtime=runtime
                )

            try:
                await runtime.wait_until_settled(wait_timeout)
            except asyncio.TimeoutError as exc:
                raise AgentCreatingTimeout(
                    f"AGENT_CREATING_TIMEOUT: user_id={key[0]} "
                    f"agent_type={key[1]}"
                ) from exc
            if runtime.is_deleted():
                raise AgentDeleted(
                    f"AGENT_DELETED: user_id={key[0]} agent_type={key[1]}"
                )

    async def get_agent(self, user_id: str, agent_type: str) -> AgentRuntime | None:
        key = AgentRuntime.normalize_key(user_id, agent_type)
        async with self._runtimes_lock:
            runtime = self._runtimes.get(key)
            return runtime.snapshot() if runtime is not None else None

    async def delete_agent(self, user_id: str, agent_type: str) -> None:
        key = AgentRuntime.normalize_key(user_id, agent_type)
        async with self._runtimes_lock:
            runtime = self._runtimes.pop(key, None)
        if runtime is not None:
            runtime.mark_deleted()

    async def list_user_agents(self, user_id: str) -> list[AgentRuntime]:
        normalized_user_id = str(user_id or "").strip()
        async with self._runtimes_lock:
            return [
                runtime.snapshot()
                for (uid, _), runtime in self._runtimes.items()
                if uid == normalized_user_id
            ]

    async def _mark_creator_failed(
        self,
        key: AgentKey,
        exc: BaseException,
        *,
        owner_runtime: AgentRuntime,
    ) -> None:
        async with self._runtimes_lock:
            if self._runtimes.get(key) is not owner_runtime:
                return
            owner_runtime.mark_failed(exc)

    async def _run_creator(
        self,
        key: AgentKey,
        agent: AgentInfo,
        creator: AgentCreator | None,
        *,
        owner_runtime: AgentRuntime,
    ) -> AgentRuntime:
        try:
            created = await creator(agent.copy()) if creator is not None else agent
            resolved = AgentRuntime.apply_creator_result(created, base=agent)
            async with self._runtimes_lock:
                if self._runtimes.get(key) is not owner_runtime:
                    raise AgentDeleted(
                        f"AGENT_DELETED: user_id={key[0]} agent_type={key[1]}"
                    )
                owner_runtime.mark_ready(resolved)
                return owner_runtime.snapshot()
        except asyncio.CancelledError as exc:
            await self._mark_creator_failed(
                key, exc, owner_runtime=owner_runtime
            )
            raise
        except AgentDeleted:
            raise
        except Exception as exc:
            await self._mark_creator_failed(
                key, exc, owner_runtime=owner_runtime
            )
            raise
