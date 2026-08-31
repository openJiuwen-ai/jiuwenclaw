# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime patch: 模型调用通道迁移到 Windows 命名管道（np://）。

桌面集成形态（claw_desktop）把 jiuwen 本地模型调用通道从 loopback TCP 迁到
命名管道：桌面写入的 .env ``API_BASE`` 形如 ``np://claw-model/v1``
（设计见 claw_desktop 仓 docs/named-pipe-migration-design.md）。本补丁在服务
启动早期 monkey-patch 两处 OpenAI 客户端构造点：

1. openjiuwen ``OpenAIModelClient._build_async_openai_client``
   （openjiuwen/core/foundation/llm/model_clients/openai_model_client.py；
   OpenAI 系 provider 共用——DeepSeek/DashScope/OpenRouter 均继承且不重写）：
   ``model_client_config.api_base`` 为 np:// 时，``http_client`` 改用
   ``httpx.AsyncClient(transport=named_pipe_transport_for(api_base),
   trust_env=False)``；``api_key`` 优先取密钥包
   ``secrets_bootstrap.get_secret('proxyKey')``（取不到回退 config 原值）。
2. ``openai.OpenAI.__init__``（同步旁路：symphony/experience/embed.py、
   agents/harness/common/tools/image_tools.py / audio_tools.py 等）：
   ``base_url`` 为 np:// 且调用方未显式传 ``http_client`` 时注入
   ``httpx.Client(transport=named_pipe_sync_transport_for(base_url),
   trust_env=False)``，``api_key`` 同样优先 proxyKey。

``trust_env=False`` 是关键：np:// 流量绝不能被 HTTP_PROXY 系环境变量劫持
（显式 transport 下 httpx 本不会用 env 代理，显式关闭同时挡住
SSL_CERT_FILE / NETRC 等其余 env 探测）。

实测结论（openai 2.32.0 / httpx 0.28.1）：AsyncOpenAI / OpenAI 构造与请求
阶段均不校验 base_url scheme——httpx 无 mount 时任意 scheme 都路由到注入的
自定义 transport，``np://claw-model/v1`` 拼 ``/chat/completions`` 后
raw_path = ``/v1/chat/completions``（/v1 前缀保留，与桌面代理路由约定一致）。
因此 base_url 原样传给 SDK，无需 http(s) 占位 URL。

非 np:// 时本补丁完全不改变行为（非桌面形态零影响）；重复 apply 幂等。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jiuwenswarm.common.np_transport import (
    is_named_pipe_url,
    named_pipe_sync_transport_for,
    named_pipe_transport_for,
)

logger = logging.getLogger("jiuwenswarm.llm_np_patch")

_PATCH_APPLIED = False

# 密钥包中模型代理令牌的路径（桌面主进程经 stdin 首帧下发，见 secrets_bootstrap）
_PROXY_KEY_PATH = "proxyKey"


def _resolve_api_key(fallback: Any) -> Any:
    """api_key 优先取密钥包 proxyKey；取不到（非桌面形态/旧版下发）回退原值。"""
    try:
        from jiuwenswarm.common.secrets_bootstrap import get_secret

        key = get_secret(_PROXY_KEY_PATH)
    except Exception:  # noqa: BLE001 - 密钥包不可用时静默回退，不影响非桌面形态
        return fallback
    return key if isinstance(key, str) and key else fallback


def _build_pipe_async_http_client(api_base: str) -> httpx.AsyncClient:
    """构造 np:// 用的异步 httpx client：命名管道 transport + 禁 env 代理探测。

    与原实现的差异：不再设置 proxy/verify/limits（管道无 TLS/连接池概念，
    NamedPipeTransport 每请求一条管道连接）。timeout 保持原行为——不落在
    httpx client 上，仍由 AsyncOpenAI 构造参数/per-request 扩展携带。
    """
    return httpx.AsyncClient(
        transport=named_pipe_transport_for(api_base),
        trust_env=False,
    )


def _patch_openjiuwen_async_client() -> None:
    try:
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
    except Exception as exc:  # openjiuwen 不可用时静默跳过（对齐 llm_sse_patch）
        logger.warning("[llm_np_patch] 未能导入 OpenAIModelClient，跳过异步补丁: %s", exc)
        return

    if getattr(OpenAIModelClient, "_np_patch_applied", False):
        return

    # monkeypatch 需包受保护方法 _build_async_openai_client，豁免 protected-access
    _orig_build = OpenAIModelClient._build_async_openai_client  # pylint: disable=protected-access

    def _build_async_openai_client_with_np(self: Any, timeout: Any = None) -> Any:
        api_base = getattr(self.model_client_config, "api_base", None)
        if not is_named_pipe_url(api_base):
            return _orig_build(self, timeout)

        from openai import AsyncOpenAI

        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        logger.info(
            "[llm_np_patch] 模型调用走命名管道: %s（timeout=%s, max_retries=%s）",
            api_base,
            final_timeout,
            self.model_client_config.max_retries,
        )
        return AsyncOpenAI(
            api_key=_resolve_api_key(self.model_client_config.api_key),
            base_url=api_base,
            http_client=_build_pipe_async_http_client(api_base),
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
        )

    OpenAIModelClient._build_async_openai_client = _build_async_openai_client_with_np  # pylint: disable=protected-access
    OpenAIModelClient._np_patch_applied = True  # pylint: disable=protected-access


def _patch_sync_openai() -> None:
    try:
        import openai
    except Exception as exc:
        logger.warning("[llm_np_patch] 未能导入 openai，跳过同步补丁: %s", exc)
        return

    if getattr(openai.OpenAI, "_np_patch_applied", False):
        return

    _orig_init = openai.OpenAI.__init__

    def _openai_init_with_np(self: Any, *args: Any, **kwargs: Any) -> None:
        base_url = kwargs.get("base_url")
        # OpenAI.__init__ 为 keyword-only（openai 2.x）；调用方显式传
        # http_client 时尊重原值不覆盖
        if is_named_pipe_url(base_url) and kwargs.get("http_client") is None:
            kwargs["http_client"] = httpx.Client(
                transport=named_pipe_sync_transport_for(base_url),
                trust_env=False,
            )
            kwargs["api_key"] = _resolve_api_key(kwargs.get("api_key"))
            logger.info("[llm_np_patch] 同步 OpenAI 旁路走命名管道: %s", base_url)
        _orig_init(self, *args, **kwargs)

    openai.OpenAI.__init__ = _openai_init_with_np
    openai.OpenAI._np_patch_applied = True


def apply_openai_np_patch() -> None:
    """应用 np:// 模型通道补丁（幂等；服务启动早期调用一次即可）。

    覆盖进程内全部 OpenAI 系 LLM 调用：openjiuwen OpenAIModelClient
    （invoke/stream，含 subagent/心跳等）与 openai.OpenAI 同步旁路。
    非 np:// base_url 时行为与原实现完全一致。
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _patch_openjiuwen_async_client()
    _patch_sync_openai()
    _PATCH_APPLIED = True
    logger.info("[llm_np_patch] np:// 模型通道补丁已应用（仅 np:// base_url 生效）")
