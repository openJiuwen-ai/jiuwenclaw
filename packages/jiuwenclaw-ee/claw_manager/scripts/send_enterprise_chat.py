#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配置完成后，经 Gateway WebChannel 发送一条用户聊天请求（验证企业策略 / 模型）。

依赖：主仓库已安装 ``websockets``（``uv sync`` 或 ``pip install websockets``）。

典型用法（先完成 provision-local + Manager 企业配置，记下 provision 返回的 web 端口）::

    # 一行（Windows PowerShell / cmd 均适用；勿用 bash 的 \\ 续行；端口放末尾便于修改）
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/send_enterprise_chat.py --group-id g_demo_sales --bot-id bot_main --user-id alice --content "用一句话说明当前使用的模型" --web-port 19234

也可把 provision-local 的 JSON 响应存为文件后自动读端口（同样放末尾）::

    uv run python .../send_enterprise_chat.py --group-id g_demo_sales --bot-id bot_main --user-id alice --provision-json provision.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 websockets，请在 jiuwenclaw 仓库根目录执行: uv sync 或 pip install websockets"
    ) from exc


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


def _browser_origin_header(host: str) -> dict[str, str]:
    """WebChannel 默认校验 Origin；Python websockets 客户端需模拟浏览器本机 Origin。"""
    origin_host = host if host in ("127.0.0.1", "localhost") else "127.0.0.1"
    return {"Origin": f"http://{origin_host}"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="经 Gateway /ws 发送 chat.send（企业路由参数在 params 内）")
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
    src.add_argument("--web-port", type=int, help="Gateway WebChannel 端口（provision 返回的 ports.web）")
    src.add_argument(
        "--provision-json",
        type=Path,
        help="provision-local 响应 JSON 文件路径（读取 data.ports.web）",
    )
    return p.parse_args()


async def _recv_json(ws: websockets.WebSocketClientProtocol, timeout: float) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(f"非 JSON 对象: {raw!r}")
    return data


async def _run_chat(args: argparse.Namespace) -> int:
    if args.provision_json is not None:
        web_port = _load_web_port_from_provision(args.provision_json)
    else:
        web_port = int(args.web_port)

    session_id = (args.session_id or "").strip() or f"sess_{uuid.uuid4().hex[:12]}"
    req_id = f"req_{uuid.uuid4().hex[:12]}"
    ws_url = f"ws://{args.host}:{web_port}{args.ws_path}"

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

    print(f"[connect] {ws_url}")
    print(f"[send] session_id={session_id} group_id={args.group_id} bot_id={args.bot_id} user_id={args.user_id}")
    print(f"[send] content={args.content!r}")

    final_text: str | None = None
    deadline = asyncio.get_running_loop().time() + args.timeout

    ws_headers = _browser_origin_header(args.host)
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
                print("[timeout] 未在时限内收到 chat.final", file=sys.stderr)
                return 2

            ftype = frame.get("type")

            if ftype == "res" and frame.get("id") == req_id:
                ok = bool(frame.get("ok"))
                payload = frame.get("payload") or {}
                print(f"[res] ok={ok} payload={json.dumps(payload, ensure_ascii=False)}")
                if not ok:
                    err = frame.get("error") or payload.get("error") or frame
                    print(f"[error] {err}", file=sys.stderr)
                    return 1
                accepted = bool(payload.get("accepted", True))
                if not accepted:
                    print("[error] chat.send 未被接受", file=sys.stderr)
                    return 1
                continue

            if ftype == "event":
                event = frame.get("event")
                payload = frame.get("payload") or {}
                if event == "connection.ack":
                    print(f"[event] connection.ack")
                    continue
                if event == "chat.delta" and args.print_deltas:
                    chunk = payload.get("content") or payload.get("text") or ""
                    if chunk:
                        print(chunk, end="", flush=True)
                    continue
                if event == "chat.final":
                    if args.print_deltas:
                        print()
                    final_text = str(payload.get("content") or payload.get("text") or "")
                    print(f"[event] chat.final session_id={payload.get('session_id', session_id)}")
                    print(final_text or "(empty)")
                    return 0
                if event == "chat.error":
                    print(f"[event] chat.error {json.dumps(payload, ensure_ascii=False)}", file=sys.stderr)
                    return 1
                # 其它事件仅 debug 级别打印
                print(f"[event] {event} {json.dumps(payload, ensure_ascii=False)[:200]}")
                continue

            print(f"[frame] {json.dumps(frame, ensure_ascii=False)[:500]}")

        if not accepted:
            print("[timeout] 未收到 chat.send 的 res 确认", file=sys.stderr)
            return 2
        print("[timeout] 已接受请求但未收到 chat.final", file=sys.stderr)
        return 2


def main() -> None:
    args = _parse_args()
    try:
        code = asyncio.run(_run_chat(args))
    except KeyboardInterrupt:
        code = 130
    except OSError as exc:
        print(f"[connect-failed] {exc}", file=sys.stderr)
        print("请确认 Gateway 已启动，且 --web-port 与 provision 返回的 ports.web 一致。", file=sys.stderr)
        code = 1
    except Exception as exc:
        print(f"[failed] {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
