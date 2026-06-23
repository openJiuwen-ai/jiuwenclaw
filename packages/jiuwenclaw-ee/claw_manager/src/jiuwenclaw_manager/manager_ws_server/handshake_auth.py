"""manager_ws_server 握手期 link-auth：校验 Gateway 出示的链路令牌（非对称 + 指纹固定）。

自包含（不依赖 jiuwenclaw 包），只用 link_auth + websockets。兼容 legacy/new
websockets 的 ``process_request`` 入参与响应形态。``CLAW_LINK_AUTH_MODE=off`` 时
:func:`check_handshake` 恒放行，行为与未引入时一致。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from openjiuwen_runtime.foundation.security.link_auth import (
    LINK_TOKEN_HEADER,
    InMemoryPinStore,
    NonceCache,
    verify_and_pin,
)

_UNAUTHORIZED_BODY = b"Unauthorized: link token invalid\n"


def _extract_headers(args: tuple[Any, ...]) -> Any:
    """从 legacy ``(path, headers)`` 或 new ``(connection, request)`` 取出 headers 容器。"""
    if len(args) >= 2:
        first, second = args[0], args[1]
        if isinstance(first, str):
            return second
        return getattr(second, "headers", second)
    if len(args) == 1:
        return getattr(args[0], "headers", None)
    return None


def _get_header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(name)
        if value is None:
            value = get(name.lower())
        return str(value) if value is not None else None
    return None


def _unauthorized_response(args: tuple[Any, ...]) -> Any:
    status = HTTPStatus.UNAUTHORIZED
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(_UNAUTHORIZED_BODY))),
    ]
    if args and not isinstance(args[0], str):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        return Response(status.value, status.phrase, Headers(headers), _UNAUTHORIZED_BODY)
    return status, headers, _UNAUTHORIZED_BODY


def check_handshake(
    args: tuple[Any, ...], nonce_cache: NonceCache, pin_store: InMemoryPinStore
) -> tuple[Any, str] | None:
    """校验握手头里 Gateway 出示的链路令牌：验签 + TOFU 指纹固定。

    放行返回 ``None``；拒绝返回 ``(reject_response, reason)``，由调用方回送该响应。
    """
    headers = _extract_headers(args)
    token = _get_header(headers, LINK_TOKEN_HEADER)
    result = verify_and_pin(
        pin_store,
        token,
        expect_type="gateway",
        nonce_cache=nonce_cache,
    )
    if result.allowed:
        return None
    return _unauthorized_response(args), result.reason
