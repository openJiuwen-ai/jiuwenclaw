#!/usr/bin/env python3
"""Standalone probe for invoke plugin skill (single WS interface, no LLM).

Two targets:

  relay   (default, product path)
          ws://127.0.0.1:19690   — CloudPluginClient 直连本地 CloudWsRelay
          不拼接路径。桌面小艺 Work 需已启动。

  mcp-run (agent-runtime-service 单接口对照)
          {base}/agent-runtime-service/v1/mcp/run
          base 来自 --base / AGENT_RUNTIME_MCP_RUN / AGENT_RUNTIME_BASEURL。
          若环境变量本身已含 /mcp/run，则不再拼接。

不依赖小艺 Work、直连云端文生图请用：
  python scripts/standalone_invoke_image.py --dry-run
  python scripts/standalone_invoke_image.py --prompt "一只柯基在滑板上"

Usage (Git Bash, repo: jiuwenswarm):

  python scripts/probe_invoke_plugin.py --dry-run
  python scripts/probe_invoke_plugin.py --prompt "一只柯基在滑板上"
  python scripts/probe_invoke_plugin.py --via-tool
  python scripts/probe_invoke_plugin.py --target mcp-run --base wss://host:port --dry-run
  python scripts/probe_invoke_plugin.py --url ws://127.0.0.1:19690 --timeout 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

_MCP_RUN_PATH = "/agent-runtime-service/v1/mcp/run"
_DEFAULT_RELAY = "ws://127.0.0.1:19690"
_ATOMIC_BUNDLE = "com.atomicservice.5765880207845681341"
_DEFAULT_PROMPT = "一只柯基在滑板上"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _http_base_to_ws(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[8:]
    if base.startswith("http://"):
        return "ws://" + base[7:]
    if base.startswith(("ws://", "wss://")):
        return base
    return f"ws://{base}"


def _already_mcp_run(url: str) -> bool:
    return "/mcp/run" in url.rstrip("/")


def resolve_relay_url(*, url_override: str = "") -> tuple[str, str]:
    if url_override:
        return url_override, "--url"
    for key in ("XIAOYI_RELAY_WS_URL", "CLAW_XIAOYI_RELAY_WS_URL", "USERACCESS_PLUGIN_WS_URL"):
        value = _env(key)
        if value:
            return value, key
    return _DEFAULT_RELAY, "default"


def resolve_mcp_run_url(*, url_override: str = "", base_override: str = "") -> tuple[str, str]:
    """Return (ws_url, source). Concatenate only when env/base is a host, not a full /mcp/run URL."""
    if url_override:
        ws = _http_base_to_ws(url_override)
        if _already_mcp_run(ws):
            return ws, "--url"
        return f"{ws.rstrip('/')}{_MCP_RUN_PATH}", "--url + " + _MCP_RUN_PATH

    for key in ("AGENT_RUNTIME_MCP_RUN", "AGENT_RUNTIME_BASEURL"):
        value = _env(key)
        if not value:
            continue
        ws = _http_base_to_ws(value)
        if _already_mcp_run(ws):
            return ws, key
        return f"{ws.rstrip('/')}{_MCP_RUN_PATH}", f"{key} + {_MCP_RUN_PATH}"

    if base_override:
        ws = _http_base_to_ws(base_override)
        if _already_mcp_run(ws):
            return ws, "--base"
        return f"{ws.rstrip('/')}{_MCP_RUN_PATH}", "--base + " + _MCP_RUN_PATH

    raise SystemExit(
        "mcp-run 需要 --url / --base，或环境变量 AGENT_RUNTIME_MCP_RUN / AGENT_RUNTIME_BASEURL"
    )


def load_request_template(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        for candidate in (
            WORKSPACE / "skills" / "request.txt",
            ROOT / "skills" / "request.txt",
        ):
            if candidate.is_file():
                path = candidate
                break
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(0))


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if any(token in lower for token in ("sign", "secret", "credential", "authorization", "token")):
            out[key] = f"{value[:8]}…(len={len(value)})" if value else ""
        else:
            out[key] = value
    return out


def _dump(title: str, obj: Any) -> None:
    print(f"\n=== {title} ===")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj)


def _needs_insecure_ssl(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return url.startswith("wss://") and (
        host.endswith("hwcloudtest.cn") or bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host))
    )


def _fallback_body(prompt: str, template: dict[str, Any] | None) -> dict[str, Any]:
    if template:
        body = json.loads(json.dumps(template))
        arguments = body.setdefault("arguments", {})
        if isinstance(arguments, dict):
            arguments["prompt"] = prompt
            arguments.setdefault("bundleName", _ATOMIC_BUNDLE)
            arguments.setdefault("functionName", "seedreamLite4Skill")
        return body
    uid = _env("AGENT_RUNTIME_UID")
    device_id = _env("AGENT_RUNTIME_DEVICE_ID") or _env("X_DEVICE_ID")
    return {
        "extraInfo": {
            "context": {
                "deviceInfo": {
                    "deviceName": _env("CLAW_DEVICE_HOSTNAME") or "sandbox_pc",
                    "ohosApiVersion": 0,
                    "romVersion": "",
                    "sysVersion": _env("CLAW_DEVICE_SANDBOX_SYSTEM") or "windows",
                    "x-device-id": device_id,
                    "x-device-type": _env("CLAW_DEVICE_SANDBOX_SYSTEM") or "windows",
                },
                "userInfo": {"uid": uid},
            },
            "session": {
                "sessionId": "probe_invoke",
                "interactionId": 0,
                "deviceId": device_id,
            },
        },
        "bundleName": _ATOMIC_BUNDLE,
        "skillName": "",
        "functionName": "seedreamLite4Skill",
        "arguments": {
            "prompt": prompt,
            "bundleName": _ATOMIC_BUNDLE,
            "functionName": "seedreamLite4Skill",
        },
        "turnContinue": False,
        "eventContexts": None,
        "progressToken": "",
        "contexts": None,
    }


def _fallback_headers() -> dict[str, str]:
    import base64
    import hashlib
    import hmac
    import time

    headers = {
        "Content-Type": "application/json",
        "x-relay-role": "plugin",
        "x-plugin-session-id": "probe_invoke",
    }
    ak = _env("CLAW_XIAOYI_AK")
    sk = _env("CLAW_XIAOYI_SK")
    agent_id = _env("CLAW_XIAOYI_AGENT_ID")
    if ak and sk and agent_id:
        ts = str(int(time.time() * 1000))
        signature = base64.b64encode(
            hmac.new(sk.encode("utf-8"), ts.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        headers.update(
            {
                "x-access-key": ak,
                "x-sign": signature,
                "x-ts": ts,
                "x-agent-id": agent_id,
            }
        )
    uid = _env("AGENT_RUNTIME_UID")
    if uid:
        headers["x-uid"] = uid
    return headers


def _try_product_body_and_headers(prompt: str) -> tuple[dict[str, Any], dict[str, str]] | None:
    try:
        from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
            CloudPluginClient,
        )
        from jiuwenswarm.agents.harness.common.tools.invoke_meta.external_tool_registry import (
            ExternalToolSpec,
        )
        from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
            build_cloud_plugin_context,
            build_runtime_headers,
        )
    except ImportError:
        return None

    spec = ExternalToolSpec(
        plugin_id=_ATOMIC_BUNDLE,
        tool_name="seedreamLite4Skill",
        protocol="WS",
        plugin_type="Cloud",
    )
    context = build_cloud_plugin_context(session_id="probe_invoke")
    body = CloudPluginClient._build_request_body(
        spec,
        {
            "bundleName": _ATOMIC_BUNDLE,
            "functionName": "seedreamLite4Skill",
            "prompt": prompt,
        },
        context=context,
        session_id="probe_invoke",
    )
    headers = build_runtime_headers(extra={"x-plugin-session-id": "probe_invoke"})
    return body, headers


async def _recv_until_done(ws: Any, *, timeout: float) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            frame = {"raw": raw}
        frames.append(frame if isinstance(frame, dict) else {"data": frame})
        event = str(frames[-1].get("event") or "").strip().lower()
        content = frames[-1].get("content", "")
        stream_final = False
        if isinstance(content, str) and content.startswith("{"):
            try:
                parsed = json.loads(content)
                info = parsed.get("streamInfo") if isinstance(parsed, dict) else {}
                stream_final = str((info or {}).get("streamType") or "").lower() == "final"
            except json.JSONDecodeError:
                stream_final = False
        if event in {"finish"} or stream_final or frames[-1].get("success") is False:
            break
    return frames


async def run_via_tool(*, prompt: str, timeout: float) -> dict[str, Any]:
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.invoke_tool import InvokeTool

    os.environ.setdefault("AGENT_RUNTIME_WS_TIMEOUT", str(timeout))
    tool = InvokeTool()
    return await tool.invoke(
        {
            "functionName": "PluginSkillExecTool",
            "arguments": {
                "functionName": "seedreamLite4Skill",
                "bundleName": _ATOMIC_BUNDLE,
                "prompt": prompt,
            },
        }
    )


async def run_via_client(*, url: str, prompt: str, timeout: float) -> dict[str, Any]:
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
        CloudPluginClient,
    )
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.external_tool_registry import (
        ExternalToolSpec,
    )
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
        build_cloud_plugin_context,
    )

    spec = ExternalToolSpec(
        plugin_id=_ATOMIC_BUNDLE,
        tool_name="seedreamLite4Skill",
        protocol="WS",
        plugin_type="Cloud",
    )
    client = CloudPluginClient(base_url=url, session_id="probe_invoke", timeout=timeout)
    context = build_cloud_plugin_context(session_id="probe_invoke")
    return await client.invoke(
        spec,
        arguments={
            "bundleName": _ATOMIC_BUNDLE,
            "functionName": "seedreamLite4Skill",
            "prompt": prompt,
        },
        context=context,
    )


async def run_raw_ws(
    *,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    insecure: bool,
) -> list[dict[str, Any]]:
    try:
        from websockets.legacy.client import connect as ws_connect
    except ImportError:
        from websockets import connect as ws_connect

    kwargs: dict[str, Any] = {
        "open_timeout": min(timeout, 30.0),
        "close_timeout": 5.0,
        "max_size": 8 * 2**20,
    }
    if headers:
        import websockets

        if ws_connect is getattr(websockets, "connect", None):
            kwargs["additional_headers"] = headers
        else:
            kwargs["extra_headers"] = headers
    if insecure and url.startswith("wss://"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx

    async with ws_connect(url, **kwargs) as ws:
        await ws.send(json.dumps(body, ensure_ascii=False))
        return await _recv_until_done(ws, timeout=timeout)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe invoke plugin skill over one WS interface")
    parser.add_argument(
        "--target",
        choices=("relay", "mcp-run"),
        default="relay",
        help="relay=本地中转（产品路径）；mcp-run=拼接 /agent-runtime-service/v1/mcp/run",
    )
    parser.add_argument("--url", default="", help="完整 WS/HTTP URL；mcp-run 下若无 /mcp/run 会拼接")
    parser.add_argument("--base", default="", help="仅 mcp-run：host/base，拼出 v1/mcp/run")
    parser.add_argument("--prompt", default="", help="覆盖生图 prompt；默认取 request.txt 或内置短句")
    parser.add_argument("--body-file", default="", help="原始请求 JSON/txt（默认 workspace/skills/request.txt）")
    parser.add_argument("--timeout", type=float, default=float(_env("AGENT_RUNTIME_WS_TIMEOUT", "120")))
    parser.add_argument("--dry-run", action="store_true", help="只打印解析后的 URL / headers / body，不连网")
    parser.add_argument(
        "--via-tool",
        action="store_true",
        help="走 InvokeTool（与桌面 LLM invoke 同路径）；仅 relay 有意义",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="不走 CloudPluginClient，直接把 body 发到 WS（适合 mcp-run 对照）",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="wss 跳过证书校验（测试域/IP 默认已开）",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    body_path = Path(args.body_file) if args.body_file else None
    template = load_request_template(body_path)
    prompt = args.prompt or (
        str((template or {}).get("arguments", {}).get("prompt") or "").strip() or _DEFAULT_PROMPT
    )

    if args.target == "relay":
        url, source = resolve_relay_url(url_override=args.url.strip())
    else:
        url, source = resolve_mcp_run_url(
            url_override=args.url.strip(),
            base_override=args.base.strip(),
        )

    product = None if (args.raw and template) else _try_product_body_and_headers(prompt)
    if args.raw and template:
        raw_body = _fallback_body(prompt, template)
        headers = _fallback_headers()
        body_source = "skills/request.txt"
    elif product is not None:
        raw_body, headers = product
        body_source = "CloudPluginClient._build_request_body"
    else:
        raw_body = _fallback_body(prompt, template)
        headers = _fallback_headers()
        body_source = "skills/request.txt" if template else "fallback (openjiuwen 未安装，dry-run 仍可用)"

    if args.target == "mcp-run":
        uid = _env("AGENT_RUNTIME_UID") or str(
            (((raw_body.get("extraInfo") or {}).get("context") or {}).get("userInfo") or {}).get("uid")
            or ""
        )
        if uid:
            headers["x-uid"] = uid

    _dump(
        "resolved",
        {
            "target": args.target,
            "url": url,
            "source": source,
            "via_tool": bool(args.via_tool),
            "raw": bool(args.raw) or args.target == "mcp-run",
            "body_source": body_source,
            "timeout": args.timeout,
            "concatenated": args.target == "mcp-run" and _MCP_RUN_PATH in url and " + " in source,
        },
    )
    _dump("headers (masked)", _mask_headers(headers))
    _dump("request body", raw_body)

    if args.dry_run:
        print("\n[dry-run] skip connect")
        return 0

    insecure = args.insecure or _needs_insecure_ssl(url)
    try:
        if args.via_tool:
            if args.target != "relay":
                print("--via-tool 只用于 relay（产品 InvokeTool 不拼接 /mcp/run）", file=sys.stderr)
                return 2
            result = asyncio.run(run_via_tool(prompt=prompt, timeout=args.timeout))
            _dump("InvokeTool result", result)
            return 0 if result.get("success") else 1

        if args.raw or args.target == "mcp-run":
            frames = asyncio.run(
                run_raw_ws(
                    url=url,
                    body=raw_body,
                    headers=headers,
                    timeout=args.timeout,
                    insecure=insecure,
                )
            )
            _dump("ws frames", frames)
            last = frames[-1] if frames else {}
            failed = last.get("success") is False or str(last.get("type") or "") == "abnormal"
            return 1 if failed else 0

        result = asyncio.run(run_via_client(url=url, prompt=prompt, timeout=args.timeout))
        _dump("CloudPluginClient result", result)
        return 0 if result.get("success") else 1
    except asyncio.TimeoutError:
        print(f"\nTIMEOUT after {args.timeout}s on {url}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
