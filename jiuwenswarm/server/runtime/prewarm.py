"""AgentServer 端到端启动预热。

进程启动时创建一个临时 JiuWenSwarm DeepAgent 并执行一次简单 query，触发模块
import / checkpointer / 模型 client / DeepAgent 工厂全链路首次初始化，降低首个
真实请求时延。后台执行，失败或超时仅告警不阻塞启动。

模型调用通过 ``WarmupModelClient`` 短路（构造期注入到 ClientRegistry，零
monkeypatch）：走通 DeepAgent 全链路构建，只短路最终 LLM HTTP 调用，不消耗 token。
"""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from typing import Any
from uuid import uuid4

from openjiuwen.core.foundation.llm.model_clients.base_model_client import (
    BaseModelClient,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import (
    AssistantMessageChunk,
)

from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod

logger = logging.getLogger(__name__)


class WarmupModelClient(BaseModelClient):
    """Mock LLM client for startup warmup.

    构造期注入：类定义即触发 ``BaseModelClient.__init_subclass__`` 自动注册到进程级
    ``ClientRegistry``（provider name = "warmup", type = "llm"）。预热 agent 构建时
    ``create_model_client`` 命中本类，走通整个 Model 构建链路（ModelClientConfig 验证、
    create_model_client、callback framework 包装、create_deep_agent 工厂、rails、
    checkpointer），只短路最终 HTTP 调用。
    """

    __client_name__ = "warmup"
    __client_type__ = "llm"

    def __init__(self, model_config: Any, model_client_config: Any) -> None:
        # 跳过 _validate_config（warmup 无需真 api_key/api_base），只存引用。
        self.model_config = model_config
        self.model_client_config = model_client_config

    async def invoke(self, messages: Any, **kwargs: Any) -> AssistantMessage:
        return AssistantMessage(content="warmup ok")

    async def stream(self, messages: Any, **kwargs: Any):  # type: ignore[override]
        yield AssistantMessageChunk(content="warmup ok")

    async def generate_image(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    async def generate_speech(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    async def generate_video(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError


def _build_warmup_config_base() -> dict[str, Any]:
    """Deep copy 当前 config 并把模型 client_provider 改成 "warmup"。

    覆盖 defaults 列表 + legacy 单条目 + react 段三条路径，确保预热 agent 构建时
    命中 WarmupModelClient。
    """
    cfg = deepcopy(get_config())
    models = cfg.setdefault("models", {})
    # defaults 列表（新格式）
    for entry in models.get("defaults", []) or []:
        mcc = entry.get("model_client_config") or {}
        mcc["client_provider"] = "warmup"
    # legacy 单条目
    default_mc = models.get("default", {})
    if isinstance(default_mc.get("model_client_config"), dict):
        default_mc["model_client_config"]["client_provider"] = "warmup"
    # react 段
    react = cfg.get("react", {})
    if isinstance(react.get("model_client_config"), dict):
        react["model_client_config"]["client_provider"] = "warmup"
    return cfg


async def _cleanup_prewarm_agent(agent: Any) -> None:
    """释放预热 agent 持有的 sys_operation / tool / rails 引用。

    预热 agent 独立构造，不进 AgentManager 缓存，用完即清理，避免与生产会话纠缠。
    """
    try:
        adapter = getattr(agent, "_adapter", None)
        if adapter is None:
            return
        # DeepAgent 资源释放：优先调用 adapter 的 close/teardown（若提供）。
        close = getattr(adapter, "close", None)
        if callable(close):
            await close()
    except Exception:  # noqa: BLE001
        logger.warning("[Prewarm] cleanup prewarm agent failed", exc_info=True)


async def run_startup_warmup(
    *,
    query: str = "hello",
    channel_id: str = "__prewarm__",
    mode: str = "agent",
    timeout_s: float = 120.0,
    mock_model: bool = True,
) -> None:
    """统一启动预热：interface_deep import → checkpointer → 临时 DeepAgent → query。

    三阶段全部 try/except，失败仅告警不抛。预热 agent 独立构造，不进 AgentManager
    缓存，与生产隔离。``ensure_interface_deep_and_checkpointer`` 的兜底语义由
    ``_startup_warmup_task`` 句柄承接（本函数的调用方负责把它挂到该句柄）。
    """
    # 延迟 import：避免模块加载时拉起 interface_deep（破坏 fire-and-forget 语义）。
    from jiuwenswarm.server.agent_ws_server import (
        _warm_interface_deep_module,
    )
    from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        ensure_persistent_checkpointer,
    )

    t_listen = time.perf_counter()

    # 阶段1：interface_deep import（原 _warm_interface_deep_module 职责）。
    # 其内部已有 ``interface_deep_warmup cache=miss ok elapsed_ms=`` 埋点。
    t0 = time.perf_counter()
    try:
        await _warm_interface_deep_module()
    except Exception:  # noqa: BLE001
        logger.warning("[Prewarm] stage=1 import_interface_deep failed", exc_info=True)
    logger.info(
        "[Prewarm] stage=1 name=import_interface_deep elapsed_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )

    # 阶段2：checkpointer（原 ensure_persistent_checkpointer 职责，DeepAgent 构建前置）。
    t0 = time.perf_counter()
    try:
        await ensure_persistent_checkpointer()
    except Exception:  # noqa: BLE001
        logger.warning("[Prewarm] stage=2 checkpointer failed", exc_info=True)
    logger.info(
        "[Prewarm] stage=2 name=checkpointer elapsed_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )

    # 阶段3a：创建临时 DeepAgent（独立临时实例，不经 AgentManager）。
    config_base = _build_warmup_config_base() if mock_model else get_config()
    agent = JiuWenSwarm()
    t0 = time.perf_counter()
    try:
        await agent.create_instance({}, mode=mode, config_base=config_base)
    except Exception:  # noqa: BLE001
        logger.warning("[Prewarm] stage=3a create_instance failed", exc_info=True)
        await _cleanup_prewarm_agent(agent)
        return
    logger.info(
        "[Prewarm] stage=3a name=create_instance elapsed_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )

    # 阶段3b：执行 query（复用首请求 latency 风格）。结果不阻塞预热完成。
    request = AgentRequest(
        request_id=f"prewarm-{uuid4().hex[:12]}",
        channel_id=channel_id,
        session_id=f"{channel_id}_session",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": query, "mode": mode},
        timestamp=time.time(),
    )
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(agent.process_message(request), timeout=timeout_s)
        logger.info(
            "[Prewarm] stage=3b name=query ok elapsed_ms=%.1f",
            (time.perf_counter() - t0) * 1000,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[Prewarm] stage=3b name=query timeout elapsed_ms=%.1f timeout_s=%.1f",
            (time.perf_counter() - t0) * 1000,
            timeout_s,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "[Prewarm] stage=3b name=query failed elapsed_ms=%.1f",
            (time.perf_counter() - t0) * 1000,
            exc_info=True,
        )
    finally:
        await _cleanup_prewarm_agent(agent)

    # 总耗时（从预热起点起）。listen 日志时间戳早于本行 = 未阻塞 listen。
    logger.info(
        "[Prewarm] done mock_model=%s total_ms=%.1f listen_to_ready_ms=%.1f",
        mock_model,
        (time.perf_counter() - t_listen) * 1000,
        (time.perf_counter() - t_listen) * 1000,
    )
