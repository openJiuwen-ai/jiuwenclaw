import asyncio
from dataclasses import dataclass
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.tenant_agent_pool import TenantAgentPool


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
    async for _chunk in handler.process_message_stream(request):
        pass


async def run_non_stream_request(handler, request: AgentRequest) -> None:
    """执行非流式请求并打印结果."""
    await handler.process_message(request)


class TestTenantAgentPool(TestCase):
    """TenantAgentPool 单元测试"""

    def setUp(self) -> None:
        """每个测试前重置单例."""
        TenantAgentPool.reset_instance()

    def tearDown(self) -> None:
        TenantAgentPool.reset_instance()

    def test_community_singleton(self) -> None:
        """未设置 AGENT_RUNTIME 时为单 AgentManager 模式."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": ""}, clear=False):
            TenantAgentPool.reset_instance()
            pool = TenantAgentPool.get_instance()
            self.assertFalse(pool._enterprise)
            self.assertIsNotNone(pool._agent_manager)

    def test_enterprise_pool(self) -> None:
        """AGENT_RUNTIME 下启用多租户 LRU."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()
            pool = TenantAgentPool.get_instance()
            self.assertTrue(pool._enterprise)

            async def _run():
                mgr1 = await pool.get_agent_manager("a1", "s1")
                mgr2 = await pool.get_agent_manager("a1", "s1")
                mgr3 = await pool.get_agent_manager("a2", "s2")
                self.assertIs(mgr1, mgr2)
                self.assertIsNot(mgr1, mgr3)
                self.assertEqual(mgr1.agent_id, "a1")
                self.assertEqual(mgr1.service_id, "s1")
                self.assertIsNotNone(mgr1.user_workspace_dir)

            asyncio.run(_run())

    def test_build_service_id(self) -> None:
        self.assertEqual(
            TenantAgentPool.build_service_id("chat1", "bot1"),
            "chat1_bot1",
        )
        self.assertEqual(
            TenantAgentPool.build_service_id(None, None),
            "unknown_chat_id_unknown_bot_app_id",
        )

    def test_process_message_dispatch(self) -> None:
        """process_message 分发到内部 AgentManager."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": ""}, clear=False):
            TenantAgentPool.reset_instance()
            pool = TenantAgentPool.get_instance()
            mock_resp = MagicMock()
            pool._agent_manager.process_message = AsyncMock(return_value=mock_resp)

            async def _run():
                req = build_request(RequestParams(
                    request_id="r1",
                    session_id="s1",
                    query="hi",
                ))
                resp = await pool.process_message(req)
                self.assertIs(resp, mock_resp)

            asyncio.run(_run())
