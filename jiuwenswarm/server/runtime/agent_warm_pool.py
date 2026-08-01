"""Process-local pool of session-bound, ready-to-run DeepAgent instances."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jiuwenswarm.common.utils import get_agent_sessions_dir
from jiuwenswarm.common.work_mode import (
    DEFAULT_PROJECT_ID_CODE,
    DEFAULT_PROJECT_ID_WORK,
)
from jiuwenswarm.server.runtime.session import project_store

if TYPE_CHECKING:
    from jiuwenswarm.server.runtime.agent_manager import AgentManager
    from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


logger = logging.getLogger(__name__)


def _normalize_project_dir(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))


@dataclass(frozen=True, slots=True)
class WarmKey:
    channel_id: str
    project_id: str
    project_dir: str
    work_mode: str
    is_swarm: bool = False

    @property
    def agent_mode(self) -> str:
        return "code" if self.work_mode == "code" else "agent"


@dataclass(frozen=True, slots=True)
class WarmRevision:
    boot_id: str
    config_fingerprint: str
    sequence: int


@dataclass(slots=True)
class WarmSlot:
    key: WarmKey
    session_id: str
    revision: WarmRevision
    agent: "JiuWenSwarm"
    ready_at: float


@dataclass(frozen=True, slots=True)
class WarmClaim:
    session_id: str
    prewarm_hit: bool
    prewarm_status: str


class AgentWarmPool:
    """Own one unclaimed, initialized session per enabled channel/project key."""

    def __init__(self, manager: "AgentManager", *, max_concurrency: int = 2) -> None:
        self._manager = manager
        self._boot_id = uuid.uuid4().hex
        self._sequence = 0
        self._revision = WarmRevision(self._boot_id, "", 0)
        self._enabled_channels: set[str] = set()
        self._slots: dict[WarmKey, WarmSlot] = {}
        self._tasks: dict[WarmKey, asyncio.Task[None]] = {}
        self._task_revisions: dict[WarmKey, WarmRevision] = {}
        self._session_tasks: dict[str, asyncio.Task[None]] = {}
        self._claimed_pins: dict[str, "JiuWenSwarm"] = {}
        self._pin_release_tasks: set[asyncio.Task[None]] = set()
        self._failed: dict[WarmKey, str] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._closed = False
        self._marker_dir = get_agent_sessions_dir() / ".prewarm"
        self._cleanup_stale_markers()

    @property
    def boot_id(self) -> str:
        return self._boot_id

    @staticmethod
    def make_key(
        *,
        channel_id: str,
        project_id: str,
        project_dir: str | None,
        work_mode: str,
        is_swarm: bool = False,
    ) -> WarmKey:
        return WarmKey(
            channel_id=str(channel_id or "default").strip() or "default",
            project_id=str(project_id or "").strip(),
            project_dir=_normalize_project_dir(project_dir),
            work_mode="code" if str(work_mode).strip().lower() == "code" else "work",
            is_swarm=bool(is_swarm),
        )

    @staticmethod
    def config_fingerprint(config: Any, env: Any = None) -> str:
        payload = json.dumps(
            {"config": config, "env": env if isinstance(env, dict) else {}},
            sort_keys=True,
            ensure_ascii=False,
            default=repr,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _next_revision(self, config: Any, env: Any = None) -> WarmRevision:
        self._sequence += 1
        return WarmRevision(
            self._boot_id,
            self.config_fingerprint(config, env),
            self._sequence,
        )

    @staticmethod
    def _new_session_id(channel_id: str) -> str:
        prefix = str(channel_id or "default").strip() or "default"
        return f"{prefix}_{int(time.time() * 1000):x}_{secrets.token_hex(6)}"

    def _marker_path(self, session_id: str) -> Path:
        return self._marker_dir / f"{session_id}.json"

    def _write_marker(self, session_id: str, key: WarmKey) -> None:
        self._marker_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "boot_id": self._boot_id,
            "session_id": session_id,
            "key": {
                "channel_id": key.channel_id,
                "project_id": key.project_id,
                "project_dir": key.project_dir,
                "work_mode": key.work_mode,
                "is_swarm": key.is_swarm,
            },
        }
        self._marker_path(session_id).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def clear_marker(self, session_id: str) -> None:
        try:
            self._marker_path(session_id).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove prewarm marker: session_id=%s", session_id)

    def _cleanup_stale_markers(self) -> None:
        if not self._marker_dir.exists():
            return
        for marker in self._marker_dir.glob("*.json"):
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if payload.get("boot_id") != self._boot_id:
                    session_id = str(payload.get("session_id") or "").strip()
                    session_dir = (get_agent_sessions_dir() / session_id).resolve()
                    sessions_root = get_agent_sessions_dir().resolve()
                    has_valid_path = (
                        bool(session_id) and session_dir.parent == sessions_root
                    )
                    is_uninitialized = session_dir.is_dir() and not (
                        session_dir / "metadata.json"
                    ).exists()
                    if has_valid_path and is_uninitialized:
                        shutil.rmtree(session_dir)
                    marker.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                logger.warning("Invalid prewarm marker left in place: %s", marker)

    def _desired_keys(self) -> set[WarmKey]:
        projects = list(project_store.list_projects(cache_bust=True))
        records = [
            (p.project_id, p.project_dir, p.work_mode)
            for p in projects
            if not p.hidden
        ]
        records.extend(
            [
                (DEFAULT_PROJECT_ID_WORK, "", "work"),
                (DEFAULT_PROJECT_ID_CODE, "", "code"),
            ]
        )
        return {
            self.make_key(
                channel_id=channel,
                project_id=project_id,
                project_dir=project_dir,
                work_mode=work_mode,
            )
            for channel in self._enabled_channels
            for project_id, project_dir, work_mode in records
        }

    async def sync(
        self,
        enabled_channels: list[str],
        *,
        config: Any,
        env: Any = None,
    ) -> dict[str, int]:
        channels = {
            str(channel).strip()
            for channel in enabled_channels
            if str(channel).strip()
        }
        revision = self._next_revision(config, env)
        async with self._lock:
            if self._closed:
                return {
                    "target": 0,
                    "ready": 0,
                    "warming": 0,
                    "failed": 0,
                    "stale": 0,
                }
            config_changed = (
                self._revision.config_fingerprint != revision.config_fingerprint
            )
            self._enabled_channels = channels
            self._revision = revision
            desired = self._desired_keys()
            if config_changed:
                self._failed.clear()
            else:
                self._failed = {
                    key: error
                    for key, error in self._failed.items()
                    if key in desired
                }
            stale_slots = [
                slot
                for key, slot in list(self._slots.items())
                if key not in desired or slot.revision.config_fingerprint != revision.config_fingerprint
            ]
            for slot in stale_slots:
                self._slots.pop(slot.key, None)
            for key, task in list(self._tasks.items()):
                task_revision = self._task_revisions.get(key)
                if (
                    key not in desired
                    or task_revision is None
                    or task_revision.config_fingerprint != revision.config_fingerprint
                ):
                    if not task.done():
                        task.cancel()
                    self._tasks.pop(key, None)
                    self._task_revisions.pop(key, None)
            for key in desired:
                slot = self._slots.get(key)
                if slot is None and key not in self._tasks:
                    self._schedule_prepare_locked(key, revision)
        for slot in stale_slots:
            asyncio.create_task(self._dispose_slot(slot))
        return await self.stats()

    async def refresh(self, *, config: Any, env: Any = None) -> dict[str, int]:
        return await self.sync(
            sorted(self._enabled_channels),
            config=config,
            env=env,
        )

    def _schedule_prepare_locked(
        self,
        key: WarmKey,
        revision: WarmRevision,
        *,
        session_id: str | None = None,
        keep_as_slot: bool = True,
    ) -> tuple[str, asyncio.Task[None]]:
        sid = session_id or self._new_session_id(key.channel_id)
        task = asyncio.create_task(
            self._prepare(key, sid, revision, keep_as_slot=keep_as_slot),
            name=f"agent-prewarm-{sid}",
        )
        if keep_as_slot:
            self._tasks[key] = task
            self._task_revisions[key] = revision
        self._session_tasks[sid] = task
        return sid, task

    async def _prepare(
        self,
        key: WarmKey,
        session_id: str,
        revision: WarmRevision,
        *,
        keep_as_slot: bool,
    ) -> None:
        agent: "JiuWenSwarm | None" = None
        pinned = False
        published = False
        if keep_as_slot:
            self._write_marker(session_id, key)
        try:
            async with self._semaphore:
                agent = await self._manager.get_agent(
                    channel_id=key.channel_id,
                    mode=key.agent_mode,
                    project_dir=key.project_dir or None,
                )
                if agent is None:
                    raise RuntimeError("agent creation returned None")
                await agent.prepare_session(
                    session_id=session_id,
                    channel_id=key.channel_id,
                    mode="code.normal" if key.work_mode == "code" else "agent",
                    project_dir=key.project_dir or None,
                )
            if not keep_as_slot:
                return
            self._manager.pin_agent(agent)
            pinned = True
            async with self._lock:
                current = self._revision
                revision_changed = (
                    current.boot_id != revision.boot_id
                    or current.config_fingerprint != revision.config_fingerprint
                )
                stale = (
                    self._closed
                    or revision_changed
                    or key not in self._desired_keys()
                )
                if not stale:
                    self._slots[key] = WarmSlot(
                        key=key,
                        session_id=session_id,
                        revision=revision,
                        agent=agent,
                        ready_at=time.time(),
                    )
                    published = True
                    self._failed.pop(key, None)
            if stale:
                await self._dispose_runtime(
                    agent, key.channel_id, session_id, pinned=True
                )
                pinned = False
        except asyncio.CancelledError:
            if agent is not None:
                await self._dispose_runtime(
                    agent, key.channel_id, session_id, pinned=pinned
                )
            raise
        except Exception as exc:
            logger.exception("Agent prewarm failed: key=%s session_id=%s", key, session_id)
            if agent is not None:
                await self._dispose_runtime(
                    agent, key.channel_id, session_id, pinned=pinned
                )
            async with self._lock:
                self._failed[key] = str(exc)
        finally:
            if keep_as_slot and not published:
                self.clear_marker(session_id)
            async with self._lock:
                current_task = asyncio.current_task()
                if self._tasks.get(key) is current_task:
                    self._tasks.pop(key, None)
                    self._task_revisions.pop(key, None)
                if self._session_tasks.get(session_id) is current_task:
                    self._session_tasks.pop(session_id, None)

    async def claim(self, key: WarmKey) -> WarmClaim:
        if key.is_swarm:
            return WarmClaim(self._new_session_id(key.channel_id), False, "bypassed")
        async with self._lock:
            if self._closed:
                raise RuntimeError("agent warm pool is closed")
            is_desired = key in self._desired_keys()
            slot = self._slots.pop(key, None)
            if slot is not None:
                self._claimed_pins[slot.session_id] = slot.agent
                pin_task = asyncio.create_task(
                    self._release_claim_pin_after(slot.session_id, 300),
                    name=f"agent-prewarm-claim-pin-timeout-{slot.session_id}",
                )
                self._pin_release_tasks.add(pin_task)
                pin_task.add_done_callback(self._pin_release_tasks.discard)
                if key not in self._tasks:
                    self._schedule_prepare_locked(key, self._revision)
                return WarmClaim(slot.session_id, True, "ready")
            if key in self._tasks:
                # Keep the target slot intact and initialize this claimed session
                # independently; its per-session adapter lock deduplicates chat.send.
                sid, _ = self._schedule_prepare_locked(
                    key, self._revision, keep_as_slot=False
                )
            else:
                sid, _ = self._schedule_prepare_locked(
                    key, self._revision, keep_as_slot=False
                )
                if is_desired:
                    self._schedule_prepare_locked(key, self._revision)
            return WarmClaim(sid, False, "warming")

    async def wait_for_session(self, session_id: str) -> None:
        async with self._lock:
            task = self._session_tasks.get(str(session_id))
        try:
            if task is not None:
                await asyncio.shield(task)
        finally:
            async with self._lock:
                pinned_agent = self._claimed_pins.pop(str(session_id), None)
            if pinned_agent is not None:
                self._manager.unpin_agent(pinned_agent)

    async def release_claim_pin(self, session_id: str) -> None:
        async with self._lock:
            pinned_agent = self._claimed_pins.pop(str(session_id), None)
        if pinned_agent is not None:
            self._manager.unpin_agent(pinned_agent)

    async def _release_claim_pin_after(
        self, session_id: str, delay_seconds: float
    ) -> None:
        await asyncio.sleep(delay_seconds)
        await self.release_claim_pin(session_id)

    async def _dispose_runtime(
        self,
        agent: "JiuWenSwarm",
        channel_id: str,
        session_id: str,
        *,
        pinned: bool,
    ) -> None:
        try:
            await agent.cleanup_session_runtime(session_id)
        finally:
            if pinned:
                self._manager.unpin_agent(agent)
            self.clear_marker(session_id)

    async def _dispose_slot(self, slot: WarmSlot) -> None:
        await self._dispose_runtime(
            slot.agent, slot.key.channel_id, slot.session_id, pinned=True
        )

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            desired = self._desired_keys()
            return {
                "target": len(desired),
                "ready": len(self._slots),
                "warming": len(self._tasks),
                "failed": len(self._failed),
                "stale": max(0, len(self._slots) - len(desired)),
            }

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = list(
                {
                    *self._tasks.values(),
                    *self._session_tasks.values(),
                    *self._pin_release_tasks,
                }
            )
            slots = list(self._slots.values())
            claimed_agents = list(self._claimed_pins.values())
            self._tasks.clear()
            self._task_revisions.clear()
            self._session_tasks.clear()
            self._slots.clear()
            self._claimed_pins.clear()
            self._pin_release_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for slot in slots:
            await self._dispose_slot(slot)
        for agent in claimed_agents:
            self._manager.unpin_agent(agent)
