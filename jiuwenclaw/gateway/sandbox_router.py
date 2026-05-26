from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jiuwenclaw.sandbox.sandbox_client import SandboxClient, SandboxConfig
from jiuwenclaw.sandbox.claw_api_key import get_claw_api_key
from jiuwenclaw.sandbox.sandbox_routing_settings import SandboxRoutingSettings
from jiuwenclaw.sandbox.sandbox_dcs_store import SandboxDcsStore
from jiuwenclaw.sandbox.sandbox_init_data import upload_sandbox_init_data
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.gateway.open_ability_client import OpenAbilityWebSocketClient
from jiuwenclaw.sandbox.open_ability import (
    OpenAbilityConfig,
    OpenAbilityEndpoint,
    build_openability_ws_uri,
)
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenclaw.utils import logger


class SandboxStatus(str, Enum):
    INITIALIZING = "initializing"
    BUSY = "busy"
    IDLE = "idle"
    TERMINATING = "terminating"
    TERMINATED = "terminated"


@dataclass
class SandboxRuntime:
    routing_key: str
    sandbox_id: str
    agent_client: AgentServerClient
    status: SandboxStatus = SandboxStatus.INITIALIZING
    task_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class SandboxRouterAgentClient(AgentServerClient):
    def __init__(
        self,
        *,
        max_sandboxes: int | None = None,
        queue_max_size: int | None = None,
        queue_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        idle_check_interval_seconds: float | None = None,
    ) -> None:
        settings = SandboxRoutingSettings.from_env()
        self._sandbox_client: SandboxClient | None = None
        self._dcs_store: SandboxDcsStore | None = None
        self._open_ability_config: OpenAbilityConfig | None = None
        self._max_sandboxes = max(
            1,
            int(max_sandboxes if max_sandboxes is not None else settings.max_sandboxes),
        )
        self._queue_enabled = True
        self._queue_max_size = max(
            1,
            int(queue_max_size if queue_max_size is not None else settings.queue_max_size),
        )
        self._queue_timeout_seconds = float(
            queue_timeout_seconds
            if queue_timeout_seconds is not None
            else settings.queue_timeout_seconds
        )
        self._idle_timeout_seconds = float(
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else settings.idle_timeout_seconds
        )
        self._idle_check_interval_seconds = float(
            idle_check_interval_seconds
            if idle_check_interval_seconds is not None
            else settings.idle_check_interval_seconds
        )
        self._runtimes: dict[str, SandboxRuntime] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pool_lock = asyncio.Lock()
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._creating_count = 0
        self._server_config: dict[str, Any] = {}
        self._server_env: dict[str, str] | None = None
        self._idle_task: asyncio.Task | None = None
        self._closed = False
        # AgentServer 主动推送（含 chat/skilldev ask_user_question）的下行处理器；
        # 每条 OA 物理连接独立持有，需在创建 client 时传递。
        self._on_server_push: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None

    async def connect(self, uri: str) -> None:
        self._ensure_idle_task()

    async def disconnect(self) -> None:
        self._closed = True
        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
        for routing_key in list(self._runtimes):
            await self._terminate_runtime(routing_key)
        if self._sandbox_client is not None:
            await self._sandbox_client.close()
        if self._dcs_store is not None:
            await self._dcs_store.close()

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        self._server_config = dict(config or {})
        self._server_env = dict(env) if env else None

    def set_server_push_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """注册 AgentServer ``send_push`` 下行处理器，并下发到已建好的所有 OA client。

        MessageHandler 仅在 __init__ 时给 router 调用一次；之后每条 OA 物理连接
        在 ``_connect_open_ability_client`` 中创建时统一沿用当前 handler。
        """
        self._on_server_push = handler
        for runtime in self._runtimes.values():
            ac = runtime.agent_client
            if hasattr(ac, "set_server_push_handler"):
                ac.set_server_push_handler(handler)

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        try:
            runtime = await self._acquire_runtime(envelope)
        except ValueError as exc:
            return self._routing_error_response(envelope, str(exc))
        try:
            return await runtime.agent_client.send_request(envelope)
        finally:
            await self._mark_task_done(runtime)

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        try:
            runtime = await self._acquire_runtime(envelope)
        except ValueError as exc:
            yield self._routing_error_chunk(envelope, str(exc))
            return
        try:
            async for chunk in runtime.agent_client.send_request_stream(envelope):
                yield chunk
        finally:
            await self._mark_task_done(runtime)

    def _routing_key(self, user_id: str | None, session_id: str | None) -> str:
        uid = str(user_id or "").strip()
        if uid:
            return f"vibeskill:user:{uid}"
        sid = str(session_id or "").strip()
        if sid:
            return f"vibeskill:session:{sid}"
        raise ValueError("user_id or session_id is required for sandbox routing")

    def _routing_error_response(self, envelope: E2AEnvelope, message: str) -> AgentResponse:
        return AgentResponse(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            ok=False,
            payload={"error": message},
        )

    def _routing_error_chunk(self, envelope: E2AEnvelope, message: str) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            payload={"error": message},
            is_complete=True,
        )

    async def _acquire_runtime(self, envelope: E2AEnvelope) -> SandboxRuntime:
        routing_key = self._routing_key(envelope.user_id, envelope.session_id)
        return await self._acquire_runtime_for_key(
            routing_key,
            user_id=envelope.user_id,
            session_id=envelope.session_id,
        )

    async def _acquire_runtime_for_key(
        self,
        routing_key: str,
        *,
        user_id: str | None,
        session_id: str | None,
    ) -> SandboxRuntime:
        self._ensure_idle_task()
        lock = self._locks.setdefault(routing_key, asyncio.Lock())
        async with lock:
            while True:
                async with self._pool_lock:
                    runtime = self._runtimes.get(routing_key)
                    if runtime is None:
                        break
                    if runtime.status != SandboxStatus.TERMINATING:
                        self._mark_task_start_unlocked(runtime)
                        return runtime
                await asyncio.sleep(0.01)
            await self._wait_for_capacity(routing_key)
            async with self._pool_lock:
                self._creating_count += 1
            sandbox_id: str | None = None
            dcs_registered = False
            try:
                sandbox_client = self._get_sandbox_client()
                result = await sandbox_client.create_sandbox()
                if not result.success:
                    raise RuntimeError(result.error or "create_sandbox failed")
                sandbox_id = str(result.output or "").strip()
                if not sandbox_id:
                    raise RuntimeError("create_sandbox returned empty sandbox_id")
                registration = await self._register_sandbox_record(sandbox_id)
                dcs_registered = bool(registration.get("api_key"))
                metadata = {
                    "routing_key": routing_key,
                    "user_id": user_id,
                    "session_id": session_id,
                    **registration,
                }
                agent_client = await self._connect_open_ability_client(sandbox_id, routing_key, metadata)
                if self._server_config:
                    agent_client.set_or_update_server_config(config=self._server_config, env=self._server_env)
                runtime = SandboxRuntime(
                    routing_key=routing_key,
                    sandbox_id=sandbox_id,
                    agent_client=agent_client,
                    status=SandboxStatus.BUSY,
                    task_count=1,
                    metadata=metadata,
                )
                async with self._pool_lock:
                    self._runtimes[routing_key] = runtime
                return runtime
            except Exception:
                if sandbox_id:
                    if dcs_registered:
                        await self._delete_sandbox_dcs_record(sandbox_id)
                    try:
                        await self._get_sandbox_client().delete_sandbox(sandbox_id)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to cleanup sandbox after runtime create failure: %s",
                            sandbox_id,
                        )
                raise
            finally:
                async with self._pool_lock:
                    self._creating_count = max(0, self._creating_count - 1)
                self._notify_next_waiter()

    async def _wait_for_capacity(self, routing_key: str) -> None:
        while True:
            waiter: asyncio.Future[None] | None = None
            async with self._pool_lock:
                if self._has_capacity_unlocked(routing_key):
                    return
                idle_routing_key = self._first_idle_runtime_key_unlocked()
                if idle_routing_key is None:
                    if not self._queue_enabled:
                        raise RuntimeError("sandbox capacity reached")
                    if len(self._waiters) >= self._queue_max_size:
                        raise RuntimeError("sandbox routing queue is full")
                    loop = asyncio.get_running_loop()
                    waiter = loop.create_future()
                    self._waiters.append(waiter)
            if idle_routing_key is not None:
                await self._terminate_runtime(idle_routing_key)
                continue
            try:
                await asyncio.wait_for(waiter, timeout=self._queue_timeout_seconds)
            except asyncio.TimeoutError as exc:
                async with self._pool_lock:
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                raise RuntimeError("sandbox routing queue wait timed out") from exc

    def _has_capacity_unlocked(self, routing_key: str) -> bool:
        runtime = self._runtimes.get(routing_key)
        if runtime is not None and runtime.status != SandboxStatus.TERMINATING:
            return True
        return len(self._runtimes) + self._creating_count < self._max_sandboxes

    def _mark_task_start_unlocked(self, runtime: SandboxRuntime) -> None:
        runtime.task_count += 1
        runtime.status = SandboxStatus.BUSY
        runtime.last_active_at = time.time()

    async def _mark_task_done(self, runtime: SandboxRuntime) -> None:
        should_cleanup = False
        async with self._pool_lock:
            runtime.task_count = max(0, runtime.task_count - 1)
            runtime.last_active_at = time.time()
            runtime.status = SandboxStatus.IDLE if runtime.task_count == 0 else SandboxStatus.BUSY
            should_cleanup = runtime.status == SandboxStatus.IDLE and any(
                not waiter.done() for waiter in self._waiters
            )
        if should_cleanup:
            await self._terminate_runtime(runtime.routing_key)

    def _first_idle_runtime_key_unlocked(self) -> str | None:
        for routing_key, runtime in self._runtimes.items():
            if runtime.status == SandboxStatus.IDLE and runtime.task_count == 0:
                return routing_key
        return None

    def _ensure_idle_task(self) -> None:
        if self._idle_task is not None or self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._idle_task = loop.create_task(self._idle_cleanup_loop(), name="sandbox-router-idle-cleanup")

    async def _idle_cleanup_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._idle_check_interval_seconds)
            now = time.time()
            for routing_key, runtime in list(self._runtimes.items()):
                if runtime.task_count == 0 and now - runtime.last_active_at >= self._idle_timeout_seconds:
                    await self._terminate_runtime(routing_key)

    async def _terminate_runtime(self, routing_key: str) -> None:
        async with self._pool_lock:
            runtime = self._runtimes.get(routing_key)
            if runtime is None or runtime.status == SandboxStatus.TERMINATING:
                return
            runtime.status = SandboxStatus.TERMINATING
        try:
            await self._disconnect_agent_client(runtime.sandbox_id, runtime.agent_client)
        finally:
            try:
                await self._delete_sandbox_dcs_record(runtime.sandbox_id)
                await self._get_sandbox_client().delete_sandbox(runtime.sandbox_id)
            finally:
                async with self._pool_lock:
                    runtime.status = SandboxStatus.TERMINATED
                    self._runtimes.pop(routing_key, None)
                self._notify_next_waiter()

    async def _register_sandbox_record(self, sandbox_id: str) -> dict[str, str]:
        store = self._get_dcs_store()
        api_key = get_claw_api_key()
        record = await store.save_sandbox(sandbox_id, api_key=api_key)
        await self._upload_sandbox_init_data(sandbox_id, api_key)
        return {
            "sandbox_id": record.sandbox_id,
            "api_key": api_key,
            "api_key_sha256": record.api_key_sha256,
        }

    async def _upload_sandbox_init_data(self, sandbox_id: str, api_key: str) -> None:
        sandbox_client = self._get_sandbox_client()
        await upload_sandbox_init_data(
            sandbox_client,
            sandbox_id=sandbox_id,
            api_key=api_key,
        )

    async def _delete_sandbox_dcs_record(self, sandbox_id: str) -> None:
        try:
            store = self._get_dcs_store()
            await store.delete_sandbox(sandbox_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to delete sandbox DCS record: %s", sandbox_id)

    def _get_dcs_store(self) -> SandboxDcsStore:
        if self._dcs_store is None:
            self._dcs_store = SandboxDcsStore.from_env()
        return self._dcs_store

    async def _connect_open_ability_client(
        self,
        sandbox_id: str,
        routing_key: str,
        metadata: dict[str, Any],
    ) -> AgentServerClient:
        store = self._get_dcs_store()
        open_ability_cfg = self._get_open_ability_config()
        endpoint = await self._wait_openability_endpoint(store, sandbox_id, open_ability_cfg)
        ws_uri = build_openability_ws_uri(endpoint, ws_path=open_ability_cfg.ws_path)
        client = OpenAbilityWebSocketClient(
            sandbox_id=sandbox_id,
            request_timeout_seconds=open_ability_cfg.request_timeout_seconds,
        )
        if self._on_server_push is not None:
            client.set_server_push_handler(self._on_server_push)
        logger.info(
            "Connecting OpenAbility WebSocket for sandbox_id=%s routing_key=%s uri=%s",
            sandbox_id,
            routing_key,
            ws_uri,
        )
        await asyncio.wait_for(
            client.connect(ws_uri),
            timeout=open_ability_cfg.connect_timeout_seconds,
        )
        return client

    async def _wait_openability_endpoint(
        self,
        store: SandboxDcsStore,
        sandbox_id: str,
        open_ability_config: OpenAbilityConfig,
    ) -> OpenAbilityEndpoint:
        deadline = time.time() + open_ability_config.readiness_timeout_seconds
        while time.time() < deadline:
            endpoint = await store.get_openability_endpoint(sandbox_id)
            if endpoint is not None:
                return endpoint
            await asyncio.sleep(open_ability_config.readiness_poll_interval_seconds)
        raise RuntimeError(
            f"OpenAbility endpoint not found in DCS for sandbox_id={sandbox_id}"
        )

    def _get_open_ability_config(self) -> OpenAbilityConfig:
        if self._open_ability_config is None:
            self._open_ability_config = OpenAbilityConfig.from_env()
        return self._open_ability_config

    async def _disconnect_agent_client(self, sandbox_id: str, agent_client: AgentServerClient | None) -> None:
        if agent_client is not None:
            await agent_client.disconnect()

    def _get_sandbox_client(self) -> SandboxClient:
        if self._sandbox_client is None:
            self._sandbox_client = SandboxClient(SandboxConfig.from_env())
        return self._sandbox_client

    def _notify_next_waiter(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
