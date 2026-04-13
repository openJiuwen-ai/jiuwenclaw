import asyncio
import json
from dataclasses import dataclass
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod
from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool


@dataclass
class RequestParams:
    """测试请求参数封装."""
    request_id: str
    session_id: str
    query: str
    is_stream: bool = False
    agent_id: str | None = None
    service_id: str | None = None
    mode: str = "agent"


def build_request(params: RequestParams) -> AgentRequest:
    """构建测试用的 AgentRequest."""
    return AgentRequest(
        request_id=params.request_id,
        channel_id="web",
        session_id=params.session_id,
        agent_id=params.agent_id,
        service_id=params.service_id,
        req_method=ReqMethod.CHAT_SEND,
        params={
            "session_id": params.session_id,
            "content": params.query,
            "mode": params.mode,
            "query": params.query,
        },
        is_stream=params.is_stream,
        timestamp=1774319436.5314593,
        metadata={"method": "chat.send"},
    )


async def run_stream_request(handler, request: AgentRequest) -> None:
    """执行流式请求并打印结果."""
    chunk_count = 0
    async for chunk in handler.process_message_stream(request):
        chunk_count += 1


async def run_non_stream_request(handler, request: AgentRequest) -> None:
    """执行非流式请求并打印结果."""
    result = await handler.process_message(request)


class TestTenantAgentPool(TestCase):
    """TenantAgentPool 单元测试"""

    def setUp(self) -> None:
        """每个测试前重置单例."""
        TenantAgentPool.reset_instance()

    def test_jiuwenclaw(self) -> None:
        """对应原 test_jiuwenclaw：单用户流式请求（plan + agent 模式）."""

        async def _run():
            handler = TenantAgentPool.get_instance()

            # hello_stream: mode="plan"
            req1 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=True,
                agent_id="default_agent",
                service_id="default_service",
                mode="plan",
            ))
            await run_stream_request(handler, req1)

            # plus_one_stream: mode="agent"
            req2 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=True,
                agent_id="default_agent",
                service_id="default_service",
                mode="agent",
            ))
            await run_stream_request(handler, req2)

        asyncio.run(_run())

    def test_jiuwenclaw_not_stream(self) -> None:
        """对应原 test_jiuwenclaw_not_stream：单用户非流式请求."""

        async def _run():
            handler = TenantAgentPool.get_instance()

            # hello_not_stream: mode="plan"
            req1 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=False,
                agent_id="default_agent",
                service_id="default_service",
                mode="plan",
            ))
            await run_non_stream_request(handler, req1)

            # plus_one_not_stream: mode="agent"
            req2 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=False,
                agent_id="default_agent",
                service_id="default_service",
                mode="agent",
            ))
            await run_non_stream_request(handler, req2)

        asyncio.run(_run())


    @patch("jiuwenclaw.gateway.cron.CronController")
    @patch("jiuwenclaw.agentserver.interface.JiuWenClaw")
    def test_jiuwenclaw_multi_user(
            self,
            mock_jiuwenclaw,
            mock_cron_controller,
    ) -> None:
        """对应原 test_jiuwenclaw_multi_user：多用户隔离测试."""

        mock_agent_instance = MagicMock()
        mock_agent_instance.create_instance = AsyncMock()
        mock_agent_instance.process_message = AsyncMock(return_value=MagicMock())
        mock_jiuwenclaw.return_value = mock_agent_instance

        mock_cron_controller.get_instance.return_value = MagicMock()

        async def _run():
            handler = TenantAgentPool.get_instance()

            # multi_user: agent_id="_userID", instance_id="_groupID_botID"
            req1 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=False,
                agent_id="_userID_agent",
                service_id="_userID_service",
                mode="plan",
            ))
            await run_non_stream_request(handler, req1)

            req2 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=False,
                agent_id="_userID_agent",
                service_id="_userID_service",
                mode="agent",
            ))
            await run_non_stream_request(handler, req2)

            # multi_user_02: agent_id="_userID_02"
            req3 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=False,
                agent_id="_userID_02_agent",
                service_id="_userID_02_service",
                mode="plan",
            ))
            await run_non_stream_request(handler, req3)

            req4 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=False,
                agent_id="_userID_02_agent",
                service_id="_userID_02_service",
                mode="agent",
            ))
            await run_non_stream_request(handler, req4)

            # multi_user_03: agent_id="_userID_01", instance_id="_groupID_botID_02"
            req5 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=False,
                agent_id="_userID_01_agent",
                service_id="_userID_01_service",
                mode="plan",
            ))
            await run_non_stream_request(handler, req5)

            req6 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=False,
                agent_id="_userID_01_agent",
                service_id="_userID_01_service",
                mode="agent",
            ))
            await run_non_stream_request(handler, req6)

            # multi_user_04: agent_id="_userID_01", instance_id="_groupID_botID_02" (同 user_03)
            req7 = build_request(RequestParams(
                request_id="req_mn3zzbar_9",
                session_id="sess_19d1a822ed6_228b32",
                query="你好",
                is_stream=False,
                agent_id="_userID_01_agent",
                service_id="_userID_01_service",
                mode="plan",
            ))
            await run_non_stream_request(handler, req7)

            req8 = build_request(RequestParams(
                request_id="req_mn3zzyec_14",
                session_id="sess_19d1a822ed6_228b32",
                query="1+1等于几",
                is_stream=False,
                agent_id="_userID_01_agent",
                service_id="_userID_01_service",
                mode="agent",
            ))
            await run_non_stream_request(handler, req8)

            # 验证实例数量（应该有 3 个不同的 agent_id）
            agent_count = await handler.get_agent_count()
            self.assertGreaterEqual(agent_count, 3, "应至少有 3 个不同 agent 的实例")

        asyncio.run(_run())

    def test_instance_id_building(self) -> None:
        """instance_id 构建逻辑测试."""
        handler = TenantAgentPool.get_instance()

        # 正常情况
        instance_id = handler.build_service_id("chat123", "bot456")
        self.assertEqual(instance_id, "chat123_bot456")

        # chat_id 为空
        instance_id_empty_chat = handler.build_service_id(None, "bot456")
        self.assertEqual(instance_id_empty_chat, "unknown_chat_id_bot456")

        # bot_app_id 为空
        instance_id_empty_bot = handler.build_service_id("chat123", None)
        self.assertEqual(instance_id_empty_bot, "chat123_unknown_bot_app_id")

        # 都为空
        instance_id_both_empty = handler.build_service_id(None, None)
        self.assertEqual(instance_id_both_empty, "unknown_chat_id_unknown_bot_app_id")
