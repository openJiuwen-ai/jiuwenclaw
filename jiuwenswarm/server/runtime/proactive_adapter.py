# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ProactiveEngine 初始化与适配层。

把主动推荐的适配逻辑从 app_agentserver 抽出，集中管理：
- build_proactive_agent: 建专用决策 agent（无 tools、单轮、输出 JSON）
- trigger_main_agent: 触发主 agent 跑一轮生成话术 → stream 推前端
- init_proactive_engine: 组装 ProactiveEngine + 注入 agent + callback

app_agentserver 只需调 init_proactive_engine(server, config)。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def build_proactive_agent():
    """Build the lightweight proactive agent for proactive recommendation decisions.

    无 tools、无 task_loop、单轮、输出 JSON。复用 _get_model 的模型选择逻辑。
    替代 proactive_actions._analyze_and_decide 里的裸 model.invoke —— 走 agent
    框架的 invoke 链路（rails / 模型选择 / 观测），不再手搓 Model/SystemMessage。
    """
    try:
        from jiuwenswarm.agents.harness.common.recommendation.proactive_actions import _get_model
        from openjiuwen.harness.factory import create_deep_agent
        from openjiuwen.core.single_agent import AgentCard
    except ImportError as exc:
        logger.warning("[AgentServer] proactive agent imports failed: %s", exc)
        return None

    model = _get_model(temperature=0.0)
    if model is None:
        logger.warning("[AgentServer] proactive agent: no model configured")
        return None
    try:
        return create_deep_agent(
            model=model,
            card=AgentCard(name="proactive_agent", id="proactive_agent"),
            system_prompt="你是用户洞察与推荐助手。严格输出 JSON 对象。",
            tools=[],
            rails=[],
            enable_task_loop=False,
            max_iterations=1,
            add_general_purpose_agent=False,
        )
    except Exception as exc:
        logger.warning("[AgentServer] proactive agent build failed: %s", exc)
        return None


async def trigger_main_agent(server, session_id: str, channel_id: str | None,
                             query: str, decision: Any) -> bool:
    """Drive the main agent to run one round with the directive-style query.

    避让：目标 session 正在跑 stream 时跳过（同 session 不支持并发 stream）。
    触发后主 agent 自己生成话术 → 进 context engine → stream 推前端。
    Returns True on triggered, False on busy/missing adapter/failure.
    """
    try:
        from jiuwenswarm.common.schema.agent import AgentRequest
    except ImportError as exc:
        logger.warning("[AgentServer] trigger_main_agent import failed: %s", exc)
        return False

    cid = channel_id or "web"
    # 用外层 agent（JiuWenSwarm）调 process_message_stream——它做 session 管理、
    # history 落盘、mode 解析等前置后委托给内层 adapter 的 process_message_stream_impl。
    # 内层 adapter 只有 impl 方法，直接调会 AttributeError。
    #
    # 用 get_agent_nowait（不自动创建）：tick 不应替用户建 agent——自动建时 cache_key
    # （mode:sub_mode:project_dir）和用户对话实际用的可能不一致（用户对话带 project_dir/
    # sub_mode），会建出第二个 agent，导致推荐进的不是用户对话用的 context。
    # agent 不在内存 = 用户尚未在该 channel 发过消息（无活跃 context 可投递）→ 跳过本次 tick，
    # 等用户用过一次、agent 建好后下个 tick 自然拿到。
    agent = server.get_agent_manager().get_agent_nowait(cid)
    if agent is None or not hasattr(agent, "process_message_stream"):
        logger.info("[ProactiveEngine] trigger: no agent for channel=%s "
                    "(user hasn't used this channel yet), skipping", cid)
        return False
    # 内层 adapter 用于避让检查（is_deep_agent_executing_for_session 在 adapter 上）
    # 用公开 resolve_adapter 避开 protected-access
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer
    adapter = AgentWebSocketServer.resolve_adapter(agent)

    # 避让：目标 session 正忙 → 跳过本次 tick
    if adapter is not None and hasattr(adapter, "is_deep_agent_executing_for_session"):
        try:
            if adapter.is_deep_agent_executing_for_session(session_id):
                logger.info("[ProactiveEngine] trigger: session %s busy, skipping", str(session_id)[:20])
                return False
        except Exception as exc:
            logger.debug("[ProactiveEngine] is_deep_agent_executing_for_session check failed: %s", exc)

    request = AgentRequest(
        request_id=f"proactive_{decision.type}_{int(time.time() * 1000)}",
        channel_id=cid,
        session_id=session_id,
        # source=proactive_recommendation 标记这是系统触发的推荐指令，不是用户说的话。
        # process_message_stream 写 user history 时据此跳过——否则刷新页面会看到
        # "[主动推荐指令] xxx" 这条用户没说过的消息。
        # proactive_type/target 给 assistant 写 history 时透传用（待通用流程支持）。
        params={
            "query": query,
            "mode": "agent.plan",
            "source": "proactive_recommendation",
            "proactive_type": decision.type,
            "proactive_target": decision.target,
        },
        is_stream=True,
    )
    try:
        async for chunk in agent.process_message_stream(request):
            # chunk 经 server.send_push 推 Gateway。send_push 内部已用
            # _current_send_lock 串行化 ws 发送，且 build_server_push_wire 走
            # chunk 分支（无 response_kind）正确编码——这里只需带齐 chunk 的
            # request_id / payload / is_complete，Gateway 才能按 request_id 路由。
            #
            # 注入 source/proactive_type：主 agent 的 chunk 是普通对话格式，
            # 不带主动推荐标记。前端靠 payload.source==='proactive_recommendation'
            # 识别卡片、payload.proactive_type 选颜色，缺这俩会退化成普通白色气泡。
            # decision.type 在手上，给每个 chunk 的 payload 补上。
            try:
                chunk_payload = dict(getattr(chunk, "payload", None) or {})
                chunk_payload.setdefault("source", "proactive_recommendation")
                chunk_payload.setdefault("proactive_type", decision.type)
                chunk_payload.setdefault("proactive_target", decision.target)
                await server.send_push({
                    "request_id": getattr(chunk, "request_id", "") or request.request_id,
                    "channel_id": cid,
                    "session_id": session_id,
                    "payload": chunk_payload,
                    "is_complete": bool(getattr(chunk, "is_complete", False)),
                })
            except Exception as exc:
                logger.debug("[ProactiveEngine] send_push chunk failed: %s", exc)
        return True
    except Exception as exc:
        logger.warning("[ProactiveEngine] trigger: process_message_stream failed: %s", exc, exc_info=True)
        return False


async def init_proactive_engine(server, config: dict[str, Any] | None = None) -> None:
    """组装 ProactiveEngine + 注入专用 agent + 触发回调，挂到 server 上。

    app_agentserver 启动时调用，把所有 proactive 适配逻辑集中在此。
    """
    from jiuwenswarm.agents.harness.common.recommendation.proactive_engine import ProactiveEngine

    try:
        proactive_config = config or {}
        proactive_engine = ProactiveEngine(proactive_config)

        # 专用 agent：只做决策（无 tools、无 task_loop、单轮、输出 JSON），
        # 替代 proactive_actions._analyze_and_decide 里的裸 model.invoke。
        proactive_agent = build_proactive_agent()
        proactive_engine.set_proactive_agent(proactive_agent)

        # 检查 agent 是否活跃——在调 LLM 之前检查，避免 agent 被 evict 后白调 LLM。
        def _check_agent_cb(channel_id):
            cid = channel_id or "web"
            agent = server.get_agent_manager().get_agent_nowait(cid)
            return agent is not None and hasattr(agent, "process_message_stream")
        proactive_engine.set_check_agent_available_callback(_check_agent_cb)

        # 推送通知回调——直接推文本到前端，不经过主 agent（不进 context）。
        # 用于"今日推荐已达上限"等系统提醒。
        async def _send_notification_cb(channel_id, text):
            cid = channel_id or "web"
            import time as _time
            try:
                await server.send_push({
                    "request_id": f"proactive_notification_{int(_time.time() * 1000)}",
                    "channel_id": cid,
                    "payload": {
                        "content": text,
                        "event_type": "chat.final",
                        "role": "assistant",
                        "source": "proactive_notification",
                    },
                })
                return True
            except Exception as exc:
                logger.debug("[ProactiveEngine] send_notification push failed: %s", exc)
                return False
        proactive_engine.set_send_notification_callback(_send_notification_cb)

        # 触发主 agent 回调：tick 决策后，把决策包成指令式 query 触发主 agent
        # 跑一轮，主 agent 自己生成话术 → 进 context engine → stream 推前端。
        async def _trigger_cb(session_id, channel_id, query, decision):
            return await trigger_main_agent(server, session_id, channel_id, query, decision)

        proactive_engine.set_trigger_main_agent_callback(_trigger_cb)
        server.set_proactive_engine(proactive_engine)
    except Exception as exc:
        logger.warning("[AgentServer] ProactiveEngine initialization failed: %s", exc)
