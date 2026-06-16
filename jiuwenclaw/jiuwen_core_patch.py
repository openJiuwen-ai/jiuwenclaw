# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import os
import re
import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import Field
import httpx
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import \
    AssistantMessageChunk, OpenAIModelClient, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client import (
    SiliconFlowModelClient,
)
from openjiuwen.core.session.stream import OutputSchema
from jiuwenclaw.tool_arguments_validator import (
    tool_arguments_failure_message,
    tool_arguments_failure_payload,
    validate_tool_arguments,
)

if TYPE_CHECKING:
    import openai

llm_logger = logging.getLogger("jiuwenclaw.app")

_GLM_TOOL_XML_TAG_RE = re.compile(
    r"</?(?:arg_value|arg_key)(?:\s[^>]*)?>",
    re.IGNORECASE,
)


def _sanitize_glm_tool_arguments(raw: str) -> str:
    """Strip GLM native tool-call XML tags leaked into OpenAI-style arguments."""
    if not raw or "<arg_" not in raw:
        return raw
    return _GLM_TOOL_XML_TAG_RE.sub("", raw)


# Session context for retry notifications.
# Set by react_agent._call_llm_stream before calling llm.stream/invoke.
_retry_session: ContextVar[Optional[Any]] = ContextVar("retry_session", default=None)


_ORIGINAL_BUILD_REQUEST_PARAMS = None
_ORIGINAL_PARSE_RESPONSE = None
_ORIGINAL_GENERATE_IMAGE = None

_HUAWEI_MAAS_API_MARKERS = (
    "modelarts-maas.com",
    "modelarts-maas.cn",
    "huaweiapaas.com",
    "agentarts",
)
_HUAWEI_MAAS_SESSION_API_KEY = "huawei-maas-session"


def _is_huawei_maas_api_base(api_base: str) -> bool:
    """Detect Huawei Cloud ModelArts MaaS image/chat endpoints."""
    base = (api_base or "").lower()
    if any(marker in base for marker in _HUAWEI_MAAS_API_MARKERS):
        return True
    return "modelarts" in base and "maas" in base


def _maybe_make_maas_span_id(client: Any) -> str:
    """MaaS 请求生成新 x-span-id（uuid4 hex），非 MaaS 返回空串。"""
    mcc = getattr(client, "model_client_config", None)
    api_base = getattr(mcc, "api_base", "") if mcc is not None else ""
    return uuid.uuid4().hex if _is_huawei_maas_api_base(api_base) else ""


def _inject_span_id_kwargs(kwargs: dict, span_id: str) -> dict:
    """把 x-span-id 合并到底层 client 的 ``custom_headers`` 中（不修改原 kwargs）。"""
    if not span_id:
        return kwargs
    out = dict(kwargs)
    headers = dict(out.get("custom_headers") or {})
    headers["x-span-id"] = span_id
    out["custom_headers"] = headers
    return out


def _llm_log_ctx() -> str:
    """日志上下文前缀：当前 session_id / request_id（缺省以 '-' 占位）。"""
    sid = "-"
    session = _retry_session.get()
    if session is not None and callable(getattr(session, "get_session_id", None)):
        sid = str(session.get_session_id() or "-") or "-"
    rid = "-"
    try:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import _LLM_TRACE_REQUEST_ID
        rid = str(_LLM_TRACE_REQUEST_ID.get() or "-") or "-"
    except Exception:
        pass
    return f"[session_id={sid}] [request_id={rid}]"


def _strip_b64_data_uri_prefix(value: str) -> str:
    """Huawei MaaS may return ``data:image/...;base64,<payload>`` in b64_json."""
    text = str(value or "").strip()
    if text.startswith("data:") and "," in text:
        return text.split(",", 1)[1]
    return text


def _normalize_huawei_image_size(size: str | None) -> str | None:
    """Huawei MaaS uses ``WxH``; accept ``*`` / ``×`` separators from tool input."""
    if size is None:
        return None
    text = str(size).strip()
    if not text:
        return None
    return text.replace("*", "x").replace("×", "x")


def configure_openjiuwen_logging_under_jiuwenclaw(subdir: str = "openjiuwen") -> None:
    """Route openjiuwen log files under JiuwenClaw's service-level log directory.

    openjiuwen defaults to ``./logs/``, which depends on the process working
    directory. JiuwenClaw owns a stable log root via ``get_logs_dir()``; this
    helper keeps openjiuwen's existing run/interface/performance layout while
    moving that root to ``<jiuwenclaw logs>/<subdir>``.
    """
    try:
        from openjiuwen.core.common.logging.log_config import (
            configure_log_config,
            get_log_config_snapshot,
        )
        from jiuwenclaw.utils import get_logs_dir

        log_root = get_logs_dir() / subdir
        log_root.mkdir(parents=True, exist_ok=True)

        config = get_log_config_snapshot()
        target = str(log_root)
        if config.get("log_path") == target:
            return

        # Patch log format to match JiuwenClaw format
        config["format"] = (
            "%(asctime)s.%(msecs)03d [%(process)d] %(levelname)s "
            "%(log_type)s %(filename)s:%(lineno)d: %(message)s"
        )
        config["log_path"] = target
        configure_log_config(config)
    except Exception as exc:
        llm_logger.warning(
            "Failed to route openjiuwen logs under JiuwenClaw log dir: %s",
            exc,
        )


# ============================================================
# LLM Retry Mechanism
# ============================================================
_orig_invoke = OpenAIModelClient.invoke
_orig_stream = OpenAIModelClient.stream # SiliconFlow 原始方法引用
_sf_orig_invoke = SiliconFlowModelClient.invoke
_sf_orig_stream = SiliconFlowModelClient.stream


@dataclass
class LlmRetryConfig:
    """LLM 调用重试配置。"""
    enabled: bool = False
    max_attempts: int = 3
    initial_backoff: float = 10.0
    max_backoff: float = 60.0
    backoff_factor: float = 2.0
    retry_on_rate_limit: bool = True


class RetryMixin:
    """LLM 重试逻辑混入基类，供所有 Patch ModelClient 复用。"""

    _RETRYABLE_KEYWORDS = (
        "connection error", "connecttimeout", "connect error",
        "network is unreachable", "connection reset", "broken pipe",
        "connection refused", "readtimeout", "timeout",
        "operation timed out", "500", "502", "503", "504",
        "too many requests", "响应超时", "timed out", "timeout",
        "connection failed", "connection closed unexpectedly", "WebSocket connection closed",
        "async stream error"
    )

    _NON_RETRYABLE_KEYWORDS = (
        "400", "401", "403", "404", "422",
        "invalid_api_key", "unauthorized", "forbidden", "authentication",
        "model cannot be none", "invalid request",
        "ssl", "certificate verify",
        "model provider is invalid",
        "model service config error",
        "model config error",
        "model invoke parameter error",
        "model client_config is invalid",
        "async invoke error"
    )

    def _resolve_stream_timeout(self, timeout: Optional[float]) -> float:
        """Streaming: call arg > stream_timeout (if set) > timeout."""
        if timeout is not None:
            return timeout
        stream_timeout = getattr(self.model_client_config, "stream_timeout", None)
        if stream_timeout is not None and stream_timeout > 0:
            return stream_timeout
        return self.model_client_config.timeout

    @classmethod
    def _classify_error(cls, exc: Exception, cfg: LlmRetryConfig) -> str:
        """分类错误原因，返回可读描述。"""
        error_msg = str(exc).lower()

        if any(kw.lower() in error_msg for kw in ("429", "rate_limit", "rate limit")):
            return "HTTP 429 限流"

        if any(kw.lower() in error_msg for kw in ("500",)):
            return "HTTP 500 服务端错误"
        if any(kw.lower() in error_msg for kw in ("502",)):
            return "HTTP 502 网关错误"
        if any(kw.lower() in error_msg for kw in ("503",)):
            return "HTTP 503 服务不可用"
        if any(kw.lower() in error_msg for kw in ("504",)):
            return "HTTP 504 网关超时"

        if any(kw.lower() in error_msg for kw in ("readtimeout", "timeout", "operation timed out")):
            return "请求超时"
        # pylint: disable=complicate-comprehension
        if any(kw.lower() in error_msg for kw in ("connection error", "connecttimeout", "connect error",
                                            "network is unreachable", "connection reset", "broken pipe",
                                            "connection refused")):  # pylint: disable=complicate-comprehension
            return "连接错误"
        if any(kw.lower() in error_msg for kw in ("forbidden",)):
            return "禁止访问"

        return "未知错误"

    @staticmethod
    def _extract_error_details(exc: Exception) -> str:
        """提取异常中的详细信息：HTTP 状态码、响应体、请求头等。"""
        parts = []

        # 提取 HTTP 状态码
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            resp = getattr(exc, "response", None)
            if resp is not None:
                status_code = getattr(resp, "status_code", None)
        if status_code is not None:
            parts.append(f"status_code={status_code}")

        # 提取异常类型名
        parts.append(f"type={type(exc).__name__}")

        # 提取响应体/message 字段
        for attr in ("message", "body", "response_text"):
            val = getattr(exc, attr, None)
            if val is not None:
                parts.append(f"{attr}={val}")
                break

        # 提取 request.id
        request_id = getattr(getattr(exc, "request", None), "id", None)
        if request_id:
            parts.append(f"request_id={request_id}")

        # 如果有 response 对象，尝试提取更多字段
        resp = getattr(exc, "response", None)
        if resp is not None:
            url = getattr(resp, "url", None)
            if url:
                parts.append(f"url={url}")
            headers = getattr(resp, "headers", None)
            if headers:
                x_request_id = headers.get("x-request-id") or headers.get("cf-ray")
                if x_request_id:
                    parts.append(f"trace_id={x_request_id}")

        return ", ".join(parts)

    @classmethod
    def _get_retry_config(cls) -> LlmRetryConfig:
        """从 config.yaml 读取重试配置，未配置则使用代码默认值。"""
        try:
            from jiuwenclaw.config import get_config
            cfg = get_config()
            react_cfg = (cfg or {}).get("react", {})
            retry_cfg = (react_cfg or {}).get("llm_retry", {})
            if retry_cfg:
                return LlmRetryConfig(
                    enabled=retry_cfg.get("enabled", True),
                    max_attempts=retry_cfg.get("max_attempts", 3),
                    initial_backoff=retry_cfg.get("initial_backoff", 10.0),
                    max_backoff=retry_cfg.get("max_backoff", 60.0),
                    backoff_factor=retry_cfg.get("backoff_factor", 2.0),
                    retry_on_rate_limit=retry_cfg.get("retry_on_rate_limit", True),
                )
        except Exception:
            llm_logger.warning("Failed to get retry config from config.yaml, use default values.")
        return LlmRetryConfig()

    def _is_retryable_error(self, exc: Exception, cfg: LlmRetryConfig) -> bool:
        """判断错误是否可重试。

        两步策略：
        1. 命中不可重试关键词 → 立即返回 False
        2. 命中可重试关键词 → 返回 True（429 受 retry_on_rate_limit 控制）
        3. 兜底返回 False（未知错误不重试）
        """
        error_msg = str(exc).lower()

        if any(kw.lower() in error_msg for kw in self._NON_RETRYABLE_KEYWORDS):
            return False

        if any(kw.lower() in error_msg for kw in ("429", "rate_limit", "rate limit")):
            return cfg.retry_on_rate_limit

        if any(kw.lower() in error_msg for kw in self._RETRYABLE_KEYWORDS):
            return True

        return False

    def _calculate_backoff(self, attempt: int, exc: Exception,
                        cfg: LlmRetryConfig) -> float:
        """指数退避 + Retry-After 头（取 max，避免等待过短）。"""
        backoff = min(cfg.initial_backoff * (cfg.backoff_factor ** attempt), cfg.max_backoff)
        retry_after = self._extract_retry_after(exc)
        if retry_after is not None:
            backoff = max(backoff, retry_after)
        return backoff

    async def _notify_retry_start(self, reason: str, attempt: int, max_attempts: int, backoff: float) -> None:
        """向前端发送重试开始通知。"""
        session = _retry_session.get()
        if session is None:
            llm_logger.debug("[notify] _notify_retry_start: session is None, skip notification")
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="retry_notification",
                    index=999,
                    payload={
                        "output": {
                            "output": f"\n\n⚠️ 模型调用异常 [{reason}], "
                                    f"将在 {backoff:.1f} 秒后进行第 {attempt}/{max_attempts} 次重试...",
                            "result_type": "text",
                        },
                    },
                )
            )
            llm_logger.info(f"retry notification sent successfully, ({attempt}/{max_attempts}), "
                            f"session_id={getattr(session, 'get_session_id', lambda: 'N/A')()}")
        except Exception as e:
            llm_logger.error(f"[notify] _notify_retry_start 发送失败: {type(e).__name__}: {e}")

    async def _notify_retry_end(self) -> None:
        """清除前端重试提示。"""
        session = _retry_session.get()
        if session is None:
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="processing_complete",
                    index=999,
                    payload={},
                )
            )
        except Exception as e:
            llm_logger.error(f"[notify] _notify_retry_end 发送失败: {type(e).__name__}: {e}")

    @staticmethod
    def _extract_retry_after(exc: Exception) -> float | None:
        """从 429 响应的 Retry-After 头读取等待时间。"""
        if hasattr(exc, "response") and exc.response is not None:
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)
                except (ValueError, TypeError):
                    pass
        return None

    async def _invoke_with_retry(self, invoke_func, *args, **kwargs):
        """带重试的 invoke 包装器。
        max_attempts 表示纯重试次数，不包含首次正常调用。
        每次实际 LLM 调用单独记一条接口日志，并为 MaaS 请求生成独立的 x-span-id。
        """
        from jiuwenclaw.interface_resp import track_llm_resp

        cfg = self._get_retry_config()
        if not cfg.enabled:
            span_id = _maybe_make_maas_span_id(self)
            llm_logger.info(f"LLM invoke 未启用重试，直接返回结果 {_llm_log_ctx()} [span_id={span_id}]")
            async with track_llm_resp(self, streaming=False, span_id=span_id):
                return await invoke_func(*args, **_inject_span_id_kwargs(kwargs, span_id))

        last_error = None
        for attempt in range(cfg.max_attempts + 1):
            span_id = _maybe_make_maas_span_id(self)
            try:
                async with track_llm_resp(self, streaming=False, span_id=span_id):
                    result = await invoke_func(*args, **_inject_span_id_kwargs(kwargs, span_id))
                llm_logger.info(f"LLM invoke 成功 {_llm_log_ctx()} [span_id={span_id}] (第 {attempt + 1}/{cfg.max_attempts + 1} 次)")
                return result
            except Exception as e:
                last_error = e
                if not self._is_retryable_error(e, cfg):
                    reason = self._classify_error(e, cfg)
                    details = self._extract_error_details(e)
                    llm_logger.error(f"LLM invoke 不可重试 {_llm_log_ctx()} [span_id={span_id}] [{reason}] [{details}], details: {e}")
                    raise
                reason = self._classify_error(e, cfg)
                details = self._extract_error_details(e)
                backoff = self._calculate_backoff(attempt, e, cfg)
                if attempt >= cfg.max_attempts:
                    break
                llm_logger.warning(f"LLM invoke 失败 {_llm_log_ctx()} [span_id={span_id}] [{reason}] [{details}]，将在 {backoff:.1f}s 后重试, "
                    f"(第 {attempt + 1} 次重试 / 共 {cfg.max_attempts} 次): {e}")
                await self._notify_retry_start(reason, attempt + 1, cfg.max_attempts, backoff)
                await asyncio.sleep(backoff)

        reason = self._classify_error(last_error, cfg)
        details = self._extract_error_details(last_error)
        llm_logger.error(f"LLM invoke 重试次数耗尽 {_llm_log_ctx()} [{reason}] [{details}]，已执行 {cfg.max_attempts} 次重试）: {last_error}")
        await self._notify_retry_end()
        raise last_error

    async def _stream_with_retry(self, stream_func, *args, **kwargs):
        """带重试的 stream 包装器。
        max_attempts 表示纯重试次数，不包含首次正常调用。
        每次实际 LLM 调用单独记一条接口日志，并为 MaaS 请求生成独立的 x-span-id。
        """
        from jiuwenclaw.interface_resp import track_llm_resp

        cfg = self._get_retry_config()
        if not cfg.enabled:
            span_id = _maybe_make_maas_span_id(self)
            llm_logger.info(f"LLM stream 未启用重试机制 {_llm_log_ctx()} [span_id={span_id}]")
            async with track_llm_resp(self, streaming=True, span_id=span_id):
                async for chunk in stream_func(*args, **_inject_span_id_kwargs(kwargs, span_id)):
                    yield chunk
            return

        last_error = None
        for attempt in range(cfg.max_attempts + 1):
            span_id = _maybe_make_maas_span_id(self)
            try:
                async with track_llm_resp(self, streaming=True, span_id=span_id):
                    async for chunk in stream_func(*args, **_inject_span_id_kwargs(kwargs, span_id)):
                        yield chunk
                llm_logger.info(f"LLM stream 成功 {_llm_log_ctx()} [x_span_id={span_id}] (第 {attempt + 1}/{cfg.max_attempts + 1} 次)")
                return  # 流式成功完成
            except Exception as e:
                last_error = e
                if not self._is_retryable_error(e, cfg):
                    reason = self._classify_error(e, cfg)
                    details = self._extract_error_details(e)
                    llm_logger.info(f"LLM stream 不可重试 {_llm_log_ctx()} [x_span_id={span_id}] [{reason}] [{details}], details: {e}")
                    raise
                reason = self._classify_error(e, cfg)
                details = self._extract_error_details(e)
                backoff = self._calculate_backoff(attempt, e, cfg)
                if attempt >= cfg.max_attempts:
                    break
                llm_logger.warning(f"LLM stream 失败 {_llm_log_ctx()} [x_span_id={span_id}] [{reason}] [{details}]，将在 {backoff:.1f}s 后重试 "
                    f"(第 {attempt + 1} 次重试 / 共 {cfg.max_attempts} 次): {e}")
                await self._notify_retry_start(reason, attempt + 1, cfg.max_attempts, backoff)
                await asyncio.sleep(backoff)

        reason = self._classify_error(last_error, cfg)
        details = self._extract_error_details(last_error)
        llm_logger.error(f"LLM stream 重试次数耗尽 {_llm_log_ctx()} [{reason}] [{details}]，已尝试{cfg.max_attempts} 次重试）: {last_error}")
        await self._notify_retry_end()
        raise last_error


def _sanitize_wire_tool_arguments(params: dict[str, Any]) -> None:
    messages = params.get("messages")
    if not isinstance(messages, list):
        return

    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        finish_reason = message.get("finish_reason")
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            validation = validate_tool_arguments(
                function.get("arguments", "{}"),
                finish_reason=finish_reason,
            )
            if validation.ok:
                function["arguments"] = validation.normalized
                continue
            function["arguments"] = "{}"
            session_id = ""
            llm_logger.warning(
                "[tool_args_validation] action=wire_sanitize tool=%s tool_call_id=%s "
                "kind=%s length=%s reason=%s session_id=%s",
                function.get("name", ""),
                tool_call.get("id", ""),
                validation.kind,
                validation.length,
                validation.reason,
                session_id,
            )


def _patched_build_request_params(self, *, stream: bool, **kwargs) -> dict:
    """Patched version: ensure usage chunks and sanitize tool arguments before provider calls."""
    params = _ORIGINAL_BUILD_REQUEST_PARAMS(self, stream=stream, **kwargs)
    if stream:
        existing = params.get("stream_options")
        if existing is None:
            params["stream_options"] = {"include_usage": True}
        elif isinstance(existing, dict) and "include_usage" not in existing:
            existing["include_usage"] = True
    _sanitize_wire_tool_arguments(params)
    # Fallback max_tokens to environment variable if not configured
    if params.get("max_tokens") is None:
        env_max_tokens = os.environ.get("LLM_MAX_TOKENS")
        if env_max_tokens:
            try:
                params["max_tokens"] = int(env_max_tokens)
            except ValueError:
                llm_logger.warning(
                    f"Invalid LLM_MAX_TOKENS env value: '{env_max_tokens}' (not an integer), ignoring"
                )
    return params


class PatchOpenAIModelClient(RetryMixin, OpenAIModelClient):

    def _create_async_openai_client(self, timeout: Optional[float] = None) -> "openai.AsyncOpenAI":
        """
        Create an OpenAI Async client with configured SSL/proxy/http client settings.

        Args:
            timeout: Optional timeout override for this specific request
        """
        from openai import AsyncOpenAI

        api_base = self.model_client_config.api_base or ""
        if "example.com" in api_base:
            raise ValueError(
                f"api_base 指向占位域名 ({api_base})，"
                "请将 API_BASE 环境变量或配置修改为真实的 API 地址。"
                "参考 .env.template 中的说明。"
            )

        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify

        proxy_url = UrlUtils.get_global_proxy_url(self.model_client_config.api_base)
        # httpx不接受空字符串proxy，需要处理
        if proxy_url and proxy_url.strip():
            http_client = httpx.AsyncClient(proxy=proxy_url, verify=verify)
        else:
            http_client = httpx.AsyncClient(verify=verify)

        # Use method-level timeout if provided, otherwise use config timeout
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        llm_logger.info(
            "Before create openai client, model client config params ready. "
            "event_type=%s, timeout=%s, max_retries=%s",
            LogEventType.LLM_CALL_START,
            final_timeout,
            self.model_client_config.max_retries
        )
        default_headers_raw = os.getenv("default_headers", None)
        try:
            parsed_default_headers = (
                json.loads(default_headers_raw) if default_headers_raw else None
            )
        except json.decoder.JSONDecodeError as error:
            llm_logger.warning(f"Model default headers parse failed: {error}")
            parsed_default_headers = None
        # Main MaaS chat uses placeholder api_key + relay-claw Basic headers in default_headers.
        # Image/other clients use real api_key and Bearer from the SDK only.
        client_default_headers = (
            parsed_default_headers
            if self.model_client_config.api_key == _HUAWEI_MAAS_SESSION_API_KEY
            else None
        )
        return AsyncOpenAI(
            api_key=self.model_client_config.api_key,
            base_url=self.model_client_config.api_base,
            http_client=http_client,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
            default_headers=client_default_headers
        )

    async def _parse_response(self, response: Any, parser: Any = None) -> AssistantMessage:
        assistant_message = await _ORIGINAL_PARSE_RESPONSE(self, response, parser)
        choices = getattr(response, 'choices', None) or []
        choice = choices[0] if choices else None
        raw_finish_reason = getattr(choice, 'finish_reason', None)
        metadata = dict(getattr(assistant_message, 'metadata', None) or {})
        metadata['raw_finish_reason'] = raw_finish_reason
        return assistant_message.model_copy(update={
            'finish_reason': raw_finish_reason or "null",
            'metadata': metadata,
        })

    def _parse_stream_chunk(self, chunk: Any) -> Optional[AssistantMessageChunk]:
        """Parse OpenAI streaming response chunk

        Args:
            chunk: OpenAI streaming response chunk

        Returns:
            AssistantMessageChunk or None
        """

        # Detect Huawei MaaS by api_base (workaround for malformed tool_calls delta)
        # Only apply workaround for glm-5.1 model on MaaS endpoint
        _is_huawei_maas = False
        _has_mcc = hasattr(self, "model_client_config")
        _mcc_is_none = self.model_client_config is None if _has_mcc else True
        _has_mc = hasattr(self, "model_config")
        _mc_is_none = self.model_config is None if _has_mc else True
        _api_base = ""
        _model_name = ""
        if _has_mcc and not _mcc_is_none:
            _api_base = getattr(self.model_client_config, "api_base", "") or ""
            _model_name = getattr(self.model_client_config, "model_name", "") or ""
        if _has_mc and not _mc_is_none and not _model_name:
            _model_name = getattr(self.model_config, "model_name", "") or ""
        # Check if it's Huawei MaaS AND model is glm-5.1 (the affected model)
        _is_maas_endpoint = (
            "modelarts-maas.com" in _api_base
            or "modelarts" in _api_base.lower()
            or "huaweiapaas.com" in _api_base
            or "agentarts" in _api_base.lower()
        )
        _is_glm51 = _model_name.lower() in ("glm-5.1", "glm5.1")
        _is_huawei_maas = _is_maas_endpoint and _is_glm51

        # When stream_options={"include_usage": true}, OpenAI sends a final
        # usage-only chunk with ``choices=[]`` and ``usage`` populated. Emit a
        # usage-only AssistantMessageChunk so downstream aggregation picks it up.
        if not chunk.choices:
            chunk_usage = getattr(chunk, 'usage', None)
            if chunk_usage:
                input_cost, output_cost, total_cost = self._extract_cost_info(chunk_usage)
                usage_metadata = UsageMetadata(
                    model_name=self.model_config.model_name,
                    input_tokens=getattr(chunk_usage, 'prompt_tokens', 0) or 0,
                    output_tokens=getattr(chunk_usage, 'completion_tokens', 0) or 0,
                    total_tokens=getattr(chunk_usage, 'total_tokens', 0) or 0,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=total_cost,
                )
                return AssistantMessageChunk(
                    content="",
                    reasoning_content=None,
                    tool_calls=None,
                    usage_metadata=usage_metadata,
                    finish_reason="null",
                )
            return None

        choice = chunk.choices[0]
        delta = choice.delta

        # Extract content
        content = getattr(delta, 'content', None) or ""
        reasoning_content = getattr(delta, 'reasoning_content', None)

        # Parse tool_calls delta
        tool_calls = []
        if hasattr(delta, 'tool_calls') and delta.tool_calls:
            for tc_delta in delta.tool_calls:
                if hasattr(tc_delta, 'function') and tc_delta.function:
                    index = getattr(tc_delta, 'index', None)
                    function_name = getattr(tc_delta.function, 'name', None) or ""
                    function_arguments = _sanitize_glm_tool_arguments(
                        getattr(tc_delta.function, 'arguments', None) or ""
                    )

                    tool_call = ToolCall(
                        id=getattr(tc_delta, 'id', '') or "",
                        type="function",
                        name=function_name,
                        arguments=function_arguments,
                        index=index if index is not None else 0,
                    )
                    tool_calls.append(tool_call)

        # Huawei MaaS workaround: merge tool_calls with same index
        # MaaS sometimes returns multiple tool_calls deltas with identical index,
        # where the second one only contains arguments increment.
        if _is_huawei_maas and len(tool_calls) > 1:
            merged_by_index: dict[int, ToolCall] = {}
            for tc in tool_calls:
                idx = tc.index if tc.index is not None else 0
                if idx not in merged_by_index:
                    merged_by_index[idx] = tc
                else:
                    existing = merged_by_index[idx]
                    new_id = tc.id or existing.id
                    new_name = tc.name or existing.name
                    new_args = existing.arguments + tc.arguments
                    merged_by_index[idx] = ToolCall(
                        id=new_id,
                        type="function",
                        name=new_name,
                        arguments=new_args,
                        index=idx,
                    )
            tool_calls = list(merged_by_index.values())

        # Build usage_metadata (usually only in the last chunk)
        usage_metadata = None
        if hasattr(chunk, 'usage') and chunk.usage:
            # Extract cost information if available
            input_cost, output_cost, total_cost = self._extract_cost_info(chunk.usage)

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=getattr(chunk.usage, 'prompt_tokens', 0) or 0,
                output_tokens=getattr(chunk.usage, 'completion_tokens', 0) or 0,
                total_tokens=getattr(chunk.usage, 'total_tokens', 0) or 0,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
            )

        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=choice.finish_reason or "null"
        )
    
    async def invoke(
        self,
        messages,
        *,
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        output_parser=None,
        timeout=None,
        **kwargs,
    ):
        return await self._invoke_with_retry(
            _orig_invoke,
            self,
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            output_parser=output_parser,
            timeout=timeout,
            **kwargs,
        )

    async def stream(
        self,
        messages,
        *,
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        output_parser=None,
        timeout=None,
        **kwargs,
    ):
        async for chunk in self._stream_with_retry(
            _orig_stream,
            self,
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            output_parser=output_parser,
            timeout=self._resolve_stream_timeout(timeout),
            **kwargs,
        ):
            yield chunk

    async def generate_image(
        self,
        messages: list[UserMessage],
        *,
        model: str | None = None,
        size: str | None = "1664*928",
        negative_prompt: str | None = None,
        n: int | None = 1,
        prompt_extend: bool = True,
        watermark: bool = False,
        seed: int = 0,
        **kwargs: Any,
    ) -> ImageGenerationResponse:
        """OpenAI-compatible image generation with Huawei MaaS request/response quirks."""
        api_base = getattr(self.model_client_config, "api_base", "") or ""
        is_huawei_maas = _is_huawei_maas_api_base(api_base)

        call_kwargs = dict(kwargs)
        call_size = size
        call_n = n
        call_negative_prompt = negative_prompt

        if is_huawei_maas:
            call_size = _normalize_huawei_image_size(size)
            call_n = 1
            call_negative_prompt = None
            call_kwargs.pop("prompt_extend", None)
            call_kwargs.pop("negative_prompt", None)
            call_kwargs.setdefault("response_format", "b64_json")
            call_kwargs["watermark"] = watermark
            if seed:
                call_kwargs["seed"] = seed

        if is_huawei_maas:
            # Upstream only forwards vendor extras via **kwargs; keep wire params there.
            result = await _ORIGINAL_GENERATE_IMAGE(
                self,
                messages,
                model=model,
                size=call_size,
                n=call_n,
                **call_kwargs,
            )
        else:
            result = await _ORIGINAL_GENERATE_IMAGE(
                self,
                messages,
                model=model,
                size=call_size,
                negative_prompt=call_negative_prompt,
                n=call_n,
                prompt_extend=prompt_extend,
                watermark=watermark,
                seed=seed,
                **call_kwargs,
            )

        if not is_huawei_maas or not result.images_base64:
            return result

        cleaned_b64 = [_strip_b64_data_uri_prefix(item) for item in result.images_base64]
        return result.model_copy(update={"images_base64": cleaned_b64})


class PatchSiliconFlowModelClient(RetryMixin, SiliconFlowModelClient):
    """带重试的 SiliconFlowModelClient。结构同 OpenAI，包装原始方法即可。"""

    async def invoke(
        self,
        messages,
        *,
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        output_parser=None,
        timeout=None,
        **kwargs,
    ):
        return await self._invoke_with_retry(
            _sf_orig_invoke,
            self,
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            output_parser=output_parser,
            timeout=timeout,
            **kwargs,
        )

    async def stream(
        self,
        messages,
        *,
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        output_parser=None,
        timeout=None,
        **kwargs,
    ):
        async for chunk in self._stream_with_retry(
            _sf_orig_stream,
            self,
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            output_parser=output_parser,
            timeout=self._resolve_stream_timeout(timeout),
            **kwargs,
        ):
            yield chunk


def _patch_railed_model_call_session() -> None:
    """Monkey-patch ReActAgent._railed_model_call to set _retry_session ContextVar
    around llm.invoke/stream calls so RetryMixin._notify_retry_start can reach the frontend."""
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    _orig_railed_model_call = ReActAgent._railed_model_call  # pylint: disable=protected-access

    async def _patched_railed_model_call(self, ctx):
        session = ctx.session
        token = _retry_session.set(session)
        try:
            return await _orig_railed_model_call(self, ctx)
        finally:
            _retry_session.reset(token)

    ReActAgent._railed_model_call = _patched_railed_model_call  # pylint: disable=protected-access


def apply_siliconflow_model_client_patch() -> None:
    """Inject retry into SiliconFlowModelClient."""
    _impl = PatchSiliconFlowModelClient.__dict__
    SiliconFlowModelClient.invoke = _impl["invoke"]
    SiliconFlowModelClient.stream = _impl["stream"]

    _instance_attrs = (
        '_stream_with_retry', '_invoke_with_retry', '_resolve_stream_timeout',
        '_classify_error', '_is_retryable_error', '_calculate_backoff',
        '_notify_retry_start', '_notify_retry_end', '_get_retry_config',
    )
    _class_attrs = ('_NON_RETRYABLE_KEYWORDS', '_RETRYABLE_KEYWORDS')
    _static_attrs = ('_extract_error_details', '_extract_retry_after')

    for _attr in _static_attrs:
        if hasattr(RetryMixin, _attr):
            setattr(SiliconFlowModelClient, _attr, staticmethod(getattr(RetryMixin, _attr)))

    for _attr in _instance_attrs + _class_attrs:
        if hasattr(RetryMixin, _attr):
            setattr(SiliconFlowModelClient, _attr, getattr(RetryMixin, _attr))


def apply_openai_model_client_patch() -> None:
    """Monkey-patch upstream OpenAIModelClient with JiuwenClaw SSL/headers/stream behavior."""
    global _ORIGINAL_BUILD_REQUEST_PARAMS, _ORIGINAL_PARSE_RESPONSE, _ORIGINAL_GENERATE_IMAGE
    if _ORIGINAL_BUILD_REQUEST_PARAMS is None:
        _ORIGINAL_BUILD_REQUEST_PARAMS = OpenAIModelClient._build_request_params
    if _ORIGINAL_PARSE_RESPONSE is None:
        _ORIGINAL_PARSE_RESPONSE = getattr(OpenAIModelClient, "_parse_response")
    if _ORIGINAL_GENERATE_IMAGE is None:
        _ORIGINAL_GENERATE_IMAGE = OpenAIModelClient.generate_image

    _impl = PatchOpenAIModelClient.__dict__
    setattr(OpenAIModelClient, "_create_async_openai_client", _impl["_create_async_openai_client"])
    setattr(OpenAIModelClient, "_parse_response", _impl["_parse_response"])
    setattr(OpenAIModelClient, "_parse_stream_chunk", _impl["_parse_stream_chunk"])
    setattr(OpenAIModelClient, "_build_request_params", _patched_build_request_params)

    OpenAIModelClient.invoke = PatchOpenAIModelClient.invoke
    OpenAIModelClient.stream = PatchOpenAIModelClient.stream
    OpenAIModelClient.generate_image = _impl["generate_image"]
    _patch_railed_model_call_session()
    _static_attrs = ('_extract_error_details', '_extract_retry_after', '_raise_mock_error')
    _instance_attrs = (
        '_stream_with_retry', '_invoke_with_retry', '_resolve_stream_timeout',
        '_classify_error', '_is_retryable_error', '_calculate_backoff',
        '_notify_retry_start', '_notify_retry_end', '_get_retry_config',
    )
    _class_attrs = ('_NON_RETRYABLE_KEYWORDS', '_RETRYABLE_KEYWORDS')

    for _attr in _static_attrs:
        if hasattr(RetryMixin, _attr):
            setattr(OpenAIModelClient, _attr, staticmethod(getattr(RetryMixin, _attr)))

    for _attr in _instance_attrs + _class_attrs:
        if hasattr(RetryMixin, _attr):
            setattr(OpenAIModelClient, _attr, getattr(RetryMixin, _attr))

    apply_tool_invoke_interface_log()


def apply_tool_invoke_interface_log() -> None:
    """Log tool execution as RESP lines in ``interface.log`` (runtime path: AbilityManager)."""
    from jiuwenclaw.interface_resp import session_id_from_context, track_tool_resp

    try:
        from openjiuwen.core.single_agent.ability_manager import AbilityManager
    except Exception:
        llm_logger.debug("AbilityManager tool interface log patch skipped")
        return

    _execute = AbilityManager._execute_single_tool_call  # pylint: disable=protected-access
    if getattr(_execute, "_jiuwen_interface_log_patched", False):
        return

    _orig_execute = _execute

    async def _patched_execute(self, tool_call, session, tag=None):
        tool_name = str(getattr(tool_call, "name", "") or "unknown")
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        sid = session_id_from_context(session) or None
        original_arguments = getattr(tool_call, "arguments", None)
        validation = validate_tool_arguments(original_arguments)
        if validation.ok:
            if hasattr(tool_call, "arguments"):
                tool_call.arguments = validation.normalized
            if validation.normalized != original_arguments:
                llm_logger.debug(
                    "[tool_args_validation] action=normalize tool=%s tool_call_id=%s "
                    "kind=%s length=%s session_id=%s",
                    tool_name,
                    tool_call_id,
                    validation.kind,
                    validation.length,
                    sid,
                )
            async with track_tool_resp(tool_name, session_id=sid):
                return await _orig_execute(self, tool_call, session, tag=tag)

        result = tool_arguments_failure_payload(
            tool_name=tool_name,
            validation=validation,
        )
        message = tool_arguments_failure_message(
            tool_name=tool_name,
            validation=validation,
        )
        llm_logger.warning(
            "[tool_args_validation] action=skip tool=%s tool_call_id=%s kind=%s "
            "length=%s reason=%s session_id=%s",
            tool_name,
            tool_call_id,
            validation.kind,
            validation.length,
            validation.reason,
            sid,
        )
        return result, ToolMessage(
            content=message,
            tool_call_id=tool_call_id,
            metadata=result,
        )

    _patched_execute._jiuwen_interface_log_patched = True  # type: ignore[attr-defined]  # pylint: disable=protected-access
    AbilityManager._execute_single_tool_call = _patched_execute  # pylint: disable=protected-access
