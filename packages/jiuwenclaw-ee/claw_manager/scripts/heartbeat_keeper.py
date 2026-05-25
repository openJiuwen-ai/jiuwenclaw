#!/usr/bin/env python3
"""演示用：周期性给已上报过的所有服务重发心跳，避免被扫描器判 offline。

默认每 15 秒扫描一次 Manager 的 /services/status，把每个服务对应原 heartbeat 重发一遍。
对最后一个实例（用名字识别 staging-cn-south）故意只保活其中第一个 gateway 之外
的服务全部停发，模拟"半离线"场景，便于前端看到混合状态。

用法：
    python3 scripts/heartbeat_keeper.py
    HEARTBEAT_INTERVAL=10 python3 scripts/heartbeat_keeper.py
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

BASE = os.environ.get("CLAWMANAGER_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "15"))


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", errors="replace")}
    except urlerror.URLError as exc:
        return {"_url_error": str(exc.reason)}


def _instances() -> list[dict[str, Any]]:
    payload = _call("GET", "/api/v1/instances?page=1&page_size=200")
    return (payload.get("data") or {}).get("items") or []


def _services_for(jid: str) -> list[dict[str, Any]]:
    payload = _call("GET", f"/api/v1/instances/{jid}/services/status")
    return (payload.get("data") or {}).get("items") or []


def _heartbeat(jid: str, svc: dict[str, Any]) -> None:
    body = {
        "service_id": svc.get("service_id"),
        "service_type": svc.get("service_type"),
        "component_role": svc.get("component_role"),
        "endpoint": svc.get("endpoint"),
        "version": svc.get("version"),
        "manager_id": "manager-default",
        "capabilities": svc.get("capabilities") or {},
        "data": svc.get("data") or {},
    }
    _call("POST", f"/api/v1/instances/{jid}/events/heartbeat", body)


STOP = False


def _on_signal(_sig, _frm) -> None:  # noqa: ANN001
    global STOP
    STOP = True


def main() -> None:
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print(f"[hb-keeper] target={BASE} interval={INTERVAL}s")
    instances = _instances()
    if not instances:
        print("[hb-keeper] no instances, exit")
        return

    # 第三个实例（staging）整体保持 offline，第二个实例只保留 gateway 在线
    skip_by_jid: dict[str, set[str]] = {}
    for idx, inst in enumerate(instances):
        jid = inst["jiuwenclaw_id"]
        name = inst.get("jiuwenclaw_name", "")
        if "staging" in name:
            services = _services_for(jid)
            skip_by_jid[jid] = {s["service_id"] for s in services}
            print(f"[hb-keeper] {jid} ({name}): WILL NOT keep alive (full offline demo)")
        elif idx == 1:
            services = _services_for(jid)
            skip_by_jid[jid] = {
                s["service_id"]
                for s in services
                if s.get("service_type") in {"agent_server", "worker"}
                and s.get("service_id", "").endswith("-02")
            }
            print(
                f"[hb-keeper] {jid} ({name}): keeping alive except "
                f"{sorted(skip_by_jid[jid])} (partial demo)"
            )
        else:
            print(f"[hb-keeper] {jid} ({name}): keep all alive")

    tick = 0
    while not STOP:
        tick += 1
        for inst in instances:
            jid = inst["jiuwenclaw_id"]
            skip = skip_by_jid.get(jid, set())
            services = _services_for(jid)
            sent = 0
            for svc in services:
                sid = svc.get("service_id")
                if not sid or sid in skip:
                    continue
                _heartbeat(jid, svc)
                sent += 1
            if tick == 1 or tick % 4 == 0:
                print(f"[hb-keeper] tick={tick} {jid}: pushed {sent}/{len(services)} heartbeats")
        for _ in range(int(INTERVAL * 10)):
            if STOP:
                break
            time.sleep(0.1)
    print("[hb-keeper] bye")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
