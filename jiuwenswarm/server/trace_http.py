# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP 端点：输入 input，执行 agent，返回本次执行的 OpenTelemetry trace。

配套 `interface_deep._set_telemetry_context_for_request`（已修复 deep 模式埋点
断裂）使用：本端点在调用 agent 前注入 W3C traceparent 到 request.metadata，
TelemetryRail 提取后 agent 侧 span 全部挂到本端点的根 trace 上；执行完
force_flush 让 BatchSpanProcessor 把 span 写入 SQLite，再用 get_trace_tree
按 trace_id 取整棵树返回。

启动：在 AgentServer 进程内随主服务一起起（env JIUWENSWARM_TRACE_HTTP_ENABLED=true）。
模型配置仍走服务端 config.yaml / 环境变量（API_BASE/API_KEY/MODEL_NAME/MODEL_PROVIDER）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = int(os.getenv("JIUWENSWARM_TRACE_HTTP_TIMEOUT", "1800"))
_DEFAULT_PORT = int(os.getenv("JIUWENSWARM_TRACE_HTTP_PORT", "18093"))
_DEFAULT_HOST = os.getenv("JIUWENSWARM_TRACE_HTTP_HOST", "127.0.0.1")


class TraceHttpServer:
    """aiohttp HTTP 服务：POST /run -> {trace_id, trace}。

    与 AgentServer 同进程运行，复用其 AgentManager 驱动 agent。
    """

    def __init__(self, agent_manager: Any, host: str = _DEFAULT_HOST,
                 port: int = _DEFAULT_PORT) -> None:
        self._agent_manager = agent_manager
        self._host = host
        self._port = port
        self._runner: Any | None = None  # AppRunner or TCPSite
        self._app: Any | None = None

    async def start(self) -> None:
        from aiohttp import web

        self._app = web.Application(client_max_size=16 * 1024 * 1024)
        self._app.router.add_post("/run", self._handle_run)
        self._app.router.add_get("/health", self._handle_health)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(
            "[TraceHttpServer] listening http://%s:%s/run (POST input -> OTel trace)",
            self._host, self._port,
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._app = None

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({"ok": True})

    async def _handle_run(self, request: Any) -> Any:
        from aiohttp import web

        try:
            body = await request.json()
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": f"invalid JSON body: {exc}"}, status=400
            )

        text = (body.get("input") or body.get("query") or "").strip()
        if not text:
            return web.json_response(
                {"ok": False, "error": "field 'input' is required"}, status=400
            )

        mode = body.get("mode") or "agent.plan"
        channel_id = body.get("channel_id") or "trace_http"
        session_id = body.get("session_id") or f"trace-http-{int(time.time())}"
        project_dir = body.get("project_dir") or body.get("cwd") or ""
        timeout = int(body.get("timeout") or _DEFAULT_TIMEOUT)

        request_id = f"trace-http-{uuid.uuid4().hex[:12]}"

        # 构造 params：query/mode 必填；project_dir/cwd/workspace_dir 给齐，
        # 兼容 resolve_request_project_dir 与 AgentManager.process_message（后者读 workspace_dir）。
        params: dict[str, Any] = {"query": text, "mode": mode}
        if project_dir:
            params["project_dir"] = project_dir
            params["cwd"] = project_dir
            params["workspace_dir"] = project_dir

        # 按次指定模型：apibase/key/model/modelprovider 四件套（可选）。
        # 带齐 apibase+key+model 时，_resolve_model_for_request 会现场构造临时
        # Model 覆盖 config 默认，仅本次请求生效；不带则走服务端 config.yaml/.env。
        # 字段名兼容多种写法（apibase/api_base、key/api_key、model/model_name）。
        apibase = (body.get("apibase") or body.get("api_base") or "").strip()
        apikey = (body.get("key") or body.get("api_key") or "").strip()
        permodel = (body.get("model") or body.get("model_name") or "").strip()
        if apibase and apikey and permodel:
            params["api_base"] = apibase
            params["api_key"] = apikey
            params["model"] = permodel
            provider = (body.get("modelprovider")
                        or body.get("model_provider")
                        or body.get("provider")
                        or "OpenAI")
            params["client_provider"] = provider.strip() or "OpenAI"
            for opt in ("timeout", "verify_ssl", "custom_headers"):
                if body.get(opt) is not None:
                    params[opt] = body[opt]

        # metadata 先留空，下面在根 span 内注入 traceparent
        agent_request = AgentRequest(
            request_id=request_id,
            channel_id=channel_id,
            session_id=session_id,
            req_method=ReqMethod.CHAT_SEND,
            params=params,
            is_stream=False,
            timestamp=time.time(),
            metadata={},
        )

        trace_id, trace, ok, error = await self._run_and_collect(agent_request, timeout)

        return web.json_response({
            "request_id": request_id,
            "session_id": session_id,
            "ok": ok,
            "trace_id": trace_id,
            "trace": trace,
            "error": error,
        })

    # ------------------------------------------------------------------
    # core: run agent under a root span, then collect its OTel trace tree
    # ------------------------------------------------------------------

    async def _run_and_collect(
        self, agent_request: AgentRequest, timeout: int
    ) -> tuple[str, list[dict[str, Any]], bool, str | None]:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import Status, StatusCode
        from jiuwenswarm.telemetry.context_propagation import inject_trace_context

        tracer = otel_trace.get_tracer("jiuwenswarm.trace_http")
        ok = True
        error: str | None = None

        # 根 span：在它的上下文内注入 traceparent 到 metadata、并执行 agent，
        # 这样（修复后的）TelemetryRail 会以本根 span 为 parent，agent 侧所有
        # span 都落在同一 trace_id 下。with 退出时根 span 自动 end。
        with tracer.start_as_current_span("http.run") as root_span:
            trace_id = format(root_span.get_span_context().trace_id, "032x")
            inject_trace_context(agent_request.metadata)  # 写入 traceparent/tracestate
            try:
                await asyncio.wait_for(
                    self._agent_manager.process_message(agent_request), timeout=timeout
                )
            except asyncio.TimeoutError as exc:
                ok = False
                error = f"agent timed out after {timeout}s"
                # 标记根 span 为 ERROR 并记录异常，否则 trace 看起来像成功
                root_span.set_status(Status(StatusCode.ERROR, error))
                root_span.record_exception(exc)
            except Exception as exc:
                ok = False
                error = f"{type(exc).__name__}: {exc}"
                root_span.set_status(Status(StatusCode.ERROR, error))
                root_span.record_exception(exc)

        # BatchSpanProcessor 默认每 5s 批量写盘；force_flush 确保 trace 已落 SQLite
        try:
            otel_trace.get_tracer_provider().force_flush()
        except Exception as exc:
            logger.warning("[TraceHttpServer] force_flush failed: %s", exc)

        # get_trace_tree 是同步 SQLite 读，放线程池跑避免阻塞事件循环（trace 多时尤其重要）
        trace = await asyncio.to_thread(self._load_trace_tree, trace_id)
        return trace_id, trace, ok, error

    def _load_trace_tree(self, trace_id: str) -> list[dict[str, Any]]:
        from jiuwenswarm.telemetry import get_trace_tree
        from jiuwenswarm.telemetry.config import load_telemetry_config

        cfg = load_telemetry_config()
        db_path = cfg.sqlite_db_path
        try:
            return get_trace_tree(db_path, trace_id)
        except Exception as exc:
            logger.warning(
                "[TraceHttpServer] get_trace_tree failed (db=%s, trace_id=%s): %s",
                db_path, trace_id, exc,
            )
            return []


__all__ = ["TraceHttpServer"]
