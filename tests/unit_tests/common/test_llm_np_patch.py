# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""llm_np_patch 单测（Windows 命名管道模型通道）。

- np:// 时 patch 后的 AsyncOpenAI 经管道完成 chat.completions.create，
  请求头 Authorization 携带密钥包 proxyKey（对端用 serve_pipe 模拟桌面模型代理）
- np:// 时同步旁路 openai.OpenAI 注入命名管道 transport
- 非 np:// 时补丁零行为变化（构造参数原样、不取 proxyKey）
- 幂等：重复 apply 不重复包装
"""

from __future__ import annotations

import json
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="命名管道仅 Windows")


def _make_openai_model_client(api_base: str, api_key: str = "config-key"):
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
        OpenAIModelClient,
    )
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
        ProviderType,
    )

    client_config = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key=api_key,
        api_base=api_base,
        use_shared_llm_http_client=False,  # 不污染进程级共享 client 缓存
    )
    return OpenAIModelClient(ModelRequestConfig(model_name="np-model"), client_config)


@pytest.fixture
def _patch_state():
    """保存/恢复被 patch 的全局状态（类方法、幂等标记、密钥包 vault、共享缓存）。"""
    import openai
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
        OpenAIModelClient,
    )

    from jiuwenswarm import llm_np_patch
    from jiuwenswarm.common import secrets_bootstrap

    saved_async_build = OpenAIModelClient._build_async_openai_client
    had_async_flag = hasattr(OpenAIModelClient, "_np_patch_applied")
    saved_sync_init = openai.OpenAI.__init__
    had_sync_flag = hasattr(openai.OpenAI, "_np_patch_applied")
    saved_module_flag = llm_np_patch._PATCH_APPLIED
    saved_secrets = dict(secrets_bootstrap._SECRETS)
    saved_loaded = secrets_bootstrap._LOADED
    OpenAIModelClient._client_cache.clear()

    yield

    OpenAIModelClient._build_async_openai_client = saved_async_build
    if had_async_flag:
        OpenAIModelClient._np_patch_applied = True
    elif hasattr(OpenAIModelClient, "_np_patch_applied"):
        delattr(OpenAIModelClient, "_np_patch_applied")
    openai.OpenAI.__init__ = saved_sync_init
    if had_sync_flag:
        openai.OpenAI._np_patch_applied = True
    elif hasattr(openai.OpenAI, "_np_patch_applied"):
        delattr(openai.OpenAI, "_np_patch_applied")
    llm_np_patch._PATCH_APPLIED = saved_module_flag
    secrets_bootstrap._SECRETS.clear()
    secrets_bootstrap._SECRETS.update(saved_secrets)
    secrets_bootstrap._LOADED = saved_loaded
    OpenAIModelClient._client_cache.clear()


def _set_proxy_key(key: str | None) -> None:
    """mock 密钥包 vault（等价于 stdin 首帧下发后的内存状态）。"""
    from jiuwenswarm.common import secrets_bootstrap

    secrets_bootstrap._SECRETS.clear()
    if key is not None:
        secrets_bootstrap._SECRETS["proxyKey"] = key


class _FakeModelProxy:
    """serve_pipe 模拟桌面模型代理：读 HTTP 请求，回固定 chat completion JSON，记录请求头。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.server = None

    async def start(self, pipe_name: str) -> "_FakeModelProxy":
        from jiuwenswarm.common.np_transport import serve_pipe

        self.server = await serve_pipe(rf"\\.\pipe\{pipe_name}", self._handle)
        return self

    async def stop(self) -> None:
        if self.server is not None:
            await self.server.stop()

    async def _handle(self, stream) -> None:
        # 极简 HTTP/1.1 服务：读到 \r\n\r\n + 按 Content-Length 收体
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = await stream.read()
            if not chunk:
                return
            buf.extend(chunk)
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        body = bytearray(rest)
        remaining = int(headers.get("content-length", "0")) - len(body)
        while remaining > 0:
            chunk = await stream.read(remaining)
            if not chunk:
                break
            body.extend(chunk)
            remaining -= len(chunk)
        self.requests.append(
            {"head": head.decode("latin-1"), "headers": headers, "body": bytes(body)}
        )
        resp_body = json.dumps(
            {
                "id": "chatcmpl-np-test",
                "object": "chat.completion",
                "created": 1,
                "model": "np-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        payload = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(resp_body)}\r\n\r\n".encode()
            + resp_body
        )
        await stream.write(payload)


class TestNpPatchAsyncClient:
    """openjiuwen OpenAIModelClient 异步构造点补丁。"""

    @pytest.mark.asyncio
    async def test_chat_completion_over_pipe_with_proxy_key(self, _patch_state) -> None:
        _set_proxy_key("np-proxy-key-123")

        from jiuwenswarm.common.np_transport import NamedPipeTransport
        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        pipe_name = f"claw-test-llmnp-{os.getpid()}-async"
        proxy = await _FakeModelProxy().start(pipe_name)
        try:
            model_client = _make_openai_model_client(f"np://{pipe_name}/v1")
            sdk_client = model_client._build_async_openai_client()
            try:
                # 构造断言：管道 transport + 禁 env 代理探测 + proxyKey 优先
                assert isinstance(sdk_client._client._transport, NamedPipeTransport)
                assert sdk_client._client.trust_env is False
                assert sdk_client.api_key == "np-proxy-key-123"

                resp = await sdk_client.chat.completions.create(
                    model="np-model",
                    messages=[{"role": "user", "content": "ping"}],
                )
                assert resp.choices[0].message.content == "pong"
            finally:
                await sdk_client.close()
        finally:
            await proxy.stop()

        assert proxy.requests, "假模型代理未收到请求"
        request = proxy.requests[0]
        assert request["head"].startswith("POST /v1/chat/completions")
        assert request["headers"].get("authorization") == "Bearer np-proxy-key-123"

    @pytest.mark.asyncio
    async def test_non_np_base_url_behavior_unchanged(self, _patch_state) -> None:
        _set_proxy_key("np-proxy-key-123")

        import httpx

        from jiuwenswarm.common.np_transport import NamedPipeTransport
        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        model_client = _make_openai_model_client(
            "http://127.0.0.1:19691/v1", api_key="config-key"
        )
        sdk_client = model_client._build_async_openai_client()
        try:
            # 构造参数原样：默认 TCP transport、config api_key（不取 proxyKey）、base_url 不变
            assert not isinstance(sdk_client._client._transport, NamedPipeTransport)
            assert isinstance(sdk_client._client._transport, httpx.AsyncHTTPTransport)
            assert sdk_client.api_key == "config-key"
            assert str(sdk_client.base_url).rstrip("/") == "http://127.0.0.1:19691/v1"
        finally:
            await sdk_client.close()


class TestNpPatchSyncOpenAI:
    """同步旁路（symphony/experience/embed.py 等 openai.OpenAI 构造点）补丁。"""

    def test_np_base_url_injects_sync_pipe_transport(self, _patch_state) -> None:
        _set_proxy_key("np-proxy-key-123")

        import openai

        from jiuwenswarm.common.np_transport import NamedPipeSyncTransport
        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        client = openai.OpenAI(
            base_url=f"np://claw-test-llmnp-{os.getpid()}-sync/v1", api_key="orig"
        )
        try:
            assert isinstance(client._client._transport, NamedPipeSyncTransport)
            assert client._client.trust_env is False
            assert client.api_key == "np-proxy-key-123"
        finally:
            client.close()

    def test_non_np_base_url_untouched(self, _patch_state) -> None:
        _set_proxy_key("np-proxy-key-123")

        import httpx
        import openai

        from jiuwenswarm.common.np_transport import NamedPipeSyncTransport
        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        client = openai.OpenAI(base_url="http://127.0.0.1:19691/v1", api_key="orig")
        try:
            assert not isinstance(client._client._transport, NamedPipeSyncTransport)
            assert isinstance(client._client._transport, httpx.HTTPTransport)
            assert client.api_key == "orig"
        finally:
            client.close()

    def test_explicit_http_client_respected(self, _patch_state) -> None:
        _set_proxy_key(None)

        import httpx
        import openai

        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        custom = httpx.Client()
        client = openai.OpenAI(
            base_url=f"np://claw-test-llmnp-{os.getpid()}-custom/v1",
            api_key="orig",
            http_client=custom,
        )
        try:
            assert client._client is custom  # 调用方显式 http_client 不被覆盖
            assert client.api_key == "orig"  # 未注入时 api_key 也不动
        finally:
            client.close()

    def test_np_without_proxy_key_falls_back_to_config(self, _patch_state) -> None:
        _set_proxy_key(None)

        import openai

        from jiuwenswarm.common.np_transport import NamedPipeSyncTransport
        from jiuwenswarm.llm_np_patch import apply_openai_np_patch

        apply_openai_np_patch()

        client = openai.OpenAI(
            base_url=f"np://claw-test-llmnp-{os.getpid()}-fb/v1", api_key="config-key"
        )
        try:
            # 密钥包无 proxyKey：仍走管道 transport，api_key 回退 config 原值
            assert isinstance(client._client._transport, NamedPipeSyncTransport)
            assert client.api_key == "config-key"
        finally:
            client.close()


def test_apply_is_idempotent(_patch_state) -> None:
    import openai
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
        OpenAIModelClient,
    )

    from jiuwenswarm.llm_np_patch import apply_openai_np_patch

    apply_openai_np_patch()
    first_async = OpenAIModelClient._build_async_openai_client
    first_sync = openai.OpenAI.__init__
    assert getattr(OpenAIModelClient, "_np_patch_applied", False) is True
    assert getattr(openai.OpenAI, "_np_patch_applied", False) is True

    apply_openai_np_patch()
    assert OpenAIModelClient._build_async_openai_client is first_async
    assert openai.OpenAI.__init__ is first_sync
