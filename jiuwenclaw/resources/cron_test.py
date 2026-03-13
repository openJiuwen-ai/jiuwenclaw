from __future__ import annotations

import asyncio
import os
import time
from typing import Any, List

from jiuwenclaw.agentserver.interface import JiuWenClaw
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.message import ReqMethod


class InlineAgentClient(AgentServerClient):
    """在本进程内直接调用 JiuWenClaw 的 AgentServerClient 实现，用于测试."""

    def __init__(self, agent: JiuWenClaw) -> None:
        self._agent = agent

    async def connect(self, uri: str) -> None:  # noqa: D401
        """测试客户端不需要真正的连接."""
        return None

    async def disconnect(self) -> None:  # noqa: D401
        """测试客户端不需要断连操作."""
        return None

    async def send_request(self, request: AgentRequest) -> AgentResponse:
        # 直接将请求转给 JiuWenClaw 处理
        return await self._agent.process_message(request)

    async def send_request_stream(self, request: AgentRequest) -> AgentResponseChunk:  # type: ignore[override]
        raise RuntimeError("InlineAgentClient does not support streaming in this test")


class TestMessageSink:
    """仅用于收集 CronScheduler 推送到各 Channel 的消息."""

    def __init__(self) -> None:
        self.messages: List[Any] = []

    async def publish_robot_messages(self, msg: Any) -> None:
        self.messages.append(msg)


async def _prepare_model_env() -> None:
    """确保模型相关环境变量已就绪，便于在本地直接调用 DashScope Qwen3."""
    # 用户需要在外部导出/写入 .env，这里只做缺省提示，不写入真实密钥。
    required_keys = ["MODEL_PROVIDER", "MODEL_NAME", "API_BASE", "API_KEY"]
    defaults = {
        "MODEL_PROVIDER": "DashScope",
        "MODEL_NAME": "qwen3-max",
        "API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)

    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "缺少模型配置环境变量，请先在系统环境或 ~/.jiuwenclaw/.env 中设置："
            "MODEL_PROVIDER, MODEL_NAME, API_BASE, API_KEY"
        )


async def main() -> None:
    """简单的 cron 集成测试，不依赖前端 WebChannel."""
    await _prepare_model_env()

    # 1. 初始化 Agent
    agent = JiuWenClaw()
    await agent.create_instance()

    client = InlineAgentClient(agent)
    sink = TestMessageSink()

    # 2. 初始化 cron 组件
    CronController.reset_instance()
    store = CronJobStore()
    scheduler = CronSchedulerService(store=store, agent_client=client, message_handler=sink)
    controller = CronController.get_instance(store=store, scheduler=scheduler)

    # 3. 创建一个立即执行的 cron 任务（通过 run_now 触发，无需等待真实 cron 时间）
    params = {
        "name": "cron-demo",
        "enabled": True,
        # 表达式只需合法，这里选择每分钟一次
        "cron_expr": "13 * * * *",
        "timezone": "Asia/Shanghai",
        "wake_offset_seconds": 60,
        "description": "你叫什么名字？",
        "targets": "web",
    }

    job = await controller.create_job(params)
    print("[cron_test] created job:", job["id"])

    # 4. 启动调度器，并立即触发一次 run_now
    await scheduler.start()
    # run_id = await controller.run_now(job["id"])
    # print("[cron_test] triggered run_now:", run_id)

    # 5. 等待一段时间，直到收到至少一条推送消息或超时
    deadline = time.time() + 180.0
    print("[cron_test] waiting for cron messages ...")
    last_count = 0
    while time.time() < deadline:
        if len(sink.messages) != last_count:
            last_count = len(sink.messages)
            print(f"[cron_test] received {last_count} message(s) so far")
        if sink.messages:
            sign = 0
            for msg in sink.messages:
                payload = getattr(msg, "payload", {}) or {}
                status = payload.get("cron", {}).get("status")
                if status == "succeeded":
                    sign = 1
                    break
            if sign==1:
                break
        await asyncio.sleep(2.0)

    await scheduler.stop()

    # 6. 打印收到的消息内容，方便人工检查
    if not sink.messages:
        print("[cron_test] no messages received within timeout")
        return

    print(f"[cron_test] total messages: {len(sink.messages)}")
    for i, msg in enumerate(sink.messages, start=1):
        payload = getattr(msg, "payload", {}) or {}
        cron_meta = (payload or {}).get("cron", {})
        content = (payload or {}).get("content", "")
        print(f"\n[cron_test] message #{i}:")
        print("  channel_id :", getattr(msg, "channel_id", None))
        print("  session_id :", getattr(msg, "session_id", None))
        print("  is_placeholder:", cron_meta.get("is_placeholder"))
        print("  status      :", cron_meta.get("status"))
        print("  push_at     :", cron_meta.get("push_at"))
        print("  wake_at     :", cron_meta.get("wake_at"))
        print("  content     :", str(content)[:500])


if __name__ == "__main__":
    asyncio.run(main())

