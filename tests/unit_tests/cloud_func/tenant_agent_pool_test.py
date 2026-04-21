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


    def test_get_lock_recreates_on_different_event_loop(self) -> None:
        """_get_lock 在事件循环变化时能正确重建锁."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def first_loop():
                pool = TenantAgentPool.get_instance()
                lock1 = pool._get_lock("test_key")
                self.assertIsInstance(lock1, asyncio.Lock)
                async with lock1:
                    pass
                return True

            self.assertTrue(asyncio.run(first_loop()))

            async def second_loop():
                pool = TenantAgentPool.get_instance()
                lock2 = pool._get_lock("test_key")
                self.assertIsInstance(lock2, asyncio.Lock)
                async with lock2:
                    pass
                return True

            self.assertTrue(asyncio.run(second_loop()))

    def test_get_lock_returns_same_lock_in_same_loop(self) -> None:
        """同一事件循环中多次调用 _get_lock 返回同一个锁对象."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def _run():
                pool = TenantAgentPool.get_instance()
                lock1 = pool._get_lock("same_loop_key")
                lock2 = pool._get_lock("same_loop_key")
                self.assertIs(lock1, lock2)

            asyncio.run(_run())

    def test_get_lock_creates_new_for_different_keys(self) -> None:
        """不同 cache_key 会创建不同的锁对象."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def _run():
                pool = TenantAgentPool.get_instance()
                lock1 = pool._get_lock("key_a")
                lock2 = pool._get_lock("key_b")
                self.assertIsNot(lock1, lock2)

            asyncio.run(_run())

    def test_ensure_agent_manager_across_event_loops(self) -> None:
        """跨事件循环调用 _ensure_agent_manager 不会报错."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def first_request():
                pool = TenantAgentPool.get_instance()
                manager = await pool._ensure_agent_manager("test_agent", "test_service")
                self.assertIsNotNone(manager)
                return True

            async def second_request():
                pool = TenantAgentPool.get_instance()
                manager = await pool._ensure_agent_manager("test_agent", "test_service")
                self.assertIsNotNone(manager)
                return True

            with patch(
                "jiuwenswarm.server.runtime.tenant_agent_pool.AgentManager",
                return_value=MagicMock(),
            ):
                self.assertTrue(asyncio.run(first_request()))
                self.assertTrue(asyncio.run(second_request()))

    def test_concurrent_requests_after_event_loop_change(self) -> None:
        """事件循环变化后，并发请求能正常工作."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def simulate_multiple_requests():
                pool = TenantAgentPool.get_instance()
                tasks = [
                    pool._ensure_agent_manager(f"agent_{i}", f"service_{i}")
                    for i in range(5)
                ]
                results = await asyncio.gather(*tasks)
                self.assertEqual(len(results), 5)
                for result in results:
                    self.assertIsNotNone(result)

            with patch(
                "jiuwenswarm.server.runtime.tenant_agent_pool.AgentManager",
                return_value=MagicMock(),
            ):
                asyncio.run(simulate_multiple_requests())

    def test_get_lock_detects_loop_change(self) -> None:
        """验证 _get_lock 能检测到事件循环变化并重建锁."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()
            pool = TenantAgentPool.get_instance()
            lock1_id = None

            async def first_loop():
                nonlocal lock1_id
                lock1 = pool._get_lock("detect_key")
                lock1_id = id(lock1)
                async with lock1:
                    pass

            async def second_loop():
                lock2 = pool._get_lock("detect_key")
                self.assertNotEqual(lock1_id, id(lock2), "事件循环变化后应创建新锁")
                async with lock2:
                    pass

            asyncio.run(first_loop())
            asyncio.run(second_loop())

    def test_lock_with_waiters_across_event_loop(self) -> None:
        """锁在有等待者时跨事件循环仍可重建后使用."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def first_loop_hold_lock():
                pool = TenantAgentPool.get_instance()
                lock = pool._get_lock("test_key")
                await lock.acquire()
                return lock

            asyncio.run(first_loop_hold_lock())

            async def second_loop_use_lock():
                pool = TenantAgentPool.get_instance()
                lock2 = pool._get_lock("test_key")
                async with lock2:
                    pass

            asyncio.run(second_loop_use_lock())

    def test_concurrent_waiters_across_event_loop(self) -> None:
        """有等待者的锁在事件循环变化后仍可服务新请求."""
        with patch.dict("os.environ", {"AGENT_RUNTIME": "k8s"}, clear=False):
            TenantAgentPool.reset_instance()

            async def first_loop_with_concurrent_requests():
                pool = TenantAgentPool.get_instance()
                tasks = [
                    asyncio.create_task(
                        pool._ensure_agent_manager("shared_agent", "shared_service")
                    )
                    for _ in range(3)
                ]
                await asyncio.sleep(0.01)
                return tasks

            with patch(
                "jiuwenswarm.server.runtime.tenant_agent_pool.AgentManager",
                return_value=MagicMock(),
            ):
                tasks = asyncio.run(first_loop_with_concurrent_requests())
                for task in tasks:
                    task.cancel()

                async def second_loop():
                    pool = TenantAgentPool.get_instance()
                    manager = await pool._ensure_agent_manager(
                        "shared_agent", "shared_service"
                    )
                    self.assertIsNotNone(manager)

                asyncio.run(second_loop())
