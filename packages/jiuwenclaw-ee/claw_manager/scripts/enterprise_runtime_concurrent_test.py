#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""并发向 Gateway WebChannel 发送 chat.send，验证 Runtime 转发与 AgentServer 池的端到端压测。

脚本：`enterprise_runtime_concurrent_test.py`（与 ``enterprise_runtime_service_config.py`` 同属
Gateway Runtime 联调/压测工具，前者读库校验 ``service_config`` 槽位，本脚本经 WebSocket 发真实
``chat.send`` 做并发端到端验证。）

默认发起 30 路并发（``--concurrency 30``），均匀分布到 3 个 AgentServer（``--shards 3``），
即每个 AgentServer 10 路；**同一分片内共用同一个 ``group_id``**
（默认 ``loadtest_s0`` / ``loadtest_s1`` / ``loadtest_s2``），以便经 Gateway 路由
均匀打到 3 个 AgentServer 实例上。``session_id`` / ``user_id`` / ``req_id`` 仍每路唯一。

依赖：主仓库已安装 ``websockets``（``uv sync`` 或 ``pip install websockets``）。

压测时可配合 ``mock_llm_server.py`` 替代真实 LLM（见设计文档 **§3.3 Mock LLM**）。

典型用法（项目根目录）::

    # 本地 provision 后的 Gateway Web 端口
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_concurrent_test.py \\
        --web-port 19234

    # 远程 Gateway
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_concurrent_test.py \\
        --ws-url ws://10.0.0.1:19001/ws

    # K8s 仅暴露 Web NodePort（5173 -> 30105）时，经 HTTP 端口的 /ws 代理连入
    # jiuwenclaw-web:19000 为 ClusterIP，集群外不可直连，须走 NodePort + /ws
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_runtime_concurrent_test.py \\
        --ws-url ws://<节点IP>:30105/ws

    # 等价写法（--web-port 填 NodePort 外部端口，非 19000）
    uv run python .../enterprise_runtime_concurrent_test.py \\
        --host <节点IP> --web-port 30105

    # 自定义总并发与 AgentServer 分片（60 路均匀打到 6 个 AgentServer，每个 10 路）
    uv run python .../enterprise_runtime_concurrent_test.py \\
        --web-port 19234 --concurrency 60 --shards 6

默认等待整轮 Agent 任务结束（``chat.usage_summary``、带正文的 ``chat.final``，或
``chat.processing_status`` 且 ``is_processing=false``，与 web_enterprise 一致）；
DeepAgent 在流式文本 iteration 结束时可能发出 **content 为空** 的 ``chat.final`` 标记，
该帧仅表示当前 LLM 轮次结束，**不是**整轮任务完成，脚本会忽略并继续等待。
权限中断（``chat.invocation_paused``）后须等 Agent 恢复并再次 idle 才视为完成。
每路真正完成时打印 ``[done]`` 行（含 idx / session_id / 耗时）。Agent 弹出权限/追问（``chat.ask_user_question``）
时自动全部允许（权限类选「总是允许」）。长任务可通过 ``--final-timeout`` 调整上限。
若仅需验证 Gateway 接受请求、不等 Agent 跑完，加 ``--accept-only``；禁用自动放行加 ``--no-auto-allow``。
排查权限/追问 WS 事件时加 ``--ws-event-log``，会打印每路收到的 event 名（含 frame.event
与 payload.event_type 对照，便于发现事件名不匹配）。

Ctrl+C 退出时，脚本会对**已接受且未完成**的会话发送 ``chat.interrupt``（``intent=cancel``），
与 ``web_enterprise`` 页面点击「取消」一致；仅关闭 WebSocket **不会**自动停止 AgentServer 上的任务。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_CONTENT = (
    " 帮我写一篇十万字的小说，主题是人生的意义，写完后保存到txt文件发给我。直接开始写，不要问我其他问题。"
)


@dataclass
class RequestResult:
    index: int
    shard: int
    session_id: str
    req_id: str
    group_id: str
    bot_id: str
    user_id: str
    ok: bool
    accepted: bool
    error: str = ""
    accept_ms: float = 0.0
    total_ms: float = 0.0
    final_received: bool = False


@dataclass
class LoadTestStats:
    total: int
    completed: int
    failed: int
    elapsed_s: float
    accept_ms: list[float] = field(default_factory=list)
    total_ms: list[float] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"total={self.total} completed={self.completed} failed={self.failed} "
            f"elapsed={self.elapsed_s:.2f}s",
        ]
        if self.accept_ms:
            sorted_accept = sorted(self.accept_ms)
            lines.append(
                f"accept_ms: p50={_percentile(sorted_accept, 0.5):.0f} "
                f"p95={_percentile(sorted_accept, 0.95):.0f} "
                f"max={max(sorted_accept):.0f} "
                f"stdev={statistics.pstdev(sorted_accept):.1f}"
            )
        if self.total_ms:
            sorted_total = sorted(self.total_ms)
            lines.append(
                f"total_ms: p50={_percentile(sorted_total, 0.5):.0f} "
                f"p95={_percentile(sorted_total, 0.95):.0f} "
                f"max={max(sorted_total):.0f}"
            )
        return "\n".join(lines)


@dataclass
class _ProgressTracker:
    total: int
    completed: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def mark_done(self) -> int:
        async with self.lock:
            self.completed += 1
            return self.completed


@dataclass
class _ActiveSession:
    index: int
    session_id: str
    ws: Any
    accepted: bool = False
    finished: bool = False
    cancel_sent: bool = False

    def can_begin_cancel(self) -> bool:
        return self.accepted and not self.finished and not self.cancel_sent


class _ActiveSessionRegistry:
    """跟踪进行中的 WS 会话；退出时对已接受的请求发送 chat.interrupt(cancel)。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _ActiveSession] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: _ActiveSession) -> None:
        async with self._lock:
            self._sessions[session.session_id] = session

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def mark_accepted(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.accepted = True

    async def mark_finished(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.finished = True

    async def try_begin_cancel(self, session_id: str) -> _ActiveSession | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.can_begin_cancel():
                return None
            session.cancel_sent = True
            return session

    async def cancel_all(self, *, wait_ack: float = 3.0) -> int:
        async with self._lock:
            sessions = list(self._sessions.values())
        if not sessions:
            return 0
        results = await asyncio.gather(
            *[
                _send_chat_interrupt_cancel(
                    s.ws,
                    session_id=s.session_id,
                    index=s.index,
                    wait_ack=wait_ack,
                    registry=self,
                )
                for s in sessions
            ],
            return_exceptions=True,
        )
        return sum(1 for item in results if item is True)


async def _send_chat_interrupt_cancel(
    ws: Any,
    *,
    session_id: str,
    index: int,
    wait_ack: float = 3.0,
    registry: _ActiveSessionRegistry | None = None,
) -> bool:
    """与 web_enterprise 点击「取消」一致：chat.interrupt intent=cancel。"""
    if registry is not None:
        session = await registry.try_begin_cancel(session_id)
        if session is None:
            return False

    req_id = f"req_cancel_{index:02d}_{uuid.uuid4().hex[:8]}"
    frame = {
        "type": "req",
        "id": req_id,
        "method": "chat.interrupt",
        "params": {"session_id": session_id, "intent": "cancel"},
    }
    try:
        await ws.send(json.dumps(frame, ensure_ascii=False))
        logger.info("[cancel] idx=%d session_id=%s", index, session_id)
        if wait_ack > 0:
            deadline = time.perf_counter() + wait_ack
            while time.perf_counter() < deadline:
                try:
                    remaining = max(0.05, deadline - time.perf_counter())
                    msg = await _recv_json(ws, remaining)
                except asyncio.TimeoutError:
                    break
                if msg.get("type") == "res" and msg.get("id") == req_id:
                    return bool(msg.get("ok"))
        return True
    except Exception as err:
        logger.warning(
            "[cancel-failed] idx=%d session_id=%s err=%s",
            index,
            session_id,
            err,
        )
        return False


def _percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int((len(sorted_values) - 1) * ratio)
    return sorted_values[idx]


def _configure_cli_logging() -> None:
    class _TimestampFormatter(logging.Formatter):
        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            from datetime import datetime

            dt = datetime.fromtimestamp(record.created)
            base = dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")
            return f"{base}.{int(record.msecs):03d}"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = _TimestampFormatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.setFormatter(fmt)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    root.addHandler(out)
    root.addHandler(err)


def _load_web_port_from_provision(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("data", raw)
    ports = data.get("ports") if isinstance(data, dict) else None
    if not isinstance(ports, dict):
        raise ValueError(f"无法在 {path} 中找到 data.ports")
    web = ports.get("web")
    if web is None:
        raise ValueError(f"无法在 {path} 中找到 data.ports.web")
    return int(web)


def _resolve_ws_url(args: argparse.Namespace) -> str:
    if args.ws_url:
        url = str(args.ws_url).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError(f"--ws-url 须为 ws:// 或 wss://，当前 scheme={parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError(f"--ws-url 无效（缺少 host）: {url!r}")
        return url
    if args.provision_json is not None:
        web_port = _load_web_port_from_provision(args.provision_json)
    else:
        web_port = int(args.web_port)
    return f"ws://{args.host}:{web_port}{args.ws_path}"


def _browser_origin_header(ws_url: str) -> dict[str, str]:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    http_scheme = "https" if parsed.scheme == "wss" else "http"
    port = parsed.port
    default_port = 443 if http_scheme == "https" else 80
    if port is not None and port != default_port:
        origin = f"{http_scheme}://{host}:{port}"
    else:
        origin = f"{http_scheme}://{host}"
    return {"Origin": origin}


def _build_shard_plan(concurrency: int, shards: int, prefix: str) -> list[tuple[int, str]]:
    """返回 (shard_index, group_id) 列表；同一 shard 内所有请求共用同一 group_id。"""
    if shards <= 0:
        raise ValueError("--shards 须 > 0")
    if concurrency <= 0:
        raise ValueError("--concurrency 须 > 0")
    if concurrency % shards != 0:
        raise ValueError(
            f"--concurrency ({concurrency}) 须能被 --shards ({shards}) 整除，"
            f"以便每个 AgentServer 接收相同数量请求"
        )
    per_shard = concurrency // shards
    shard_group_ids = [f"{prefix}_s{shard}" for shard in range(shards)]
    out: list[tuple[int, str]] = []
    for shard in range(shards):
        group_id = shard_group_ids[shard]
        for _ in range(per_shard):
            out.append((shard, group_id))
    return out


async def _recv_json(ws: Any, timeout: float) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(f"非 JSON 对象: {raw!r}")
    return data


def _normalize_event_frame(frame: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """从 WS event 帧解析 event 名与 payload（兼容 payload 内嵌 event_type）。"""
    if frame.get("type") != "event":
        return None, {}
    event = frame.get("event")
    payload = frame.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if not event:
        nested = payload.get("event_type") or payload.get("event")
        if isinstance(nested, str):
            event = nested
    return (str(event) if event else None), payload


def _final_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if content is None:
        return ""
    return str(content).strip()


def _is_intra_turn_chat_final(event: str | None, payload: dict[str, Any]) -> bool:
    """DeepAgent 流式文本 iteration 结束时的空 chat.final，仅标记当前 LLM 轮次完成。"""
    return event == "chat.final" and not _final_content(payload)


def _is_invoke_complete_event(event: str | None, payload: dict[str, Any]) -> bool:
    """判断 WS event 是否表示整轮 chat.send 任务已结束（可安全退出监听）。"""
    if event == "chat.usage_summary":
        return True
    if event == "chat.final" and not _is_intra_turn_chat_final(event, payload):
        return True
    return False


def _is_processing_idle(payload: dict[str, Any]) -> bool:
    """chat.processing_status 是否表示 Agent 已停止处理（与 web_enterprise 一致）。"""
    if "is_processing" not in payload:
        return False
    return not bool(payload.get("is_processing"))


def _should_complete_on_processing_idle(
    *,
    accepted: bool,
    saw_agent_output: bool,
    hitl_paused: bool,
    payload: dict[str, Any],
) -> bool:
    if not accepted or not saw_agent_output or hitl_paused:
        return False
    return _is_processing_idle(payload)


_AGENT_ACTIVITY_EVENTS = frozenset({
    "chat.delta",
    "chat.tool_call",
    "chat.tool_result",
    "chat.tool_calls.delta",
    "chat.tool_update",
    "todo.updated",
    "chat.file",
    "chat.final",
})


def _log_ws_event(
    *,
    index: int,
    session_id: str,
    frame: dict[str, Any],
    resolved_event: str | None,
    payload: dict[str, Any],
) -> None:
    """打印 WS event 帧，便于对照 frame.event 与 payload.event_type。"""
    frame_event = frame.get("event")
    nested_event_type = payload.get("event_type") if isinstance(payload, dict) else None
    nested_event = payload.get("event") if isinstance(payload, dict) else None
    request_id = frame.get("request_id")
    source = payload.get("source") if isinstance(payload, dict) else None
    parts = [
        f"idx={index}",
        f"session_id={session_id}",
        f"resolved={resolved_event or '<none>'}",
        f"frame.event={frame_event!r}",
    ]
    if nested_event_type is not None:
        parts.append(f"payload.event_type={nested_event_type!r}")
    if nested_event is not None and nested_event != nested_event_type:
        parts.append(f"payload.event={nested_event!r}")
    if request_id:
        parts.append(f"request_id={request_id}")
    if source:
        parts.append(f"source={source!r}")
    logger.info("[ws-event] %s", " ".join(parts))


def _pick_allow_option(options: list[Any]) -> str:
    """从选项列表中选取「允许」类答案，优先「总是允许」。"""
    labels: list[str] = []
    for opt in options:
        if isinstance(opt, dict):
            labels.append(str(opt.get("label") or "").strip())
        elif isinstance(opt, str):
            labels.append(opt.strip())
    prefer = (
        "总是允许",
        "Always allow",
        "Allow always",
        "本次允许",
        "Allow once",
        "Allow",
        "允许",
        "接收",
        "Create",
        "Yes",
        "是",
        "确认",
    )
    deny = frozenset({"拒绝", "Reject", "Deny", "否", "No"})
    for token in prefer:
        for lab in labels:
            if lab == token or token in lab:
                return lab
    for lab in labels:
        if lab and lab not in deny:
            return lab
    return labels[0] if labels else "总是允许"


def _build_allow_answers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """为 ask_user_question / 权限审批构造「全部允许」答案列表。"""
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return [{"selected_options": ["总是允许"]}]
    answers: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, dict):
            answers.append({"selected_options": ["总是允许"]})
            continue
        options = question.get("options")
        option_list = options if isinstance(options, list) else []
        answers.append({"selected_options": [_pick_allow_option(option_list)]})
    return answers


async def _send_auto_allow(
    ws: Any,
    *,
    index: int,
    session_id: str,
    payload: dict[str, Any],
    bot_id: str,
    group_id: str,
    user_id: str,
    mode: str,
    answered_ids: set[str],
) -> bool:
    """响应 Agent 权限/追问：默认选「总是允许」类选项。"""
    interrupt_request_id = str(payload.get("request_id") or "").strip()
    if not interrupt_request_id or interrupt_request_id in answered_ids:
        return False
    answered_ids.add(interrupt_request_id)

    source = str(payload.get("source") or "").strip()
    answers = _build_allow_answers(payload)
    first_choice = ""
    if answers and isinstance(answers[0], dict):
        opts = answers[0].get("selected_options")
        if isinstance(opts, list) and opts:
            first_choice = str(opts[0])

    if source == "permission_interrupt":
        method = "chat.send"
        params: dict[str, Any] = {
            "session_id": session_id,
            "query": "",
            "content": "",
            "request_id": interrupt_request_id,
            "answers": answers,
            "mode": mode,
            "group_id": group_id,
            "bot_id": bot_id,
            "user_id": user_id,
        }
    else:
        method = "chat.user_answer"
        params = {
            "session_id": session_id,
            "request_id": interrupt_request_id,
            "answers": answers,
        }
        if source:
            params["source"] = source

    approve_req_id = f"req_allow_{index:02d}_{uuid.uuid4().hex[:8]}"
    await ws.send(
        json.dumps(
            {"type": "req", "id": approve_req_id, "method": method, "params": params},
            ensure_ascii=False,
        )
    )
    logger.info(
        "[auto-allow] idx=%d session_id=%s interrupt_request_id=%s method=%s choice=%r",
        index,
        session_id,
        interrupt_request_id,
        method,
        first_choice or "总是允许",
    )
    return True


async def _run_single_request(
    *,
    ws_url: str,
    ws_headers: dict[str, str],
    index: int,
    shard: int,
    group_id: str,
    bot_id: str,
    user_id: str,
    content: str,
    mode: str,
    accept_timeout: float,
    accept_only: bool,
    final_timeout: float,
    auto_allow: bool,
    ws_event_log: bool,
    progress: _ProgressTracker,
    registry: _ActiveSessionRegistry,
) -> RequestResult:
    import websockets

    session_id = f"sess_load_{index:02d}_{uuid.uuid4().hex[:8]}"
    req_id = f"req_load_{index:02d}_{uuid.uuid4().hex[:8]}"
    result = RequestResult(
        index=index,
        shard=shard,
        session_id=session_id,
        req_id=req_id,
        group_id=group_id,
        bot_id=bot_id,
        user_id=user_id,
        ok=False,
        accepted=False,
    )
    logger.info(
        "[send] idx=%d shard=%d session_id=%s req_id=%s group_id=%s bot_id=%s user_id=%s",
        index,
        shard,
        session_id,
        req_id,
        group_id,
        bot_id,
        user_id,
    )
    t0 = time.perf_counter()

    params: dict[str, Any] = {
        "session_id": session_id,
        "content": content,
        "query": content,
        "mode": mode,
        "group_id": group_id,
        "bot_id": bot_id,
        "user_id": user_id,
    }
    req = {
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "params": params,
    }

    deadline = t0 + (accept_timeout if accept_only else final_timeout)

    async def _log_terminal(*, success: bool, event: str, detail: str = "") -> None:
        done_n = await progress.mark_done()
        level = logger.info if success else logger.error
        level(
            "[%s] %d/%d idx=%d shard=%d total_ms=%.0f session_id=%s req_id=%s "
            "group_id=%s user_id=%s%s",
            event,
            done_n,
            progress.total,
            index,
            shard,
            result.total_ms,
            session_id,
            req_id,
            group_id,
            user_id,
            f" {detail}" if detail else "",
        )

    try:
        async with websockets.connect(
            ws_url,
            open_timeout=15,
            additional_headers=ws_headers,
        ) as ws:
            await registry.add(_ActiveSession(index=index, session_id=session_id, ws=ws))
            try:
                await ws.send(json.dumps(req, ensure_ascii=False))
                accepted = False
                answered_interrupt_ids: set[str] = set()
                hitl_paused = False
                saw_agent_output = False

                while time.perf_counter() < deadline:
                    remaining = max(0.1, deadline - time.perf_counter())
                    try:
                        frame = await _recv_json(ws, remaining)
                    except asyncio.TimeoutError:
                        break

                    ftype = frame.get("type")

                    if ftype == "res" and frame.get("id") == req_id:
                        ok = bool(frame.get("ok"))
                        payload = frame.get("payload") or {}
                        result.ok = ok
                        if not ok:
                            err = frame.get("error") or payload.get("error") or frame
                            result.error = json.dumps(err, ensure_ascii=False)
                            result.total_ms = (time.perf_counter() - t0) * 1000
                            await _log_terminal(success=False, event="fail", detail=f"error={result.error}")
                            return result
                        accepted = bool(payload.get("accepted", True))
                        result.accepted = accepted
                        result.accept_ms = (time.perf_counter() - t0) * 1000
                        if accepted:
                            await registry.mark_accepted(session_id)
                        if not accepted:
                            result.error = "chat.send 未被接受"
                            result.total_ms = result.accept_ms
                            await _log_terminal(success=False, event="reject")
                            return result
                        logger.info(
                            "[accepted] idx=%d accept_ms=%.0f session_id=%s req_id=%s "
                            "group_id=%s bot_id=%s user_id=%s",
                            index,
                            result.accept_ms,
                            session_id,
                            req_id,
                            group_id,
                            bot_id,
                            user_id,
                        )
                        if accept_only:
                            result.ok = True
                            result.final_received = False
                            result.total_ms = result.accept_ms
                            await _log_terminal(success=True, event="done")
                            return result
                        continue

                    if ftype == "event":
                        event, payload = _normalize_event_frame(frame)
                        if ws_event_log:
                            _log_ws_event(
                                index=index,
                                session_id=session_id,
                                frame=frame,
                                resolved_event=event,
                                payload=payload,
                            )
                        if auto_allow and event == "chat.ask_user_question":
                            hitl_paused = True
                            await _send_auto_allow(
                                ws,
                                index=index,
                                session_id=session_id,
                                payload=payload,
                                bot_id=bot_id,
                                group_id=group_id,
                                user_id=user_id,
                                mode=mode,
                                answered_ids=answered_interrupt_ids,
                            )
                            continue
                        if event == "chat.invocation_paused":
                            hitl_paused = True
                            continue
                        if event in _AGENT_ACTIVITY_EVENTS:
                            saw_agent_output = True
                        if event == "chat.processing_status":
                            if payload.get("is_processing") is True:
                                hitl_paused = False
                            elif _should_complete_on_processing_idle(
                                accepted=accepted,
                                saw_agent_output=saw_agent_output,
                                hitl_paused=hitl_paused,
                                payload=payload,
                            ):
                                result.final_received = True
                                result.ok = True
                                result.accepted = True
                                result.total_ms = (time.perf_counter() - t0) * 1000
                                await _log_terminal(
                                    success=True,
                                    event="done",
                                    detail="reason=processing_status_idle",
                                )
                                return result
                            continue
                        if _is_intra_turn_chat_final(event, payload):
                            if ws_event_log:
                                logger.info(
                                    "[ws-event] idx=%d session_id=%s skip intra-turn chat.final "
                                    "(empty content; waiting for usage_summary / final chat.final / idle)",
                                    index,
                                    session_id,
                                )
                            continue
                        if _is_invoke_complete_event(event, payload):
                            result.final_received = True
                            result.ok = True
                            result.accepted = True
                            result.total_ms = (time.perf_counter() - t0) * 1000
                            await _log_terminal(
                                success=True,
                                event="done",
                                detail=f"reason={event}",
                            )
                            return result
                        if event == "chat.error":
                            result.error = json.dumps(payload, ensure_ascii=False)
                            result.total_ms = (time.perf_counter() - t0) * 1000
                            await _log_terminal(success=False, event="fail", detail=f"chat.error={result.error}")
                            return result
                        continue

                if not accepted:
                    result.error = "超时：未收到 chat.send 确认"
                elif not accept_only:
                    result.error = (
                        "超时：已接受但未收到 chat.usage_summary / 带正文的 chat.final / processing idle"
                    )
                result.total_ms = (time.perf_counter() - t0) * 1000
                await _log_terminal(success=False, event="timeout", detail=result.error)
                return result
            except asyncio.CancelledError:
                if result.accepted and not result.final_received:
                    await _send_chat_interrupt_cancel(
                        ws,
                        session_id=session_id,
                        index=index,
                        registry=registry,
                    )
                raise
            finally:
                await registry.mark_finished(session_id)
                await registry.remove(session_id)
    except Exception as err:
        result.error = str(err)
        result.total_ms = (time.perf_counter() - t0) * 1000
        await _log_terminal(success=False, event="fail", detail=f"exception={result.error}")
        return result


async def _run_loadtest(args: argparse.Namespace) -> int:
    ws_url = _resolve_ws_url(args)
    ws_headers = _browser_origin_header(ws_url)
    group_plan = _build_shard_plan(args.concurrency, args.shards, args.group_prefix)

    per_shard = args.concurrency // args.shards
    logger.info(
        "[plan] ws=%s concurrency=%d shards=%d per_shard=%d",
        ws_url,
        args.concurrency,
        args.shards,
        per_shard,
    )
    logger.info("[plan] content=%r", args.content)
    logger.info(
        "[plan] accept_only=%s auto_allow=%s ws_event_log=%s accept_timeout=%ss final_timeout=%ss",
        args.accept_only,
        args.auto_allow,
        args.ws_event_log,
        args.accept_timeout,
        args.final_timeout,
    )
    for shard in range(args.shards):
        group_id = next(gid for s, gid in group_plan if s == shard)
        logger.info(
            "[plan] shard=%d group_id=%s requests=%d (idx %d..%d)",
            shard,
            group_id,
            per_shard,
            shard * per_shard,
            shard * per_shard + per_shard - 1,
        )

    progress = _ProgressTracker(total=args.concurrency)
    registry = _ActiveSessionRegistry()
    t0 = time.perf_counter()
    task_objs = [
        asyncio.create_task(
            _run_single_request(
                ws_url=ws_url,
                ws_headers=ws_headers,
                index=idx,
                shard=shard,
                group_id=group_id,
                bot_id=args.bot_id,
                user_id=f"{args.user_id_prefix}_{idx:02d}",
                content=args.content,
                mode=args.mode,
                accept_timeout=args.accept_timeout,
                accept_only=args.accept_only,
                final_timeout=args.final_timeout,
                auto_allow=args.auto_allow,
                ws_event_log=args.ws_event_log,
                progress=progress,
                registry=registry,
            )
        )
        for idx, (shard, group_id) in enumerate(group_plan)
    ]
    try:
        raw_results = await asyncio.gather(*task_objs, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("[shutdown] 收到中断信号，正在 cancel 进行中的会话（chat.interrupt）...")
        cancelled = await registry.cancel_all()
        logger.info("[shutdown] 已向 %d 路会话发送 cancel", cancelled)
        for task in task_objs:
            task.cancel()
        await asyncio.gather(*task_objs, return_exceptions=True)
        raise
    elapsed = time.perf_counter() - t0

    results: list[RequestResult] = []
    for idx, item in enumerate(raw_results):
        if isinstance(item, Exception):
            shard, group_id = group_plan[idx]
            fail_result = RequestResult(
                index=idx,
                shard=shard,
                session_id="",
                req_id="",
                group_id=group_id,
                bot_id=args.bot_id,
                user_id=f"{args.user_id_prefix}_{idx:02d}",
                ok=False,
                accepted=False,
                error=str(item),
            )
            done_n = await progress.mark_done()
            logger.error(
                "[fail] %d/%d idx=%d shard=%d session_id= req_id= group_id=%s user_id=%s "
                "exception=%s",
                done_n,
                progress.total,
                idx,
                shard,
                group_id,
                fail_result.user_id,
                fail_result.error,
            )
            results.append(fail_result)
        else:
            results.append(item)

    def _is_success(r: RequestResult) -> bool:
        if args.accept_only:
            return r.accepted and r.ok
        return r.final_received and r.ok

    completed = sum(1 for r in results if _is_success(r))
    failed = args.concurrency - completed
    stats = LoadTestStats(
        total=args.concurrency,
        completed=completed,
        failed=failed,
        elapsed_s=elapsed,
        accept_ms=[r.accept_ms for r in results if r.accept_ms > 0],
        total_ms=[r.total_ms for r in results if r.total_ms > 0],
    )

    logger.info("\n[result] %s", stats.summary())

    logger.info("\n[requests] 各请求路由参数汇总（按 idx 排序）:")
    for r in sorted(results, key=lambda x: x.index):
        status = "ok" if _is_success(r) else "fail"
        logger.info(
            "[requests] idx=%02d shard=%d status=%s final=%s total_ms=%.0f accept_ms=%.0f "
            "session_id=%s req_id=%s group_id=%s bot_id=%s user_id=%s",
            r.index,
            r.shard,
            status,
            r.final_received,
            r.total_ms,
            r.accept_ms,
            r.session_id,
            r.req_id,
            r.group_id,
            r.bot_id,
            r.user_id,
        )

    shard_counts: dict[int, list[RequestResult]] = {s: [] for s in range(args.shards)}
    for r in results:
        shard_counts[r.shard].append(r)
    for shard in range(args.shards):
        shard_ok = sum(1 for r in shard_counts[shard] if _is_success(r))
        logger.info("[shard] shard=%d completed=%d/%d", shard, shard_ok, len(shard_counts[shard]))

    return 0 if failed == 0 else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gateway Runtime 并发 chat.send 压测（经 WebChannel /ws 验证 AgentServer 池）",
    )
    p.add_argument("--host", default="127.0.0.1", help="Gateway 主机，默认 127.0.0.1")
    p.add_argument("--ws-path", default="/ws", help="WebSocket 路径，默认 /ws")
    p.add_argument(
        "--content",
        default=_DEFAULT_CONTENT,
        help="用户消息正文（同时写入 content 与 query）",
    )
    p.add_argument("--bot-id", default="bot_main", help="企业策略 bot_id")
    p.add_argument("--user-id-prefix", default="loadtest_user", help="user_id 前缀，实际为 {prefix}_{idx:02d}")
    p.add_argument("--mode", default="agent.plan", help="运行模式，如 agent.plan")
    p.add_argument(
        "--concurrency",
        "--total",
        dest="concurrency",
        type=int,
        default=30,
        metavar="N",
        help="总并发请求数，默认 30（须能被 --shards 整除）",
    )
    p.add_argument(
        "--shards",
        "--agent-shards",
        dest="shards",
        type=int,
        default=3,
        metavar="K",
        help="均匀分布到的 AgentServer 数量，默认 3（每个接收 concurrency/shards 个请求）",
    )
    p.add_argument(
        "--group-prefix",
        default="loadtest",
        help="group_id 前缀，分片 i 共用 {prefix}_s{i}（如 loadtest_s0 / loadtest_s1 / loadtest_s2）",
    )
    p.add_argument(
        "--accept-timeout",
        type=float,
        default=60.0,
        help="等待 chat.send 被接受的最长时间（秒），默认 60",
    )
    p.add_argument(
        "--accept-only",
        action="store_true",
        help="仅等待 chat.send 被接受，不等待任务完成（默认会等到 usage_summary / final / processing idle）",
    )
    p.add_argument(
        "--no-auto-allow",
        action="store_true",
        help="禁用自动响应 Agent 权限/追问（默认自动选「总是允许」）",
    )
    p.add_argument(
        "--ws-event-log",
        action="store_true",
        help="打印每路 WS event 名（frame.event / payload.event_type），排查权限事件是否到达",
    )
    p.add_argument(
        "--final-timeout",
        type=float,
        default=7200.0,
        help="等待任务完成的最长时间（秒），默认 7200",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--web-port", type=int, help="Gateway WebChannel 端口")
    src.add_argument("--provision-json", type=Path, help="provision-local 响应 JSON（读取 data.ports.web）")
    src.add_argument("--ws-url", help="完整 WebSocket URL，如 ws://host:19001/ws")
    args = p.parse_args()
    args.auto_allow = not args.no_auto_allow
    return args


def main() -> int:
    _configure_cli_logging()
    try:
        import websockets  # noqa: F401
    except ImportError:
        logger.error(
            "缺少 websockets，请在 jiuwenclaw 仓库根目录执行: uv sync 或 pip install websockets"
        )
        return 1

    args = _parse_args()
    try:
        return asyncio.run(_run_loadtest(args))
    except KeyboardInterrupt:
        return 130
    except ValueError as err:
        logger.error("[invalid-args] %s", err)
        return 2
    except OSError as connect_err:
        logger.error("[connect-failed] %s", connect_err)
        logger.error(
            "请确认 Gateway 已启动，且 --web-port / --provision-json / --ws-url 指向可访问的 WebChannel。"
        )
        return 1
    except Exception as err:
        logger.error("[failed] %s", err)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
