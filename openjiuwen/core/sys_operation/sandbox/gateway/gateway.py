# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import time
from typing import Any, AsyncIterator, Dict, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.sys_operation.sandbox.gateway.sandbox_store import (
    InMemorySandboxStore,
    SandboxRecord,
    SandboxStatus,
)
from openjiuwen.core.sys_operation.sandbox.sandbox_registry import SandboxRegistry
from openjiuwen.core.sys_operation.config import (
    SandboxGatewayConfig,
    GatewayConfig,
    GatewayInvokeRequest,
    SandboxCreateRequest,
)
from openjiuwen.core.common.exception.codes import StatusCode


def _compute_container_config_hash(config):
    """Compute hash of container-level configuration fields."""
    import hashlib
    import json
    if config is None:
        return "none"
    container_fields = {
        "image": getattr(config, "image", None),
        "env": dict(sorted((getattr(config, "env", None) or {}).items())),
        "volumes": sorted(getattr(config, "volumes", None) or []),
        "resource_limits": getattr(config, "resource_limits", None),
        "network": getattr(config, "network", None),
        "service_port": getattr(config, "service_port", None),
    }
    payload = json.dumps(container_fields, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class SandboxEndpoint(BaseModel):
    base_url: str
    sandbox_id: Optional[str] = None


class GatewayResponse(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class SandboxGateway:
    _instance: Optional["SandboxGateway"] = None

    def __init__(
            self,
            config: Optional[GatewayConfig] = None,
    ) -> None:
        self._config = config or GatewayConfig()
        self._provider_cache: Dict[str, Any] = {}
        self._store = InMemorySandboxStore()

        self._register_builtin_launchers()
        self._register_builtin_providers()

    @staticmethod
    def _register_builtin_launchers():
        """注册内置的launcher"""
        from openjiuwen.core.sys_operation.sandbox.launchers.pre_deployment_launcher import PreDeploymentLauncher

        SandboxRegistry.register_launcher("pre_deploy", PreDeploymentLauncher)

    @staticmethod
    def _register_builtin_providers():
        """Import provider modules to trigger @SandboxRegistry.provider registration."""
        import openjiuwen.extensions.sys_operation.sandbox.providers.aio  # noqa: F401

    @classmethod
    def get_instance(
            cls,
            config: Optional[GatewayConfig] = None,
    ) -> "SandboxGateway":
        if cls._instance is None:
            cls._instance = cls(config=config)
        return cls._instance

    # ── Full-chain routing: handle_request / handle_stream_request ──

    async def handle_request(
            self,
            config: SandboxGatewayConfig,
            request: GatewayInvokeRequest,
    ) -> GatewayResponse:
        """Resolve endpoint → select Provider → call method → return result."""
        try:
            provider = await self._get_or_create_provider(
                config=config, isolation_key=request.isolation_key, op_type=request.op_type,
            )
            handler = getattr(provider, request.method, None)
            if handler is None:
                return GatewayResponse(code=StatusCode.ERROR.code, message=f"Method '{request.method}' "
                                                                           f"not found on provider", data=None)
            result = await handler(**request.params)
            return GatewayResponse(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=result)
        except Exception as e:
            return GatewayResponse(code=StatusCode.ERROR.code, message=str(e), data=None)

    async def handle_stream_request(
            self,
            config: SandboxGatewayConfig,
            request: GatewayInvokeRequest,
    ) -> AsyncIterator:
        """Resolve endpoint → select Provider → call streaming method → return async iterator."""
        provider = await self._get_or_create_provider(
            config=config, isolation_key=request.isolation_key, op_type=request.op_type,
        )
        handler = getattr(provider, request.method, None)
        if handler is None:
            raise AttributeError(f"Method '{request.method}' not found on provider")
        return handler(**request.params)

    async def _get_or_create_provider(
            self,
            config: SandboxGatewayConfig,
            isolation_key: Optional[str],
            op_type: str,
    ) -> Any:
        """Get a cached provider or create one by resolving the sandbox endpoint first."""
        cache_key = f"{isolation_key}:{op_type}"
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]

        endpoint = await self._get_endpoint(config=config, isolation_key=isolation_key)
        provider = SandboxRegistry.create_provider(
            sandbox_type=config.launcher_config.sandbox_type,
            operation_type=op_type,
            endpoint=endpoint,
            config=config,
        )
        self._provider_cache[cache_key] = provider
        return provider

    def _evict_provider_cache(self, isolation_key: str) -> None:
        """Remove all cached providers for a given isolation_key."""
        keys_to_remove = [k for k in self._provider_cache if k.startswith(f"{isolation_key}:")]
        for k in keys_to_remove:
            del self._provider_cache[k]

    # ── Legacy endpoint-only API (kept for backward compatibility) ──

    async def get_sandbox(
            self,
            request: SandboxCreateRequest,
    ) -> GatewayResponse:
        """Gateway only resolves and returns sandbox endpoint."""

        try:
            endpoint = await self._get_endpoint(config=request.config, isolation_key=request.isolation_key)
            return GatewayResponse(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=endpoint)
        except Exception as e:
            return GatewayResponse(code=StatusCode.ERROR.code, message=str(e), data=None)

    async def release_sandbox(self, isolation_key: str, on_stop: str = "delete") -> GatewayResponse:
        self._evict_provider_cache(isolation_key)
        record = await self._store.hdel(isolation_key)
        if not record:
            return GatewayResponse(code=StatusCode.ERROR.code, message="Sandbox record not found", data=False)
        try:
            if on_stop == "keep":
                pass  # Do nothing, sandbox stays running (externally managed)
            elif on_stop == "pause":
                await self.pause_sandbox(record)
            else:
                await self.delete_sandbox(record)
            return GatewayResponse(code=StatusCode.SUCCESS.code, message=StatusCode.SUCCESS.errmsg, data=True)
        except Exception as e:
            return GatewayResponse(code=StatusCode.ERROR.code, message=str(e), data=False)

    async def pause_sandbox(self, record: SandboxRecord):
        launcher = SandboxRegistry.create_launcher(record.launcher_type)
        await launcher.pause(record.sandbox_id)

    async def delete_sandbox(self, record: SandboxRecord):
        launcher = SandboxRegistry.create_launcher(record.launcher_type)
        await launcher.delete(record.sandbox_id)

    async def _get_endpoint(
            self,
            config: SandboxGatewayConfig,
            isolation_key: Optional[str] = None,
    ) -> SandboxEndpoint:
        launcher_type = config.launcher_config.launcher_type

        key = isolation_key
        now = time.time()

        record = await self._store.get(key)
        if record and record.status == SandboxStatus.RUNNING:
            record.last_used_ts = now
            return SandboxEndpoint(base_url=record.base_url, sandbox_id=record.sandbox_id)

        record = await self._store.get(key)

        if record is None:
            launched = await self._create_new_sandbox(key, now, config)
            return SandboxEndpoint(base_url=launched.base_url, sandbox_id=launched.sandbox_id)

        launcher = SandboxRegistry.create_launcher(launcher_type)
        real_status = await launcher.check_status(record.sandbox_id)

        if real_status == SandboxStatus.RUNNING:
            record.status = SandboxStatus.RUNNING
            record.last_used_ts = now
            await self._store.set(key, record)
            return SandboxEndpoint(base_url=record.base_url, sandbox_id=record.sandbox_id)

        if real_status == SandboxStatus.PAUSED:
            await launcher.resume(record.sandbox_id)
            record.status = SandboxStatus.RUNNING
            record.last_used_ts = now
            await self._store.set(key, record)
            return SandboxEndpoint(base_url=record.base_url, sandbox_id=record.sandbox_id)

        await self._store.hdel(key)
        launched = await self._create_new_sandbox(key, now, config)
        return SandboxEndpoint(base_url=launched.base_url, sandbox_id=launched.sandbox_id)

    async def _create_new_sandbox(self, key: str, now: float, config: SandboxGatewayConfig):
        await self._evict_idle(now, config)

        launcher = SandboxRegistry.create_launcher(config.launcher_config.launcher_type)
        launched = await launcher.launch(
            config.launcher_config,
            timeout_seconds=config.timeout_seconds,
            isolation_key=key,
        )
        record = SandboxRecord(
            sandbox_id=launched.sandbox_id,
            base_url=launched.base_url,
            status=SandboxStatus.RUNNING,
            launcher_type=config.launcher_config.launcher_type,
            sandbox_type=config.launcher_config.sandbox_type,
            container_config_hash=_compute_container_config_hash(config.launcher_config),
            last_used_ts=now,
        )
        await self._store.set(key, record)
        return launched

    async def _evict_idle(self, now: float, config: SandboxGatewayConfig) -> None:
        idle_ttl = getattr(config.launcher_config, "idle_ttl_seconds", None)
        if idle_ttl is None:
            return
        for record in await self._store.evict_expired(idle_ttl, now):
            launcher = SandboxRegistry.create_launcher(record.launcher_type)
            await launcher.delete(record.sandbox_id)
