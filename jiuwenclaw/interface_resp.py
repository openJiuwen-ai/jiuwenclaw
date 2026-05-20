# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pipe-delimited RESP access log → ``interface.log`` (same directory as ``full.log``)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

_RESP_LOGGER = "jiuwenclaw.interface.resp"
_TZ = ZoneInfo("Asia/Shanghai")
_CHAT = "/v1/chat/completions"

_configured = False
_version: str | None = None


def get_interface_request_id() -> str:
    """与 ``interface_deep._LLM_TRACE_REQUEST_ID`` / full.log 对齐（单一来源）。"""
    try:
        from jiuwenclaw.agentserver.deep_agent.interface_deep import _LLM_TRACE_REQUEST_ID

        trace_rid = str(_LLM_TRACE_REQUEST_ID.get() or "").strip()
        if trace_rid:
            return trace_rid[:256]
    except Exception:
        logging.getLogger(__name__).debug("interface request id lookup failed", exc_info=True)
    return ""


def session_id_from_context(session: Any | None = None) -> str:
    """从显式 session 或 ``_retry_session`` ContextVar 解析 session_id。"""
    if session is not None and callable(getattr(session, "get_session_id", None)):
        sid = session.get_session_id()
        return str(sid) if sid else ""
    try:
        from jiuwenclaw.jiuwen_core_patch import _retry_session

        ctx_session = _retry_session.get()
        if ctx_session is not None and callable(getattr(ctx_session, "get_session_id", None)):
            sid = ctx_session.get_session_id()
            return str(sid) if sid else ""
    except Exception:
        logging.getLogger(__name__).debug("session_id from context failed", exc_info=True)
    return ""


def ensure_interface_logger() -> logging.Logger:
    global _configured
    log = logging.getLogger(_RESP_LOGGER)
    if _configured:
        return log
    try:
        from jiuwenclaw.utils import (
            SafeRotatingFileHandler,
            _LOG_FILE_BACKUP_COUNT,
            _LOG_FILE_MAX_BYTES,
            get_logs_dir,
        )

        root = get_logs_dir()
        root.mkdir(parents=True, exist_ok=True)
        h = SafeRotatingFileHandler(
            filename=root / "interface.log",
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setFormatter(logging.Formatter("%(message)s"))
        h.setLevel(logging.INFO)
        log.setLevel(logging.INFO)
        log.propagate = False
        log.handlers.clear()
        log.addHandler(h)
        _configured = True
    except Exception:
        logging.getLogger(__name__).debug("interface.log setup failed", exc_info=True)
    return log


def _ver() -> str:
    global _version
    if _version:
        return _version
    try:
        from importlib.metadata import version as pkg_version

        _version = pkg_version("jiuwenclaw")
    except Exception:
        _version = "0.1.0"
    return _version


def _cell(v: str | None) -> str:
    s = str(v or "").replace("\n", " ").replace("\r", "").strip()
    return "-" if not s else s.replace("|", "%7C")


def _rid(header: str | None, fallback: str | None) -> str:
    """Resolve request_id: header → explicit fallback → agent context (never synthesize UUID)."""
    for c in (header, fallback, get_interface_request_id()):
        t = str(c or "").strip()[:256]
        if t:
            return t
    return ""


def _http_status(exc: BaseException | None) -> str:
    if exc is None:
        return "502"
    if isinstance(exc, asyncio.TimeoutError):
        return "504"
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code is not None:
        return str(code)
    msg = str(exc).lower()
    for c in ("401", "403", "404", "422", "429", "500", "502", "503", "504"):
        if c in msg:
            return c
    return "502"


def _write_line(
    *,
    method_path: str,
    src: str,
    peer: str,
    host: str,
    elapsed_ms: int,
    ok: bool,
    status_code: str,
    session_id: str | None,
    rid: str,
    info: dict[str, Any] | None,
) -> None:
    info_out = {k: v for k, v in (info or {}).items() if k != "session_id"}
    line = "|".join(
        [
            _cell(_ver()),
            _cell(datetime.now(tz=_TZ).isoformat(timespec="milliseconds")),
            "RESP",
            _cell(method_path),
            _cell(src),
            _cell(peer),
            _cell(host),
            str(max(0, int(elapsed_ms))),
            "Y" if ok else "N",
            _cell(status_code),
            _cell(session_id),
            _cell(rid),
            json.dumps(info_out, ensure_ascii=False, separators=(",", ":")) if info_out else "{}",
        ]
    )
    try:
        ensure_interface_logger().info("%s", line)
    except Exception:
        logging.getLogger(__name__).debug("interface RESP line write failed", exc_info=True)


class InterfaceRespLog:
    """Start → set_result → write (one RESP line per logical call)."""

    __slots__ = ("_host", "_info", "_method_path", "_ok", "_peer", "_rid", "_session_id", "_src", "_t0", "_status")

    def __init__(
        self,
        *,
        method_path: str,
        src: str = "-",
        peer: str = "-",
        host: str = "-",
        session_id: str | None = None,
        rid: str | None = None,
        header_rid: str | None = None,
        info: dict[str, Any] | None = None,
        start_mono: float | None = None,
    ) -> None:
        self._t0 = start_mono if start_mono and start_mono > 0 else time.monotonic()
        self._method_path = method_path
        self._src, self._peer, self._host = src, peer, host
        self._session_id = session_id
        self._rid = _rid(header_rid, rid)
        self._info = dict(info) if info else {}
        self._ok: bool | None = None
        self._status: str | None = None

    @classmethod
    def start(cls, **kwargs: Any) -> InterfaceRespLog:
        return cls(**kwargs)

    def set_result(
        self,
        ok: bool,
        *,
        status_code: str | None = None,
        err: BaseException | None = None,
    ) -> InterfaceRespLog:
        self._ok = ok
        self._status = status_code or ("200" if ok else _http_status(err))
        return self

    def write(self) -> None:
        if self._ok is None or self._status is None:
            return
        ms = max(0, int((time.monotonic() - self._t0) * 1000))
        _write_line(
            method_path=self._method_path,
            src=self._src,
            peer=self._peer,
            host=self._host,
            elapsed_ms=ms,
            ok=self._ok,
            status_code=self._status,
            session_id=self._session_id,
            rid=self._rid,
            info=self._info,
        )


def finish_iface_record(record: InterfaceRespLog | Any, err: BaseException | None) -> None:
    """Write one RESP line; safe when ``record`` is not an ``InterfaceRespLog``."""
    if not isinstance(record, InterfaceRespLog):
        return
    try:
        record.set_result(ok=err is None, err=err).write()
    except Exception:
        logging.getLogger(__name__).debug("interface RESP write failed", exc_info=True)


@asynccontextmanager
async def _tracked_resp(rec: InterfaceRespLog):
    err: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        err = exc
        raise
    finally:
        finish_iface_record(rec, err)


@asynccontextmanager
async def track_llm_resp(client: Any, *, streaming: bool):
    """One RESP line per LLM invoke/stream (including retries)."""
    rec = start_llm_resp(client, streaming=streaming, session_id=session_id_from_context())
    async with _tracked_resp(rec):
        yield


def _ws_peer(ws: Any) -> tuple[str, str, str, str | None]:
    src, peer, host, hdr_rid = "-", "-", "-", None
    ra, la = getattr(ws, "remote_address", None), getattr(ws, "local_address", None)
    if isinstance(ra, (list, tuple)) and ra:
        src = str(ra[0])
    if isinstance(la, (list, tuple)) and la:
        peer = str(la[0])
    headers = getattr(ws, "request_headers", None) or getattr(getattr(ws, "request", None), "headers", None)
    if headers is not None:
        for key in ("Host", "host"):
            hv = headers.get(key) if hasattr(headers, "get") else None
            if hv:
                host = str(hv)
                break
        for key in ("X-Request-ID", "x-request-id"):
            rv = headers.get(key) if hasattr(headers, "get") else None
            if rv:
                hdr_rid = str(rv)
                break
    return src, peer, host, hdr_rid


# AgentServer E2A 入站：聊天类请求一条 RESP（客户端 WS 边界不单独记 POST，避免 begin 无 finish 落盘）
_E2A_CHAT_METHODS = frozenset({
    "chat.send",
    "chat.resume",
    "chat.interrupt",
    "chat.user_answer",
})


def e2a_method_label(req_method: Any | None, params: dict[str, Any] | None) -> str:
    if req_method is not None:
        return str(getattr(req_method, "value", "") or "").strip()
    if isinstance(params, dict):
        return str(params.get("event_type") or "").strip()
    return ""


def should_log_e2a_interface(req_method: Any | None, params: dict[str, Any] | None) -> bool:
    return e2a_method_label(req_method, params) in _E2A_CHAT_METHODS


def start_gateway_e2a_resp(
    ws: Any,
    *,
    request_id: str,
    channel_id: str,
    session_id: str | None,
    method: str,
) -> InterfaceRespLog:
    """Gateway → AgentServer inbound E2A request (one RESP line per envelope)."""
    src, peer, host, hdr = _ws_peer(ws)
    method_path = f"E2A {method}".strip()
    return InterfaceRespLog.start(
        method_path=method_path,
        src=src,
        peer=peer,
        host=host,
        session_id=session_id or None,
        rid=_rid(hdr, request_id),
        header_rid=hdr,
        info={"kind": "e2a", "channel_id": channel_id, "method": method},
    )


@asynccontextmanager
async def track_e2a_resp(
    ws: Any,
    *,
    request_id: str,
    channel_id: str,
    session_id: str | None,
    method: str,
):
    rec = start_gateway_e2a_resp(
        ws,
        request_id=request_id,
        channel_id=channel_id,
        session_id=session_id,
        method=method,
    )
    async with _tracked_resp(rec):
        yield


@asynccontextmanager
async def maybe_track_e2a_resp(
    ws: Any,
    *,
    req_method: Any | None,
    params: dict[str, Any] | None,
    request_id: str,
    channel_id: str,
    session_id: str | None,
):
    if should_log_e2a_interface(req_method, params):
        async with track_e2a_resp(
            ws,
            request_id=request_id,
            channel_id=channel_id,
            session_id=session_id,
            method=e2a_method_label(req_method, params),
        ):
            yield
    else:
        yield


def start_llm_resp(client: Any, *, streaming: bool, session_id: str) -> InterfaceRespLog:
    host, api_base = "-", ""
    mcc = getattr(client, "model_client_config", None)
    if mcc is not None:
        api_base = str(getattr(mcc, "api_base", "") or "").strip()
        if api_base:
            host = urlparse(api_base).hostname or "-"
    model = ""
    mc = getattr(client, "model_config", None)
    if mc is not None:
        model = str(getattr(mc, "model_name", "") or "")
    provider = str(getattr(mcc, "client_provider", "") or "unknown").lower() if mcc else "unknown"
    info: dict[str, Any] = {
        "kind": "llm",
        "streaming": streaming,
        "model": model,
        "system": provider,
    }
    if api_base:
        info["api_base"] = api_base
    return InterfaceRespLog.start(
        method_path=f"POST {_CHAT}",
        host=host,
        session_id=session_id or None,
        info=info,
    )


@asynccontextmanager
async def track_tool_resp(tool_name: str, *, session_id: str | None = None):
    sid = session_id or session_id_from_context() or None
    rec = InterfaceRespLog.start(
        method_path=f"TOOL {tool_name}",
        session_id=sid,
        info={"kind": "tool", "tool": tool_name},
    )
    async with _tracked_resp(rec):
        yield
