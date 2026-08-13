# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Skill 管理通用工具函数：路径安全、网络代理、安全删除、常量定义."""

from __future__ import annotations

import logging
import os
import shutil
import ssl
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

import urllib3
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from jiuwenclaw.utils import (
    get_agent_root_dir,
    get_agent_skills_dir,
    get_builtin_skills_dir,
    is_package_installation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_EVOLUTION_FILENAME = "evolutions.json"

_SKILLNET_DOWNLOAD_TIMEOUT: int = int(os.environ.get("SKILLNET_DOWNLOAD_TIMEOUT", "60"))
_SKILLNET_MAX_RETRIES: int = int(os.environ.get("SKILLNET_MAX_RETRIES", "3"))
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_SKILLNET_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_SKILLNET_NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
_FREE_SEARCH_DEFAULT_NO_PROXY = "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com"

_OPENJIUWEN_MARKET_TIMEOUT: float = float(os.environ.get("OPENJIUWEN_MARKET_TIMEOUT", "60"))
_OPENJIUWEN_MARKET_BASE_URL_DEFAULT = "https://teamskills.openjiuwen.com"
_OPENJIUWEN_DEFAULT_ALLOWED_DOWNLOAD_HOSTS: tuple[str, ...] = ("openjiuwen-market.obs.*.myhuaweicloud.com",)
_IMPORT_LOCAL_REMOTE_TIMEOUT: float = float(os.environ.get("IMPORT_LOCAL_REMOTE_TIMEOUT", "60"))
_IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS: tuple[str, ...] = ("*.obs.*.myhuaweicloud.com",)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SkillNetEmptyDownloadError(Exception):
    """skillnet-ai ``download()`` returned None; 前端用 detail_key 做多语言."""

    def __init__(self, *, github_context: str = "") -> None:
        self.github_context = (github_context or "").strip()
        self.detail_key = "skills.skillNet.errors.emptyDownloadResult"
        hint = f"\n{self.github_context[:800]}" if self.github_context else ""
        self.detail_params = {"hint": hint}
        super().__init__(self.github_context or "empty download path")


class _ImportLocalTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ssl_version=ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# ---------------------------------------------------------------------------
# 目录解析
# ---------------------------------------------------------------------------


def _get_agent_root_dir() -> "Path":
    return get_agent_root_dir()


def _get_marketplace_dir() -> "Path":
    return get_agent_skills_dir() / "_marketplace"


def _get_state_file() -> "Path":
    return get_agent_skills_dir() / "skills_state.json"


# ---------------------------------------------------------------------------
# 网络代理 / SSL
# ---------------------------------------------------------------------------


def _is_valid_http_mirror_url(url: str) -> bool:
    """Return True if url is a plausible http(s) mirror base (for SkillDownloader)."""
    s = url.strip()
    if not s or len(s) > 2048:
        return False
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _get_free_search_proxy_url() -> str:
    return str(os.environ.get(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _free_search_ssl_verify() -> bool:
    return _env_bool(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _disable_insecure_request_warning() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _skillnet_proxy_mapping() -> dict[str, str]:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url:
        return {}
    return {"http": proxy_url, "https": proxy_url}


def _configure_skillnet_requests_session(session: Any) -> None:
    proxies = _skillnet_proxy_mapping()
    if proxies:
        session.proxies.update(proxies)
    verify = _free_search_ssl_verify()
    session.verify = verify
    if verify is False:
        _disable_insecure_request_warning()


@contextmanager
def _skillnet_network_context():
    """Expose the configured proxy to third-party SkillNet clients during one call."""
    proxy_url = _get_free_search_proxy_url()
    env_keys = (*_SKILLNET_PROXY_ENV_KEYS, *_SKILLNET_NO_PROXY_ENV_KEYS)
    previous = {key: os.environ.get(key) for key in env_keys}
    try:
        if proxy_url:
            for key in _SKILLNET_PROXY_ENV_KEYS:
                os.environ[key] = proxy_url
            if not os.environ.get("NO_PROXY") and not os.environ.get("no_proxy"):
                for key in _SKILLNET_NO_PROXY_ENV_KEYS:
                    os.environ[key] = _FREE_SEARCH_DEFAULT_NO_PROXY
        if _free_search_ssl_verify() is False:
            _disable_insecure_request_warning()
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------


def _safe_path_name(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"invalid {label} name")
    path_value = Path(raw)
    invalid_name_checks = (
        raw in (".", ".."),
        "/" in raw,
        "\\" in raw,
        path_value.is_absolute(),
        PureWindowsPath(raw).is_absolute(),
    )
    if any(invalid_name_checks):
        raise ValueError(f"invalid {label} name: {raw}")
    return raw


def _safe_child_path(base: Path, name: Any, label: str) -> Path:
    safe_name = _safe_path_name(name, label)
    base_resolved = base.resolve()
    candidate = (base / safe_name).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"invalid {label} path: {safe_name}") from exc
    return candidate


def _log_rejected_name(operation: str, label: str, value: Any, exc: ValueError) -> None:
    logger.warning(
        "rejected invalid %s name: operation=%s value=%r error=%s",
        label,
        operation,
        value,
        exc,
    )


# ---------------------------------------------------------------------------
# 安全删除
# ---------------------------------------------------------------------------


def _safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return True

    import time
    import stat

    max_retries = 3
    retry_delay = 0.2

    for attempt in range(max_retries):
        try:
            if os.name == "nt":
                for root, dirs, files in os.walk(path):
                    for name in files:
                        filepath = Path(root) / name
                        try:
                            os.chmod(filepath, stat.S_IWRITE)
                        except OSError:
                            pass
            shutil.rmtree(path)
            return True
        except OSError as exc:
            logger.debug("删除目录失败（尝试 %d/%d）: %s", attempt + 1, max_retries, exc)

            if attempt == max_retries - 1:
                logger.warning("删除目录失败（已重试 %d 次）: %s", max_retries, path)
                return False

            if os.name == "nt":
                try:
                    def _remove_readonly_and_retry(func, path_, exc_val):
                        try:
                            os.chmod(path_, stat.S_IWRITE)
                            func(path_)
                        except OSError:
                            pass

                    shutil.rmtree(path, onerror=_remove_readonly_and_retry)
                    return True
                except OSError:
                    pass

            time.sleep(retry_delay)
            retry_delay *= 2

    return False


# ---------------------------------------------------------------------------
# URL 白名单校验（OpenJiuwen / import_local 共用）
# ---------------------------------------------------------------------------


def _host_matches_rule(host: str, rule: str) -> bool:
    host_parts = host.split(".")
    rule_parts = rule.split(".")
    if len(host_parts) != len(rule_parts):
        return False
    for host_part, rule_part in zip(host_parts, rule_parts):
        if rule_part == "*":
            continue
        if host_part != rule_part:
            return False
    return True


def _get_allowed_download_hosts(
    env_key: str,
    default_hosts: tuple[str, ...],
) -> list[str]:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return list(default_hosts)
    hosts: list[str] = []
    for token in raw.split(","):
        host = token.strip().lower()
        if not host:
            continue
        hosts.append(host)
    return hosts or list(default_hosts)


def _assert_download_url_allowed(
    download_url: str,
    *,
    env_key: str,
    default_hosts: tuple[str, ...],
    label: str,
) -> None:
    parsed = urlparse(download_url)
    if parsed.scheme != "https":
        raise RuntimeError(f"{label} URL 必须使用 HTTPS")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise RuntimeError(f"{label} URL 缺少主机名")
    for rule in _get_allowed_download_hosts(env_key, default_hosts):
        if rule.startswith("."):
            if host.endswith(rule):
                return
            continue
        if _host_matches_rule(host, rule):
            return
    raise RuntimeError(f"{label} URL host 不在白名单: {host}")


def _is_http_download_target(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
