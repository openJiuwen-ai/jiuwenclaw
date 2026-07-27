# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Shared WebSocket Origin validation helpers."""

from __future__ import annotations

import ipaddress
import os
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

_ENABLE_ORIGIN_CHECK_ENV = "JIUWENSWARM_ENABLE_ORIGIN_CHECK"
_ALLOWED_ORIGIN_HOSTS_ENV = "JIUWENSWARM_WS_ALLOWED_ORIGIN_HOSTS"
_FORBIDDEN_BODY = b"Forbidden: Origin not allowed\n"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_origin_check_enabled() -> bool:
    """Return whether WebSocket Origin validation is enabled."""
    return os.getenv(_ENABLE_ORIGIN_CHECK_ENV, "").strip() == "1"


def get_allowed_origin_hosts() -> set[str]:
    """Return the global WebSocket Origin hostname allowlist from environment."""
    raw = os.getenv(_ALLOWED_ORIGIN_HOSTS_ENV)
    if raw is None:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_allowed_browser_origin(origin: str | None) -> bool:
    """校验浏览器 Origin 是否允许访问 WebSocket 服务。"""
    allowed_hosts = get_allowed_origin_hosts()
    if origin is None:
        return "none" in allowed_hosts

    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False

    return (parsed.hostname or "").lower() in allowed_hosts


def is_sensitive_browser_origin_allowed(origin: str | None, host: str | None) -> bool:
    """Fail-closed Origin boundary for browser RPCs that mutate credentials.

    The owner-operated v1 accepts an explicit configured hostname, an exact
    same-origin authority, or a loopback-to-loopback browser connection.  A
    missing Origin is intentionally rejected here even when the general
    WebSocket origin check is disabled; non-browser clients use their own
    channels and never need this exception.
    """
    if origin is None or host is None:
        return False
    try:
        parsed_origin = urlsplit(origin)
        parsed_host = urlsplit(f"//{host}")
        origin_port = parsed_origin.port
        host_port = parsed_host.port
    except ValueError:
        return False

    origin_hostname = (parsed_origin.hostname or "").lower()
    host_hostname = (parsed_host.hostname or "").lower()
    if parsed_origin.scheme not in {"http", "https"}:
        return False
    if not origin_hostname:
        return False
    if not host_hostname:
        return False
    if parsed_origin.username is not None:
        return False
    if parsed_origin.password is not None:
        return False
    if parsed_origin.path not in {"", "/"}:
        return False
    if parsed_origin.query:
        return False
    if parsed_origin.fragment:
        return False

    if origin_hostname in get_allowed_origin_hosts():
        return True
    if origin_hostname in _LOOPBACK_HOSTS and host_hostname in _LOOPBACK_HOSTS:
        return True

    default_port = 443 if parsed_origin.scheme == "https" else 80
    return (
        origin_hostname == host_hostname
        and (origin_port or default_port) == (host_port or default_port)
    )


def is_sensitive_browser_request_allowed(ws: Any) -> bool:
    """Validate the Origin/Host pair attached to a WebSocket connection."""
    headers = (
        getattr(getattr(ws, "request", None), "headers", None)
        or getattr(ws, "request_headers", None)
    )
    return is_sensitive_browser_origin_allowed(
        get_header_value(headers, "Origin"),
        get_header_value(headers, "Host"),
    )


def is_loopback_websocket_peer(ws: Any) -> bool:
    """Return whether the server-observed WebSocket peer is local loopback.

    This intentionally ignores client-controlled forwarding and Origin headers.
    Missing, hostname-only, Unix-socket, and malformed peer values fail closed.
    """

    remote = getattr(ws, "remote_address", None)
    if not isinstance(remote, (tuple, list)) or not remote:
        return False
    host = remote[0]
    if not isinstance(host, str) or not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)


def extract_handshake_request(args: tuple[Any, ...]) -> tuple[str, Any]:
    """Extract path and headers from legacy/new websockets process_request args."""
    path = ""
    headers = None

    if len(args) >= 2:
        first, second = args[0], args[1]
        if isinstance(first, str):
            path = first
            headers = second
        else:
            path = getattr(second, "path", "") or ""
            headers = getattr(second, "headers", second)

    return path, headers


def get_header_value(headers: Any, key: str) -> str | None:
    """Read a header from either legacy or modern websockets header containers."""
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(key)
        if value is None:
            value = get(key.lower())
        return str(value) if value is not None else None
    return None


def forbidden_origin_response(process_request_args: tuple[Any, ...]) -> Any:
    """Build a 403 response for legacy/new websockets process_request APIs."""
    status = HTTPStatus.FORBIDDEN
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(_FORBIDDEN_BODY))),
    ]

    if process_request_args and not isinstance(process_request_args[0], str):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        return Response(status.value, status.phrase, Headers(headers), _FORBIDDEN_BODY)

    return status, headers, _FORBIDDEN_BODY
