# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web fetch tools implemented with openjiuwen @tool style."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import requests
import urllib3
from openjiuwen.core.foundation.tool import tool

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_FREE_SEARCH_DEFAULT_NO_PROXY = (
    "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com"
)
_CHARSET_HEADER_RE = re.compile(r"charset=([^\s;]+)", flags=re.IGNORECASE)
_CHARSET_META_RE = re.compile(
    br"""<meta[^>]+charset=["']?\s*([A-Za-z0-9._-]+)""",
    flags=re.IGNORECASE,
)

# Query parameter names that may carry credentials / presigned tokens. When any of
# these is present, the URL must never be forwarded to a third-party reader.
_SENSITIVE_QUERY_PARAMS = frozenset(
    {
        # AWS S3 / CloudFront presigned URLs
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-algorithm",
        "x-amz-date",
        "x-amz-expires",
        "x-amz-signedheaders",
        # Common credential / token parameters
        "signature",
        "sig",
        "token",
        "access_token",
        "id_token",
        "refresh_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "client_secret",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "session",
        "session_id",
        "sessionid",
        "private_key",
    }
)


def _extract_declared_charset(response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "") or ""
    header_match = _CHARSET_HEADER_RE.search(content_type)
    if header_match:
        return header_match.group(1).strip().strip("\"'")

    head_bytes = (response.content or b"")[:4096]
    meta_match = _CHARSET_META_RE.search(head_bytes)
    if meta_match:
        try:
            return meta_match.group(1).decode("ascii", errors="ignore").strip()
        except Exception:  # noqa: BLE001 - malformed charset falls back to ""
            return ""
    return ""


def _get_free_search_proxy_url() -> str:
    return str(os.environ.get(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _free_search_ssl_verify() -> bool:
    return _env_bool(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _disable_insecure_request_warning() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _no_proxy_entries() -> list[str]:
    configured = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or _FREE_SEARCH_DEFAULT_NO_PROXY
    return [entry.strip().lower() for entry in configured.split(",") if entry.strip()]


def _should_bypass_free_search_proxy(url: str) -> bool:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url:
        return True
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False
    for entry in _no_proxy_entries():
        if entry == "*":
            return True
        if entry.startswith(".") and (hostname == entry[1:] or hostname.endswith(entry)):
            return True
        if hostname == entry or hostname.endswith(f".{entry}"):
            return True
    return False


def _apply_free_search_proxy(url: str, kwargs: dict[str, object]) -> bool:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url or _should_bypass_free_search_proxy(url):
        return False
    kwargs.setdefault("proxies", {"http": proxy_url, "https": proxy_url})
    return True


def _decode_response_text(response: requests.Response) -> str:
    raw = response.content or b""
    if not raw:
        return ""

    declared = (_extract_declared_charset(response) or "").lower()
    response_encoding = (response.encoding or "").strip().lower()
    apparent = (response.apparent_encoding or "").strip().lower()

    # Prefer explicit non-latin declaration first; then utf-8; then heuristics.
    candidates: list[str] = []
    if declared and declared not in {"iso-8859-1", "latin-1", "latin1"}:
        candidates.append(declared)

    candidates.extend(
        [
            "utf-8",
            apparent,
            response_encoding,
            "gb18030",
            "big5",
            "shift_jis",
            "cp1252",
            "iso-8859-1",
        ]
    )

    seen: set[str] = set()
    for enc in candidates:
        enc = (enc or "").strip().lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue

    # Last-resort fallback.
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str, **kwargs) -> requests.Response:
    """Try normal requests first; retry without env proxies on ProxyError."""
    explicit_proxy = _apply_free_search_proxy(url, kwargs)
    verify = _free_search_ssl_verify()
    kwargs.setdefault("verify", verify)
    if verify is False:
        _disable_insecure_request_warning()
    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.ProxyError:
        if explicit_proxy:
            raise
        with requests.Session() as session:
            session.trust_env = False
            return session.get(url, **kwargs)


def _clip_text(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n...[truncated]"


def _strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _decode_ddg_redirect(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path != "/l/":
        return url
    query = parse_qs(parsed.query)
    target = query.get("uddg")
    if not target:
        return url
    return unquote(target[0])


def _normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    decoded = _decode_ddg_redirect(raw)
    if decoded.startswith(("http://", "https://")):
        return decoded
    return f"https://{decoded}"


def _fetch_via_jina_reader_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
    reader_url = f"https://r.jina.ai/{url}"
    response = _http_get(reader_url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
    response.raise_for_status()
    return {
        "url": url,
        "status_code": response.status_code,
        "title": "",
        "content": _decode_response_text(response).strip(),
    }


def _is_private_or_loopback(hostname: str) -> bool:
    """True if the hostname resolves to a private/loopback/link-local/reserved address."""
    raw = hostname.strip("[]")
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    try:
        infos = socket.getaddrinfo(raw, None)
    except socket.gaierror:
        # DNS resolution failed locally: we cannot prove the target is public.
        # Do NOT block the fallback here - a local DNS outage is precisely the
        # case the reader fallback exists for, and a resolvable hostname is not
        # evidence of an internal address.
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def _should_skip_jina_fallback(url: str) -> bool:
    """Security guard: never forward credential-bearing or non-public URLs to r.jina.ai.

    The Jina fallback rewrites the URL to https://r.jina.ai/<original-url>, which sends
    the full original URL (including any query string) to a third-party service. Skip
    the fallback when the URL is not a public http(s) target so presigned tokens, query
    credentials and internal hostnames never leave the trust boundary.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    hostname = parsed.hostname
    if not hostname:
        return True
    if _is_private_or_loopback(hostname):
        return True
    query = parse_qs(parsed.query)
    return any(key.lower() in _SENSITIVE_QUERY_PARAMS for key in query)


def _fetch_webpage_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
    try:
        response = _http_get(url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        # Network-level failures: try the reader fallback before giving up,
        # unless the URL must not leave the trust boundary.
        if not _should_skip_jina_fallback(url):
            try:
                return _fetch_via_jina_reader_sync(url, timeout_seconds)
            except Exception as reader_exc:
                raise exc from reader_exc
        raise
    if response.status_code in {401, 403, 429}:
        if not _should_skip_jina_fallback(url):
            return _fetch_via_jina_reader_sync(url, timeout_seconds)
        response.raise_for_status()
    response.raise_for_status()

    text = _decode_response_text(response)
    content_type = response.headers.get("Content-Type", "")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = _strip_tags(title_match.group(1)) if title_match else ""

    if "html" in content_type.lower():
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = _strip_tags(text)
    else:
        text = re.sub(r"\s+", " ", text).strip()

    return {
        "url": response.url,
        "status_code": response.status_code,
        "title": title,
        "content": text,
    }


def _classify_fetch_error(exc: Exception) -> str:
    """Classify fetch failures so agents can tell access errors from empty results."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection"
    if isinstance(exc, requests.exceptions.HTTPError):
        return "http_error"
    if isinstance(exc, requests.exceptions.RequestException):
        return "request"
    return "unknown"


@tool(
    name="mcp_fetch_webpage",
    description=(
        "Fetch webpage text content from URL. Returns status/title/plain text content. "
        "Set max_chars=0 to disable output clipping. "
        "Use a larger timeout_seconds for slow websites. "
        "A result starting with [FETCH_ERROR: <category>] means the page could NOT be "
        "ACCESSED (network/HTTP failure), not that the content does not exist — try an "
        "alternative source (API endpoint, search engine) before concluding absence."
    ),
)
async def mcp_fetch_webpage(url: str, max_chars: int = 0, timeout_seconds: int = 30) -> str:
    url = _normalize_url(url)
    if not url:
        return "[ERROR]: url cannot be empty."

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = 0
    max_chars = max(max_chars, 0)

    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = 30
    try:
        max_timeout_seconds = int(os.getenv("MCP_FETCH_WEBPAGE_MAX_TIMEOUT_SECONDS") or "3600")
    except ValueError:
        max_timeout_seconds = 3600
    max_timeout_seconds = max(1, max_timeout_seconds)
    timeout_seconds = max(1, min(timeout_seconds, max_timeout_seconds))

    try:
        data = await asyncio.to_thread(_fetch_webpage_sync, url, timeout_seconds)
    except requests.exceptions.RequestException as exc:
        category = _classify_fetch_error(exc)
        return (
            f"[FETCH_ERROR: {category}] failed to fetch webpage: {exc}\n"
            "This is an ACCESS failure (network/HTTP), not an empty result. "
            "Retry with an alternative source (API endpoint, search engine) "
            "before concluding the content does not exist."
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure to the model
        return f"[ERROR]: failed to fetch webpage: {exc}"

    lines = [
        f"URL: {data.get('url', url)}",
        f"Status: {data.get('status_code', '')}",
    ]
    if data.get("title"):
        lines.append(f"Title: {data['title']}")
    lines.append("Content:")
    lines.append(_clip_text(str(data.get("content", "") or ""), max_chars) or "[empty]")
    return "\n".join(lines)
