# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lifecycle owner for the shared JiuwenSwarm agent runtime.

This module deliberately has no transport concerns.  AgentServer and the
process-style CLI both own an ``AgentRuntime`` instance and use its public
operations; WebSocket framing remains in AgentServer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from jiuwenswarm.server.runtime.agent_manager import AgentManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
    from jiuwenswarm.runtime.events import RuntimeEvent
    from jiuwenswarm.runtime.plan import PlanModeController


class RuntimeStateError(RuntimeError):
    """Raised when an operation violates the runtime lifecycle."""


async def _initialize_runtime_dependencies() -> None:
    """Initialize shared runtime dependencies without starting a server."""
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        ensure_persistent_checkpointer,
    )

    await ensure_persistent_checkpointer()


class AgentRuntime:
    """Own the existing ``AgentManager`` and its in-memory resources.

    The class is intentionally one-shot: after ``close`` it cannot be started
    again.  A process-style CLI creates one instance per command, while
    AgentServer owns one instance for its service lifetime.
    """

    def __init__(
        self,
        *,
        agent_manager: AgentManager | None = None,
        initializer: Callable[[], Awaitable[None]] | None = None,
        plan_controller: PlanModeController | None = None,
    ) -> None:
        self._agent_manager = agent_manager or AgentManager()
        self._initializer = initializer or _initialize_runtime_dependencies
        self._initialize_extensions = initializer is None
        self._manage_runner = initializer is None
        self._runner_started = False
        self._checkpointer_started = False
        self._owned_extension_manager: Any = None
        if plan_controller is None:
            from jiuwenswarm.runtime.plan import PlanModeController

            plan_controller = PlanModeController()
        self._plan_controller = plan_controller
        self._stateless_agents: dict[str, Any] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    @property
    def agent_manager(self) -> AgentManager:
        """Return the single AgentManager owned by this runtime."""
        return self._agent_manager

    @property
    def plan_controller(self) -> PlanModeController:
        return self._plan_controller

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        """Initialize runtime dependencies exactly once."""
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeStateError("runtime is already closed")
            if self._started:
                return
            try:
                await self._initializer()
                if self._manage_runner:
                    self._checkpointer_started = True
                    from openjiuwen.core.runner import Runner

                    await Runner.start()
                    self._runner_started = True
                if self._initialize_extensions:
                    await self._ensure_extensions()
                self._started = True
            except BaseException:
                try:
                    await self._rollback_start()
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception(
                        "Runtime start rollback encountered a cleanup error"
                    )
                raise

    async def create_or_resume_session(
        self,
        *,
        channel_id: str,
        session_id: str | None = None,
    ) -> str:
        """Allocate a Runtime session id or retain an explicit persisted id."""
        self._require_started()
        requested = str(session_id or "").strip()
        if requested:
            from jiuwenswarm.server.runtime.session.session_history import (
                is_valid_session_id,
            )

            if not is_valid_session_id(requested):
                raise ValueError("invalid session_id")
        return await self._agent_manager.create_session(
            channel_id=channel_id,
            session_id=requested or None,
        )

    async def prepare_chat_turn(
        self,
        request: AgentRequest,
        channel_id: str,
        *,
        sync_metadata: bool = True,
    ) -> tuple[str, str | None, object]:
        """Resolve session semantics and return this Runtime's selected agent."""
        self._require_started()
        from jiuwenswarm.runtime.request import prepare_chat_turn

        return await prepare_chat_turn(
            self._agent_manager,
            request,
            channel_id,
            sync_metadata=sync_metadata,
        )

    async def cancel_request(
        self,
        request: AgentRequest,
        *,
        allow_create: bool = False,
    ) -> AgentResponse:
        """Cancel the target request/session without crossing a transport."""
        await self.start()
        from jiuwenswarm.runtime.request import cancel_request

        return await cancel_request(
            self._agent_manager,
            request,
            allow_create=allow_create,
        )

    async def invoke(self, request: AgentRequest) -> list[RuntimeEvent]:
        """Execute one non-streaming request and return Runtime events."""
        await self.start()
        from jiuwenswarm.runtime.events import RuntimeEvent

        await self._trigger_before_chat_request_hook(request)
        channel_id = request.channel_id or "default"
        foreground = request.req_method in self._chat_turn_methods()
        if foreground:
            await self._agent_manager.begin_foreground_chat()
        events: list[RuntimeEvent] = []
        agent: Any = None
        readonly_goal_get = self._is_readonly_goal_get_request(request)
        stateless = self._is_stateless_method_request(request)
        try:
            if stateless:
                agent = await self._get_stateless_agent(channel_id)
            else:
                mode, sub_mode, agent = await self.prepare_chat_turn(
                    request,
                    channel_id,
                    sync_metadata=not readonly_goal_get,
                )
                if not readonly_goal_get:
                    plan_result = await self._plan_controller.ensure_state(
                        request,
                        mode,
                        sub_mode,
                        agent,
                    )
                    events.extend(
                        self._control_events(
                            request,
                            plan_result.events,
                        )
                    )
            response = await agent.process_message(request)
            events.append(
                RuntimeEvent.from_agent_message(
                    response,
                    request_id=request.request_id,
                    channel_id=channel_id,
                    session_id=request.session_id,
                    default_agent_ref=request.agent_ref,
                    default_complete=True,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            events.append(
                RuntimeEvent.error(
                    request_id=request.request_id,
                    channel_id=channel_id,
                    session_id=request.session_id,
                    error=exc,
                )
            )
        finally:
            if agent is not None and not stateless and not readonly_goal_get:
                events.extend(
                    self._control_events(
                        request,
                        await self._plan_controller.check_post_process_exit(
                            request,
                            agent,
                        ),
                    )
                )
            if foreground:
                await self._agent_manager.end_foreground_chat()
        return events

    async def answer_interaction(
        self,
        request: AgentRequest,
    ) -> list[RuntimeEvent]:
        """Answer a paused Runtime interaction through the existing Agent."""
        from jiuwenswarm.common.schema.message import ReqMethod

        if request.req_method != ReqMethod.CHAT_ANSWER:
            raise ValueError("interaction answer must use ReqMethod.CHAT_ANSWER")
        return await self.invoke(request)

    async def stream(self, request: AgentRequest) -> AsyncIterator[RuntimeEvent]:
        """Execute one request and yield the shared Runtime event stream."""
        await self.start()
        from jiuwenswarm.runtime.events import RuntimeEvent

        await self._trigger_before_chat_request_hook(request)
        channel_id = request.channel_id or "default"
        foreground = request.req_method in self._chat_turn_methods()
        if foreground:
            await self._agent_manager.begin_foreground_chat()
        agent: Any = None
        readonly_goal_get = self._is_readonly_goal_get_request(request)
        stateless = self._is_stateless_method_request(request)
        error: Exception | None = None
        cancelled = False
        try:
            if stateless:
                agent = await self._get_stateless_agent(channel_id)
            else:
                mode, sub_mode, agent = await self.prepare_chat_turn(
                    request,
                    channel_id,
                    sync_metadata=not readonly_goal_get,
                )
                if not readonly_goal_get:
                    plan_result = await self._plan_controller.ensure_state(
                        request,
                        mode,
                        sub_mode,
                        agent,
                    )
                    for event in self._control_events(
                        request,
                        plan_result.events,
                    ):
                        yield event
            async for chunk in agent.process_message_stream(request):
                yield RuntimeEvent.from_agent_message(
                    chunk,
                    request_id=request.request_id,
                    channel_id=channel_id,
                    session_id=request.session_id,
                    default_agent_ref=request.agent_ref,
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001
            error = exc
        finally:
            if (
                not cancelled
                and agent is not None
                and not stateless
                and not readonly_goal_get
            ):
                for event in self._control_events(
                    request,
                    await self._plan_controller.check_post_process_exit(
                        request,
                        agent,
                    ),
                ):
                    yield event
            if foreground:
                await self._agent_manager.end_foreground_chat()
        if error is not None:
            yield RuntimeEvent.error(
                request_id=request.request_id,
                channel_id=channel_id,
                session_id=request.session_id,
                error=error,
            )

    async def cleanup_session(self, *, channel_id: str, session_id: str) -> bool:
        """Release in-memory resources owned by one Runtime session."""
        self._require_started()
        cleaned = await self._agent_manager.cleanup_session_runtime(
            channel_id=channel_id,
            session_id=session_id,
        )
        self._plan_controller.reset_session(session_id)
        return cleaned

    async def close(self) -> None:
        """Cancel in-flight work and release all owned Runtime resources."""
        async with self._lifecycle_lock:
            if self._closed:
                return
            cleanup_errors: list[Exception] = []
            try:
                await self._agent_manager.cancel_all_inflight_work(
                    "[runtime close] "
                )
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(exc)
            for agent in self._stateless_agents.values():
                cleanup = getattr(agent, "cleanup", None)
                if callable(cleanup):
                    try:
                        await cleanup()
                    except Exception as exc:  # noqa: BLE001
                        cleanup_errors.append(exc)
            self._stateless_agents.clear()
            try:
                await self._agent_manager.cleanup()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(exc)
            if self._owned_extension_manager is not None:
                from jiuwenswarm.extensions.registry import ExtensionRegistry

                try:
                    await self._owned_extension_manager.shutdown_all_extensions()
                except Exception as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
                finally:
                    self._owned_extension_manager = None
                    ExtensionRegistry.reset_instance()
            if self._runner_started:
                from openjiuwen.core.runner import Runner

                try:
                    await Runner.stop()
                except Exception as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
                finally:
                    self._runner_started = False
            if self._checkpointer_started:
                from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
                    close_persistent_checkpointer,
                )

                try:
                    await close_persistent_checkpointer()
                except Exception as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
                finally:
                    self._checkpointer_started = False
            self._started = False
            self._closed = True
            if cleanup_errors:
                raise cleanup_errors[0]

    def _require_started(self) -> None:
        if self._closed:
            raise RuntimeStateError("runtime is already closed")
        if not self._started:
            raise RuntimeStateError("runtime is not started")

    @staticmethod
    def _chat_turn_methods() -> frozenset[Any]:
        from jiuwenswarm.runtime.request import CHAT_TURN_METHODS

        return CHAT_TURN_METHODS

    @staticmethod
    def _is_stateless_method_request(request: AgentRequest) -> bool:
        return (
            request.req_method is not None
            and request.req_method.value.startswith(
                ("skills.", "skilldev.", "plugins.", "symphony.")
            )
        )

    @staticmethod
    def _is_readonly_goal_get_request(request: AgentRequest) -> bool:
        from jiuwenswarm.common.schema.message import ReqMethod

        if request.req_method != ReqMethod.COMMAND_GOAL:
            return False
        params = request.params if isinstance(request.params, dict) else {}
        return str(params.get("action") or "get").strip().lower() == "get"

    async def _get_stateless_agent(self, channel_id: str) -> Any:
        cached = self._agent_manager.get_agent_nowait(
            channel_id=channel_id,
            mode="agent",
        )
        if cached is not None:
            return cached
        agent = self._stateless_agents.get(channel_id)
        if agent is None:
            from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm

            agent = JiuWenSwarm()
            self._stateless_agents[channel_id] = agent
        return agent

    async def _ensure_extensions(self) -> None:
        from openjiuwen.core.runner import Runner

        from jiuwenswarm.extensions.manager import ExtensionManager
        from jiuwenswarm.extensions.registry import ExtensionRegistry

        try:
            ExtensionRegistry.get_instance()
            return
        except RuntimeError:
            pass
        registry = ExtensionRegistry.create_instance(
            callback_framework=Runner.callback_framework,
            config={},
            logger=logging.getLogger(__name__),
        )
        manager = ExtensionManager(registry=registry)
        await manager.load_all_extensions(include_transport_extensions=False)
        self._owned_extension_manager = manager

    async def _rollback_start(self) -> None:
        """Undo partially initialized owned dependencies after start failure."""
        cleanup_errors: list[Exception] = []
        if self._owned_extension_manager is not None:
            from jiuwenswarm.extensions.registry import ExtensionRegistry

            try:
                await self._owned_extension_manager.shutdown_all_extensions()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(exc)
            finally:
                self._owned_extension_manager = None
                ExtensionRegistry.reset_instance()
        if self._runner_started:
            from openjiuwen.core.runner import Runner

            try:
                await Runner.stop()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(exc)
            finally:
                self._runner_started = False
        if self._checkpointer_started:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
                close_persistent_checkpointer,
            )

            try:
                await close_persistent_checkpointer()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(exc)
            finally:
                self._checkpointer_started = False
        if cleanup_errors:
            raise cleanup_errors[0]

    @staticmethod
    def _control_events(
        request: AgentRequest,
        payloads: list[dict[str, Any]],
    ) -> list[RuntimeEvent]:
        from jiuwenswarm.runtime.events import RuntimeEvent

        return [
            RuntimeEvent.control(
                request_id=request.request_id,
                channel_id=request.channel_id or "default",
                session_id=request.session_id,
                payload=payload,
            )
            for payload in payloads
        ]

    @staticmethod
    async def _trigger_before_chat_request_hook(request: AgentRequest) -> None:
        if request.req_method not in AgentRuntime._chat_turn_methods():
            return
        from jiuwenswarm.extensions.hook_event import AgentServerHookEvents
        from jiuwenswarm.extensions.hooks_context import AgentServerChatHookContext
        from jiuwenswarm.extensions.registry import ExtensionRegistry

        params = request.params if isinstance(request.params, dict) else {}
        if not isinstance(request.params, dict):
            request.params = params
        context = AgentServerChatHookContext(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            req_method=(
                request.req_method.value if request.req_method is not None else None
            ),
            params=params,
        )
        await ExtensionRegistry.get_instance().trigger(
            AgentServerHookEvents.BEFORE_CHAT_REQUEST,
            context,
        )


__all__ = ["AgentRuntime", "RuntimeStateError"]
