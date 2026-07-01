# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""[临时测试接口] 企业版 broker 上的 HTTP 直连聊天接口，供测试在 /docs 里免登录发对话。

用假的浏览器连接驱动一次 chat.send：把 user_id/group_id/bot_id 同时注入握手 query
(_browser_query) 与 chat.send params（与真前端 extSettingsTo* 一致），收集网关回来的事件
到 status=completed/error（或超时/空闲）后一次性返回。

>>> 生产环境删除本功能：删掉本文件 + app.py 里 include_router(build_test_chat_router(...)) 一行
>>> + 把 create_enterprise_broker_app 的 docs_url 恢复成 None。整套解耦，删除无残留。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jiuwenclaw.webserver.enterprise_broker import EnterpriseWebWsServer


class TestChatQuery(BaseModel):
    """[测试专用] /test/chat 的查询参数（归属 id + 会话/超时控制，具名封装）。"""

    user_id: str = Field("", description="归属 user_id")
    group_id: str = Field("", description="归属 group_id")
    bot_id: str = Field("", description="归属 bot_id")
    session_id: str = Field("", description="会话 id；留空自动生成")
    timeout: float = Field(60.0, description="整体超时（秒）")
    idle_timeout: float = Field(15.0, description="多久收不到新事件即结束（秒）")


def build_test_chat_router(broker: EnterpriseWebWsServer) -> APIRouter:
    """构造 [测试专用] /test/chat 路由（挂在 broker app 上，出现在 /docs）。"""
    router = APIRouter(prefix="/test", tags=["test-only"])

    @router.post("/chat")
    async def test_chat(
        query: Annotated[TestChatQuery, Query()],
        content: str = Body(..., embed=True, description="用户消息内容"),
    ) -> JSONResponse:
        """[测试专用] 免登录直连网关发一句 chat.send，聚合事件后返回。"""
        user_id, group_id, bot_id = query.user_id, query.group_id, query.bot_id
        conn_id = f"test-{uuid.uuid4().hex}"
        sid = query.session_id or f"test-sess-{uuid.uuid4().hex}"
        req_id = f"test-req-{uuid.uuid4().hex}"
        queue: asyncio.Queue[str] = asyncio.Queue()

        class _Collector:
            """假浏览器：把 broker 回程的帧塞进队列，代替真 WebSocket。"""

            async def send(self, data: str) -> None:
                await queue.put(data)

            async def close(self, code: int = 1000) -> None:
                return None

        broker.register_browser_connection(conn_id, _Collector())
        # 归属 id 走握手 query（被注入为 _browser_query，与真前端 extSettingsToQueryFields 一致）
        query: dict[str, list[str]] = {}
        if user_id:
            query["user_id"] = [user_id]
        if group_id:
            query["group_id"] = [group_id]
        if bot_id:
            query["bot_id"] = [bot_id]
        broker.record_browser_query(conn_id, query)

        # 归属 id 同时写进 chat.send params（与真前端 extSettingsToRoutingParams 一致）
        params: dict[str, Any] = {"content": content, "session_id": sid}
        if user_id:
            params["user_id"] = user_id
        if group_id:
            params["group_id"] = group_id
        if bot_id:
            params["bot_id"] = bot_id
        frame = {"type": "req", "id": req_id, "method": "chat.send", "params": params}

        events: list[Any] = []
        final_status = "timeout"
        try:
            await broker.route_browser_frame(conn_id, json.dumps(frame, ensure_ascii=False))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + query.timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=min(remaining, query.idle_timeout))
                except asyncio.TimeoutError:
                    break
                try:
                    parsed: Any = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw": raw}
                events.append(parsed)
                payload = parsed.get("payload") if isinstance(parsed, dict) else None
                status = payload.get("status") if isinstance(payload, dict) else None
                if status in ("completed", "error"):
                    final_status = status
                    break
        finally:
            await broker.teardown_browser(conn_id)

        return JSONResponse({
            "conn_id": conn_id,
            "session_id": sid,
            "request_id": req_id,
            "final_status": final_status,
            "event_count": len(events),
            "events": events,
        })

    return router
