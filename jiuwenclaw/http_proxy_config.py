# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Overlay-aware HTTP proxy resolution for outbound requests.

Reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` via tip (:func:`read_env`)
first so namespaced tenant env and task overlays apply, then falls back to bare
``os.environ`` for process/spawn values. Callers pass explicit ``proxies`` /
``proxy`` and disable ``trust_env`` so requests does not scrape the whole
process environment.
"""

from __future__ import annotations

import ipaddress
import os
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


def _read_env_then_environ(keys: tuple[str, ...]) -> str:
    """Tip/overlay first, then process env."""
    for key in keys:
        raw = read_env(key, "").strip()
        if raw:
            return raw
    for key in keys:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return ""


def read_proxy_url() -> str:
    """Return the configured proxy URL for the current env context."""
    return _read_env_then_environ(_PROXY_ENV_KEYS)


def read_no_proxy_list() -> list[str]:
    """Parse NO_PROXY for the current env context (deduped, lowercased)."""
    result: list[str] = []
    seen: set[str] = set()

    def _consume(raw: str) -> None:
        normalized = raw.replace(" ", ",").replace(";", ",")
        for item in normalized.split(","):
            entry = item.strip().lower()
            if entry and entry not in seen:
                seen.add(entry)
                result.append(entry)

    tip_hit = False
    for key in _NO_PROXY_ENV_KEYS:
        raw = read_env(key, "").strip()
        if raw:
            tip_hit = True
            _consume(raw)
    if tip_hit:
        return result
    for key in _NO_PROXY_ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            _consume(raw)
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


def _ssl_verify_raw() -> str:
    raw = read_env("JIUWENCLAW_SSL_VERIFY", "").strip()
    if raw:
        return raw
    return (os.environ.get("JIUWENCLAW_SSL_VERIFY") or "").strip()


def ssl_verify_enabled(default: bool = True) -> bool:
    """Whether TLS verification is enabled (tip, then process environ)."""
    text = _ssl_verify_raw().strip().lower()
    if text in ("0", "false", "no", "off"):
        return False
    if text in ("1", "true", "yes", "on"):
        return True
    return default


def resolve_requests_verify() -> bool | str:
    """Return requests ``verify`` kwarg: False | CA path | True.

    Priority:
    1. ``JIUWENCLAW_SSL_VERIFY`` falsy → ``False``
    2. ``REQUESTS_CA_BUNDLE`` exists → CA path
    3. otherwise → ``True``

    Explicit CA injection is required because ``trust_env=False`` prevents
    requests from reading ``REQUESTS_CA_BUNDLE`` from the process environment.
    """
    if not ssl_verify_enabled():
        return False
    ca_bundle = (os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle
    return True


def _requests_verify() -> bool | str:
    return resolve_requests_verify()


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
