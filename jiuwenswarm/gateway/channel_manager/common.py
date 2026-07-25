# jiuwenswarm/gateway/channel_manager/common.py
"""WebSocket 连接公共工具函数"""
from urllib.parse import urlparse, parse_qs
from typing import Any


def extract_token(ws: Any) -> str | None:
    """从 WebSocket 连接中提取 JWT token

    优先级：
    1. URL 查询参数 ?token=xxx
    2. HTTP 请求头 Authorization: Bearer xxx
    3. HTTP 请求头 X-Token: xxx
    """
    # 1. 从 URL 查询参数提取
    path = getattr(ws, "path", "") or ""
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]
    if token:
        return token

    # 2. 从请求头提取
    headers = extract_headers(ws)
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]

    # 3. 从 X-Token 请求头提取
    x_token = headers.get("X-Token", "")
    if x_token:
        return x_token

    return None


def extract_headers(ws: Any) -> dict:
    """提取 WebSocket 连接的 HTTP 请求头"""
    headers = (
            getattr(getattr(ws, "request", None), "headers", None)
            or getattr(ws, "request_headers", None)
    )
    if headers:
        return dict(headers)
    return {}


def get_remote_addr(ws: Any) -> str:
    """获取客户端 IP 地址"""
    remote = getattr(ws, "remote_address", None)
    if remote:
        if isinstance(remote, (list, tuple)):
            return f"{remote[0]}:{remote[1]}"
        return str(remote)
    return ""