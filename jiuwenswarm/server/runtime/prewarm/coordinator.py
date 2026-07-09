# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PrewarmCoordinator — fires a max_tokens=1 request to warm the vLLM prefix cache.

The coordinator borrows the existing ModelClient's parameter builder
(``_build_and_sanitize_params``) so the prewarm body's token sequence
matches the real LLM call exactly. It then issues its own aiohttp POST
bypassing the Model wrapper's callback decoration, so prewarm requests
do not pollute LLM call telemetry or recurse into the rail.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import aiohttp

from jiuwenswarm.server.runtime.prewarm.config import PrewarmConfig

try:
    from openjiuwen.core.common.logging import logger
except Exception:  # pragma: no cover - fallback if logger not importable in tests
    import logging
    logger = logging.getLogger("prewarm")

_PREWARM_LOG_PREFIX = "[PrewarmCoordinator]"


class PrewarmCoordinator:
    """Builds and fires prewarm requests, swallowing all exceptions."""

    def __init__(self, config: Optional[PrewarmConfig] = None):
        self._config = config or PrewarmConfig.from_env()

    @property
    def config(self) -> PrewarmConfig:
        return self._config

    def is_supported_client(self, client: Any) -> bool:
        """Only InferenceAffinity (vLLM) clients support prewarm semantics."""
        name = getattr(client, "__client_name__", "") or ""
        return "InferenceAffinity" in name or "OpenAI" in name or "vLLM" in name

    def _build_body(
        self,
        client: Any,
        *,
        messages: List[Any],
        tools: Optional[List[Any]],
        model_name: Optional[str],
        enable_cache_sharing: bool,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """Build the prewarm request body via the client's own param builder."""
        build_fn = getattr(client, "_build_and_sanitize_params", None)
        if callable(build_fn):     
            params = client._build_and_sanitize_params(
            messages=messages,
            tools=tools,
            temperature=0,
            top_p=None,
            model=model_name,
            max_tokens=1,
            stop=None,
            stream=False,
            session_id=session_id if enable_cache_sharing else None,
            enable_cache_sharing=enable_cache_sharing,
        )
        else :
            params = client._build_request_params(
                messages=messages,
                tools=tools,
                temperature=0,
                top_p=None,
                model=model_name,
                max_tokens=1,
                stop=None,
                stream=False,
            )
            sanitized_fn = getattr(client, "_sanitize_request_params", None)
            if callable(sanitized_fn):
                params["messages"] = sanitized_fn(params.get("messages", []))
            if enable_cache_sharing and session_id:
                params["cache_sharing"] = True
                params["cache_salt"] = session_id
        params["max_tokens"] = 1
        params["temperature"] = 0
        params["stream"] = False
        params.pop("tool_choice", None)
        if tools:
            params["tool_choice"] = "auto"
        return params

    async def _fire_request(self, client: Any, body: Dict[str, Any]) -> None:
        """Issue a single aiohttp POST; swallow all errors."""
        cfg = client.model_client_config
        url = f"{cfg.api_base.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        custom = getattr(cfg, "custom_headers", None) or {}
        headers.update(custom)

        timeout_obj = aiohttp.ClientTimeout(total=self._config.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout_obj) as http:
                async with http.post(url, headers=headers, json=body) as resp:
                    # Drain the body so the connection is released cleanly.
                    _ = await resp.read()
                    if resp.status != 200:
                        logger.warning(
                            "%s prewarm non-200 status=%s (url=%s)",
                            _PREWARM_LOG_PREFIX, resp.status, url,
                        )
                    else:
                        logger.info(
                            "%s prewarm ok scenario_body tokens~%s",
                            _PREWARM_LOG_PREFIX,
                            _approx_input_tokens(body),
                        )
        except Exception as exc:  # noqa: BLE001 - prewarm must never fail the agent
            logger.warning("%s prewarm failed: %s", _PREWARM_LOG_PREFIX, exc)

    async def prewarm(
        self,
        client: Any,
        *,
        messages: List[Any],
        tools: Optional[List[Any]],
        model_name: Optional[str],
        session_id: Optional[str],
        enable_cache_sharing: bool,
        scenario: str,
    ) -> None:
        """Fire a prewarm request as a background task (fire-and-forget)."""
        if not self._config.enabled:
            logger.info("%s prewarm disabled, skipping", _PREWARM_LOG_PREFIX)
            return
        if not self.is_supported_client(client):
            logger.info("%s prewarm unsupported client=%s, skipping", _PREWARM_LOG_PREFIX, type(client))
            return
        if not messages:
            logger.info("%s prewarm empty messages, skipping", _PREWARM_LOG_PREFIX)
            return

        try:
            body = self._build_body(
                client,
                messages=messages,
                tools=tools,
                model_name=model_name,
                enable_cache_sharing=enable_cache_sharing,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s prewarm body build failed: %s", _PREWARM_LOG_PREFIX, exc)
            return

        asyncio.create_task(self._fire_request(client, body))
        
def _approx_input_tokens(body: Dict[str, Any]) -> int:
    """Rough token estimate for logging only."""
    msgs = body.get("messages") or []
    total = 0
    for m in msgs:
        content = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(content, str):
            total += max(1, len(content) // 4)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text", "")
                    if isinstance(txt, str):
                        total += max(1, len(txt) // 4)
    return total
