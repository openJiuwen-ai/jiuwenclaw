# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for converting ``config.yaml`` MCP entries to runtime configs."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig

try:
    from openjiuwen.core.foundation.tool.mcp.client import (
        sse_client as _sse_client,  # noqa: F401
        stdio_client as _stdio_client,  # noqa: F401
        streamable_http_client as _streamable_http_client,  # noqa: F401
    )
except ImportError:
    pass

_HTTP_MCP_TRANSPORTS = frozenset({"sse", "http", "streamable-http", "streamable_http"})


def extract_enabled_mcp_server_entries(config_base: dict[str, Any]) -> list[dict[str, Any]]:
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
        return False, f"tcp connect to {host}:{port} failed: {type(exc).__name__}: {exc}"

    writer.close()
    try:
        await writer.wait_closed()
    except Exception as exc:
        logger.debug(
            "[mcp-preflight] reachability probe socket close failed for %s:%s: %r",
            host, port, exc,
        )
    return True, ""


def _stable_mcp_server_id(scope: str, name: str, payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value
        for key, value in payload.items()
        if key != "server_id"
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
    raise ValueError(f"不支持的 command 类型: '{command}'，目前仅支持 node/python/npx/uvx 及其绝对路径")


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


_DANGEROUS_ARGS_PATTERN = frozenset({
    "-e", "--eval", "-c", "--command", "-i",
})


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
            raise ValueError("请求级 cat_cafe_mcp 使用相对脚本路径时必须提供位于受信根下的 cwd")
        try:
            if cwd_path is not None and not candidate.is_absolute():
                resolved = (cwd_path / candidate).resolve()
            else:
                resolved = candidate.resolve()
        except OSError:
            continue
        if not _path_is_under_trusted_root(resolved, roots):
            raise ValueError(f"请求级 cat_cafe_mcp 参数路径不在受信根目录下: {resolved}")


_REQUEST_REMOTE_BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "metadata.azure.com",
})

_REQUEST_REMOTE_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata",
    "metadata.azure.com",
})


def _loopback_mcp_allowed() -> bool:
    return (os.getenv("JIUWENSWARM_ALLOW_LOOPBACK_MCP") or "").strip().lower() in ("1", "true", "yes")


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
        params["env"] = {str(k): str(v) for k, v in env.items() if k is not None and v is not None}
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
            params=params
        )

    if client_type == "streamable-http":
        if not url:
            raise ValueError(
                f"工具 '{tool_name}'（'{client_type}'）需要 url"
            )
        headers = _optional_auth_dict(tool_config, "auth_headers")
        query = _optional_auth_dict(tool_config, "auth_query_params")
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="streamable-http",
            auth_headers=headers,
            auth_query_params=query,
            params=params
        )

    if client_type == "playwright":
        if not url:
            raise ValueError(
                f"工具 '{tool_name}'（'{client_type}'）需要 url"
            )
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="playwright",
            params=params
        )

    if client_type == "openapi":
        if not url:
            raise ValueError(
                f"工具 '{tool_name}'（'{client_type}'）需要 url"
            )
        return McpServerConfig(
            server_id=server_id or tool_name,
            server_name=tool_name,
            server_path=url,
            client_type="openapi",
            params=params
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
        params=params
    )


__all__ = [
    "build_enabled_mcp_server_configs",
    "build_mcp_server_config",
    "create_mcp_tool",
    "extract_enabled_mcp_server_entries",
    "preflight_mcp_server_reachable",
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
