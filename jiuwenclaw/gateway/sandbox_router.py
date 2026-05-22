from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jiuwenclaw.config import get_config
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.sandbox.sandbox_client import ExecutionResult, SandboxClient, SandboxConfig
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


class _UnconnectedAgentClient(AgentServerClient):
    """沙箱 Agent 反向建链未配置时的占位 client，便于分模块调试。"""

    _ERROR = "sandbox agent connection is not configured"

    async def connect(self, uri: str) -> None:
        _ = uri

    async def disconnect(self) -> None:
        return None

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        _ = config, env

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        logger.warning("%s (request_id=%s)", self._ERROR, envelope.request_id)
        return AgentResponse(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            ok=False,
            payload={"error": self._ERROR},
        )

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        logger.warning("%s (request_id=%s)", self._ERROR, envelope.request_id)
        yield AgentResponseChunk(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            payload={"error": self._ERROR},
            is_complete=True,
        )


class SandboxRouterAgentClient(AgentServerClient):
    def __init__(
        self,
        *,
        max_sandboxes: int | None = None,
        queue_enabled: bool | None = None,
        queue_max_size: int | None = None,
        queue_timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        idle_check_interval_seconds: float | None = None,
    ) -> None:
        gateway_cfg = self._gateway_config()
        sandbox_routing_cfg = (
            gateway_cfg.get("sandbox_routing")
            if isinstance(gateway_cfg.get("sandbox_routing"), dict)
            else {}
        )
        self._sandbox_client: SandboxClient | None = None
        max_sandboxes_value = (
            max_sandboxes
            if max_sandboxes is not None
            else sandbox_routing_cfg.get("max_sandboxes")
            or 4
        )
        self._max_sandboxes = max(1, int(max_sandboxes_value))
        self._queue_enabled = self._cfg_bool(
            queue_enabled if queue_enabled is not None else sandbox_routing_cfg.get("queue_enabled"),
            True,
        )
        queue_max_size_value = (
            queue_max_size
            if queue_max_size is not None
            else sandbox_routing_cfg.get("queue_max_size")
            or 100
        )
        self._queue_max_size = max(1, int(queue_max_size_value))
        self._queue_timeout_seconds = float(
            queue_timeout_seconds
            if queue_timeout_seconds is not None
            else sandbox_routing_cfg.get("queue_timeout_seconds")
            or 60.0
        )
        self._idle_timeout_seconds = float(
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else sandbox_routing_cfg.get("idle_timeout_seconds")
            or 300.0
        )
        self._idle_check_interval_seconds = max(
            30.0,
            float(
                idle_check_interval_seconds
                if idle_check_interval_seconds is not None
                else sandbox_routing_cfg.get("idle_check_interval_seconds")
                or 30.0
            ),
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

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        self._server_config = dict(config or {})
        self._server_env = dict(env) if env else None

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        runtime = await self._acquire_runtime(envelope)
        try:
            return await runtime.agent_client.send_request(envelope)
        finally:
            await self._mark_task_done(runtime)

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        runtime = await self._acquire_runtime(envelope)
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
        raise RuntimeError("sandbox routing requires user_id or session_id")

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
            try:
                sandbox_client = self._get_sandbox_client()
                result = await sandbox_client.create_sandbox()
                sandbox_id = self._extract_sandbox_id(result)
                if not sandbox_id:
                    raise RuntimeError("create_sandbox returned empty sandbox_id")
                registry_record = await self._register_sandbox_record(sandbox_id)
                metadata = {
                    "routing_key": routing_key,
                    "user_id": user_id,
                    "session_id": session_id,
                }
                if registry_record.get("api_key"):
                    metadata["api_key"] = registry_record["api_key"]
                if registry_record.get("created_at"):
                    metadata["sandbox_created_at"] = registry_record["created_at"]
                agent_client = await self._wait_agent_connected(sandbox_id, routing_key, metadata)
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
                await self._get_sandbox_client().delete_sandbox(runtime.sandbox_id)
            finally:
                async with self._pool_lock:
                    runtime.status = SandboxStatus.TERMINATED
                    self._runtimes.pop(routing_key, None)
                self._notify_next_waiter()

    async def _register_sandbox_record(self, sandbox_id: str) -> dict[str, str]:
        from jiuwenclaw.sandbox.sandbox_registry import register_sandbox_record_async

        try:
            return await register_sandbox_record_async(sandbox_id, created_at=time.time())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to register sandbox record for %s", sandbox_id)
            return {"sandbox_id": sandbox_id, "api_key": "", "created_at": ""}

    async def _wait_agent_connected(
        self,
        sandbox_id: str,
        routing_key: str,
        metadata: dict[str, Any],
    ) -> AgentServerClient:
        _ = routing_key, metadata
        logger.warning(
            "sandbox agent connection is not configured (sandbox_id=%s)",
            sandbox_id,
        )
        return _UnconnectedAgentClient()

    async def _disconnect_agent_client(self, sandbox_id: str, agent_client: AgentServerClient | None) -> None:
        if agent_client is not None:
            await agent_client.disconnect()

    def _get_sandbox_client(self) -> SandboxClient:
        if self._sandbox_client is not None:
            return self._sandbox_client
        gateway_cfg = self._gateway_config()
        sandbox_client_cfg = (
            gateway_cfg.get("sandbox_client")
            if isinstance(gateway_cfg.get("sandbox_client"), dict)
            else {}
        )
        api_base = str(
            sandbox_client_cfg.get("api_base")
            or sandbox_client_cfg.get("sandbox_manager_base_url")
            or ""
        ).strip()
        if not api_base:
            raise RuntimeError("gateway.sandbox_routing.enabled=true requires gateway.sandbox_client.api_base")
        duration_raw = (
            sandbox_client_cfg.get("duration_seconds")
            or sandbox_client_cfg.get("sandbox_default_duration_seconds")
        )
        sandbox_metadata = sandbox_client_cfg.get("metadata")
        metadata: dict[str, str] = {}
        if isinstance(sandbox_metadata, dict):
            metadata = {str(k): str(v) for k, v in sandbox_metadata.items()}
        self._sandbox_client = SandboxClient(
            SandboxConfig(
                api_base=api_base,
                template_id=str(
                    sandbox_client_cfg.get("template_id")
                    or sandbox_client_cfg.get("sandbox_default_template_id")
                    or ""
                ).strip(),
                duration_seconds=self._optional_int(duration_raw) or 900,
                timeout_seconds=int(
                    float(
                        sandbox_client_cfg.get("timeout_seconds")
                        or sandbox_client_cfg.get("sandbox_api_timeout_seconds")
                        or 120.0
                    )
                ),
                metadata=metadata,
                command_timeout_seconds=int(
                    sandbox_client_cfg.get("command_timeout_seconds") or 60
                ),
                code_timeout_seconds=int(sandbox_client_cfg.get("code_timeout_seconds") or 60),
            )
        )
        return self._sandbox_client

    @staticmethod
    def _gateway_config() -> dict[str, Any]:
        cfg = get_config()
        gateway_cfg = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
        return gateway_cfg

    @staticmethod
    def _cfg_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        return int(raw)

    def _notify_next_waiter(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return

    @staticmethod
    def _extract_sandbox_id(result: Any) -> str | None:
        if isinstance(result, ExecutionResult):
            if result.success and result.output.strip():
                return result.output.strip()
            data = result.to_dict()
            for key in ("sandbox_id", "sandboxId", "id"):
                value = data.get(key)
                if value:
                    return str(value).strip()
            output = data.get("output")
            if output:
                return str(output).strip()
            return None
        if isinstance(result, str):
            return result.strip() or None
        for attr in ("sandbox_id", "sandboxId", "id"):
            value = getattr(result, attr, None)
            if value:
                return str(value).strip()
        if isinstance(result, dict):
            for key in ("sandbox_id", "sandboxId", "id"):
                value = result.get(key)
                if value:
                    return str(value).strip()
            payload = result.get("payload")
            if isinstance(payload, dict):
                return SandboxRouterAgentClient._extract_sandbox_id(payload)
        payload = getattr(result, "payload", None)
        if isinstance(payload, dict):
            return SandboxRouterAgentClient._extract_sandbox_id(payload)
        return None
