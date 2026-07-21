# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team Agent 流式处理辅助方法.

从 interface_deep.py 中提取的 Team 模式核心处理逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.team import get_team_manager
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.stream_utils import parse_stream_chunk
from jiuwenclaw.schema.agent import AgentResponseChunk

logger = logging.getLogger(__name__)

# Keyed by RuntimeScopeKey.session_key() for multi-tenant isolation.
_pending_waiters: dict[tuple[str, str, str], list[tuple[str, asyncio.Queue]]] = {}


def _broadcast_event(waiter_key: tuple[str, str, str], event: dict) -> None:
    """广播事件到所有等待的请求队列."""
    waiters = _pending_waiters.get(waiter_key, [])
    for request_id, queue in waiters:
        try:
            queue.put_nowait(dict(event))
        except Exception:
            logger.debug(
                "[TeamHelpers] 广播事件失败: key=%s request_id=%s",
                waiter_key,
                request_id,
            )


async def process_team_message_stream(
    request: Any,
    inputs: dict[str, Any],
    deep_agent: DeepAgent,
    *,
    runtime_scope: RuntimeScopeKey | None = None,
) -> AsyncIterator[AgentResponseChunk]:
    """处理 Team 模式的流式消息.

    Args:
        request: AgentRequest 对象
        inputs: 已构建好的输入字典
        deep_agent: DeepAgent 实例
        runtime_scope: explicit tenant+session scope (required for multi-tenant)

    Yields:
        AgentResponseChunk 流式响应块
    """
    scope = runtime_scope or RuntimeScopeKey.from_request(request, include_session=True)
    if not scope.session_id:
        scope = scope.with_session(getattr(request, "session_id", None) or "default")
    waiter_key = scope.session_key()
    session_id = scope.session_id
    rid = request.request_id
    cid = request.channel_id

    team_manager = get_team_manager()

    try:
        if deep_agent is None:
            raise RuntimeError("DeepAgent 未初始化")

        team_agent = await team_manager.get_or_create_team(
            scope,
            deep_agent=deep_agent,
            request_id=rid,
            channel_id=cid,
            request_metadata=request.metadata,
        )

    except Exception as exc:
        logger.exception("[TeamHelpers] TeamAgent create failed: %s", exc)
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={"event_type": "chat.error", "error": str(exc)},
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )
        return

    query = inputs.get("query", "")

    is_first_request = not team_manager.has_stream_task(scope)

    request_queue: asyncio.Queue | None = None

    try:
        if is_first_request:
            request_queue = asyncio.Queue()
            if waiter_key not in _pending_waiters:
                _pending_waiters[waiter_key] = []
            _pending_waiters[waiter_key].append((rid, request_queue))
            logger.info(
                "[TeamHelpers] 首次请求,启动stream: scope=%s session_id=%s, query=%s",
                scope.tenant(),
                session_id,
                query[:50] if query else "",
            )

            monitor_handler = TeamMonitorHandler(team_agent, session_id)
            try:
                await monitor_handler.start()
                team_manager.register_monitor(scope, monitor_handler)
                logger.info(
                    "[TeamHelpers] Monitor 启动成功: scope=%s session_id=%s",
                    scope.tenant(),
                    session_id,
                )
            except Exception as e:
                logger.warning("[TeamHelpers] Monitor 启动失败，将继续运行: %s", e)

            stream_task = asyncio.create_task(
                _consume_stream_with_query(
                    scope,
                    team_agent,
                    query,
                )
            )
            team_manager.register_stream_task(scope, stream_task)

            if monitor_handler.is_running:
                asyncio.create_task(
                    _consume_monitor_events(
                        waiter_key,
                        session_id,
                        monitor_handler,
                    )
                )
        else:
            logger.info(
                "[TeamHelpers] 后续请求,调用interact: scope=%s session_id=%s, query=%s",
                scope.tenant(),
                session_id,
                query[:100] if query else "",
            )

            if query:
                success = await team_manager.interact(scope, query)
                if not success:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload={"event_type": "chat.error", "error": "interact失败"},
                        is_complete=False,
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload=None,
                        is_complete=True,
                    )
                    return
            logger.info(
                "[TeamHelpers] follow-up request submitted without waiter: scope=%s session_id=%s request_id=%s",
                scope.tenant(),
                session_id,
                rid,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=None,
                is_complete=True,
            )
            return

        try:
            while team_manager.has_stream_task(scope):
                if request_queue is None:
                    break
                try:
                    event = await asyncio.wait_for(request_queue.get(), timeout=0.1)

                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=cid,
                        payload=event,
                        is_complete=False,
                    )

                    if isinstance(event, dict) and event.get("event_type") == "team.error":
                        break

                except asyncio.TimeoutError:
                    if not team_manager.has_stream_task(scope):
                        break
                    continue

        except asyncio.CancelledError:
            logger.info(
                "[TeamHelpers] 事件流被取消: scope=%s session_id=%s request_id=%s",
                scope.tenant(),
                session_id,
                rid,
            )
            raise
        except Exception as exc:
            logger.exception(
                "[TeamHelpers] 事件流异常: scope=%s session_id=%s error=%s",
                scope.tenant(),
                session_id,
                exc,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    finally:
        if request_queue is not None:
            waiters = _pending_waiters.get(waiter_key, [])
            _pending_waiters[waiter_key] = [
                (req_id, q) for req_id, q in waiters if req_id != rid
            ]

            if not _pending_waiters.get(waiter_key, []):
                _pending_waiters.pop(waiter_key, None)
                logger.info(
                    "[TeamHelpers] Session 无等待者，清理: scope=%s session_id=%s",
                    scope.tenant(),
                    session_id,
                )


async def _consume_stream_with_query(
    scope: RuntimeScopeKey,
    team_agent: Any,
    initial_query: str,
) -> None:
    """后台持续消费Team的stream，并广播事件到所有等待者."""
    waiter_key = scope.session_key()
    session_id = scope.session_id
    try:
        logger.info(
            "[TeamHelpers] Stream协程开始: scope=%s session_id=%s, initial_query=%s",
            scope.tenant(),
            session_id,
            initial_query[:50] if initial_query else "",
        )

        async for chunk in Runner.run_agent_team_streaming(
            agent_team=team_agent,
            inputs={"query": initial_query},
            session=session_id,
        ):
            parsed = parse_stream_chunk(chunk)
            if parsed is not None:
                _broadcast_event(waiter_key, parsed)

        logger.warning(
            "[TeamHelpers] Stream意外结束: scope=%s session_id=%s",
            scope.tenant(),
            session_id,
        )

    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] Stream协程被取消: scope=%s session_id=%s",
            scope.tenant(),
            session_id,
        )
        raise
    except Exception as e:
        logger.error(
            "[TeamHelpers] Stream协程异常: scope=%s session_id=%s, error=%s",
            scope.tenant(),
            session_id,
            e,
        )
        error_event = {
            "event_type": "team.error",
            "error": str(e),
            "session_id": session_id,
        }
        _broadcast_event(waiter_key, error_event)
    finally:
        team_manager = get_team_manager()
        team_manager.pop_stream_task(scope)


async def _consume_monitor_events(
    waiter_key: tuple[str, str, str],
    session_id: str,
    monitor_handler: TeamMonitorHandler,
) -> None:
    """后台持续消费Monitor的事件，并广播到所有等待者."""
    try:
        logger.info(
            "[TeamHelpers] Monitor事件协程开始: key=%s session_id=%s",
            waiter_key,
            session_id,
        )

        async for event in monitor_handler.events():
            _broadcast_event(waiter_key, event)

        logger.info(
            "[TeamHelpers] Monitor事件协程结束: key=%s session_id=%s",
            waiter_key,
            session_id,
        )

    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] Monitor事件协程被取消: key=%s session_id=%s",
            waiter_key,
            session_id,
        )
        raise
    except Exception as e:
        logger.error(
            "[TeamHelpers] Monitor事件协程异常: key=%s session_id=%s, error=%s",
            waiter_key,
            session_id,
            e,
        )


async def teardown_team_runtime(
    team_monitors: dict[str, Any],
    team_agents: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """清理所有 Team 运行时."""
    for session_id, monitor in list(team_monitors.items()):
        try:
            await monitor.stop()
        except Exception as exc:
            logger.warning(
                "[TeamHelpers] TeamMonitor stop failed: session_id=%s err=%s",
                session_id,
                exc,
            )

    return {}, {}
