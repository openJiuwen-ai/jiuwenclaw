#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel Trace HTTP 端点客户端：发 input，打印返回的 trace 树。

前提：AgentServer 已开启 trace HTTP 端点在运行。启动方式见本文件末尾注释。

用法：
  python scripts/trace_http_client.py --input "用一句话介绍 jiuwenswarm"
  python scripts/trace_http_client.py --input "..." --url http://127.0.0.1:18093/run --timeout 240
  echo "用一句话介绍 jiuwenswarm" | python scripts/trace_http_client.py

启动服务端（另开终端）：
  export JIUWENSWARM_TRACE_HTTP_ENABLED=true
  export JIUWENSWARM_TRACE_HTTP_PORT=18093          # 可选，默认 18093
  export OTEL_SQLITE_DB_PATH=/tmp/traces.db         # 可选，指定 sqlite 路径
  .venv/bin/python -m jiuwenswarm.server.app_agentserver --port 18092
  # 看到日志 "[TraceHttpServer] listening http://127.0.0.1:18093/run" 即就绪
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _walk(span: dict[str, Any], depth: int) -> None:
    name = span.get("name", "?")
    dur = (span.get("duration_ns") or 0) / 1_000_000
    status = span.get("status_code") or "-"
    attrs = span.get("attributes") or {}
    rid = attrs.get("jiuwenclaw.request.id") or attrs.get("jiuwenswarm.request.id") or ""
    model = attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model") or ""
    extra = ""
    if rid:
        extra += f"  req={rid}"
    if model:
        extra += f"  model={model}"
    print(f"{'  ' * depth}{name}  [{status}] {dur:.0f}ms{extra}")
    for child in span.get("children", []) or []:
        _walk(child, depth + 1)


def main() -> int:
    p = argparse.ArgumentParser(description="调用 jiuwenswarm trace HTTP 端点并打印 trace 树")
    p.add_argument("--url", default="http://127.0.0.1:18093/run", help="trace HTTP 端点地址")
    p.add_argument("--input", help="用户输入；不传则从 stdin 读")
    p.add_argument("--mode", default="agent.plan", help="运行模式")
    p.add_argument("--timeout", type=int, default=240, help="超时秒数")
    # 按次指定模型（可选；带齐 apibase+key+model 即覆盖服务端 config 默认）
    p.add_argument("--apibase", help="模型 OpenAI 兼容端点（按次覆盖）")
    p.add_argument("--key", help="模型 API Key（按次覆盖）")
    p.add_argument("--model", help="模型名（按次覆盖）")
    p.add_argument("--modelprovider", default="OpenAI",
                   help="模型 provider（openai/InferenceAffinity 等）")
    args = p.parse_args()

    text = args.input
    if text is None:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        print("error: empty input", file=sys.stderr)
        return 2

    # 延迟 import，避免 --help 时拖慢
    import urllib.request
    import urllib.error

    body: dict[str, Any] = {"input": text, "mode": args.mode, "timeout": args.timeout}
    if args.apibase and args.key and args.model:
        body["apibase"] = args.apibase
        body["key"] = args.key
        body["model"] = args.model
        body["modelprovider"] = args.modelprovider
    elif args.apibase or args.key or args.model:
        print("error: 按次指定模型需同时提供 --apibase --key --model", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        args.url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout + 10) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"连接失败: {e}\n确认 AgentServer 已启动且 JIUWENSWARM_TRACE_HTTP_ENABLED=true", file=sys.stderr)
        return 1

    print(f"ok         : {data.get('ok')}")
    print(f"request_id : {data.get('request_id')}")
    print(f"session_id : {data.get('session_id')}")
    print(f"trace_id   : {data.get('trace_id')}")
    print(f"error      : {data.get('error')}")
    print()
    trace = data.get("trace") or []
    if not trace:
        print("(trace 为空：telemetry 可能未启用，或 force_flush 之前 span 未落盘)")
        return 0 if data.get("ok") else 1
    for root in trace:
        _walk(root, 0)
    # 统计 span 总数
    def count(spans):
        n = 0
        for s in spans:
            n += 1 + count(s.get("children", []) or [])
        return n
    print(f"\nspan 总数: {count(trace)}")
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
