#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""演示如何远程调用 jiuwenswarm agent，覆盖尽可能多的可配置参数。

调用方式总览（当前 dolores 分支）：
  1. 直连 AgentServer WebSocket（本脚本采用）—— 端口默认 18092，最低延迟、参数最全。
     服务入口：jiuwenswarm/server/app_agentserver.py:207
     连接处理：jiuwenswarm/server/agent_ws_server.py:_connection_handler
  2. 经 Gateway WebSocket —— 外部渠道（IM/Web/TUI）连 Gateway，Gateway 作为 WS client 反连 AgentServer。
     Gateway：jiuwenswarm/gateway/app_gateway.py；CLI 客户端：jiuwenswarm/cli/gateway_client.py
  3. CLI / TUI —— `jiuwenswarm` 命令行交互式调用（走 Gateway）。
  4. 进程内 SDK —— 直接实例化 AgentWebSocketServer 的 agent_manager 调 agent.process_message（无 WS，适合同进程嵌入）。

本脚本走方式 1。流程：
  连上 -> 服务端先发 connection.ack -> 客户端发一条 chat.send 帧 -> 收流式 chunk 直到 is_complete。

模型配置（apibase/key/model/modelprovider）说明：
  这些是【服务端配置级】，不在每次请求里。AgentServer 启动时从 config.yaml
  (jiuwenswarm/resources/config.yaml:190) 或环境变量加载：
    API_BASE / API_KEY / MODEL_NAME / MODEL_PROVIDER
  如需【按次指定模型】（RL 场景），见 trace_http.py /run 的 apibase/key/model 参数。

用法示例：
  # 1. 先启动 AgentServer（另一终端，配好 API_BASE 等环境变量）
  #    python -m jiuwenswarm.server.app_agentserver --port 18092
  # 2. 运行本脚本
  python scripts/agent_call_demo.py --query "用一句话介绍 jiuwenswarm" --mode agent.plan
  # 非流式
  python scripts/agent_call_demo.py --query "..." --no-stream
  # 指定 session / 项目目录
  python scripts/agent_call_demo.py --query "..." --session-id rl-001 --project-dir /path/to/proj
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from typing import Any


def _connect_ws(url: str):
    """优先用 legacy client（与仓库 jiuwenswarm/cli/gateway_client.py 一致）。"""
    try:
        from websockets.legacy.client import connect as legacy_connect
        return legacy_connect(url, close_timeout=2.0, max_size=8 * 2**20)
    except ImportError:
        import websockets
        return websockets.connect(url, close_timeout=2.0, max_size=8 * 2**20)


def build_request_frame(args: argparse.Namespace) -> dict[str, Any]:
    """构造一条 legacy chat.send 请求帧（AgentServer._payload_to_request 可解析）。"""
    params: dict[str, Any] = {
        # --- 核心输入 ---
        # query：用户原始 query，等价于 AgentRequest.params["query"]
        "query": args.query,
        # mode：运行模式。可选值见 resolve_agent_request_mode (agent_ws_server.py:318)
        #   agent.plan / agent.normal / code.plan / code.normal / code.team / team / team.plan
        "mode": args.mode,
    }

    # --- 工作目录相关（resolve_request_project_dir 用到，agent_ws_server.py:275）---
    if args.project_dir:
        # project_dir：稳定的项目身份，用于 agent 构建（新客户端首选）
        params["project_dir"] = args.project_dir
    if args.cwd:
        # cwd：动态工作目录（legacy 客户端常用，project_dir 缺省时回退为项目身份）
        params["cwd"] = args.cwd
    if args.trusted_dirs:
        # trusted_dirs：可信目录白名单（project_dir/cwd 都缺省时取第一项作项目身份）
        params["trusted_dirs"] = list(args.trusted_dirs)

    # --- 会话/渠道标识 ---
    # channel_id：渠道标识，决定 agent 实例归属；默认 "web"
    channel_id = args.channel_id
    # session_id：会话 id，trace 按 session 解析；不传由服务端生成
    session_id = args.session_id or f"demo-{int(time.time())}"

    # 透传额外 params（高级用法，如 plan_entry_source / question / turn_index 等）
    if args.extra_params:
        params.update(json.loads(args.extra_params))

    frame: dict[str, Any] = {
        # request_id：本次请求唯一 id；同时作为 trace_id（parse_session 以此切段）
        "request_id": args.request_id or f"req-{uuid.uuid4().hex[:12]}",
        "channel_id": channel_id,
        "session_id": session_id,
        # req_method：chat.send / chat.resume / chat.user_answer / chat.interrupt
        #   见 jiuwenswarm/common/schema/message.py:14
        "req_method": args.req_method,
        # is_stream：True 走 _handle_stream（逐 chunk），False 走 _handle_unary（单条响应）
        "is_stream": not args.no_stream,
        "timestamp": time.time(),
        "params": params,
        # metadata：透传元数据（project_dir/cwd 也可放这里，见 resolve_request_project_dir）
        "metadata": args.metadata or {},
        # enable_memory：是否启用记忆系统（None 走默认）
        "enable_memory": args.enable_memory,
    }
    # 过滤 None，避免序列化噪音
    frame["metadata"] = {k: v for k, v in frame["metadata"].items() if v is not None}
    if frame["enable_memory"] is None:
        frame.pop("enable_memory")
    return frame


async def _recv_json(ws: Any) -> dict[str, Any]:
    raw = await ws.recv()
    return json.loads(raw)


def _chunk_text(frame: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    """从一条响应帧里提取可读文本与 is_complete 标志。

    服务端用 E2A 信封（encode_agent_chunk_for_wire / encode_agent_response_for_wire），
    payload.event_type 标识事件类型（见 message.py:205-226）：
      chat.delta / chat.final / chat.reasoning / chat.tool_call / chat.tool_result /
      chat.error / chat.usage_metadata / keepalive / chat.session_result ...
    """
    # E2A 信封：响应体可能在 response.payload 或顶层 payload
    payload = frame.get("payload") or {}
    if not payload and isinstance(frame.get("response"), dict):
        payload = frame["response"].get("payload") or {}

    event_type = payload.get("event_type") or payload.get("type") or ""
    is_complete = bool(frame.get("is_complete") or payload.get("is_complete")
                       or frame.get("status") == "completed")

    text = ""
    if event_type == "keepalive":
        text = ""
    elif event_type in ("chat.delta", "chat.final"):
        text = payload.get("content") or payload.get("text") or ""
    elif event_type == "chat.reasoning":
        text = payload.get("content") or ""
    elif event_type == "chat.error":
        text = f"[error] {payload.get('content') or payload.get('error') or ''}"
        is_complete = True
    elif event_type in ("chat.tool_call", "chat.tool_result", "chat.subtask_update",
                        "chat.symphony_status", "chat.processing_status", "chat.session_result"):
        text = ""  # 结构化事件，整体打印 payload 供调试
    return text, is_complete, payload


async def call_agent(args: argparse.Namespace) -> int:
    url = args.agent_url
    frame = build_request_frame(args)

    print(f"[demo] 连接 AgentServer: {url}")
    print(f"[demo] 发送请求: req_method={frame['req_method']} is_stream={frame['is_stream']} "
          f"session={frame['session_id']} channel={frame['channel_id']}")
    print(f"[demo] params={json.dumps(frame['params'], ensure_ascii=False)}")
    print("-" * 60)

    async with _connect_ws(url) as ws:
        # 1) 服务端先发 connection.ack
        ack = await _recv_json(ws)
        if ack.get("event") != "connection.ack":
            print(f"[demo] 警告：期望 connection.ack，收到 {ack}")
        else:
            print(f"[demo] 收到 connection.ack: {ack.get('payload')}")

        # 2) 发送 chat.send 帧
        await ws.send(json.dumps(frame, ensure_ascii=False))

        # 3) 收响应
        if frame["is_stream"]:
            chunk_count = 0
            final_text = ""
            while True:
                resp = await _recv_json(ws)
                chunk_count += 1
                text, done, payload = _chunk_text(resp)
                et = payload.get("event_type", "")
                if et in ("chat.delta", "chat.reasoning"):
                    print(text, end="", flush=True)
                    if et == "chat.delta":
                        final_text += text
                elif et == "chat.final":
                    final_text = text or final_text
                    print(f"\n[demo] final: {final_text}")
                elif et == "keepalive":
                    pass  # 空闲心跳，忽略
                else:
                    # 结构化事件（tool_call/tool_result/error/session_result 等）整体打印
                    print(f"\n[{et or 'chunk'}] {json.dumps(payload, ensure_ascii=False)[:400]}")
                if done or et in ("chat.error", "chat.session_result"):
                    # session_result / error 视为结束
                    if et == "chat.session_result":
                        pass
                    break
            print("\n" + "-" * 60)
            print(f"[demo] 完成，共收到 {chunk_count} 个 chunk")
        else:
            # 非流式：收一条 E2AResponse 线 JSON
            resp = await _recv_json(ws)
            payload = resp.get("payload") or (resp.get("response") or {}).get("payload") or {}
            ok = resp.get("ok", True)
            print(f"[demo] ok={ok}")
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
            if not ok:
                return 1
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="远程调用 jiuwenswarm agent 的演示脚本（直连 AgentServer WebSocket）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- 连接 ---
    p.add_argument("--agent-url", default=os.getenv("JW_AGENT_URL", "ws://127.0.0.1:18092"),
                   help="AgentServer WebSocket 地址")
    p.add_argument("--token", default=os.getenv("JW_AGENT_TOKEN", ""),
                   help="鉴权 token（直连 AgentServer 通常不需要；Gateway HTTP/扩展层可能需要）")

    # --- 核心请求 ---
    p.add_argument("--query", default=None, help="用户输入 query（AgentRequest.params['query']）")
    p.add_argument("--req-method", default="chat.send",
                   choices=["chat.send", "chat.resume", "chat.user_answer", "chat.interrupt"],
                   help="请求方法（ReqMethod 枚举值）")
    p.add_argument("--mode", default=os.getenv("JW_MODE", "agent.plan"),
                   help="运行模式：agent.plan / agent.normal / code.plan / code.normal / code.team / team / team.plan")
    p.add_argument("--no-stream", action="store_true",
                   help="非流式（走 _handle_unary，返回单条响应）")

    # --- 会话与渠道 ---
    p.add_argument("--channel-id", default=os.getenv("JW_CHANNEL_ID", "web"),
                   help="渠道 id，决定 agent 实例归属")
    p.add_argument("--session-id", default=os.getenv("JW_SESSION_ID", ""),
                   help="会话 id（trace 按此解析；不传自动生成）")
    p.add_argument("--request-id", default=os.getenv("JW_REQUEST_ID", ""),
                   help="请求 id（同时作 trace_id；不传自动生成）")
    p.add_argument("--chat-id", default=os.getenv("JW_CHAT_ID", ""),
                   help="chat id（多轮对话分组）")

    # --- 工作目录 ---
    p.add_argument("--project-dir", default=os.getenv("JW_PROJECT_DIR", ""),
                   help="稳定项目身份目录（agent 构建用，新客户端首选）")
    p.add_argument("--cwd", default=os.getenv("JW_CWD", ""),
                   help="动态工作目录（project_dir 缺省时回退为项目身份）")
    p.add_argument("--trusted-dirs", nargs="*", default=None,
                   help="可信目录白名单（列表）")

    # --- 记忆与元数据 ---
    p.add_argument("--enable-memory", action="store_true", default=None,
                   help="启用记忆系统（不传走服务端默认）")
    p.add_argument("--metadata", default=None,
                   help="元数据 JSON（project_dir/cwd 等也可放这里）")

    # --- 高级透传 ---
    p.add_argument("--extra-params", default=None,
                   help="额外 params JSON，透传到 AgentRequest.params（如 "
                        '{"plan_entry_source":"slash_command","question":"...","turn_index":0}）')

    # --- 模型配置（仅提示，实际在服务端 config/环境变量）---
    p.add_argument("--show-model-env", action="store_true",
                   help="只打印服务端模型配置所需的环境变量后退出（这些是配置级，不在请求里）")

    args = p.parse_args()
    if not args.show_model_env and not args.query:
        p.error("--query 是必需的（除非用 --show-model-env）")
    if args.metadata:
        args.metadata = json.loads(args.metadata)
    else:
        args.metadata = {}
    return args


def _print_model_env() -> None:
    print("模型配置为【服务端配置级】，启动 AgentServer 前设置以下环境变量（或在 config.yaml 配置）：")
    print("  API_BASE        模型 OpenAI 兼容端点")
    print("  API_KEY         模型 API Key")
    print("  MODEL_NAME      模型名（默认 xiaomi/mimo-v2-omni）")
    print("  MODEL_PROVIDER  模型 provider（client_provider，须为 ProviderType 枚举值）")
    print("对应 config: jiuwenswarm/resources/config.yaml:190 (models.defaults.model_client_config)")
    print("服务端读取处: jiuwenswarm/server/agent_ws_server.py:4516-4519")
    print("按次指定模型（RL 用）：请求带 apibase/key/model/modelprovider")


def main() -> None:
    args = parse_args()
    if args.show_model_env:
        _print_model_env()
        return
    raise SystemExit(asyncio.run(call_agent(args)))


if __name__ == "__main__":
    main()
