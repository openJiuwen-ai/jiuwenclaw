# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for converting ``config.yaml`` MCP entries to runtime configs."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import os
import re
import sys
import threading
import time
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlparse

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, Tool, ToolCard

try:
    from openjiuwen.core.foundation.tool.mcp.client import (
        sse_client as _sse_client,  # noqa: F401
        stdio_client as _stdio_client,  # noqa: F401
        streamable_http_client as _streamable_http_client,  # noqa: F401
    )
except ImportError:
    pass

_HTTP_MCP_TRANSPORTS = frozenset({"sse", "http", "streamable-http", "streamable_http"})


def extract_enabled_mcp_server_entries(
    config_base: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return enabled ``mcp.servers`` entries from a resolved config mapping."""
    if not isinstance(config_base, dict):
        return []
    mcp_cfg = config_base.get("mcp", {})
    if not isinstance(mcp_cfg, dict):
        return []
    servers = mcp_cfg.get("servers", [])
    if not isinstance(servers, list):
        return []

    result: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        result.append(item)
    return result


def build_mcp_server_config(
    entry: dict[str, Any],
    *,
    server_id_scope: str | None = None,
) -> McpServerConfig | None:
    """Build a ``McpServerConfig`` from one ``mcp.servers`` entry.

    Args:
        entry: One config entry under ``mcp.servers``.
        server_id_scope: Optional scope used to derive a stable ``server_id``.
            When omitted, openjiuwen's default random id behavior is preserved.
    """
    name = str(entry.get("name", "")).strip()
    if not name:
        return None
    transport = str(entry.get("transport", "")).strip().lower()
    if transport not in {"stdio", "sse", "http", "streamable-http", "streamable_http"}:
        return None

    payload: dict[str, Any] = {
        "server_name": name,
        "client_type": transport,
    }
    explicit_server_id = str(entry.get("server_id", "") or "").strip()
    if explicit_server_id:
        payload["server_id"] = explicit_server_id

    if transport == "stdio":
        command = str(entry.get("command", "")).strip()
        if not command:
            return None
        params: dict[str, Any] = {"command": command}
        args = entry.get("args")
        if isinstance(args, list):
            params["args"] = [str(item) for item in args]
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            params["cwd"] = cwd.strip()
        env = entry.get("env")
        if isinstance(env, dict):
            params["env"] = {str(k): str(v) for k, v in env.items()}
        timeout_s = entry.get("timeout_s")
        if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
            params["timeout_s"] = int(timeout_s)
        payload["server_path"] = f"stdio://{name}"
        payload["params"] = params
    else:
        url = str(entry.get("url", "")).strip()
        if not url:
            return None
        payload["server_path"] = url
        params: dict[str, Any] = {}
        headers = entry.get("headers")
        if isinstance(headers, dict):
            params["headers"] = {str(k): str(v) for k, v in headers.items()}
        timeout_s = entry.get("timeout_s")
        if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
            params["timeout_s"] = int(timeout_s)
        if params:
            payload["params"] = params

    if server_id_scope and "server_id" not in payload:
        payload["server_id"] = _stable_mcp_server_id(server_id_scope, name, payload)

    return McpServerConfig(**payload)


def build_enabled_mcp_server_configs(
    config_base: dict[str, Any],
    *,
    server_id_scope: str | None = None,
) -> list[McpServerConfig]:
    """Build all enabled MCP server configs, skipping invalid entries."""
    configs: list[McpServerConfig] = []
    for entry in extract_enabled_mcp_server_entries(config_base):
        cfg = build_mcp_server_config(entry, server_id_scope=server_id_scope)
        if cfg is not None:
            configs.append(cfg)
    return configs


async def preflight_mcp_server_reachable(
    cfg: McpServerConfig, *, timeout: float = 3.0
) -> tuple[bool, str]:
    """Cheap reachability probe for HTTP-based MCP servers.

    Why this exists: when an HTTP MCP server is unreachable, openjiuwen still
    enters the mcp ``streamablehttp_client`` async context (which spins up an
    anyio task group with background request tasks) before failing on
    ``session.initialize()``. Tearing that context back down leaks orphaned
    background tasks and raises noisy ``aclose(): asynchronous generator is
    already running`` / ``Attempted to exit cancel scope in a different task``
    errors. Probing the host:port first lets us skip registration cleanly.

    Returns ``(reachable, reason)``. Non-HTTP transports (stdio/playwright/…)
    report reachable — they are spawned locally and have no cheap probe.
    """
    transport = (getattr(cfg, "client_type", "") or "").strip().lower()
    if transport not in _HTTP_MCP_TRANSPORTS:
        return True, ""

    url = (getattr(cfg, "server_path", "") or "").strip()
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False, f"invalid url: {url!r}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except asyncio.TimeoutError:
        return False, f"tcp connect to {host}:{port} timed out after {timeout}s"
    except Exception as exc:
        # Connection refused / DNS failure / etc. — also defensive: the probe
        # itself must never break startup with an unexpected exception type.
        return (
            False,
            f"tcp connect to {host}:{port} failed: {type(exc).__name__}: {exc}",
        )

    writer.close()
    try:
        await writer.wait_closed()
    except Exception as exc:
        logger.debug(
            "[mcp-preflight] reachability probe socket close failed for %s:%s: %r",
            host,
            port,
            exc,
        )
    return True, ""


def is_asyncio_outer_cancellation() -> bool:
    """Return True when the current task has a pending outer cancel request.

    Used to distinguish real interrupt / WebSocket disconnect cancels from
    anyio TaskGroup connect-failure paths that cancel()+uncancel() the host
    task (leaving ``cancelling()`` at 0 when ``CancelledError`` reaches the
    caller). Requires Python 3.11+ (``Task.cancelling()``).
    """
    current = asyncio.current_task()
    return bool(current is not None and current.cancelling())


def _stable_mcp_server_id(scope: str, name: str, payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value for key, value in payload.items() if key != "server_id"
    }
    raw = json.dumps(
        {"scope": scope, "payload": stable_payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    safe_scope = _safe_id_part(scope, default="scope")
    safe_name = _safe_id_part(name, default="server")
    return f"mcp_{safe_scope}_{safe_name}_{digest}"


def _safe_id_part(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return (normalized or default)[:48]


def _normalize_stdio_command_kind(command: str) -> str:
    """将 command 归一化为 'node'、'python'、'npx' 或 'uvx'。

    支持绝对路径如 /usr/local/bin/node、C:\\Program Files\\node.exe 等。
    npx/uvx为包运行器，参数为包名而非本地脚本路径，安全模型与 node/python 不同。
    """
    raw = str(command or "").strip()
    if not raw:
        raise ValueError("工具配置缺少 'command' 字段")

    normalized = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if normalized in ("node", "node.exe"):
        return "node"
    if normalized.startswith("python"):
        return "python"
    if normalized in ("npx", "npx.exe", "npx.cmd", "npx.bat"):
        return "npx"
    if normalized in ("uvx", "uvx.exe"):
        return "uvx"
    raise ValueError(
        f"不支持的 command 类型: '{command}'，目前仅支持 node/python/npx/uvx 及其绝对路径"
    )


def _normalize_mcp_client_type(raw_type: object) -> str:
    if raw_type is None:
        return "stdio"
    s = str(raw_type).strip().lower().replace("_", "-")
    if "streamable" in s:
        return "streamable-http"
    if s == "sse":
        return "sse"
    if s == "stdio":
        return "stdio"
    return s if s else "stdio"


def _pick_mcp_url(tool_config: dict) -> str:
    v = tool_config.get("url")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ""


def _optional_auth_dict(tool_config: dict, key: str) -> dict | None:
    raw = tool_config.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"字段 {key!r} 必须是 JSON 对象")
    return dict(raw)


_DANGEROUS_ARGS_PATTERN = frozenset(
    {
        "-e",
        "--eval",
        "-c",
        "--command",
        "-i",
    }
)


def _check_dangerous_args(tool_name: str, args: list) -> None:
    if not isinstance(args, list):
        return
    for arg in args:
        arg_str = str(arg).strip()
        if arg_str in _DANGEROUS_ARGS_PATTERN:
            raise ValueError(
                f"安全拦截阻断：工具 '{tool_name}' 的 args 包含危险标志 '{arg_str}'，"
                "禁止通过参数注入执行任意代码。"
            )
        for dangerous_prefix in ("-e=", "--eval=", "-c=", "--command="):
            if arg_str.lower().startswith(dangerous_prefix):
                raise ValueError(
                    f"安全拦截阻断：工具 '{tool_name}' 的 args 包含危险标志 '{arg_str}'，"
                    "禁止通过参数注入执行任意代码。"
                )


def _trusted_cat_cafe_stdio_roots() -> list[Path]:
    roots: list[Path] = []
    raw = (os.getenv("CAT_CAFE_MCP_CWD") or "").strip()
    if raw:
        try:
            roots.append(Path(raw).expanduser().resolve())
        except OSError:
            pass
    try:
        roots.append((Path.home() / ".office-claw").resolve())
    except OSError:
        pass
    try:
        roots.append(Path(sys.executable).resolve().parent)
    except OSError:
        pass
    lp = (os.getenv("LOCALAPPDATA") or "").strip()
    if lp:
        inst = Path(lp) / "Programs" / "OfficeClaw"
        try:
            if inst.exists():
                roots.append(inst.resolve())
        except OSError:
            pass
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.getenv(env_key, "").strip()
        if not base:
            continue
        inst = Path(base) / "OfficeClaw"
        try:
            if inst.exists():
                roots.append(inst.resolve())
        except OSError:
            pass
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = os.path.normcase(str(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _path_is_under_trusted_root(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_cat_cafe_request_scoped_stdio(params: dict[str, Any]) -> None:
    """限制请求级 stdio：禁止内联代码执行面，脚本路径须在受信根目录下。

    npx/uvx 为包运行器，参数为包名及其参数，不适用本地脚本路径受信根校验
    （代码来源为包仓库而非本地文件，信任决策在于包名而非路径）。
    """
    cmd = str(params.get("command") or "").strip()
    args = params.get("args") or []
    if not isinstance(args, list):
        raise ValueError("stdio MCP 的 args 须为列表")

    kind = _normalize_stdio_command_kind(cmd)
    flat = [str(a) for a in args]
    lowered = [a.strip().lower() for a in flat]
    if any(x == "-c" or x == "--command" for x in lowered):
        raise ValueError("请求级 cat_cafe_mcp 禁止使用 python -c / --command")
    if kind == "node" and any(x in ("-e", "--eval") for x in lowered):
        raise ValueError("请求级 cat_cafe_mcp 禁止使用 node -e / --eval")

    if kind in ("npx", "uvx"):
        return

    cwd_path: Path | None = None
    cwd_raw = params.get("cwd")
    if isinstance(cwd_raw, str) and cwd_raw.strip():
        try:
            cwd_path = Path(cwd_raw).expanduser().resolve()
        except OSError as exc:
            raise ValueError(f"请求级 cat_cafe_mcp cwd 无效: {cwd_raw}") from exc
        if not _path_is_under_trusted_root(cwd_path, _trusted_cat_cafe_stdio_roots()):
            raise ValueError(f"请求级 cat_cafe_mcp cwd 不在受信根目录下: {cwd_path}")

    roots = _trusted_cat_cafe_stdio_roots()
    for a in flat:
        s = a.strip()
        if not s or s.startswith("-"):
            continue
        path_like_suffix = s.lower().endswith((".js", ".mjs", ".cjs", ".py"))
        if "/" not in s and "\\" not in s and not path_like_suffix:
            continue
        candidate = Path(s).expanduser()
        if not candidate.is_absolute() and cwd_path is None:
            raise ValueError(
                "请求级 cat_cafe_mcp 使用相对脚本路径时必须提供位于受信根下的 cwd"
            )
        try:
            if cwd_path is not None and not candidate.is_absolute():
                resolved = (cwd_path / candidate).resolve()
            else:
                resolved = candidate.resolve()
        except OSError:
            continue
        if not _path_is_under_trusted_root(resolved, roots):
            raise ValueError(
                f"请求级 cat_cafe_mcp 参数路径不在受信根目录下: {resolved}"
            )


_REQUEST_REMOTE_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
        "metadata.azure.com",
    }
)

_REQUEST_REMOTE_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.azure.com",
    }
)


def _loopback_mcp_allowed() -> bool:
    return (os.getenv("JIUWENSWARM_ALLOW_LOOPBACK_MCP") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _is_blocked_host(host: str) -> bool:
    if not host:
        return False
    host = host.lower().strip()
    allow_loopback = _loopback_mcp_allowed()
    if allow_loopback and host == "localhost":
        return False
    if host in _REQUEST_REMOTE_METADATA_HOSTS:
        return True
    if not allow_loopback and host in _REQUEST_REMOTE_BLOCKED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if allow_loopback and ip.is_loopback:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_request_scoped_remote_mcp(tool_name: str, cfg: dict) -> None:
    if not isinstance(cfg, dict):
        return
    if _loopback_mcp_allowed():
        logger.warning(
            "[mcp-config][WARN] JIUWENSWARM_ALLOW_LOOPBACK_MCP 已启用，"
            "loopback/localhost 地址放行（仅开发测试用，生产环境勿设此变量）"
        )
    url = cfg.get("url")
    if isinstance(url, str) and url:
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""
        if _is_blocked_host(host):
            raise ValueError(
                f"安全拦截阻断：请求级 sse/http MCP ({tool_name!r}) 的 url 指向"
                f"内网/元数据地址 ({host})，疑似 SSRF 攻击。"
            )
    for field in ("auth_headers", "auth_query_params"):
        val = cfg.get(field)
        if not isinstance(val, dict):
            continue
        for k, v in val.items():
            if not isinstance(v, str):
                continue
            try:
                host = (urlparse(v).hostname or "") if "://" in v else v.strip()
            except Exception:
                host = v.strip()
            if _is_blocked_host(host):
                raise ValueError(
                    f"安全拦截阻断：请求级 sse/http MCP ({tool_name!r}) 的 "
                    f"{field}[{k!r}] 值指向内网/元数据地址，疑似凭证外泄/SSRF。"
                )


def create_mcp_tool(config_str: str) -> McpServerConfig:
    """从 JSON 字符串解析并构造 ``McpServerConfig``。

    Args:
        1.stdio类型:
        config_str: JSON 格式配置字符串，格式为：
            {
                "name": "tool_name",
                "command": "node" | "python" | "npx" | "uvx",
                "args": ["xxx.js"] | ["xxx.py"] | ["-y", "@scope/pkg"] | ["pkg"]
            }

        2.streamable-http类型:
            {
                "type": "streamableHttp",
                "url": "http://127.0.0.1:3002/mcp",
                "env": {},
                "auth_headers": {
                    "Authorization": "Bearer xxx"
                },
                "auth_query_params": {
                    "token": "yyy"
                }
            }

        3.sse类型:
            {
                "name": "my-sse-mcp",
                "type": "sse",
                "url": "http://127.0.0.1:3001/sse",
                "env": {},
                "auth_headers": {
                    "Authorization": "Bearer xxx"
                },
                "auth_query_params": {
                    "token": "yyy"
                }
            }
        4.playwright类型:
            {
                "name": "my-playwright-mcp",
                "description": "可选说明",
                "type": "playwright",
                "url": "http://127.0.0.1:3003/sse",
                "env": {}
            }

    Returns:
        ``McpServerConfig``，由调用方通过 ``Runner.resource_mgr.add_mcp_server(..., tag=...)`` 注册。

    Raises:
        ValueError: JSON 解析失败或配置不合法时
    """
    try:
        config = json.loads(config_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"无效的 JSON 配置") from e

    if isinstance(config, list):
        if len(config) == 0:
            raise ValueError("工具配置数组不能为空")
        tool_config = config[0]
    elif isinstance(config, dict):
        tool_config = config
    else:
        raise ValueError("配置必须是字典或数组类型")

    if not isinstance(tool_config, dict):
        raise ValueError("工具配置必须是字典类型")

    tool_name = tool_config.get("name")
    server_id = str(tool_config.get("server_id") or tool_name or "").strip()
    command = tool_config.get("command")
    args = tool_config.get("args", [])
    env = tool_config.get("env")
    cwd = tool_config.get("cwd")

    if not tool_name:
        raise ValueError("工具配置缺少 'name' 字段")

    url = _pick_mcp_url(tool_config)
    client_type = _normalize_mcp_client_type(tool_config.get("type"))
    params = {}
    if isinstance(env, dict) and env:
        params["env"] = {
            str(k): str(v) for k, v in env.items() if k is not None and v is not None
        }
    if client_type == "sse":
        if not url:
            raise ValueError(f"工具 '{tool_name}'（'{client_type}'）需要 url")
        headers = _optional_auth_dict(tool_config, "auth_headers")
        query = _optional_auth_dict(tool_config, "auth_query_params")
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="sse",
            auth_headers=headers,
            auth_query_params=query,
            params=params,
        )

    if client_type == "streamable-http":
        if not url:
            raise ValueError(f"工具 '{tool_name}'（'{client_type}'）需要 url")
        headers = _optional_auth_dict(tool_config, "auth_headers")
        query = _optional_auth_dict(tool_config, "auth_query_params")
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="streamable-http",
            auth_headers=headers,
            auth_query_params=query,
            params=params,
        )

    if client_type == "playwright":
        if not url:
            raise ValueError(f"工具 '{tool_name}'（'{client_type}'）需要 url")
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="playwright",
            params=params,
        )

    if client_type == "openapi":
        if not url:
            raise ValueError(f"工具 '{tool_name}'（'{client_type}'）需要 url")
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="openapi",
            params=params,
        )

    if not isinstance(args, list):
        raise ValueError(f"工具 '{tool_name}' 的 args 必须是列表类型")

    _check_dangerous_args(tool_name, args)

    normalized_command = str(command or "").strip()
    _normalize_stdio_command_kind(normalized_command)
    params["command"] = normalized_command
    params["args"] = args
    if isinstance(cwd, str) and cwd.strip():
        params["cwd"] = cwd.strip()
    return McpServerConfig(
        server_id=server_id or tool_name,
        server_name=tool_name,
        server_path=f"stdio://{tool_name}",
        client_type="stdio",
        params=params,
    )


_OFFICE_CLAW_MCP_ENV_KEYS = frozenset(
    {
        "OFFICE_CLAW_API_URL",
        "OFFICE_CLAW_INVOCATION_ID",
        "OFFICE_CLAW_CALLBACK_TOKEN",
        "OFFICE_CLAW_USER_ID",
        "OFFICE_CLAW_AGENT_ID",
        "OFFICE_CLAW_SIGNAL_USER",
        "OFFICE_CLAW_MCP_EXCLUDED_TOOLS",
        "NODE_EXTRA_CA_CERTS",
    }
)

_OFFICE_CLAW_MCP_SCHEMA_CACHE_ENV = "JIUWENSWARM_MCP_SCHEMA_CACHE"
_OFFICE_CLAW_MCP_SCHEMA_CACHE_OFF = frozenset({"0", "false", "no", "off"})
_office_claw_mcp_schema_cache: dict[str, list[dict[str, Any]]] = {}
_office_claw_mcp_schema_inflight: dict[
    tuple[int, int, str], asyncio.Task[list[dict[str, Any]]]
] = {}
_office_claw_mcp_schema_cache_lock = threading.Lock()
_office_claw_mcp_schema_generation = 0


def _office_claw_mcp_schema_cache_enabled() -> bool:
    return (
        str(os.environ.get(_OFFICE_CLAW_MCP_SCHEMA_CACHE_ENV, "") or "").strip().lower()
        not in _OFFICE_CLAW_MCP_SCHEMA_CACHE_OFF
    )


def _office_claw_mcp_build_fingerprint(
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Fingerprint the MCP bundle file(s) so a rebuild rotates the cache key.

    Relative script paths in ``command``/``args`` are resolved against
    ``params["cwd"]`` first — the stdio MCP process is spawned with that cwd,
    so it is the directory that actually owns the bundle — and only fall back
    to the sidecar process cwd. Resolving against the process cwd alone would
    either miss the real bundle or stat the wrong file, so a bundle rebuild
    would not change the fingerprint and the cache would serve a stale schema.
    """
    fingerprints: list[dict[str, Any]] = []
    base_dirs: list[Path] = []
    cwd_raw = str(params.get("cwd") or "").strip()
    if cwd_raw:
        base_dirs.append(Path(cwd_raw).expanduser())
    base_dirs.append(Path.cwd())
    candidates = [params.get("command"), *(params.get("args") or [])]
    seen: set[str] = set()
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw:
            continue
        probe = Path(raw).expanduser()
        resolved: Path | None = None
        if probe.is_absolute():
            resolved = probe
        else:
            for base in base_dirs:
                candidate_path = base / probe
                if candidate_path.is_file():
                    resolved = candidate_path
                    break
        if resolved is None:
            try:
                resolved = probe.resolve(strict=False)
            except OSError:
                continue
        resolved_str = str(resolved)
        if not resolved.is_file() or resolved_str in seen:
            continue
        seen.add(resolved_str)
        try:
            stat = resolved.stat()
        except OSError:
            continue
        fingerprints.append(
            {
                "path": _normalized_path(resolved_str),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return fingerprints


def _office_claw_mcp_schema_cache_key(params: Mapping[str, Any]) -> str:
    raw_excluded = str(
        (params.get("env") or {}).get("OFFICE_CLAW_MCP_EXCLUDED_TOOLS") or ""
    )
    payload = {
        "command": _normalized_path(str(params.get("command") or "")),
        "args": [str(value) for value in params.get("args") or []],
        "cwd": _normalized_path(str(params.get("cwd") or "")),
        "excluded_tools": sorted(
            {item.strip() for item in raw_excluded.split(",") if item.strip()}
        ),
        "build_files": _office_claw_mcp_build_fingerprint(params),
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def invalidate_office_claw_mcp_schema_cache() -> None:
    """Drop the entire OfficeClaw MCP schema cache and abort coalesced discovery.

    Called when the agent catalog revision changes (Relay resync) so that a
    rebuilt MCP bundle or changed excluded-tool set is re-discovered instead
    of serving a stale schema. Bumping ``_office_claw_mcp_schema_generation``
    also prevents any in-flight discovery task from writing back into a newer
    generation.
    """
    global _office_claw_mcp_schema_generation
    with _office_claw_mcp_schema_cache_lock:
        _office_claw_mcp_schema_generation += 1
        _office_claw_mcp_schema_cache.clear()
        _office_claw_mcp_schema_inflight.clear()


def _clear_office_claw_mcp_schema_cache_for_tests() -> None:
    invalidate_office_claw_mcp_schema_cache()


@dataclass(frozen=True)
class OfficeClawMcpRegistration:
    """Tools installed on one session agent for one Relay request."""

    request_id: str
    tool_ids: tuple[str, ...]
    tool_names: tuple[str, ...]


OFFICE_CLAW_REQUEST_TOOL_ID_PREFIX = "office-claw-request-"

# Attribute name used to store the request-scoped OfficeClaw tool id allowlist.
# The allowlist must cross the asyncio task boundary between the caller's task
# (where ``bind_active_office_claw_mcp_tools`` sets a ContextVar) and the
# DeepAgent's supervisor / round task (created at session startup, before the
# ContextVar is set).  Storing the ids on the shared ability_manager gives the
# round task a reliable source to re-bind the ContextVar before invoking an
# OfficeClaw MCP tool.
_OFFICE_CLAW_TOOL_IDS_ATTR = "_active_office_claw_tool_ids"

# Task-local allowlist for request-scoped OfficeClaw tool ids. Set for the
# duration of one Relay chat.send so a concurrent session cannot invoke this
# request's MCP tools (or vice versa) via a polluted short-name AbilityManager.
_active_office_claw_tool_ids: ContextVar[frozenset[str] | None] = ContextVar(
    "active_office_claw_tool_ids",
    default=None,
)


def _office_claw_tool_ids_carrier(agent: Any) -> Any:
    """Return the object that carries the request-scoped allowlist.

    The DeepAgent and its inner ReActAgent share the same ``ability_manager``
    (openjiuwen wires ``agent.ability_manager = deep_agent.ability_manager``),
    so storing the allowlist on the ability_manager makes it visible to
    whichever agent object runs the round (``ctx.agent`` may be either the
    DeepAgent or the ReActAgent).
    """

    if agent is None:
        return None
    ability_manager = getattr(agent, "ability_manager", None)
    if ability_manager is not None:
        return ability_manager
    return agent


@contextmanager
def bind_active_office_claw_mcp_tools(
    tool_ids: Iterable[str] | None,
) -> Iterator[None]:
    """Bind the request-scoped OfficeClaw tool id allowlist for this task."""

    allowed = frozenset(str(tool_id) for tool_id in (tool_ids or ()) if str(tool_id))
    token = _active_office_claw_tool_ids.set(allowed)
    try:
        yield
    finally:
        _active_office_claw_tool_ids.reset(token)


def set_agent_office_claw_tool_ids(agent: Any, tool_ids: Iterable[str] | None) -> None:
    """Store the request-scoped OfficeClaw tool id allowlist on the shared ability_manager.

    The ContextVar ``_active_office_claw_tool_ids`` cannot propagate to the
    DeepAgent's supervisor task because that task is created (via
    ``asyncio.create_task``) at session startup — before the caller enters
    ``bind_active_office_claw_mcp_tools``.  Storing the ids on the shared
    ability_manager gives the round task a way to re-bind the ContextVar
    before invoking an OfficeClaw MCP tool.
    """

    carrier = _office_claw_tool_ids_carrier(agent)
    if carrier is None:
        return
    ids = frozenset(str(tid) for tid in (tool_ids or ()) if str(tid))
    if ids:
        setattr(carrier, _OFFICE_CLAW_TOOL_IDS_ATTR, ids)
    else:
        try:
            delattr(carrier, _OFFICE_CLAW_TOOL_IDS_ATTR)
        except AttributeError:
            pass


def clear_agent_office_claw_tool_ids(agent: Any) -> None:
    """Remove the request-scoped allowlist from the shared ability_manager."""

    carrier = _office_claw_tool_ids_carrier(agent)
    if carrier is None:
        return
    try:
        delattr(carrier, _OFFICE_CLAW_TOOL_IDS_ATTR)
    except AttributeError:
        pass


@contextmanager
def bind_office_claw_from_agent(agent: Any) -> Iterator[None]:
    """Re-bind the ContextVar from the shared ability_manager for the current task.

    Call this inside the round task (e.g. in ``ProgressiveToolRail``) right
    before invoking an OfficeClaw MCP tool.  If the shared ability_manager
    carries a stored allowlist, the ContextVar is set for the duration of the
    call so that ``ensure_request_scoped_office_claw_tool_allowed`` passes.
    """

    carrier = _office_claw_tool_ids_carrier(agent)
    ids = getattr(carrier, _OFFICE_CLAW_TOOL_IDS_ATTR, None) if carrier is not None else None
    if not ids:
        logger.debug(
            "OfficeClaw request-scoped allowlist not found on carrier; "
            "leaving ContextVar unbound for this invoke (agent_type=%s)",
            type(agent).__name__ if agent is not None else None,
        )
        yield
        return
    token = _active_office_claw_tool_ids.set(ids)
    try:
        yield
    finally:
        _active_office_claw_tool_ids.reset(token)


def ensure_request_scoped_office_claw_tool_allowed(tool_id: str) -> None:
    """Refuse request-scoped OfficeClaw tools outside the active request allowlist."""

    normalized = str(tool_id or "").strip()
    if not normalized.startswith(OFFICE_CLAW_REQUEST_TOOL_ID_PREFIX):
        return
    allowed = _active_office_claw_tool_ids.get()
    if allowed is None:
        raise RuntimeError(
            "OfficeClaw MCP tool invoked without an active request binding; "
            "refusing unbound request-scoped invoke"
        )
    if normalized not in allowed:
        raise RuntimeError(
            "OfficeClaw MCP tool is bound to another request; refusing cross-request invoke"
        )


def get_active_office_claw_mcp_tool_ids() -> frozenset[str] | None:
    """Return the request-local OfficeClaw tool id allowlist, if bound."""

    return _active_office_claw_tool_ids.get()


def resolve_active_office_claw_tool_id(tool_name: str) -> str | None:
    """Map a short tool name to this request's OfficeClaw tool id.

    Request-scoped ids look like
    ``office-claw-request-<hash>.office-claw.<tool_name>``. ProgressiveToolRail
    may hold a stale AbilityManager card whose id points at another request's
    (already cleaned-up) registration; the active allowlist is the authority
    for *this* chat.send.
    """

    name = str(tool_name or "").strip()
    if not name:
        return None
    allowed = _active_office_claw_tool_ids.get()
    if not allowed:
        return None
    suffix = f".{name}"
    matches = [
        tool_id
        for tool_id in allowed
        if tool_id.endswith(suffix) or tool_id.rsplit(".", 1)[-1] == name
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # Prefer the canonical request-scoped shape when multiple ids match.
    preferred = [
        tool_id
        for tool_id in matches
        if tool_id.startswith(OFFICE_CLAW_REQUEST_TOOL_ID_PREFIX) and tool_id.endswith(suffix)
    ]
    return preferred[0] if preferred else matches[0]


def extract_office_claw_mcp(params: Any) -> dict[str, Any] | None:
    """Return only the legacy ``office_claw_mcp`` request field."""

    if not isinstance(params, dict):
        return None
    raw = params.get("office_claw_mcp")
    if not isinstance(raw, dict) or not raw:
        return None
    return dict(raw)


def _normalized_path(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def validate_office_claw_mcp_config(
    config: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate request config against Relay's startup-time MCP identity.

    The request may choose only callback-related environment values. The
    executable, arguments, and working directory must exactly match the values
    with which Relay started Jiuwen, preventing this request field from
    becoming a general-purpose subprocess execution surface.
    """

    env_source = os.environ if environ is None else environ
    expected_command = str(env_source.get("OFFICE_CLAW_MCP_COMMAND") or "").strip()
    expected_args_raw = str(env_source.get("OFFICE_CLAW_MCP_ARGS_JSON") or "").strip()
    expected_cwd = str(env_source.get("OFFICE_CLAW_MCP_CWD") or "").strip()
    if not expected_command or not expected_args_raw or not expected_cwd:
        raise ValueError("Relay OfficeClaw MCP startup identity is incomplete")

    try:
        expected_args = json.loads(expected_args_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OFFICE_CLAW_MCP_ARGS_JSON is invalid") from exc
    if not isinstance(expected_args, list):
        raise ValueError("OFFICE_CLAW_MCP_ARGS_JSON must contain a list")
    expected_args = [str(item) for item in expected_args]

    command = str(config.get("command") or "").strip()
    args = config.get("args")
    cwd = str(config.get("cwd") or "").strip()
    request_env = config.get("env")
    if not command:
        raise ValueError("office_claw_mcp command is required")
    if not isinstance(args, list):
        raise ValueError("office_claw_mcp args must be a list")
    if not cwd:
        raise ValueError("office_claw_mcp cwd is required")
    if not isinstance(request_env, dict):
        raise ValueError("office_claw_mcp env must be an object")

    normalized_args = [str(item) for item in args]
    if _normalized_path(command) != _normalized_path(expected_command):
        raise ValueError(
            "office_claw_mcp command does not match Relay startup identity"
        )
    if normalized_args != expected_args:
        raise ValueError("office_claw_mcp args do not match Relay startup identity")
    if _normalized_path(cwd) != _normalized_path(expected_cwd):
        raise ValueError("office_claw_mcp cwd does not match Relay startup identity")

    unknown_env_keys = {str(key) for key in request_env}.difference(
        _OFFICE_CLAW_MCP_ENV_KEYS
    )
    if unknown_env_keys:
        raise ValueError(
            "office_claw_mcp env contains unsupported keys: "
            + ", ".join(sorted(unknown_env_keys))
        )

    return {
        "command": command,
        "args": normalized_args,
        "cwd": cwd,
        "env": {
            str(key): str(value)
            for key, value in request_env.items()
            if key is not None and value is not None
        },
    }


def _stdio_server_parameters(params: Mapping[str, Any]):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=str(params["command"]),
        args=list(params["args"]),
        env=dict(params.get("env") or {}),
        cwd=str(params["cwd"]),
        encoding_error_handler="strict",
    )


async def _list_office_claw_mcp_tools_uncached(
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Start OfficeClaw MCP once, collect its tool schemas, then stop it."""

    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    stack = AsyncExitStack()
    try:
        read, write = await stack.enter_async_context(
            stdio_client(_stdio_server_parameters(params))
        )
        session = await stack.enter_async_context(
            ClientSession(read, write, sampling_callback=None)
        )
        await session.initialize()
        response = await session.list_tools()
        return [
            {
                "name": tool.name,
                "description": getattr(tool, "description", "") or "",
                "input_params": getattr(tool, "inputSchema", {}) or {},
            }
            for tool in response.tools
        ]
    finally:
        await stack.aclose()


async def _discover_and_cache_office_claw_mcp_schema(
    params: Mapping[str, Any],
    cache_key: str,
    loop_key: tuple[int, int, str],
    generation: int,
) -> list[dict[str, Any]]:
    """Producer: run one discovery, write the cache, then clean up in-flight.

    The cache write and the in-flight removal are bound to THIS task's own
    completion (its ``finally``), never to whichever waiter happened to start
    it. A cancelled waiter cannot abort the shared discovery or orphan its
    result, because ``asyncio.shield`` keeps this task alive regardless of
    waiter cancellation; this task owns both the write-back and the cleanup.
    """
    started_at = time.monotonic()
    try:
        tools = await _list_office_claw_mcp_tools_uncached(params)
        frozen = copy.deepcopy(tools)
        with _office_claw_mcp_schema_cache_lock:
            if generation == _office_claw_mcp_schema_generation:
                _office_claw_mcp_schema_cache[cache_key] = frozen
        logger.info(
            "OfficeClaw MCP schema cache filled: key=%s tools=%s duration_ms=%.1f",
            cache_key[:12],
            len(frozen),
            (time.monotonic() - started_at) * 1000,
        )
        return frozen
    finally:
        current = asyncio.current_task()
        with _office_claw_mcp_schema_cache_lock:
            if _office_claw_mcp_schema_inflight.get(loop_key) is current:
                _office_claw_mcp_schema_inflight.pop(loop_key, None)


async def list_office_claw_mcp_tools(
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return immutable OfficeClaw tool schemas, coalescing identical discovery.

    The tool list / name / description / JSON Schema is static while the MCP
    bundle file is unchanged; only callback tokens, credentials and call
    results are per-request. When ``JIUWENSWARM_MCP_SCHEMA_CACHE`` is enabled
    (Relay default), the first discovery fills a process-local cache keyed by
    the command/args/cwd/excluded-tools and the bundle build fingerprint
    (size + mtime_ns); subsequent identical discoveries return a deep copy so
    callers cannot mutate the frozen schema. Concurrent discoveries for the
    same event loop + generation + key are coalesced into one in-flight
    producer task; waiters are pure consumers wrapped in ``asyncio.shield``,
    so a cancelled waiter cannot abort the shared discovery or evict it from
    the in-flight table. ``invalidate_office_claw_mcp_schema_cache`` drops
    everything on catalog revision change. Setting the env var to
    ``0/false/no/off`` falls back to the uncached path.
    """

    if not _office_claw_mcp_schema_cache_enabled():
        return await _list_office_claw_mcp_tools_uncached(params)

    cache_key = _office_claw_mcp_schema_cache_key(params)
    with _office_claw_mcp_schema_cache_lock:
        generation = _office_claw_mcp_schema_generation
        loop_key = (id(asyncio.get_running_loop()), generation, cache_key)
        cached = _office_claw_mcp_schema_cache.get(cache_key)
        if cached is not None:
            logger.debug("OfficeClaw MCP schema cache hit: key=%s", cache_key[:12])
            return copy.deepcopy(cached)
        task = _office_claw_mcp_schema_inflight.get(loop_key)
        if task is None:
            task = asyncio.create_task(
                _discover_and_cache_office_claw_mcp_schema(
                    params, cache_key, loop_key, generation
                ),
                name=f"office-claw-mcp-schema-{cache_key[:12]}",
            )
            _office_claw_mcp_schema_inflight[loop_key] = task

    # Pure consumer: a cancelled waiter must not affect the shared producer
    # task. The producer owns the cache write and its own in-flight cleanup.
    tools = await asyncio.shield(task)
    return copy.deepcopy(tools)


class RequestScopedOfficeClawMcpTool(Tool):
    """Invoke one OfficeClaw tool through a fresh request-configured process."""

    def __init__(self, card: ToolCard, params: Mapping[str, Any]) -> None:
        super().__init__(card)
        self._params = dict(params)

    async def stream(self, inputs: Any, **kwargs: Any):
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        tool_id = str(getattr(self._card, "id", "") or "")
        try:
            ensure_request_scoped_office_claw_tool_allowed(tool_id)
        except RuntimeError as exc:
            raise build_error(
                StatusCode.TOOL_MCP_EXECUTION_ERROR,
                cause=exc,
                reason=str(exc),
                method="invoke",
                card=self._card,
            ) from exc

        arguments = inputs if isinstance(inputs, dict) else {}
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(
                stdio_client(_stdio_server_parameters(self._params))
            )
            session = await stack.enter_async_context(
                ClientSession(read, write, sampling_callback=None)
            )
            await session.initialize()
            result = await session.call_tool(self._card.name, arguments=arguments)
            result_content: str | None = None
            if result.content:
                result_content = getattr(result.content[-1], "text", None)
            return {"result": result_content}
        except Exception as exc:
            raise build_error(
                StatusCode.TOOL_MCP_EXECUTION_ERROR,
                cause=exc,
                reason=str(exc),
                method="invoke",
                card=self._card,
            ) from exc
        finally:
            await stack.aclose()


__all__ = [
    "OFFICE_CLAW_REQUEST_TOOL_ID_PREFIX",
    "OfficeClawMcpRegistration",
    "RequestScopedOfficeClawMcpTool",
    "bind_active_office_claw_mcp_tools",
    "bind_office_claw_from_agent",
    "clear_agent_office_claw_tool_ids",
    "set_agent_office_claw_tool_ids",
    "build_enabled_mcp_server_configs",
    "build_mcp_server_config",
    "create_mcp_tool",
    "ensure_request_scoped_office_claw_tool_allowed",
    "extract_enabled_mcp_server_entries",
    "extract_office_claw_mcp",
    "get_active_office_claw_mcp_tool_ids",
    "invalidate_office_claw_mcp_schema_cache",
    "list_office_claw_mcp_tools",
    "preflight_mcp_server_reachable",
    "resolve_active_office_claw_tool_id",
    "validate_office_claw_mcp_config",
    "_check_dangerous_args",
    "_is_blocked_host",
    "_loopback_mcp_allowed",
    "_normalize_mcp_client_type",
    "_normalize_stdio_command_kind",
    "_optional_auth_dict",
    "_path_is_under_trusted_root",
    "_pick_mcp_url",
    "_trusted_cat_cafe_stdio_roots",
    "_validate_cat_cafe_request_scoped_stdio",
    "_validate_request_scoped_remote_mcp",
]
