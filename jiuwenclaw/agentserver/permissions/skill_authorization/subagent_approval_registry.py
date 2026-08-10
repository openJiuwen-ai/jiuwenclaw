# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""子 Agent 委托审批注册表：保持子 Agent 协程存活，路由答案到 pending Future。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class SubagentApprovalCancelled(RuntimeError):
    """Pending delegated approval was withdrawn by lifecycle management."""


class SubagentApprovalCapacityError(RuntimeError):
    """A scope/session already reached its delegated approval capacity."""


class SubagentApprovalKind(str, Enum):
    """Delegated request types; answers may never cross these namespaces."""

    SKILL_LOAD = "skill_load"
    TOOL_PERMISSION = "tool_permission"


@dataclass(frozen=True)
class SubagentApprovalRequest:
    approval_id: str
    kind: SubagentApprovalKind
    session_id: str
    agent_scope_id: str
    tool_call_id: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)


@dataclass
class _PendingApproval:
    request: SubagentApprovalRequest
    future: asyncio.Future[Any]
    loop: asyncio.AbstractEventLoop


ApprovalSender = Callable[[SubagentApprovalRequest], Awaitable[None]]


class SubagentApprovalRegistry:
    """Process-local, one-shot approval router with per-session serialization."""

    _instance: ClassVar["SubagentApprovalRegistry | None"] = None

    def __init__(self, *, max_pending_per_session: int = 5) -> None:
        if max_pending_per_session <= 0:
            raise ValueError("max_pending_per_session 必须大于 0")
        self._guard = threading.RLock()
        self._pending: dict[str, _PendingApproval] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._max_pending_per_session = max_pending_per_session

    @classmethod
    def get_instance(cls) -> "SubagentApprovalRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def peek_instance(cls) -> "SubagentApprovalRegistry | None":
        """返回已创建的单例；未创建时返回 ``None``（不触发初始化）。"""
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        instance = cls._instance
        cls._instance = None
        if instance is not None:
            instance.clear_all()

    @property
    def pending_count(self) -> int:
        with self._guard:
            return len(self._pending)

    def pending_requests(self) -> tuple[SubagentApprovalRequest, ...]:
        with self._guard:
            return tuple(item.request for item in self._pending.values())

    async def request(
        self,
        *,
        kind: SubagentApprovalKind,
        session_id: str,
        agent_scope_id: str,
        tool_call_id: str,
        payload: dict[str, Any],
        sender: ApprovalSender,
        timeout: float,
    ) -> Any:
        sid = str(session_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        call_id = str(tool_call_id or "").strip()
        if not sid or not scope or not call_id:
            raise ValueError("session_id、agent_scope_id、tool_call_id 均不能为空")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        approval_id = f"subagent_{kind.value}_{uuid.uuid4().hex}"
        request = SubagentApprovalRequest(
            approval_id=approval_id,
            kind=kind,
            session_id=sid,
            agent_scope_id=scope,
            tool_call_id=call_id,
            payload=dict(payload),
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._guard:
            if any(
                item.request.session_id == sid
                and item.request.agent_scope_id == scope
                for item in self._pending.values()
            ):
                raise SubagentApprovalCapacityError(
                    f"scope already has a pending approval: session={sid!r} scope={scope!r}",
                )
            session_count = sum(
                item.request.session_id == sid for item in self._pending.values()
            )
            if session_count >= self._max_pending_per_session:
                raise SubagentApprovalCapacityError(
                    f"session pending approval limit reached: session={sid!r} "
                    f"limit={self._max_pending_per_session}",
                )
            self._pending[approval_id] = _PendingApproval(request, future, loop)
            session_lock = self._session_locks.setdefault(sid, asyncio.Lock())
        sender_task: asyncio.Task[None] | None = None
        try:
            async with session_lock:
                if future.done():
                    return await future
                # sender 耗时（前端展示）不计入响应 TTL，但生命周期取消必须能
                # 立即打断一个尚未返回的 sender，不能等发送链路自己结束。
                sender_task = asyncio.create_task(sender(request))
                done, _pending = await asyncio.wait(
                    {sender_task, future},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if future in done:
                    if not sender_task.done():
                        sender_task.cancel()
                    await asyncio.gather(sender_task, return_exceptions=True)
                    return await future
                await sender_task
                if future.done():
                    return await future
                return await asyncio.wait_for(future, timeout=timeout)
        finally:
            if sender_task is not None and not sender_task.done():
                sender_task.cancel()
                await asyncio.gather(sender_task, return_exceptions=True)
            with self._guard:
                current = self._pending.get(approval_id)
                if current is not None and current.future is future:
                    self._pending.pop(approval_id, None)
                # 该 session 已无 pending 时回收锁，避免长跑进程泄漏。
                if not any(
                    item.request.session_id == sid for item in self._pending.values()
                ):
                    self._session_locks.pop(sid, None)
            if not future.done():
                future.cancel()

    def resolve(
        self,
        *,
        session_id: str,
        approval_id: str,
        kind: SubagentApprovalKind,
        answer: Any,
        agent_scope_id: str | None = None,
    ) -> bool:
        sid = str(session_id or "").strip()
        rid = str(approval_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        if not sid or not rid or not scope:
            return False
        with self._guard:
            pending = self._pending.get(rid)
            if pending is None:
                return False
            request = pending.request
            if request.session_id != sid or request.kind != kind:
                return False
            if request.agent_scope_id != scope:
                logger.warning(
                    "[skill_authorization] subagent.approval_scope_mismatch "
                    "session=%s expected_scope=%s got_scope=%s kind=%s",
                    sid, request.agent_scope_id, scope, kind.value,
                )
                return False
            self._pending.pop(rid, None)
        pending.loop.call_soon_threadsafe(
            self._complete_future,
            pending.future,
            answer,
            None,
        )
        logger.info(
            "[skill_authorization] subagent.approval_resolved kind=%s session=%s scope=%s",
            kind.value,
            sid,
            request.agent_scope_id,
        )
        return True

    def cancel_scope(self, session_id: str, agent_scope_id: str) -> int:
        sid = str(session_id or "").strip()
        scope = str(agent_scope_id or "").strip()
        return self._cancel_matching(
            lambda item: item.request.session_id == sid
            and item.request.agent_scope_id == scope,
        )

    def cancel_session(self, session_id: str) -> int:
        sid = str(session_id or "").strip()
        return self._cancel_matching(lambda item: item.request.session_id == sid)

    def clear_all(self) -> int:
        return self._cancel_matching(lambda _item: True)

    def _cancel_matching(self, predicate: Callable[[_PendingApproval], bool]) -> int:
        with self._guard:
            matches = [rid for rid, item in self._pending.items() if predicate(item)]
            pending = [self._pending.pop(rid) for rid in matches]
            # 清理无 pending 的 session 锁，避免长跑进程 _session_locks 字典无限增长。
            if pending:
                active_sessions = {
                    item.request.session_id for item in self._pending.values()
                }
                stale_locks = [
                    sid for sid in self._session_locks if sid not in active_sessions
                ]
                for sid in stale_locks:
                    self._session_locks.pop(sid, None)
        for item in pending:
            item.loop.call_soon_threadsafe(
                self._complete_future,
                item.future,
                None,
                SubagentApprovalCancelled("delegated approval was cancelled"),
            )
        return len(pending)

    @staticmethod
    def _complete_future(
        future: asyncio.Future[Any],
        result: Any,
        error: BaseException | None,
    ) -> None:
        """只在 Future 所属事件循环完成终态，避免跨线程直接变更。"""
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)


def get_subagent_approval_registry() -> SubagentApprovalRegistry:
    return SubagentApprovalRegistry.get_instance()
