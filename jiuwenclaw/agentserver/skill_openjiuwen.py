# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""OpenJiuwen Marketplace 操作：HTTP 客户端、URL 白名单校验、搜索/安装."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from jiuwenclaw.agentserver.skill_utils import (
    _OPENJIUWEN_DEFAULT_ALLOWED_DOWNLOAD_HOSTS,
    _OPENJIUWEN_MARKET_BASE_URL_DEFAULT,
    _OPENJIUWEN_MARKET_TIMEOUT,
    _assert_download_url_allowed,
    _get_allowed_download_hosts,
    _host_matches_rule,
)

logger = logging.getLogger(__name__)

_OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS_ENV = "OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS"


def get_openjiuwen_market_base_url() -> str:
    raw = (os.getenv("OPENJIUWEN_MARKET_BASE_URL") or _OPENJIUWEN_MARKET_BASE_URL_DEFAULT).strip()
    return raw.rstrip("/")


def get_openjiuwen_allowed_download_hosts() -> list[str]:
    return _get_allowed_download_hosts(
        _OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS_ENV,
        _OPENJIUWEN_DEFAULT_ALLOWED_DOWNLOAD_HOSTS,
    )


def assert_openjiuwen_download_url_allowed(download_url: str) -> None:
    _assert_download_url_allowed(
        download_url,
        env_key=_OPENJIUWEN_ALLOWED_DOWNLOAD_HOSTS_ENV,
        default_hosts=_OPENJIUWEN_DEFAULT_ALLOWED_DOWNLOAD_HOSTS,
        label="OpenJiuwen download_url",
    )


async def openjiuwen_http_get_data(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = _OPENJIUWEN_MARKET_TIMEOUT,
) -> Any:
    base_url = get_openjiuwen_market_base_url()
    rel_path = path if path.startswith("/") else f"/{path}"
    req_url = f"{base_url}{rel_path}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(req_url, params=params)
    except Exception as exc:
        raise RuntimeError(f"无法连接 OpenJiuwen marketplace: {exc}") from exc

    if not resp.is_success:
        detail = (resp.text or "").strip()[:300]
        raise RuntimeError(f"OpenJiuwen API 错误 HTTP {resp.status_code}: {detail}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"OpenJiuwen API 响应不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OpenJiuwen API 响应格式错误")

    code = payload.get("code", 200)
    if int(code) != 200:
        message = str(payload.get("message", "")).strip() or "OpenJiuwen API 返回失败"
        raise RuntimeError(message)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("OpenJiuwen API 响应 data 格式错误")
    return data
