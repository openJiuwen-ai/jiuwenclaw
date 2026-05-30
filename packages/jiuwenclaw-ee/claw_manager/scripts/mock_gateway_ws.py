#!/usr/bin/env python3
"""Demo-only mock Gateway WS client.

为给 Claw Manager 灌 mock 数据时，承接 manager 的 config.push 双写 ACK。
启动后会：
  1. 拉取 Manager 所有现存 instance；
  2. 为每个 instance 起一条 WebSocket 连接，register 自己为 service_type=gateway；
  3. 收到 config.push 立刻回 config.ack(success_flag=True)。

用法：
    python3 scripts/mock_gateway_ws.py
    GATEWAY_MANAGER_WS_URL=ws://127.0.0.1:8766 python3 scripts/mock_gateway_ws.py
    INSTANCE_IDS="sp-aaa,sp-bbb" python3 scripts/mock_gateway_ws.py  # 也可显式指定

依赖：仅 stdlib + websockets（jiuwenclaw_manager 已带）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from typing import Any
from urllib import request as urlrequest

try:
    import websockets
except ImportError:  # pragma: no cover
    print("[mock-gw] 需要 websockets 库；pip install websockets", file=sys.stderr)
    sys.exit(1)

MANAGER_REST = os.environ.get("CLAWMANAGER_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
MANAGER_WS = os.environ.get("GATEWAY_MANAGER_WS_URL", "ws://127.0.0.1:8766")
EXPLICIT_INSTANCE_IDS = [
    s.strip() for s in os.environ.get("INSTANCE_IDS", "").split(",") if s.strip()
]


def _http_json(path: str) -> Any:
    req = urlrequest.Request(f"{MANAGER_REST}{path}")
    req.add_header("Accept", "application/json")
    with urlrequest.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_instance_ids() -> list[str]:
    if EXPLICIT_INSTANCE_IDS:
        return EXPLICIT_INSTANCE_IDS
    payload = _http_json("/api/v1/instances?page=1&page_size=200")
    data = payload.get("data") or {}
    items = data.get("items") or []
    return [str(it["jiuwenclaw_id"]) for it in items if it.get("jiuwenclaw_id")]


_SECTIONS_RETURNING_POLICY_ID = {
    "config_effective_service_policies",
    "config_effective_global_policies",
    "config_effective_agent_policies",
}
_SECTIONS_RETURNING_MAPPING_ID = {
    "config_default_template_mappings",
}


def _build_ack_result(config: dict[str, Any], counters: dict[str, int]) -> dict[str, Any]:
    """根据 push 的 config 内容，决定 ack.result 该带什么 id 字段。"""
    result: dict[str, Any] = {"applied_sections": list(config.keys())}
    for section, body in config.items():
        if not isinstance(body, dict):
            continue
        op = body.get("op")
        if op != "create":
            continue
        if section in _SECTIONS_RETURNING_POLICY_ID:
            counters[section] = counters.get(section, 0) + 1
            result["policy_id"] = counters[section]
        elif section in _SECTIONS_RETURNING_MAPPING_ID:
            counters[section] = counters.get(section, 0) + 1
            result["mapping_id"] = counters[section]
    return result


async def _handshake(ws: Any, *, reconnect_id: str | None = None) -> str:
    raw = await ws.recv()
    msg = json.loads(raw)
    if msg.get("type") == "event" and msg.get("event") == "connection.ack":
        pass
    payload: dict[str, Any] = {"service_type": "gateway"}
    if reconnect_id:
        payload["jiuwenclaw_id"] = reconnect_id
    await ws.send(json.dumps({"type": "register", "payload": payload}))
    raw_ack = await ws.recv()
    ack = json.loads(raw_ack)
    if ack.get("type") == "error":
        raise RuntimeError(str(ack.get("payload")))
    ack_payload = ack.get("payload") or {}
    jid = str(ack_payload.get("jiuwenclaw_id") or "").strip()
    if not jid:
        raise RuntimeError("register.ack missing jiuwenclaw_id")
    return jid


async def _run_one(instance_id: str | None, stop: asyncio.Event) -> None:
    counters: dict[str, int] = {}
    backoff = 1.0
    label = instance_id or "new"
    while not stop.is_set():
        try:
            async with websockets.connect(MANAGER_WS, ping_interval=20, ping_timeout=60) as ws:
                jid = await _handshake(ws, reconnect_id=instance_id)
                print(f"[mock-gw] {label}: connected jiuwenclaw_id={jid}")

                async def _mock_heartbeat() -> None:
                    while True:
                        await asyncio.sleep(10.0)
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "heartbeat",
                                    "payload": {
                                        "jiuwenclaw_id": jid,
                                        "service_type": "gateway",
                                    },
                                }
                            )
                        )

                hb_task = asyncio.create_task(_mock_heartbeat())
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        mtype = msg.get("type")
                        if mtype == "config.push":
                            payload = msg.get("payload") or {}
                            revision = payload.get("revision")
                            cfg = payload.get("config") or {}
                            result = _build_ack_result(cfg, counters)
                            print(
                                f"[mock-gw] {jid}: config.push rev={revision} "
                                f"sections={result.get('applied_sections')} ack.result={result}"
                            )
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "config.ack",
                                        "payload": {
                                            "revision": revision,
                                            "success_flag": True,
                                            "result": result,
                                        },
                                    }
                                )
                            )
                        elif mtype == "event":
                            evt = msg.get("event")
                            if evt == "register.ack":
                                print(f"[mock-gw] {jid}: register.ack")
                        elif mtype == "error":
                            print(f"[mock-gw] {jid}: error {msg.get('payload')}")
                finally:
                    hb_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await hb_task
            backoff = 1.0
        except Exception as exc:  # noqa: BLE001
            if stop.is_set():
                return
            print(f"[mock-gw] {label}: connection error {exc}; retry in {backoff:.1f}s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 10.0)


async def _main() -> None:
    instance_ids = _fetch_instance_ids()
    if not instance_ids:
        print(
            "[mock-gw] no instances in DB; starting one mock gateway (manager will assign id)"
        )
        instance_ids = [None]
    print(f"[mock-gw] target manager_rest={MANAGER_REST} ws={MANAGER_WS}")
    print(f"[mock-gw] mocking {len(instance_ids)} gateway connection(s):")
    for jid in instance_ids:
        print(f"          - {jid or '(new)'}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    tasks = [asyncio.create_task(_run_one(jid, stop)) for jid in instance_ids]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    print("[mock-gw] bye")


if __name__ == "__main__":
    asyncio.run(_main())
