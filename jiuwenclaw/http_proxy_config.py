# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Overlay-aware HTTP proxy resolution for outbound requests.

Reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` via :func:`read_env`
so namespaced tenant env and task overlays apply. Callers pass explicit
``proxies`` / ``proxy`` and disable ``trust_env`` instead of relying on bare
``os.environ``.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import requests

from jiuwenclaw.local_env_config import read_env

_PROXY_ENV_KEYS: tuple[str, ...] = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
)
_NO_PROXY_ENV_KEYS: tuple[str, ...] = ("NO_PROXY", "no_proxy")


def read_proxy_url() -> str:
    """Return the configured proxy URL for the current env context."""
    for key in _PROXY_ENV_KEYS:
        raw = read_env(key, "").strip()
        if raw:
            return raw
    return ""


def read_no_proxy_list() -> list[str]:
    """Parse NO_PROXY for the current env context (deduped, lowercased)."""
    result: list[str] = []
    seen: set[str] = set()
    for key in _NO_PROXY_ENV_KEYS:
        raw = read_env(key, "").strip()
        if not raw:
            continue
        normalized = raw.replace(" ", ",").replace(";", ",")
        for item in normalized.split(","):
            entry = item.strip().lower()
            if entry and entry not in seen:
                seen.add(entry)
                result.append(entry)
    return result


def _hostname_matches_no_proxy(hostname: str, no_proxy_list: list[str]) -> bool:
    hostname_lower = hostname.lower()
    for entry in no_proxy_list:
        if entry == "*":
            return True
        if entry == hostname_lower:
            return True
        if entry.startswith(".") and hostname_lower.endswith(entry):
            return True
        if _is_ip_match(hostname_lower, entry):
            return True
    return False


def _is_ip_match(hostname: str, entry: str) -> bool:
    try:
        ip_addr = ipaddress.ip_address(hostname)
        if "/" in entry:
            network = ipaddress.ip_network(entry, strict=False)
            return ip_addr in network
        return ip_addr == ipaddress.ip_address(entry)
    except ValueError:
        return False


def should_bypass_proxy(url: str) -> bool:
    """Return True when *url* should not use the configured HTTP proxy."""
    parsed = urlparse(url or "")
    hostname = parsed.hostname
    if not hostname:
        return False
    no_proxy_list = read_no_proxy_list()
    if not no_proxy_list:
        return False
    return _hostname_matches_no_proxy(hostname, no_proxy_list)


def resolve_requests_proxies(url: str) -> dict[str, str] | None:
    """Return a ``requests`` proxies mapping, or ``None`` for a direct connection."""
    if url and should_bypass_proxy(url):
        return None
    proxy_url = read_proxy_url()
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def resolve_httpx_proxy(url: str) -> str | None:
    """Return an ``httpx`` proxy URL, or ``None`` for a direct connection."""
    if url and should_bypass_proxy(url):
        return None
    proxy_url = read_proxy_url()
    return proxy_url or None


def prepare_requests_kwargs(url: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge overlay-aware proxy settings into ``requests`` keyword arguments."""
    out = dict(kwargs or {})
    proxies = resolve_requests_proxies(url)
    if proxies:
        out["proxies"] = proxies
    return out


def _requests_verify() -> bool:
    raw = read_env("JIUWENCLAW_SSL_VERIFY", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True


def requests_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Issue a ``requests`` call using overlay-aware proxy settings only."""
    method_up = method.upper()
    kwargs = prepare_requests_kwargs(url, kwargs)
    kwargs.setdefault("verify", _requests_verify())
    try:
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method_up, url, **kwargs)
    except requests.exceptions.ProxyError:
        fallback = dict(kwargs)
        fallback.pop("proxies", None)
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method_up, url, **fallback)


def requests_get(url: str, **kwargs: Any) -> requests.Response:
    return requests_request("GET", url, **kwargs)


def requests_post(url: str, **kwargs: Any) -> requests.Response:
    return requests_request("POST", url, **kwargs)
