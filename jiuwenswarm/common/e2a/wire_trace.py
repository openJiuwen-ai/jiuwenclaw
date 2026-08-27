# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""E2A 原始报文落盘（测试用）。

在服务端把「客户端发来的原始请求」与「服务端回给客户端的每一帧原始响应」，
按会话落成 .jsonl，供测试人员捞输入/输出。

缺省关闭；两种开启方式（任一即可，正式 exe 不设置即为关）：

1. 环境变量（源码/自行启动后端时）：
    JIUWENSWARM_E2A_TRACE=1
    JIUWENSWARM_E2A_TRACE_DIR=<可选，默认 <workspace>/e2a_traces>

2. 数据目录开关文件（测试人员只有 exe 时用）：
    在 <JIUWENSWARM_DATA_DIR>/ 下放 e2a_trace.json：
      { "enabled": true, "dir": "D:\\\\test\\\\e2a_traces" }
    dir 可选，缺省 <workspace>/e2a_traces；改文件后 3 秒内生效，无需重启。

落盘布局（按场景/会话分目录）：
    <root>/<会话标题>__<session_id>/<method>__<request_id>.jsonl
      第 1 行：{"role":"in",  "ts":..., "data":<客户端原始请求 JSON>}
      后续行：{"role":"out", "ts":..., "data":<服务端原始响应帧 JSON>}
    文件名带 method+request_id，同一会话多次请求各自一个文件，互不覆盖。
    本模块只落原始输入/输出报文，不做任何业务对错判定。

会话标题来自 session.create / session.rename（即测试给会话起的名字/首条消息），
用于区分「在测哪个场景」；每次 chat.send 是一条 request，文件名里带 method+request_id
即可区分多次运行。无标题的会话落在 <session_id> 目录，会话无关的请求
（initialize 等）落在 <root>/__sessionless__/。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# request_id -> session_id（发送响应时按 request_id 找回会话）
_REQUEST_SESSION: dict[str, str] = {}
# request_id -> method（文件名 <method>__<request_id>.jsonl 用）
_REQUEST_METHOD: dict[str, str] = {}
# request_id -> 待认领的会话标题（session.create/rename 请求先记，响应/后续回填）
_PENDING_TITLE: dict[str, str] = {}
# session_id -> 会话标题（目录命名 <标题>__<session_id>）
_SESSION_TITLE: dict[str, str] = {}

_TRUE_VALUES = {"1", "true", "on", "yes", "enable", "enabled"}
# 开关文件缓存（避免每一帧都读磁盘；改动后最多 3 秒生效）
_MARKER_TTL_SECONDS = 3.0
_MARKER_CACHE: dict[str, Any] = {"ts": 0.0, "data": {}}


def _workspace_dir() -> Path:
    data_dir = os.environ.get("JIUWENSWARM_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir)
    try:
        from jiuwenswarm.common.utils import get_user_workspace_dir
        return Path(get_user_workspace_dir())
    except Exception:  # noqa: BLE001
        return Path(".")


def _marker_path() -> Path:
    return _workspace_dir() / "e2a_trace.json"


def _read_marker() -> dict[str, Any]:
    now = time.time()
    if now - _MARKER_CACHE["ts"] < _MARKER_TTL_SECONDS:
        return _MARKER_CACHE["data"]
    try:
        path = _marker_path()
        if not path.exists():
            _MARKER_CACHE.update(ts=now, data={})
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) else {}
        _MARKER_CACHE.update(ts=now, data=data)
        return data
    except Exception:  # noqa: BLE001
        _MARKER_CACHE.update(ts=now, data={})
        return {}


def _enabled() -> bool:
    if os.environ.get("JIUWENSWARM_E2A_TRACE", "").strip().lower() in _TRUE_VALUES:
        return True
    return bool(_read_marker().get("enabled", False))


def _trace_root() -> Path:
    explicit = os.environ.get("JIUWENSWARM_E2A_TRACE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    marker_dir = _read_marker().get("dir")
    if isinstance(marker_dir, str) and marker_dir.strip():
        return Path(marker_dir.strip())
    return _workspace_dir() / "e2a_traces"


def _sanitize(value: Any) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return "unknown"
    for ch in '\\/:*?"<>|':
        value = value.replace(ch, "_")
    return value[:160]


def _session_label(session_id: Any) -> str:
    """会话目录名：有标题用 <标题>__<session_id>，否则 session_id。"""
    sid = _sanitize(session_id)
    title = _SESSION_TITLE.get(str(session_id).strip()) if isinstance(session_id, str) else None
    if title:
        return f"{_sanitize(title)}__{sid}"
    return sid


def _note_request_meta(payload: dict[str, Any]) -> None:
    """从请求里记录 method / 会话标题（session.create / session.rename）。"""
    method = payload.get("method")
    rid = payload.get("request_id")
    if isinstance(rid, str) and rid.strip() and isinstance(method, str) and method.strip():
        _REQUEST_METHOD[rid] = method
    params = payload.get("params")
    if not isinstance(params, dict):
        return
    title = params.get("title")
    if not (isinstance(title, str) and title.strip()):
        return
    if method == "session.create" and isinstance(rid, str) and rid.strip():
        _PENDING_TITLE[rid] = title.strip()
    elif method == "session.rename":
        sid = payload.get("session_id")
        if isinstance(sid, str) and sid.strip():
            _SESSION_TITLE[sid] = title.strip()


def _note_session_from_result(wire: dict[str, Any]) -> None:
    """session.create 响应带回 session_id，把待认领标题回填。"""
    body = wire.get("body")
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return
    sid = result.get("session_id") or result.get("sessionId")
    if not (isinstance(sid, str) and sid.strip()):
        return
    rid = wire.get("request_id")
    title = _PENDING_TITLE.pop(rid, None) if isinstance(rid, str) else None
    if title:
        _SESSION_TITLE[sid] = title


def _session_from_wire(wire: dict[str, Any]) -> str | None:
    body = wire.get("body")
    if isinstance(body, dict):
        delta = body.get("delta")
        if isinstance(delta, dict):
            sid = delta.get("session_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
        result = body.get("result")
        if isinstance(result, dict):
            sid = result.get("session_id") or result.get("sessionId")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
    top = wire.get("session_id")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return None


def _folder_for(session_id: Any) -> str:
    raw = session_id if isinstance(session_id, str) else None
    if not raw or not raw.strip() or raw == "unknown":
        return "__sessionless__"
    return _session_label(raw)


def _append(role: str, session_id: Any, request_id: Any, data: Any) -> None:
    if not _enabled():
        return
    rid = _sanitize(request_id)
    method = _sanitize(_REQUEST_METHOD.get(rid, ""))
    try:
        with _LOCK:
            dest_dir = _trace_root() / _folder_for(session_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{method}__{rid}.jsonl"
            line = json.dumps(
                {"role": role, "ts": time.time(), "data": data},
                ensure_ascii=False,
                default=str,
            )
            with open(dest, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[e2a_trace] 写原始报文失败: %s", exc)


def trace_inbound(payload: Any) -> None:
    """记录一条客户端请求（原始 JSON）。"""
    if not _enabled() or not isinstance(payload, dict):
        return
    _note_request_meta(payload)
    session_id = payload.get("session_id")
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id.strip() and isinstance(session_id, str) and session_id.strip():
        _REQUEST_SESSION[request_id] = session_id
    _append("in", session_id, request_id, payload)


def trace_outbound(wire: Any) -> None:
    """记录一帧服务端响应（原始 JSON）。"""
    if not _enabled() or not isinstance(wire, dict):
        return
    _note_session_from_result(wire)
    request_id = wire.get("request_id")
    session_id = (
        _REQUEST_SESSION.get(request_id) if isinstance(request_id, str) else None
    ) or _session_from_wire(wire)
    if not session_id and wire.get("type") == "event":
        session_id = "__server__"
    _append("out", session_id, request_id, wire)


__all__ = ["trace_inbound", "trace_outbound"]
