# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import (
    AgentCreatingTimeout,
    AgentDeleted,
    AgentManager,
    AgentRuntime,
    SUPPORTED_AGENT_TYPES,
)
from jiuwenswarm.extensions.agentos.agentos_router.models import AgentInfo, AgentStatus
from jiuwenswarm.extensions.agentos.agentos_router.registry_client import RegistryClient
from jiuwenswarm.extensions.agentos.agentos_router.ssh_relay import YuanrongSshRelay
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
        ssh_relay: YuanrongSshRelay | None = None,
    ) -> None:
        self._yuanrong = yuanrong
        self._registry = registry
        self._agent_manager = agent_manager
        self._ssh_relay = ssh_relay
        self._server_ready = False
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        # 用户当前 agent_type（3rdagent.switch 成功后更新）；SSH 接入跟随此值。
        self._current_agent_types: dict[str, str] = {}

    def get_current_agent_type(self, user_id: str) -> str:
        """Return the user's current agent_type (default ``jiuwenswarm``)."""
        uid = str(user_id or "").strip()
        return self._current_agent_types.get(uid) or "jiuwenswarm"

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
        # 3rdagent.list / 3rdagent.switch are handled by Gateway ThirdAgent
        # (TUI local_handler), not via E2A send_request.
        if self._is_ssh_relay_request(envelope):
            return await self._handle_ssh_relay(envelope)
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

    async def thirdagent_list(
        self,
        *,
        user_id: str,
        current_agent_type: str = "",
    ) -> dict[str, Any]:
        """Handle ``3rdagent.list``: list switchable third-party agent images."""
        uid = str(user_id or "").strip()
        if not uid:
            return {
                "ok": False,
                "error": "user_id is required for AgentOS routing",
                "code": "BAD_REQUEST",
            }
        images = await self._registry.list_user_images(uid)
        agents: list[dict[str, Any]] = []
        for image in images:
            agent_type = str(
                (image.metadata or {}).get("agent_type") or image.image_name or ""
            ).strip()
            if not agent_type:
                continue
            agents.append(
                {
                    "agent_type": agent_type,
                    "image_name": image.image_name,
                    "image_uri": image.image_uri,
                    "metadata": dict(image.metadata or {}),
                }
            )
        current = (
            str(current_agent_type or "").strip()
            or self.get_current_agent_type(uid)
        )
        return {
            "ok": True,
            "payload": {
                "agents": agents,
                "current_agent_type": current,
            },
        }

    async def thirdagent_switch(
        self,
        *,
        user_id: str,
        agent_type: str,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Handle ``3rdagent.switch``: ensure agent exists without forwarding chat."""
        uid = str(user_id or "").strip()
        if not uid:
            return {
                "ok": False,
                "error": "user_id is required for AgentOS routing",
                "code": "BAD_REQUEST",
            }
        try:
            normalized = AgentRuntime.normalize_agent_type(agent_type)
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "code": "UNSUPPORTED_AGENT_TYPE",
            }
        try:
            runtime = await self._agent_manager.get_or_create_agent(
                uid,
                normalized,
                key_values={"session_id": session_id} if session_id else None,
                creator=self._create_agent,
                metadata={"session_id": session_id} if session_id else None,
            )
        except (ValueError, AgentCreatingTimeout) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "code": "INTERNAL_ERROR",
            }
        info = runtime.info
        status = info.status.value if hasattr(info.status, "value") else str(info.status)
        # 记录用户当前 agent_type，后续 SSH 接入默认跟随
        self._current_agent_types[uid] = normalized
        return {
            "ok": True,
            "payload": {
                "agent_id": info.agent_id,
                "agent_type": info.agent_type,
                "sandbox_id": info.sandbox_id,
                "status": status,
            },
        }

    async def shutdown(self) -> None:
        await self.disconnect()

    # ---------- SSH relay (northbound SshChannel -> YuanRong instance) ----------

    @staticmethod
    def _is_ssh_relay_request(envelope: E2AEnvelope) -> bool:
        return str(envelope.method or "") == ReqMethod.SSH_RELAY.value

    async def _handle_ssh_relay(self, envelope: E2AEnvelope) -> AgentResponse:
        """Start the southbound SSH relay for an ``ssh.relay`` request.

        Agent resolution (YuanRong instance creation) and the PTY relay run
        in a background task so the gateway forward loop is not blocked for
        the whole SSH session; the northbound channel waits on the relay
        session ``done`` event instead of this response.
        """
        session_id = str(envelope.session_id or "")
        params = envelope.params if isinstance(envelope.params, dict) else {}
        # Live SshRelaySession handed over in-process by the northbound
        # SshChannel; pop it so it never leaks into serialization/logging.
        relay_session = params.pop("relay_session", None)
        if relay_session is None:
            return self._routing_error_response(
                envelope, f"ssh relay session not found in params: {session_id}"
            )
        if self._ssh_relay is None:
            msg = "ssh relay is not configured for AgentOS router"
            relay_session.exit_code = 1
            relay_session.done.set()
            return self._routing_error_response(envelope, msg)

        task = asyncio.create_task(
            self._run_ssh_relay(envelope, relay_session),
            name=f"agentos-ssh-relay-{session_id[:24]}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return AgentResponse(
            request_id=str(envelope.request_id or ""),
            channel_id=str(envelope.channel or ""),
            ok=True,
            payload={"method": ReqMethod.SSH_RELAY.value, "status": "relay_started"},
        )

    async def _run_ssh_relay(self, envelope: E2AEnvelope, relay_session: Any) -> None:
        ssh_relay = self._ssh_relay
        if ssh_relay is None:
            # _handle_ssh_relay already guards this; keep a safe fallback.
            relay_session.exit_code = 1
            relay_session.done.set()
            return
        self._apply_current_agent_type_for_ssh(envelope)
        try:
            runtime = await self._resolve_agent(envelope)
        except (ValueError, AgentCreatingTimeout, AgentDeleted) as exc:
            ssh_relay.fail_session(
                relay_session, f"agent resolve failed: {exc}"
            )
            return
        except Exception as exc:  # noqa: BLE001 - creation errors must release the client
            logger.exception(
                "[AgentOSRouter] ssh relay agent creation failed: session=%s",
                relay_session.session_id,
            )
            ssh_relay.fail_session(
                relay_session, f"agent creation failed: {exc}"
            )
            return

        instance_id = str(runtime.info.sandbox_id or "").strip()
        if not instance_id:
            ssh_relay.fail_session(
                relay_session,
                f"agent has no yuanrong instance_id: user={runtime.info.user_id}",
            )
            return

        runtime.attach_to_envelope(envelope)
        logger.info(
            "[AgentOSRouter] ssh relay start: session=%s user=%s instance=%s",
            relay_session.session_id,
            runtime.info.user_id,
            instance_id,
        )
        await ssh_relay.run(relay_session, instance_id)

    def _apply_current_agent_type_for_ssh(self, envelope: E2AEnvelope) -> None:
        """SSH 接入跟随用户当前 agent_type（由 3rdagent.switch 记录）。"""
        params = envelope.params if isinstance(envelope.params, dict) else {}
        if str(params.get("agent_type") or "").strip():
            return
        user_id = str(envelope.user_id or "").strip()
        current = self.get_current_agent_type(user_id)
        params = dict(params)
        params["agent_type"] = current
        envelope.params = params
        logger.info(
            "[AgentOSRouter] ssh relay follows user current agent_type: "
            "user=%s agent_type=%s",
            user_id,
            current,
        )

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
        urn = str(
            image_info.image_uri
            or image_info.metadata.get("urn")
            or self._yuanrong.function_version_urn
        ).strip()
        if not urn:
            raise ValueError(
                f"function urn is required to create sandbox for agent_type={agent_info.agent_type}"
            )
        sandbox = await self._yuanrong.create_sandbox(
            namespace=self._yuanrong.agent_namespace,
            name=f"{agent_info.user_id}+{agent_info.agent_type}",
            urn=urn,
        )
        instance_id = sandbox.sandbox_id
        agent_info.sandbox_id = instance_id
        agent_info.metadata.update(
            {
                "instance_id": instance_id,
                "urn": urn,
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
            await self._yuanrong.delete_sandbox(agent_info.sandbox_id)
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
