#!/usr/bin/env python3
"""Standalone 文生图 invoke：直连云端 /ws/link，不依赖小艺 Work 是否打开。

链路（与桌面 CloudWsRelay 云端半段对齐，跳过 127.0.0.1:19690）：

  1. POST /user-credential/v1/business-credential/create 换 businessCredential
  2. WSS 握手 /openclaw/v1/ws/link（businessCredential + x-uid + x-device-id）
  3. 可选 clawd_bot_init + 心跳
  4. 发送 skills/request.txt 形态的 seedreamLite4Skill 请求
  5. 等到 event=text / streamType=final，解析 items 图片 URL

环境变量（也可用命令行覆盖）：

  INVOKE_UID                 业务数字 uid（必填；不能是 unionId/MDEr…）
  INVOKE_UAT                 华为账号 access_token；无 INVOKE_BUSINESS_CREDENTIAL 时用来创建凭据
  INVOKE_BUSINESS_CREDENTIAL 已有凭据则跳过创建
  INVOKE_DEVICE_ID           设备 ID（缺省用本机 hostname 哈希）
  INVOKE_CLIENT_ID           OAuth client_id（缺省 6917612679526496902）
  INVOKE_OPENID              可选；与 UAT 一起调 getTokenInfo 补 uid
  INVOKE_WS_URL              缺省蓝区 wss://lfhagcp.hwcloudtest.cn:58447/openclaw/v1/ws/link
  INVOKE_CREDENTIAL_URL      缺省蓝区 credential create
  INVOKE_ZONE                blue|green（green 切内网 IP）
  INVOKE_PROMPT              覆盖 prompt

用法：

  export INVOKE_UID=30086000686785686
  export INVOKE_UAT='...'
  python scripts/standalone_invoke_image.py --dry-run
  python scripts/standalone_invoke_image.py --self-check
  python scripts/standalone_invoke_image.py --prompt "一只柯基在滑板上"
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

DEFAULT_WS_URL = "wss://lfhagcp.hwcloudtest.cn:58447/openclaw/v1/ws/link"
DEFAULT_CRED_URL = (
    "https://lfhagmirror.hwcloudtest.cn:8450/user-credential/v1/business-credential/create"
)
DEFAULT_TOKEN_INFO_URL = "https://oauth-login.cloud.huawei.com/rest.php"
DEFAULT_CLIENT_ID = "6917612679526496902"
ATOMIC_BUNDLE = "com.atomicservice.5765880207845681341"
DEFAULT_PROMPT = "一只柯基在滑板上"
HEARTBEAT_SEC = 20.0

_ZONE_PAIRS = (
    # domain, ip, plain_http_on_green
    ("lfhagmirror.hwcloudtest.cn", "10.33.87.20", True),
    ("lfhagcp.hwcloudtest.cn", "10.33.233.153", False),
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def apply_zone(url: str, zone: str) -> str:
    if zone not in {"blue", "green"}:
        return url
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/?#]+)(.*)$", url.strip())
    if not match:
        return url
    scheme, authority, rest = match.group(1), match.group(2), match.group(3)
    colon = authority.rfind(":")
    host = (authority[:colon] if colon >= 0 else authority).lower()
    port = authority[colon:] if colon >= 0 else ""
    pair = next((p for p in _ZONE_PAIRS if p[0] == host or p[1] == host), None)
    if pair is None:
        return url
    domain, ip, plain = pair
    target = ip if zone == "green" else domain
    out_scheme = scheme
    if plain:
        proto = scheme[:-3].lower()
        mapped = (
            {"https": "http", "wss": "ws"}.get(proto)
            if zone == "green"
            else {"http": "https", "ws": "wss"}.get(proto)
        )
        if mapped:
            out_scheme = f"{mapped}://"
    return f"{out_scheme}{target}{port}{rest}"


def default_device_id() -> str:
    raw = f"{socket.gethostname()}|{platform.system()}|{platform.node()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_business_uid(uid: str) -> bool:
    return bool(uid) and uid.isdigit()


def load_request_template(path: Path | None = None) -> dict[str, Any] | None:
    candidates = []
    if path is not None:
        candidates.append(path)
    candidates.extend((WORKSPACE / "skills" / "request.txt", ROOT / "skills" / "request.txt"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
    return None


def build_plugin_body(*, prompt: str, uid: str, device_id: str, template: dict[str, Any] | None) -> dict[str, Any]:
    if template:
        body = json.loads(json.dumps(template))
    else:
        body = {
            "extraInfo": {
                "context": {
                    "deviceInfo": {
                        "deviceName": socket.gethostname(),
                        "ohosApiVersion": 0,
                        "romVersion": "",
                        "sysVersion": platform.system().lower(),
                        "x-device-id": device_id,
                        "x-device-type": platform.system().lower(),
                    },
                    "userInfo": {"uid": uid},
                },
                "session": {
                    "sessionId": str(uuid.uuid4()),
                    "interactionId": 1,
                    "deviceId": device_id,
                },
            },
            "bundleName": ATOMIC_BUNDLE,
            "skillName": "",
            "functionName": "seedreamLite4Skill",
            "arguments": {},
            "turnContinue": False,
            "eventContexts": None,
            "progressToken": "",
            "contexts": None,
        }
    extra = body.setdefault("extraInfo", {})
    context = extra.setdefault("context", {})
    user_info = context.setdefault("userInfo", {})
    device_info = context.setdefault("deviceInfo", {})
    session = extra.setdefault("session", {})
    user_info["uid"] = uid
    device_info["x-device-id"] = device_id
    session["deviceId"] = device_id
    body["bundleName"] = ATOMIC_BUNDLE
    body["functionName"] = "seedreamLite4Skill"
    arguments = body.setdefault("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
        body["arguments"] = arguments
    arguments["bundleName"] = ATOMIC_BUNDLE
    arguments["functionName"] = "seedreamLite4Skill"
    arguments["prompt"] = prompt
    return body


def _insecure_ssl() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _needs_insecure(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return True
    return host == "hwcloudtest.cn" or host.endswith(".hwcloudtest.cn")


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], *, timeout: float = 30.0) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", str(len(data)))
    ctx = _insecure_ssl() if (url.startswith("https://") and _needs_insecure(url)) else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text


def fetch_uid_from_token_info(*, openid: str, uat: str) -> str:
    body = urllib.parse.urlencode(
        {
            "nsp_svc": "huawei.oauth2.user.getTokenInfo",
            "open_id": openid,
            "access_token": uat,
        }
    ).encode("utf-8")
    url = _env("CLAW_TOKEN_INFO_URL", DEFAULT_TOKEN_INFO_URL)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Content-Length", str(len(body)))
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    uid = str(raw.get("uid") or "").strip()
    if not is_business_uid(uid):
        raise SystemExit(f"getTokenInfo 未返回数字 uid：{raw!r}"[:400])
    return uid


def credential_ok(raw: dict[str, Any], status: int) -> bool:
    if status < 200 or status >= 300:
        return False
    code = raw.get("code")
    return code in {0, "0", "0.0"} or str(code).strip() == "0"


def extract_credential(raw: dict[str, Any]) -> str | None:
    cred_result = raw.get("credentialResult") if isinstance(raw.get("credentialResult"), dict) else None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else None
    for source in (cred_result, data, raw):
        if not isinstance(source, dict):
            continue
        for key in ("businessCredential", "credential"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def create_business_credential(
    *,
    url: str,
    uat: str,
    uid: str,
    device_id: str,
    client_id: str,
) -> str:
    status, raw = _http_post_json(
        url,
        {
            "Authorization": f"Bearer {uat}",
            "x-uid": uid,
            "x-client-version": "1.0.0",
            "x-hag-trace-id": uuid.uuid4().hex,
        },
        {
            "deviceId": device_id,
            "uid": uid,
            "clientId": client_id,
            "sysVer": "5.0.0",
            "version": "1.0.0",
            "ts": str(int(time.time() * 1000)),
        },
    )
    if not isinstance(raw, dict):
        raise SystemExit(f"business-credential 响应非 JSON（HTTP {status}）: {raw!r}"[:400])
    if not credential_ok(raw, status):
        code = raw.get("code")
        msg = raw.get("message") or raw.get("msg") or raw.get("desc") or raw
        raise SystemExit(f"business-credential 创建失败（HTTP {status}, code={code}）: {msg}")
    credential = extract_credential(raw)
    if not credential:
        raise SystemExit(f"business-credential 响应缺少 credential: {json.dumps(raw, ensure_ascii=False)[:500]}")
    return credential


def mask_secret(value: str) -> str:
    if not value:
        return "(空)"
    return f"{value[:12]}…(len={len(value)})"


def dump(title: str, obj: Any) -> None:
    print(f"\n=== {title} ===")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj)


def extract_image_urls(frames: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for frame in frames:
        parsed = _content_obj(frame)
        if not parsed:
            continue
        items = parsed.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            url = str(item).strip() if item else ""
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _content_obj(frame: dict[str, Any]) -> dict[str, Any] | None:
    content = frame.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.startswith("{"):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def is_plugin_final(frame: dict[str, Any]) -> bool:
    if str(frame.get("type") or "").lower() == "abnormal":
        return True
    if frame.get("success") is False:
        return True
    event = str(frame.get("event") or "").strip().lower()
    if event == "finish":
        return True
    parsed = _content_obj(frame)
    if parsed is None:
        return False
    info = parsed.get("streamInfo") if isinstance(parsed.get("streamInfo"), dict) else {}
    return event == "text" and str((info or {}).get("streamType") or "").strip().lower() == "final"


def is_plugin_response(frame: dict[str, Any]) -> bool:
    if str(frame.get("msgType") or "") or str(frame.get("method") or ""):
        return False
    return isinstance(frame.get("event"), str) and bool(frame.get("event"))


def _ws_connect():
    try:
        from websockets.legacy.client import connect as legacy_connect
    except ImportError:
        legacy_connect = None
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("缺少 websockets：pip install websockets") from exc
    if legacy_connect is not None:
        return legacy_connect, "extra_headers"
    return websockets.connect, "additional_headers"


def device_envelope(payload: dict[str, Any], *, uid: str, device_id: str, agent_id: str) -> dict[str, Any]:
    """对齐 CloudWsRelay.cloudSend：init/心跳注入设备字段。插件体不走此函数。"""
    out = dict(payload)
    out["agentId"] = agent_id
    out["uid"] = uid
    out["sandboxType"] = "sandbox_pc"
    out["hostname"] = socket.gethostname()
    out["sandboxSystem"] = platform.system().lower()
    out["deviceId"] = device_id
    out.setdefault("workSpaces", [])
    out.setdefault("allowMobileConnect", False)
    return out


async def invoke_image(
    *,
    ws_url: str,
    credential: str,
    uid: str,
    device_id: str,
    body: dict[str, Any],
    timeout: float,
    send_init: bool,
    heartbeat: bool,
    agent_id: str,
    init_wait: float,
) -> list[dict[str, Any]]:
    connect, header_key = _ws_connect()
    headers = {
        "businessCredential": credential,
        "x-uid": uid,
        "x-device-id": device_id,
    }
    kwargs: dict[str, Any] = {
        "open_timeout": min(timeout, 30.0),
        "close_timeout": 5.0,
        "max_size": 8 * 2**20,
        header_key: headers,
    }
    if ws_url.startswith("wss://") and _needs_insecure(ws_url):
        kwargs["ssl"] = _insecure_ssl()

    frames: list[dict[str, Any]] = []
    stop_heartbeat = asyncio.Event()

    async def _heartbeat(ws: Any) -> None:
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=HEARTBEAT_SEC)
                return
            except asyncio.TimeoutError:
                await ws.send(
                    json.dumps(
                        device_envelope(
                            {
                                "msgType": "heartbeat",
                                "timestamp": int(time.time() * 1000),
                            },
                            uid=uid,
                            device_id=device_id,
                            agent_id=agent_id,
                        ),
                        ensure_ascii=False,
                    )
                )

    async with connect(ws_url, **kwargs) as ws:
        hb_task = asyncio.create_task(_heartbeat(ws)) if heartbeat else None
        try:
            if send_init:
                await ws.send(
                    json.dumps(
                        device_envelope(
                            {"msgType": "clawd_bot_init"},
                            uid=uid,
                            device_id=device_id,
                            agent_id=agent_id,
                        ),
                        ensure_ascii=False,
                    )
                )
                if init_wait > 0:
                    await asyncio.sleep(init_wait)
            await ws.send(json.dumps(body, ensure_ascii=False))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    frame = {"raw": raw}
                if not isinstance(frame, dict):
                    frame = {"data": frame}
                dump("ws frame", frame)
                if str(frame.get("msgType") or "") or str(frame.get("method") or ""):
                    continue
                if is_plugin_response(frame) or is_plugin_final(frame):
                    frames.append(frame)
                    if is_plugin_final(frame):
                        break
        finally:
            stop_heartbeat.set()
            if hb_task is not None:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass
    return frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone 文生图 invoke（直连 /ws/link，不依赖小艺 Work）")
    parser.add_argument("--uid", default=_env("INVOKE_UID"))
    parser.add_argument("--uat", default=_env("INVOKE_UAT"))
    parser.add_argument("--openid", default=_env("INVOKE_OPENID"))
    parser.add_argument("--credential", default=_env("INVOKE_BUSINESS_CREDENTIAL"))
    parser.add_argument("--device-id", default=_env("INVOKE_DEVICE_ID") or default_device_id())
    parser.add_argument("--client-id", default=_env("INVOKE_CLIENT_ID") or DEFAULT_CLIENT_ID)
    parser.add_argument("--ws-url", default=_env("INVOKE_WS_URL") or DEFAULT_WS_URL)
    parser.add_argument("--credential-url", default=_env("INVOKE_CREDENTIAL_URL") or DEFAULT_CRED_URL)
    parser.add_argument("--zone", choices=("blue", "green"), default=_env("INVOKE_ZONE") or "blue")
    parser.add_argument("--prompt", default=_env("INVOKE_PROMPT"))
    parser.add_argument("--timeout", type=float, default=float(_env("INVOKE_TIMEOUT") or "120"))
    parser.add_argument("--agent-id", default=_env("INVOKE_AGENT_ID") or "standalone-invoke")
    parser.add_argument("--init-wait", type=float, default=float(_env("INVOKE_INIT_WAIT") or "0.5"))
    parser.add_argument("--body-file", default="", help="原始请求 JSON/txt（默认 workspace/skills/request.txt）")
    parser.add_argument("--skip-init", action="store_true", help="不发 clawd_bot_init，只发插件体")
    parser.add_argument("--no-heartbeat", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-check", action="store_true", help="只检查 zone 映射 / uid 规则")
    return parser.parse_args()


def self_check() -> int:
    green_ws = apply_zone(DEFAULT_WS_URL, "green")
    green_cred = apply_zone(DEFAULT_CRED_URL, "green")
    assert green_ws == "wss://10.33.233.153:58447/openclaw/v1/ws/link", green_ws
    assert green_cred == "http://10.33.87.20:8450/user-credential/v1/business-credential/create", green_cred
    assert apply_zone(DEFAULT_WS_URL, "blue") == DEFAULT_WS_URL
    assert apply_zone("wss://10.33.233.153:58447/openclaw/v1/ws/link", "blue") == DEFAULT_WS_URL
    assert is_business_uid("30086000686785686")
    assert not is_business_uid("MDEr0otbarUMqtSgf47ibMm6icObpib12paLkjGGG3fGibXbmQ")
    sample = {
        "event": "text",
        "content": json.dumps(
            {"items": ["https://example.com/a.jpg"], "streamInfo": {"streamType": "final"}},
            ensure_ascii=False,
        ),
    }
    assert is_plugin_final(sample)
    assert extract_image_urls([sample]) == ["https://example.com/a.jpg"]
    print("self-check ok")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    args = parse_args()
    if args.self_check:
        return self_check()

    ws_url = apply_zone(args.ws_url, args.zone)
    cred_url = apply_zone(args.credential_url, args.zone)
    template = load_request_template(Path(args.body_file) if args.body_file else None)
    prompt = args.prompt or str((template or {}).get("arguments", {}).get("prompt") or "").strip() or DEFAULT_PROMPT
    uid = args.uid
    template_uid = str(
        (((template or {}).get("extraInfo") or {}).get("context") or {}).get("userInfo", {}).get("uid") or ""
    )
    if args.dry_run and not uid:
        uid = template_uid
    if args.openid and args.uat and not is_business_uid(uid):
        print("INVOKE_UID 不是数字 uid，尝试 getTokenInfo …")
        uid = fetch_uid_from_token_info(openid=args.openid, uat=args.uat)

    if not is_business_uid(uid):
        print(
            "需要业务数字 uid（INVOKE_UID / --uid）。\n"
            "不能使用 unionId（如 MDEr…），否则 credential 会报 uid must not be null。\n"
            "也可提供 INVOKE_OPENID + INVOKE_UAT，由脚本调 getTokenInfo。\n"
            "--dry-run 时可回退 skills/request.txt 里的样例 uid。",
            file=sys.stderr,
        )
        return 2

    body = build_plugin_body(prompt=prompt, uid=uid, device_id=args.device_id, template=template)
    dump(
        "resolved",
        {
            "ws_url": ws_url,
            "credential_url": cred_url,
            "zone": args.zone,
            "uid": uid,
            "device_id": args.device_id,
            "client_id": args.client_id,
            "has_uat": bool(args.uat),
            "has_credential": bool(args.credential),
            "send_init": not args.skip_init,
            "agent_id": args.agent_id,
            "init_wait": args.init_wait,
            "timeout": args.timeout,
        },
    )
    dump("request body", body)
    if not args.skip_init:
        dump(
            "init (not plugin body)",
            device_envelope(
                {"msgType": "clawd_bot_init"},
                uid=uid,
                device_id=args.device_id,
                agent_id=args.agent_id,
            ),
        )

    if args.dry_run:
        print("\n[dry-run] skip credential + websocket")
        return 0

    credential = args.credential
    if not credential:
        if not args.uat:
            print("缺少 INVOKE_BUSINESS_CREDENTIAL 或 INVOKE_UAT", file=sys.stderr)
            return 2
        print("\ncreating businessCredential …")
        credential = create_business_credential(
            url=cred_url,
            uat=args.uat,
            uid=uid,
            device_id=args.device_id,
            client_id=args.client_id,
        )
    dump("handshake headers (masked)", {
        "businessCredential": mask_secret(credential),
        "x-uid": uid,
        "x-device-id": args.device_id,
    })

    try:
        frames = asyncio.run(
            invoke_image(
                ws_url=ws_url,
                credential=credential,
                uid=uid,
                device_id=args.device_id,
                body=body,
                timeout=args.timeout,
                send_init=not args.skip_init,
                heartbeat=not args.no_heartbeat,
                agent_id=args.agent_id,
                init_wait=args.init_wait,
            )
        )
    except asyncio.TimeoutError:
        print(f"\nTIMEOUT after {args.timeout}s on {ws_url}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if frames and str(frames[-1].get("type") or "").lower() == "abnormal":
        dump("error frame", frames[-1])
        return 1
    urls = extract_image_urls(frames)
    dump("image urls", urls)
    if not urls:
        print("未解析到 items 图片 URL", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
