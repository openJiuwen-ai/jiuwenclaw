# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""本机代理（桌面 FileUploadProxy 等）请求的应用层令牌注入。

背景（docs/named-pipe-migration-design.md §2 支柱 3，claw_desktop 仓）：
桌面本地代理从 loopback TCP 迁到命名管道（np://）后补令牌校验；jiuwen 侧
打本地代理的请求（np:// 管道 或 127.0.0.1/localhost 直连形态都算）携带
``Authorization: Bearer <uploadToken>``。令牌经 stdin 密钥包下发
（common/secrets_bootstrap.get_secret）；取不到则不带——旧版桌面未下发时
令牌缺省，代理侧对应不强制。

注意：令牌只允许注入本机代理请求，绝不随外网 URL（OBS 签名直传等）出站。
"""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from jiuwenswarm.common.np_transport import is_named_pipe_url
from jiuwenswarm.common.secrets_bootstrap import get_secret

_LOCAL_PROXY_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_local_proxy_url(url: str | None) -> bool:
    """URL 是否打向本机代理（np:// 管道 或 loopback 直连形态）。"""
    if not url:
        return False
    if is_named_pipe_url(url):
        return True
    try:
        host = urlsplit(url).hostname or ""
    except Exception:  # noqa: BLE001 - 非法 URL 一律视为非本地代理
        return False
    return host.lower() in _LOCAL_PROXY_HOSTS


def with_local_proxy_bearer(
    headers: Mapping[str, str],
    url: str,
    *,
    secret_key: str = "uploadToken",
) -> dict[str, str]:
    """打本地代理的请求补 ``Authorization: Bearer <token>``；否则原样返回。

    令牌取自密钥包（get_secret）；取不到（旧版桌面未下发/非桌面形态）时不带头。
    返回值始终是新 dict，不改动入参。
    """
    out = dict(headers)
    if not is_local_proxy_url(url):
        return out
    token = str(get_secret(secret_key, "") or "").strip()
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out
