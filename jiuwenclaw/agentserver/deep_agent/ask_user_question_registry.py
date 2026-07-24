# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Registry for AskUserQuestion tool: correlate Gateway push with chat.user_answer."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey

logger = logging.getLogger(__name__)

ASK_REQUEST_PREFIX = "ask_uq_"

_interactive_ask_cv: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "jiuwenclaw_interactive_ask",
    default=False,
)
_session_id_cv: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_ask_session_id",
    default="",
)
_stream_request_id_cv: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_ask_stream_request_id",
    default="",
)
_channel_id_cv: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_ask_channel_id",
    default="",
)
_service_id_cv: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_ask_service_id",
    default="default",
)
_agent_id_cv: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_ask_agent_id",
    default="default",
)

# (service_id, agent_id, request_id)
_AskKey = tuple[str, str, str]
# (service_id, agent_id, session_id | stream_request_id)
_TenantSessionKey = tuple[str, str, str]


@contextlib.asynccontextmanager
async def ask_user_question_request_scope(
    *,
    interactive_ask: bool,
    session_id: str,
    stream_request_id: str,
    channel_id: str,
    service_id: str | None = None,
    agent_id: str | None = None,
    scope: RuntimeScopeKey | None = None,
) -> AsyncIterator[None]:
    """Bind per-request context for AskUserQuestion (push routing / user_answer)."""
    if scope is not None:
        sid_tenant = scope.service_id
        aid_tenant = scope.agent_id
        sess = scope.session_id or session_id
    else:
        sid_tenant = (service_id or "default").strip() or "default"
        aid_tenant = (agent_id or "default").strip() or "default"
        sess = session_id

    reg = AskUserQuestionRegistry.get_instance()
    sr = str(stream_request_id or "").strip()
    sid = str(sess or "").strip()
    runtime_scope = RuntimeScopeKey.from_ids(sid_tenant, aid_tenant, sid)
    if sr or sid:
        reg.bind_stream_chat_flags(
            runtime_scope, sr, bool(interactive_ask), session_id=sid,
        )
    t_ia = _interactive_ask_cv.set(bool(interactive_ask))
    t_sid = _session_id_cv.set(sid)
    t_rid = _stream_request_id_cv.set(stream_request_id or "")
    t_cid = _channel_id_cv.set(channel_id or "")
    t_svc = _service_id_cv.set(runtime_scope.service_id)
    t_aid = _agent_id_cv.set(runtime_scope.agent_id)
    try:
        yield
    finally:
        _interactive_ask_cv.reset(t_ia)
        _session_id_cv.reset(t_sid)
        _stream_request_id_cv.reset(t_rid)
        _channel_id_cv.reset(t_cid)
        _service_id_cv.reset(t_svc)
        _agent_id_cv.reset(t_aid)
        if sr:
            reg.unbind_stream_chat_flags(runtime_scope, sr)


def get_ask_request_context() -> tuple[bool, str, str, str]:
    """Return (interactive_ask, session_id, stream_request_id, channel_id)."""
    return (
        _interactive_ask_cv.get(),
        _session_id_cv.get(),
        _stream_request_id_cv.get(),
        _channel_id_cv.get(),
    )


def get_ask_runtime_scope() -> RuntimeScopeKey:
    """Return the tenant + session scope bound for the current ask request."""
    return RuntimeScopeKey.from_ids(
        _service_id_cv.get(),
        _agent_id_cv.get(),
        _session_id_cv.get(),
    )


class AskUserQuestionRegistry:
    """Maps ask correlation ids to Futures completed by chat.user_answer.

    Keys include ``(service_id, agent_id, ...)`` so catalog-shared processes
    cannot cross-resolve answers across tenants.
    """

    _instance: ClassVar["AskUserQuestionRegistry | None"] = None

    def __init__(self) -> None:
        self._pending: dict[_AskKey, asyncio.Future[Any]] = {}
        self._pending_sessions: dict[_AskKey, str] = {}
        self._stream_interactive_ask: dict[_TenantSessionKey, bool] = {}
        self._session_interactive_ask: dict[_TenantSessionKey, bool] = {}

    @classmethod
    def get_instance(cls) -> "AskUserQuestionRegistry":
        if cls._instance is None:
            cls._instance = AskUserQuestionRegistry()
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    @staticmethod
    def _ask_key(scope: RuntimeScopeKey, request_id: str) -> _AskKey:
        return (scope.service_id, scope.agent_id, str(request_id or "").strip())

    @staticmethod
    def _tenant_key(scope: RuntimeScopeKey, local_id: str) -> _TenantSessionKey:
        return (scope.service_id, scope.agent_id, str(local_id or "").strip())

    def bind_stream_chat_flags(
        self,
        scope: RuntimeScopeKey,
        stream_request_id: str,
        interactive_ask: bool,
        *,
        session_id: str = "",
    ) -> None:
        rid = str(stream_request_id or "").strip()
        if rid:
            self._stream_interactive_ask[self._tenant_key(scope, rid)] = bool(interactive_ask)
        sid = str(session_id or scope.session_id or "").strip()
        if sid:
            self._session_interactive_ask[self._tenant_key(scope, sid)] = bool(interactive_ask)

    def unbind_stream_chat_flags(self, scope: RuntimeScopeKey, stream_request_id: str) -> None:
        self._stream_interactive_ask.pop(
            self._tenant_key(scope, str(stream_request_id or "").strip()),
            None,
        )

    def stream_interactive_ask_enabled(self, scope: RuntimeScopeKey, stream_request_id: str) -> bool:
        rid = str(stream_request_id or "").strip()
        return bool(rid) and bool(self._stream_interactive_ask.get(self._tenant_key(scope, rid)))

    def session_interactive_ask_enabled(self, scope: RuntimeScopeKey, session_id: str) -> bool:
        sid = str(session_id or "").strip()
        return bool(sid) and bool(self._session_interactive_ask.get(self._tenant_key(scope, sid)))

    def resolve(
        self,
        scope: RuntimeScopeKey,
        request_id: str,
        answers: Any,
        *,
        status: str = "answered",
    ) -> bool:
        key = self._ask_key(scope, request_id)
        if not key[2]:
            return False
        norm: list[Any] = answers if isinstance(answers, list) else []
        normalized_status = str(status or "answered").strip().lower()
        if normalized_status not in {"answered", "skipped"}:
            normalized_status = "answered"
        if normalized_status == "skipped" and norm:
            logger.warning(
                "[AskUserQuestionRegistry] rejected skipped request with non-empty answers request_id=%s",
                key[2],
            )
            return False
        fut = self._pending.pop(key, None)
        self._pending_sessions.pop(key, None)
        if fut is None or fut.done():
            return False
        result: Any = (
            {"status": "skipped", "answers": []}
            if normalized_status == "skipped"
            else norm
        )
        fut.set_result(result)
        logger.info(
            "[AskUserQuestionRegistry] resolved request_id=%s tenant=(%s,%s)",
            key[2],
            key[0],
            key[1],
        )
        return True

    def register(self, scope: RuntimeScopeKey, request_id: str) -> asyncio.Future[Any]:
        key = self._ask_key(scope, request_id)
        if not key[2]:
            raise ValueError("request_id 不能为空")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[key] = fut
        self._pending_sessions[key] = str(scope.session_id or "")
        return fut

    def cleanup(self, scope: RuntimeScopeKey, request_id: str) -> None:
        key = self._ask_key(scope, request_id)
        fut = self._pending.pop(key, None)
        self._pending_sessions.pop(key, None)
        if fut is not None and not fut.done():
            fut.cancel()

    def cancel_for_session(self, scope: RuntimeScopeKey) -> None:
        sid = str(scope.session_id or "")
        if not sid:
            return
        to_cancel = []
        for key, rid_sid in self._pending_sessions.items():
            if key[0] != scope.service_id:
                continue
            if key[1] != scope.agent_id:
                continue
            if rid_sid != sid:
                continue
            to_cancel.append(key)
        for key in to_cancel:
            fut = self._pending.pop(key, None)
            self._pending_sessions.pop(key, None)
            if fut is not None and not fut.done():
                fut.cancel()
        if to_cancel:
            logger.info(
                "[AskUserQuestionRegistry] cancelled pending ask requests for "
                "session_id=%s tenant=(%s,%s) count=%d",
                sid,
                scope.service_id,
                scope.agent_id,
                len(to_cancel),
            )
        self._session_interactive_ask.pop(self._tenant_key(scope, sid), None)

    async def wait_for_answer(self, request_id: str) -> Any:
        scope = get_ask_runtime_scope()
        fut = self.register(scope, request_id)
        try:
            return await fut
        except asyncio.CancelledError:
            self.cleanup(scope, request_id)
            raise
        finally:
            key = self._ask_key(scope, request_id)
            self._pending.pop(key, None)
            self._pending_sessions.pop(key, None)
