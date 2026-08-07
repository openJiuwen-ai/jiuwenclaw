#!/usr/bin/env python3
"""Probe CSPL API in sandbox — compare xy_channel format vs legacy vs skill_scan.

Usage (sandbox):
  set CSPL_SERVICE_URL=http://lfhagmirror.hwcloudtest.cn:80
  set CSPL_UID=your-uid
  set CSPL_API_KEY=your-key
  set CSPL_TRACE_ID=8b0b0478-e0dc-4712-95be-af5e9b721f19&19&ea5d&0
  python scripts/probe_cspl_sandbox.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jiuwenswarm.agents.harness.common.rails.cspl.client import (  # noqa: E402
    CsplConfig,
    _build_headers,
    _build_payload,
    build_behaviordetect_request,
)
from jiuwenswarm.agents.harness.common.rails.cspl.constants import (  # noqa: E402
    API_URL_SUFFIX,
    TOOL_INPUT_SCAN,
)

SAMPLE_QUESTION = json.dumps(
    {
        "subSceneID": "TOOL_INPUT",
        "tool": "bash",
        "hash": "",
        "url": "",
        "size": 0,
        "source": "echo hello",
        "content": "",
    },
    ensure_ascii=False,
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _post(url: str, headers: dict[str, str], payload: dict) -> tuple[int, dict | str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _print_case(name: str, status: int, body: dict | str) -> None:
    print(f"\n=== {name} ===")
    print(f"HTTP {status}")
    if isinstance(body, dict):
        print(json.dumps(body, ensure_ascii=False, indent=2))
        ret = body.get("retCode")
        err = body.get("errorCode")
        if ret == "0" or ret == 0:
            sr = (body.get("data") or {}).get("securityResult")
            print(f">>> SUCCESS securityResult={sr!r}")
        elif err:
            print(f">>> GATEWAY errorCode={err!r} errorMsg={body.get('errorMsg')!r}")
        else:
            print(f">>> retCode={ret!r} retMsg={body.get('retMsg')!r}")
    else:
        print(body)


def main() -> int:
    service_url = _env("CSPL_SERVICE_URL")
    uid = _env("CSPL_UID")
    api_key = _env("CSPL_API_KEY")
    trace_id = _env("CSPL_TRACE_ID", "probe-trace-001")
    extra_user_id = _env("CSPL_EXTRA_USERID")

    if not service_url or not uid or not api_key:
        print(
            "Set CSPL_SERVICE_URL, CSPL_UID, CSPL_API_KEY (optional CSPL_TRACE_ID, CSPL_EXTRA_USERID).\n"
            "Example:\n"
            "  CSPL_SERVICE_URL=http://lfhagmirror.hwcloudtest.cn:80\n"
            "  CSPL_UID=uid-virtual\n"
            "  CSPL_API_KEY=sk-xxx\n"
        )
        return 1

    url = f"{service_url.rstrip('/')}{API_URL_SUFFIX}"
    cfg_data = {
        "enabled": True,
        "service_url": service_url,
        "uid": uid,
        "api_key": api_key,
        "skill_id": "skill-scope",
        "request_from": "openclaw",
    }
    if extra_user_id:
        cfg_data["extra_user_id"] = extra_user_id
    cfg = CsplConfig.from_dict(cfg_data)

    # A: sandbox-verified format (session headers + extra userId)
    headers_a = _build_headers(cfg, trace_id)
    payload_a = _build_payload(cfg, SAMPLE_QUESTION, TOOL_INPUT_SCAN)
    _print_case(
        "A skill-scope + session headers + extra userId (sandbox E-verified)",
        *_post(url, headers_a, payload_a),
    )

    # B: legacy format that triggered behaviordetect 2002
    bd = build_behaviordetect_request(TOOL_INPUT_SCAN, cfg)
    headers_b = dict(headers_a)
    headers_b["x-prd-pkg-name"] = cfg.package_name
    payload_b = {
        "questionText": SAMPLE_QUESTION,
        "textSource": cfg.text_source,
        "action": TOOL_INPUT_SCAN,
        "extra": bd,
        "behaviordetect": {"request": bd},
    }
    _print_case("B legacy (object extra + behaviordetect)", *_post(url, headers_b, payload_b))

    # C: skill_scan route (experimental)
    cfg_scan = CsplConfig.from_dict({**cfg.__dict__, "skill_id": "skill_scan", "request_from": "openclaw"})
    headers_c = _build_headers(cfg_scan, trace_id)
    payload_c = _build_payload(cfg_scan, SAMPLE_QUESTION, TOOL_INPUT_SCAN)
    _print_case("C skill_scan + xy_channel body", *_post(url, headers_c, payload_c))

    print("\nDone. A should match sandbox curl experiment E (retCode=0).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
