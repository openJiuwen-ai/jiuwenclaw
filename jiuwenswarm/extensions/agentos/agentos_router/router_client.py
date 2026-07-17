# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import (
    AgentCreatingTimeout,
    AgentManager,
    AgentRuntime,
    SUPPORTED_AGENT_TYPES,
)
from jiuwenswarm.extensions.agentos.agentos_router.models import AgentInfo, AgentStatus
from jiuwenswarm.extensions.agentos.agentos_router.registry_client import RegistryClient
from jiuwenswarm.extensions.yuanrong_frontend_client import (
    YuanrongFrontendAgentClient,
)
from jiuwenswarm.gateway.routing.agent_client import AgentServerClient


logger = logging.getLogger(__name__)


class UnsupportedAgentType(ValueError):
    pass


class AgentOSRouterClient(AgentServerClient):
    """AgentServerClient implementation backed by YuanRong and AgentManager."""

    def __init__(
        self,
        yuanrong: YuanrongFrontendAgentClient,
        registry: RegistryClient,
        agent_manager: AgentManager,
    ) -> None:
        self._yuanrong = yuanrong
        self._registry = registry
        self._agent_manager = agent_manager
        self._server_ready = False
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def server_ready(self) -> bool:
        return self._server_ready and self._yuanrong.server_ready

    async def connect(self, uri: str) -> None:
        await self._yuanrong.connect(uri)
        self._closed = False
        self._server_ready = True

    async def disconnect(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server_ready = False
        await self._drain_background_tasks()
        try:
            await self._yuanrong.disconnect()
        finally:
            await self._registry.close()

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        self._yuanrong.set_or_update_server_config(config=config, env=env)

    def set_server_push_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        setter = getattr(self._yuanrong, "set_server_push_handler", None)
        if callable(setter):
            setter(handler)

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        try:
            runtime = await self._resolve_agent(envelope)
        except (ValueError, AgentCreatingTimeout) as exc:
            return self._routing_error_response(envelope, str(exc))
        runtime.attach_to_envelope(envelope)
        return await self._yuanrong.send_request(envelope)

    async def send_request_stream(
        self, envelope: E2AEnvelope
    ) -> AsyncIterator[AgentResponseChunk]:
        try:
            runtime = await self._resolve_agent(envelope)
        except (ValueError, AgentCreatingTimeout) as exc:
            yield self._routing_error_chunk(envelope, str(exc))
            return
        runtime.attach_to_envelope(envelope)
        async for chunk in self._yuanrong.send_request_stream(envelope):
            yield chunk

    async def shutdown(self) -> None:
        await self.disconnect()

    async def _drain_background_tasks(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _resolve_agent(self, envelope: E2AEnvelope) -> AgentRuntime:
        user_id = self._extract_user_id(envelope)
        agent_type = self._extract_agent_type(envelope)
        return await self._agent_manager.get_or_create_agent(
            user_id,
            agent_type,
            key_values={"session_id": envelope.session_id},
            creator=self._create_agent,
            metadata={"session_id": envelope.session_id},
        )

    async def _create_agent(self, agent_info: AgentInfo) -> AgentInfo:
        if agent_info.agent_type not in SUPPORTED_AGENT_TYPES:
            raise UnsupportedAgentType(
                f"unsupported agent_type: {agent_info.agent_type}"
            )

        image_info = await self._registry.get_image_info(agent_info.agent_type)
        sandbox = await self._yuanrong.create_sandbox(
            user_id=agent_info.user_id,
            agent_type=agent_info.agent_type,
            agent_id=agent_info.agent_id,
            image_name=image_info.image_name,
            metadata={
                "session_id": agent_info.metadata.get("session_id"),
                "image_info": dict(image_info.metadata),
            },
        )
        agent_info.sandbox_id = sandbox.sandbox_id
        agent_info.metadata.update(
            {
                "image": image_info.image_name,
                "image_info": dict(image_info.metadata),
                "sandbox": dict(sandbox.metadata),
            }
        )
        agent_info.status = AgentStatus.READY

        task = asyncio.create_task(
            self._register_agent(agent_info.copy()),
            name=f"agentos-register-{agent_info.agent_id[:12]}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return agent_info

    async def delete_agent(
        self,
        user_id: str,
        agent_type: str,
        *,
        key_values: dict[str, Any] | None = None,
    ) -> None:
        """Delete agent mapping and release its YuanRong sandbox."""
        resolved_key_values = dict(key_values or {})
        runtime = await self._agent_manager.get_agent(
            user_id, agent_type, key_values=resolved_key_values or None
        )
        if runtime is None:
            return
        agent_info = runtime.info
        if (
            "session_id" not in resolved_key_values
            and agent_info.metadata.get("session_id")
        ):
            resolved_key_values["session_id"] = agent_info.metadata.get(
                "session_id"
            )
        if agent_info.sandbox_id:
            await self._yuanrong.delete_sandbox(
                agent_info.sandbox_id,
                user_id=agent_info.user_id,
                agent_type=agent_info.agent_type,
            )
        await self._agent_manager.delete_agent(
            agent_info.user_id,
            agent_info.agent_type,
            key_values=resolved_key_values or None,
        )

    async def _register_agent(self, agent_info: AgentInfo) -> None:
        try:
            await self._registry.register_agent(agent_info)
        except Exception:
            logger.exception(
                "[AgentOSRouter] async registry registration failed: agent_id=%s",
                agent_info.agent_id,
            )

    @staticmethod
    def _extract_user_id(envelope: E2AEnvelope) -> str:
        user_id = str(envelope.user_id or "").strip()
        if not user_id:
            raise ValueError("user_id is required for AgentOS routing")
        return user_id

    @staticmethod
    def _extract_agent_type(envelope: E2AEnvelope) -> str:
        raw = envelope.params.get("agent_type")
        if raw is None:
            raw = envelope.channel_context.get("agent_type")
        try:
            return AgentRuntime.normalize_agent_type(raw)
        except ValueError as exc:
            raise UnsupportedAgentType(str(exc)) from exc

    @staticmethod
    def _routing_error_response(
        envelope: E2AEnvelope,
        message: str,
    ) -> AgentResponse:
        return AgentResponse(
            request_id=str(envelope.request_id or ""),
            channel_id=str(envelope.channel or ""),
            ok=False,
            payload={"error": message},
        )

    @staticmethod
    def _routing_error_chunk(
        envelope: E2AEnvelope,
        message: str,
    ) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=str(envelope.request_id or ""),
            channel_id=str(envelope.channel or ""),
            payload={"error": message},
            is_complete=True,
        )
