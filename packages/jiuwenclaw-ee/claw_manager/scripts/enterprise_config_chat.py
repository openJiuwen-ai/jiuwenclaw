#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配置完成后，经 Gateway WebChannel 发送一条用户聊天请求（验证 AgentServer 侧企业策略 / 模型 / Embedding / Skill / 扩展配置）。

依赖：主仓库已安装 ``websockets``（``uv sync`` 或 ``pip install websockets``）。

典型用法（先完成 provision-local + ``enterprise_config_demo_data_config.py``，记下 provision 返回的 web 端口）::

    # alice → default/vision=M3，embedding=B3；Skill W1；扩展 E3
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/enterprise_config_chat.py \\
        --group-id g_demo_sales --bot-id bot_main --user-id alice \\
        --content "用一句话说明当前使用的模型" --web-port 19234

    # bob → default=M5，vision=M2，embedding=B2；Skill W1+W2；扩展 E1+E2
    uv run python .../enterprise_config_chat.py \\
        --group-id g_demo_sales --bot-id bot_main --user-id bob --web-port 19234

    # g_unknown → 四模型槽位均为 M1，embedding=B1；Skill W3；扩展 E4
    uv run python .../enterprise_config_chat.py \\
        --group-id g_unknown --bot-id bot_main --user-id bob --web-port 19234

``service_config`` 由 Gateway Runtime 加载，请用 ``enterprise_runtime_service_config.py`` 验证（见数据模型 §3.2）。

也可把 provision-local 的 JSON 响应存为文件后自动读端口（同样放末尾）::

    uv run python .../enterprise_config_chat.py \\
        --group-id g_demo_sales --bot-id bot_main --user-id alice \\
        --provision-json provision.json

连接远程 Gateway WebChannel::

    uv run python .../enterprise_config_chat.py \\
        --group-id g_demo_sales --bot-id bot_main --user-id alice \\
        --ws-url ws://10.0.0.1:19001/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_stream_logger = logging.getLogger(f"{__name__}.stream")


def _configure_cli_logging() -> None:
    """INFO 走 stdout，ERROR 走 stderr，便于联调脚本对照终端习惯。"""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(message)s")
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.setFormatter(fmt)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    root.addHandler(out)
    root.addHandler(err)

    _stream_logger.handlers.clear()
    _stream_logger.propagate = False
    _stream_logger.setLevel(logging.INFO)
    stream_out = logging.StreamHandler(sys.stdout)
    stream_out.setLevel(logging.INFO)
    stream_out.setFormatter(fmt)
    stream_out.terminator = ""
    _stream_logger.addHandler(stream_out)


def _write_stream(text: str) -> None:
    """流式输出模型片段（非结构化日志，保持单行连续打印）。"""
    _stream_logger.info(text)


# enterprise_config_demo_data_config.py 写入后的预期槽位（便于联调对照 AgentServer 日志）
_MODEL_SLOT_KEYS = ("default_model", "vision_model", "video_model", "audio_model")

_DEMO_ENTERPRISE_EXPECTATIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("g_demo_sales", "alice"): {
        "model_slots": {
            "default_model": "M3 VIP-加强对话 (gpt-5)",
            "vision_model": "M3 VIP-加强对话 (gpt-5)",
            "video_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B3 VIP 向量模型 (text-embedding-3-large, dimensions=3072)",
        "skill_whitelist": "W1 销售组-天气 Skill",
        "extension_config": "E3 Agent Server 错误恢复（覆盖服务 E1+E2）",
        "note": "2.7.1 覆盖 2.6.1 的 default/vision/embedding；video/audio 由 2.8 回填 M1",
    },
    ("g_demo_sales", "bob"): {
        "model_slots": {
            "default_model": "M5 销售组映射专用 (gpt-4o-group-map)",
            "vision_model": "M2 销售组-标准型 (gpt-4o)",
            "video_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B2 销售组向量模型 (text-embedding-3-large, dimensions=1536)",
        "skill_whitelist": "W1 销售组-天气 Skill + W2 销售组-CRM Skill（继承 2.6.1）",
        "extension_config": "E1 Gateway 请求前鉴权 + E2 Gateway 请求后日志（继承 2.6.1）",
        "note": "default=M5（2.7.2+2.9.2）；vision/embedding 继承 2.6.1；video/audio 由 2.8 回填 M1",
    },
    ("g_unknown", "bob"): {
        "model_slots": {
            "default_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
            "vision_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
            "video_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
            "audio_model": "M1 全局兜底-经济型 (gpt-4o-mini)",
        },
        "embedding_model": "B1 全局兜底向量模型 (text-embedding-3-small)",
        "skill_whitelist": "W3 全局兜底 Skill",
        "extension_config": "E4 Gateway 定时清理",
        "note": "未命中服务策略，四个模型槽位与 embedding 均走 2.8 全局兜底",
    },
}


def _log_demo_expectation(group_id: str, user_id: str) -> None:
    hint = _DEMO_ENTERPRISE_EXPECTATIONS.get((group_id, user_id))
    if hint is None:
        hint = _DEMO_ENTERPRISE_EXPECTATIONS.get((group_id, "bob"))
    if hint is None:
        return
    slots = hint.get("model_slots") or {}
    slot_parts = [f"{k}={slots.get(k, '未配置')}" for k in _MODEL_SLOT_KEYS]
    logger.info("[expect] 演示 seed 预期模型槽位: %s", "; ".join(slot_parts))
    logger.info("[expect] embedding_model=%s", hint["embedding_model"])
    if hint.get("note"):
        logger.info("[expect] 说明: %s", hint["note"])
    logger.info(
        "[expect] skill_whitelist=%s; extension_config=%s",
        hint["skill_whitelist"],
        hint["extension_config"],
    )
    logger.info(
        "[expect] 可在 AgentServer 日志中查找 "
        "[enterprise_config] loaded enterprise config 确认各槽位 template_ref"
    )
    logger.info(
        "[expect] service_config 由 Gateway Runtime 验证，见 enterprise_runtime_service_config.py"
    )


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
    """解析 WebSocket 目标：--ws-url 优先，否则由 host + web 端口 + path 拼装。"""
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
    """WebChannel 默认校验 Origin；Python websockets 客户端需模拟浏览器 Origin。"""
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="经 Gateway /ws 发送 chat.send（企业路由参数在 params 内）"
    )
    p.add_argument("--host", default="127.0.0.1", help="Gateway 主机，默认 127.0.0.1")
    p.add_argument("--ws-path", default="/ws", help="WebSocket 路径，默认 /ws")
    p.add_argument("--session-id", default="", help="会话 ID；留空则由 Gateway 自动生成")
    p.add_argument(
        "--content",
        default="你好，请用一句话回复，并说明你当前使用的模型名称。",
        help="用户消息正文（会同时写入 content 与 query）",
    )
    p.add_argument("--group-id", default="g_demo_sales", help="企业策略 group_id")
    p.add_argument("--bot-id", default="bot_main", help="企业策略 bot_id")
    p.add_argument("--user-id", default="alice", help="企业策略 user_id / agent_id 匹配")
    p.add_argument("--mode", default="agent.plan", help="运行模式，如 agent.plan")
    p.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="等待 chat.final 的最长时间（秒）",
    )
    p.add_argument(
        "--print-deltas",
        action="store_true",
        help="打印 chat.delta 流式片段（默认只打印 final）",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--web-port", type=int, help="Gateway WebChannel 端口（provision 返回的 ports.web）"
    )
    src.add_argument(
        "--provision-json",
        type=Path,
        help="provision-local 响应 JSON 文件路径（读取 data.ports.web）",
    )
    src.add_argument(
        "--ws-url",
        help="完整 WebSocket URL（连接远程 Gateway），如 ws://host:19001/ws 或 wss://host/ws",
    )
    return p.parse_args()


async def _recv_json(ws: Any, timeout: float) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(f"非 JSON 对象: {raw!r}")
    return data


async def _run_chat(args: argparse.Namespace) -> int:
    import websockets

    ws_url = _resolve_ws_url(args)

    session_id = (args.session_id or "").strip() or f"sess_{uuid.uuid4().hex[:12]}"
    req_id = f"req_{uuid.uuid4().hex[:12]}"

    params: dict[str, Any] = {
        "session_id": session_id,
        "content": args.content,
        "query": args.content,
        "mode": args.mode,
        "group_id": args.group_id,
        "bot_id": args.bot_id,
        "user_id": args.user_id,
    }

    req = {
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "params": params,
    }

    logger.info("[connect] %s", ws_url)
    logger.info(
        "[send] session_id=%s group_id=%s bot_id=%s user_id=%s",
        session_id,
        args.group_id,
        args.bot_id,
        args.user_id,
    )
    _log_demo_expectation(args.group_id, args.user_id)
    logger.info("[send] content=%r", args.content)

    deadline = asyncio.get_running_loop().time() + args.timeout

    ws_headers = _browser_origin_header(ws_url)
    async with websockets.connect(
        ws_url,
        open_timeout=15,
        additional_headers=ws_headers,
    ) as ws:
        await ws.send(json.dumps(req, ensure_ascii=False))

        accepted = False
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            try:
                frame = await _recv_json(ws, remaining)
            except asyncio.TimeoutError:
                logger.error("[timeout] 未在时限内收到 chat.final")
                return 2

            ftype = frame.get("type")

            if ftype == "res" and frame.get("id") == req_id:
                ok = bool(frame.get("ok"))
                payload = frame.get("payload") or {}
                logger.info(
                    "[res] ok=%s payload=%s",
                    ok,
                    json.dumps(payload, ensure_ascii=False),
                )
                if not ok:
                    err = frame.get("error") or payload.get("error") or frame
                    logger.error("[error] %s", err)
                    return 1
                accepted = bool(payload.get("accepted", True))
                if not accepted:
                    logger.error("[error] chat.send 未被接受")
                    return 1
                continue

            if ftype == "event":
                event = frame.get("event")
                payload = frame.get("payload") or {}
                if event == "connection.ack":
                    logger.info("[event] connection.ack")
                    continue
                if event == "chat.delta" and args.print_deltas:
                    chunk = payload.get("content") or payload.get("text") or ""
                    if chunk:
                        _write_stream(chunk)
                    continue
                if event == "chat.final":
                    if args.print_deltas:
                        _write_stream("\n")
                    final_text = str(payload.get("content") or payload.get("text") or "")
                    logger.info(
                        "[event] chat.final session_id=%s",
                        payload.get("session_id", session_id),
                    )
                    logger.info("%s", final_text or "(empty)")
                    return 0
                if event == "chat.error":
                    logger.error(
                        "[event] chat.error %s",
                        json.dumps(payload, ensure_ascii=False),
                    )
                    return 1
                logger.info(
                    "[event] %s %s",
                    event,
                    json.dumps(payload, ensure_ascii=False)[:200],
                )
                continue

            logger.info("[frame] %s", json.dumps(frame, ensure_ascii=False)[:500])

        if not accepted:
            logger.error("[timeout] 未收到 chat.send 的 res 确认")
            return 2
        logger.error("[timeout] 已接受请求但未收到 chat.final")
        return 2


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
        return asyncio.run(_run_chat(args))
    except KeyboardInterrupt:
        return 130
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
