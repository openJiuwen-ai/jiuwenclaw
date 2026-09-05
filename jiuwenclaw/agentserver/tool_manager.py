
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ToolManager - tools.add 等 RPC：落盘用户 MCP 工具配置并交给 mcp_toolkits 生成 McpServerConfig。"""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import os
import re
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner

from jiuwenclaw.utils import get_agent_tools_dir, logger

from jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool import (
    EphemeralStdioMcpTool,  # noqa: F401 (保留旧引用/patch 路径)
    stdio_params_from_mcp_config,
)
from jiuwenclaw.agentserver.tools.mcp_toolkits import _normalize_stdio_command_kind, create_mcp_tool
from jiuwenclaw.agentserver.tools.request_scoped_mcp_sessions import (
    PooledRequestMcpTool,
    build_remote_connect_params,
    discover_remote_mcp_tools,
    discover_stdio_mcp_tools,
    release_request_scoped_mcp_sessions,
    release_session_mcp_workers,
)

_OFFICE_CLAW_SERVER_NAME_PREFIX = "office-claw"

_REQUEST_STDIO_PARAMS: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar("_REQUEST_STDIO_PARAMS", default=None)


# ── 安全拦截规则 ──
# 对于从 RPC（如浏览器）提交的 tools.add 请求，这些字段属于绝对禁区。
# 它们分别对应：子进程执行 (RCE)、工作目录逃逸、环境变量投毒、远端连接 (SSRF)、
# 认证头/查询参数透传（可用于绕过服务端认证、SSRF 增强、凭证窃取）。
_RPC_BLOCKED_KEYS = frozenset({
    "command", "args",
    "cwd", "env", "url",
    "auth_headers", "auth_query_params",
})


def _check_mcp_security(tool_name: str, cfg: dict) -> None:
    """拦截高危 MCP 配置，防止通过非受信渠道注册任意执行权限或远端连接。"""
    if not isinstance(cfg, dict):
        return
    found_keys = set(cfg.keys())
    dangerous = found_keys.intersection(_RPC_BLOCKED_KEYS)
    if dangerous:
        raise ValueError(
            f"安全拦截阻断：禁止通过非受信渠道注册包含以下高危字段的工具 ({tool_name!r}): "
            f"{', '.join(dangerous)}。"
        )


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
    return (os.getenv("JIUWENCLAW_ALLOW_LOOPBACK_MCP") or "").strip().lower() in ("1", "true", "yes")


def _is_blocked_host(host: str) -> bool:
    """判断 host 是否指向内网/环回/元数据地址（SSRF 防护）。"""
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
    """请求级 sse/streamable-http MCP 安全校验。

    安全边界（评审结论）：
    - url 不做域名白名单（业务需要连接任意合法外网 MCP 端点），但拦截内网/环回/
      元数据地址，缓解 SSRF
    - auth_headers / auth_query_params 的字符串值做同样的内网/元数据地址黑名单，
      防止被用作重定向入口
    - 不拦截字段本身（sse/http 功能必需 url 与 auth）
    - JIUWENCLAW_ALLOW_LOOPBACK_MCP=1 时放行 loopback/localhost（仅开发测试用，
      metadata 地址仍拦截，private/link_local 仍拦截）
    """
    if not isinstance(cfg, dict):
        return
    if _loopback_mcp_allowed():
        logger.warning(
            "[ToolManager][WARN] JIUWENCLAW_ALLOW_LOOPBACK_MCP 已启用，"
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
            # 值可能是完整 URL（含 host）或裸 host/IP
            try:
                host = (urlparse(v).hostname or "") if "://" in v else v.strip()
            except Exception:
                host = v.strip()
            if _is_blocked_host(host):
                raise ValueError(
                    f"安全拦截阻断：请求级 sse/http MCP ({tool_name!r}) 的 "
                    f"{field}[{k!r}] 值指向内网/元数据地址，疑似凭证外泄/SSRF。"
                )


def _make_stdio_params_getter(server_name: str) -> Callable[[], dict[str, Any]]:
    def _get() -> dict[str, Any]:
        params_map = _REQUEST_STDIO_PARAMS.get()
        if params_map is None:
            return {}
        return params_map.get(server_name, {})
    return _get


def _trusted_cat_cafe_stdio_roots() -> list[Path]:
    """请求级 stdio MCP 允许加载的脚本/工作目录须落在这些根路径之下。"""
    roots: list[Path] = []
    raw = (os.getenv("CAT_CAFE_MCP_CWD") or "").strip()
    if raw:
        try:
            roots.append(Path(raw).expanduser().resolve())
        except OSError:
            pass
    mcp_cwd = (os.getenv("OFFICE_CLAW_MCP_CWD") or "").strip()
    if mcp_cwd:
        try:
            roots.append(Path(mcp_cwd).expanduser().resolve())
        except OSError as e:
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


def _mcp_add_result_is_ok(result: Any) -> bool:
    """解析 ``add_mcp_server`` 返回值。"""
    if result is None:
        return True
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        try:
            return bool(is_ok())
        except Exception:
            return False
    return False


def _mcp_add_result_error_text(result: Any) -> str:
    """与 ``browser_tools._result_error_text`` 一致。"""
    if result is None:
        return ""
    for attr in ("error", "msg"):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                value = fn()
                if value is not None:
                    return str(value)
            except Exception:
                logger.debug("[ToolManager] callable error")
                pass
    value = getattr(result, "_error", None)
    if value is not None:
        return str(value)
    return str(result)


async def _add_mcp_server_and_ability(agent: Any, mcp_cfg: Any, *, tag: str) -> None:
    """调用 ``add_mcp_server``，按返回值决定是否 ``ability_manager.add``；失败抛 ``RuntimeError``。

    注意：不在此处捕获 ``asyncio.CancelledError``。MCP 不可达导致的 cancel 由
    ``ResourceMgr.add_mcp_server`` 隔离并转为 Error 结果；外层真取消必须继续向上传播。
    """
    try:
        result = await Runner.resource_mgr.add_mcp_server(mcp_cfg, tag=tag)
    except Exception as e:
        error_msg = (
            f"add_mcp_server 失败: "
            f"name={mcp_cfg.server_name} url={mcp_cfg.server_path}: {e}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

    if _mcp_add_result_is_ok(result):
        agent.ability_manager.add(mcp_cfg)
        logger.debug(
            "[ToolManager] add_mcp_server 成功: name=%s url=%s",
            mcp_cfg.server_name,
            mcp_cfg.server_path,
        )
        return

    err = _mcp_add_result_error_text(result)
    if "already exist" in err.lower():
        agent.ability_manager.add(mcp_cfg)
        logger.info(
            "[ToolManager] add_mcp_server 已存在，仍加入 ability_manager: name=%s, 错误信息: %s",
            mcp_cfg.server_name,
            err,
        )
        return

    error_msg = f"add_mcp_server 失败: {err}" if err else "add_mcp_server 失败"
    logger.error(
        "[ToolManager] %s: name=%s url=%s",
        error_msg,
        mcp_cfg.server_name,
        mcp_cfg.server_path,
    )
    raise RuntimeError(error_msg)

# ---------------------------------------------------------------------------
# 落盘 JSON 模板：列表顺序即写入顺序；每项为 (disk_key, default, kind)。
# kind:
#   tool_name — 使用 mcpServers 的 key，忽略 cfg 里的同名键
#   text      — 字符串；cfg 缺省用 default，非 str 则 str()
#   list      — 列表；cfg 非 list 则用 default 的拷贝
#   any       — cfg.get(source_key, default)；default 为 dict/list 时做浅拷贝
# 扩展字段：在列表末尾追加 ("new_key", default, "text"|"list"|"any") 即可。
# ---------------------------------------------------------------------------
TOOL_DISK_SCHEMA: list[tuple[str, Any, str]] = [
    ("name", "", "tool_name"),
    ("description", "", "text"),
    ("type", "", "text"),
    ("url", "", "text"),
    ("env", {}, "any"),
    ("auth_headers", {}, "any"),
    ("auth_query_params", {}, "any"),
    ("command", "", "text"),
    ("args", [], "list"),
]

# 落盘字段名 -> mcpServers 内对象中的键名（缺省表示与 disk_key 相同）
TOOL_DISK_SOURCE_MAP: dict[str, str] = {}


def _mutable_default_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _coerce_tool_disk_value(
        disk_key: str,
        default: Any,
        kind: str,
        tool_name: str,
        cfg: dict[str, Any],
) -> Any:
    src = TOOL_DISK_SOURCE_MAP.get(disk_key, disk_key)
    if kind == "tool_name":
        return (tool_name or "").strip()
    if kind == "text":
        raw = cfg.get(src, default)
        if raw is None:
            return ""
        return raw if isinstance(raw, str) else str(raw)
    if kind == "list":
        raw = cfg.get(src, default)
        return list(raw) if isinstance(raw, list) else _mutable_default_copy(default)
    if kind == "any":
        if src not in cfg:
            return _mutable_default_copy(default)
        return cfg[src]
    raise ValueError(f"TOOL_DISK_SCHEMA 未知 kind={kind!r}，字段={disk_key!r}")


def _tool_record_for_disk(tool_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """按 TOOL_DISK_SCHEMA + TOOL_DISK_SOURCE_MAP 生成落盘对象，再合并 cfg 中未消费的键。"""
    record: dict[str, Any] = {}
    sources_used: set[str] = set()

    for disk_key, default, kind in TOOL_DISK_SCHEMA:
        src = TOOL_DISK_SOURCE_MAP.get(disk_key, disk_key)
        sources_used.add(src)
        record[disk_key] = _coerce_tool_disk_value(disk_key, default, kind, tool_name, cfg)

    for key, val in cfg.items():
        if key in record or key in sources_used:
            continue
        record[key] = val

    return record


def _safe_tool_file_stem(tool_name: str) -> str:
    name = (tool_name or "").strip()
    if not name:
        raise ValueError("工具名不能为空")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"非法工具名: {tool_name!r}")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    if not safe or not re.search(r"[a-zA-Z0-9]", safe):
        raise ValueError(f"非法工具名: {tool_name!r}")
    return safe


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class ToolManager:
    """管理 tools 相关 RPC（与 SkillManager 的 handler 命名风格一致）。"""

    def __init__(
            self,
            get_agent: Callable[[], Any] | None = None,
            *,
            get_tools_dir: Callable[[], Path] | None = None,
            get_session_key: Callable[[], str] | None = None,
    ) -> None:
        """get_agent: 返回当前底层 Agent，用于 ``Runner.resource_mgr`` / ``ability_manager``。

        get_tools_dir: 可选；返回 MCP 配置落盘目录。多租户下由 ``JiuWenClaw`` 传入
        ``jiuwenclaw_workspace/tools``；缺省回退 ``utils.get_agent_tools_dir()``。

        get_session_key: 可选；返回当前 session 的池化键（channel::mode::session_id）。
        请求级 MCP worker 按 (session_key, server, params 指纹) 池化，
        HITL 审批拆出的多个请求复用同一 worker，浏览器不再随请求闪退。
        """
        self._get_agent = get_agent
        self._get_tools_dir = get_tools_dir
        self._get_session_key = get_session_key
        self._request_registrations: dict[str, list[dict[str, Any]]] = {}
        self._request_registrations_lock = asyncio.Lock()

    def _resolve_tools_dir(self) -> Path:
        if self._get_tools_dir is not None:
            return self._get_tools_dir()
        return get_agent_tools_dir()

    def _resolve_session_key(self) -> str:
        """解析当前请求所属 session 的池化键（channel::mode::session_id）。

        get_session_key 回调由 JiuWenClaw 注入（per-session agent 实例粒度）；
        缺回调（测试/legacy 路径）回退 request_id，保持请求粒度旧行为。
        """
        if self._get_session_key is not None:
            try:
                return str(self._get_session_key() or "")
            except Exception:
                logger.exception("[ToolManager] 解析 session_key 失败，回退 request_id 粒度")
        return ""

    @staticmethod
    def find_host_project_mcp_json() -> Path | None:
        """固定从宿主 Clowder AI 根目录查找 ``.mcp.json``。"""
        host_root = (os.getenv("CAT_CAFE_MCP_CWD") or "").strip()
        if not host_root:
            return None
        candidate = Path(host_root).resolve() / ".mcp.json"
        if candidate.is_file():
            return candidate
        return None

    async def load_project_mcp_json(self, mcp_json_path: str | Path) -> dict[str, Any]:
        """从项目根目录的 ``.mcp.json`` 导入工具，并复用 ``tools.add`` 的注册逻辑。"""
        path = Path(mcp_json_path)
        if not path.exists():
            return {
                "source": str(path),
                "saved": [],
                "registered_tools": [],
                "skipped": True,
                "reason": "not_found",
            }

        try:
            mcp_json = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"读取项目 MCP 配置失败: {exc}") from exc

        payload = await self.handle_tools_add({"mcp_json": mcp_json}, source="local")
        payload["source"] = str(path.resolve())
        payload["skipped"] = False
        return payload

    async def handle_tools_add(self, params: dict, *, source: str = "rpc") -> dict[str, Any]:
        """按工具名拆分落盘；对每个工具调用 ``create_mcp_tool`` 注册。

        params:
            mcp_json: str，根对象须含 ``mcpServers``，每个 key 为工具名。
        source: 
            str，标记来源。默认为 "rpc" (触发安全拦截)，
            本地加载请传入 "local"。
        """
        mcp_json = params.get("mcp_json")
        if not isinstance(mcp_json, str) or not mcp_json.strip():
            raise ValueError("缺少参数：请提供 mcp_json（JSON 字符串）")

        try:
            root = json.loads(mcp_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

        if not isinstance(root, dict):
            raise ValueError("根节点必须是 JSON 对象")
        servers = root.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError("缺少有效的 mcpServers 对象")

        for tool_name, cfg in servers.items():
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError(f"非法的工具名: {tool_name!r}")
            if not isinstance(cfg, dict):
                raise ValueError(f"mcpServers[{tool_name!r}] 必须是对象")

        # 安全拦截：非本地来源禁止通过 RPC（如浏览器）直接注册高危配置
        if source != "local":
            for tool_name, cfg in servers.items():
                _check_mcp_security(tool_name, cfg)

        agent = self._get_agent() if self._get_agent else None
        if agent is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        saved: list[dict[str, str]] = []
        registered: list[dict[str, str]] = []
        tools_dir = self._resolve_tools_dir()
        for tool_name, cfg in servers.items():
            stem = _safe_tool_file_stem(tool_name)
            out_path = tools_dir / f"{stem}.json"
            record = _tool_record_for_disk(tool_name, cfg)
            _atomic_write_json(out_path, record)
            saved.append({"name": tool_name, "path": str(out_path.resolve())})
            logger.info("[ToolManager] 已写入工具配置 name=%s path=%s", tool_name, out_path)

            single_json = json.dumps(record, ensure_ascii=False)
            mcp_cfg = create_mcp_tool(single_json)
            try:
                await _add_mcp_server_and_ability(agent, mcp_cfg, tag=mcp_cfg.server_name)
            except Exception as exc:
                logger.error("[ToolManager] 注册工具失败 name=%s: %s", tool_name, exc, extra={'user_visible': 'critical'})
                raise
            registered.append({"name": mcp_cfg.server_name, "id": mcp_cfg.server_id})
            logger.info("[ToolManager] 已注册工具 name=%s id=%s", mcp_cfg.server_name, mcp_cfg.server_id, 
                        extra={'user_visible': 'critical'})

        return {
            "saved": saved,
            "tools_dir": str(tools_dir.resolve()),
            "registered_tools": registered,
        }

    async def load_tools_from_disk(self, skip_server_names: set[str] | None = None) -> dict[str, Any]:
        """启动时扫描 ``tools/*.json``，按落盘记录注册 MCP 工具（MR !379）。"""
        agent = self._get_agent() if self._get_agent else None
        if agent is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        tools_dir = self._resolve_tools_dir()
        tools_dir.mkdir(parents=True, exist_ok=True)
        registered: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        skipped_names = {name for name in (skip_server_names or set()) if isinstance(name, str) and name}

        for path in sorted(tools_dir.glob("*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("[ToolManager] 跳过无效工具配置 %s: %s", path, exc)
                errors.append({"path": str(path), "error": str(exc)})
                continue
            if not isinstance(record, dict):
                logger.warning("[ToolManager] 跳过非对象 JSON: %s", path)
                errors.append({"path": str(path), "error": "根节点须为 JSON 对象"})
                continue

            name_hint = record.get("name") if isinstance(record.get("name"), str) else path.stem
            if name_hint in skipped_names:
                logger.info("[ToolManager] 跳过已从项目 .mcp.json 同步的工具 name=%s path=%s", name_hint, path)
                continue
            try:
                single_json = json.dumps(record, ensure_ascii=False)
                mcp_cfg = create_mcp_tool(single_json)
                await _add_mcp_server_and_ability(agent, mcp_cfg, tag=mcp_cfg.server_name)
            except Exception as exc:
                logger.error("[ToolManager] 启动加载工具失败 %s (%s): %s", path, name_hint, exc)
                errors.append({"path": str(path), "error": str(exc)})
                continue

            registered.append({"name": mcp_cfg.server_name, "id": mcp_cfg.server_id})
            logger.info(
                "[ToolManager] 启动已加载工具 name=%s id=%s path=%s",
                mcp_cfg.server_name,
                mcp_cfg.server_id,
                path,
            )

        return {
            "tools_dir": str(tools_dir.resolve()),
            "registered_tools": registered,
            "errors": errors,
        }

    async def register_request_scoped_mcp(self, payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        """注册请求级 MCP（stdio / sse / streamable-http），全部走长生命周期 session 池。

        - 每个连接器（server）独立 try/except，单连接器失败（发现失败/工具全部
          注册失败）只 skip 该连接器并记入 failed，不中断同请求其它连接器的注册；
        - 跨连接器 seen_names 去重：重名/非法工具名仅跳过该工具，不中断整次注册；
        - stdio 与 remote 统一由 request_scoped_mcp_sessions 池化：worker 按
          (session_key, server, params 指纹) 键控，同会话跨请求复用（HITL 审批
          拆请求时浏览器不闪退），轮次正常结束/取消/空闲超时时销毁。
        """
        if not isinstance(payload, dict):
            raise ValueError("request_mcp_servers 必须是对象")

        servers = payload.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            raise ValueError("缺少有效的 mcpServers 对象")

        agent = self._get_agent() if self._get_agent else None
        if agent is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")

        registered: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        # 跨连接器去重（qualified name 粒度，含 server 前缀天然隔离不同连接器；
        # 同一连接器重复上报同名工具时此处兜底）。
        seen_names: set[str] = set()
        # 池化键：worker 生命周期挂在 session 上（HITL 审批拆出的请求复用），
        # 注册表仍按 request_id 记录（每请求重注册/重摘除工具卡片）。
        session_key = self._resolve_session_key()

        for tool_name, cfg in servers.items():
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError(f"非法的工具名: {tool_name!r}")
            if not isinstance(cfg, dict):
                raise ValueError(f"mcpServers[{tool_name!r}] 必须是对象")

            record = {"name": tool_name, **cfg}
            single_json = json.dumps(record, ensure_ascii=False)
            # 安全层：危险参数过滤 / stdio 命令类型校验 / url 提取。
            # 单个连接器配置非法只 skip 该连接器（对齐 dev-stable per-connector 隔离），
            # 不中断同请求其它连接器的注册。
            try:
                mcp_cfg = create_mcp_tool(single_json)
            except ValueError as exc:
                logger.error(
                    "[ToolManager] 请求级 MCP 连接器配置非法，跳过: name=%s error=%s "
                    "request_id=%s",
                    tool_name, exc, request_id,
                )
                failed.append({"name": tool_name, "error": str(exc)})
                continue

            server_name = mcp_cfg.server_name
            client_type = str(getattr(mcp_cfg, "client_type", "") or "").lower()
            request_scoped_server_id = f"{server_name}::{request_id}"

            if client_type == "stdio":
                # 单个 stdio 连接器失败不中断整次注册（per-connector 隔离）。
                try:
                    _validate_cat_cafe_request_scoped_stdio(mcp_cfg.params or {})
                except ValueError as exc:
                    logger.error(
                        "[ToolManager] 请求级 stdio MCP 校验失败，跳过: name=%s error=%s",
                        server_name, exc,
                    )
                    failed.append({"name": server_name, "error": str(exc)})
                    continue

                stdio_sp = stdio_params_from_mcp_config(mcp_cfg.params or {})
                params_map = dict(_REQUEST_STDIO_PARAMS.get() or {})
                params_map[server_name] = stdio_sp
                _REQUEST_STDIO_PARAMS.set(params_map)

                tool_defs = await discover_stdio_mcp_tools(mcp_cfg.params or {}, server_name=server_name)
                if not tool_defs:
                    logger.error(
                        "[ToolManager] 请求级 stdio MCP 未发现任何工具，跳过: "
                        "request_id=%s name=%s",
                        request_id, server_name,
                    )
                    failed.append({"name": server_name, "error": "stdio MCP 未发现工具（发现失败或超时）"})
                    continue

                # stdio 连接参数（含 _mcp_client_type 标记，供 worker 分派）。
                pool_params = dict(stdio_sp)
                pool_params["_mcp_client_type"] = "stdio"

                connector_tool_names = await self._register_pooled_mcp_tools(
                    agent,
                    request_id=request_id,
                    server_name=server_name,
                    request_scoped_server_id=request_scoped_server_id,
                    tool_defs=tool_defs,
                    get_params=_make_stdio_params_getter(server_name),
                    pool_params=pool_params,
                    seen_names=seen_names,
                    kind="stdio",
                    session_key=session_key,
                )
                if not connector_tool_names:
                    failed.append({"name": server_name, "error": "该 stdio 连接器的工具全部注册失败（重名或 add_tool 错误）"})
                    continue

                registered.append({"name": server_name, "server_id": request_scoped_server_id, "kind": "stdio"})
                logger.info(
                    "[ToolManager] 已注册请求级 MCP（stdio）request_id=%s name=%s tools=%s",
                    request_id, server_name, connector_tool_names,
                )
            elif client_type in ("sse", "streamable-http"):
                try:
                    _validate_request_scoped_remote_mcp(tool_name, cfg)
                except ValueError as exc:
                    logger.error(
                        "[ToolManager] 请求级 remote MCP 校验失败，跳过: name=%s error=%s",
                        server_name, exc,
                    )
                    failed.append({"name": server_name, "error": str(exc)})
                    continue

                logger.info(
                    "[ToolManager][AUDIT] 请求级 MCP 注册 request_id=%s kind=%s name=%s "
                    "url=%s has_auth=%s",
                    request_id, client_type, server_name,
                    cfg.get("url", ""),
                    bool(cfg.get("auth_headers") or cfg.get("auth_query_params")),
                )
                # remote 发现（connect/list_tools/disconnect，带超时）：
                # 单个连接器发现失败只 skip，不中断其它连接器。
                tool_defs = await discover_remote_mcp_tools(server_name, mcp_cfg, client_type)
                if not tool_defs:
                    logger.error(
                        "[ToolManager] 请求级 remote MCP 未发现任何工具，跳过: "
                        "request_id=%s name=%s url=%s",
                        request_id, server_name, cfg.get("url", "?"),
                    )
                    failed.append({"name": server_name, "error": f"{client_type} MCP 未发现工具（连接失败或超时）"})
                    continue

                connector_tool_names = await self._register_pooled_mcp_tools(
                    agent,
                    request_id=request_id,
                    server_name=server_name,
                    request_scoped_server_id=request_scoped_server_id,
                    tool_defs=tool_defs,
                    get_params=lambda _cfg=mcp_cfg, _n=server_name: build_remote_connect_params(_n, _cfg),
                    pool_params=build_remote_connect_params(server_name, mcp_cfg),
                    seen_names=seen_names,
                    kind=client_type,
                    session_key=session_key,
                )
                if not connector_tool_names:
                    failed.append({"name": server_name, "error": "该 remote 连接器的工具全部注册失败（重名或 add_tool 错误）"})
                    continue

                registered.append({
                    "name": server_name,
                    "server_id": request_scoped_server_id,
                    "kind": client_type,
                })
                logger.info(
                    "[ToolManager] 已注册请求级 MCP（%s）request_id=%s name=%s tools=%s",
                    client_type, request_id, server_name, connector_tool_names,
                )
            else:
                # 不支持的 transport（playwright/openapi 等）：保持跳过并记录。
                logger.warning(
                    "[ToolManager] 请求级 MCP transport '%s' 不支持，跳过: name=%s",
                    client_type or "unknown", server_name,
                )
                failed.append({"name": server_name, "error": f"unsupported transport: {client_type or 'unknown'}"})
                continue

        return {
            "registered": True,
            "request_id": request_id,
            "tools": registered,
            "failed": failed,
        }

    async def _register_pooled_mcp_tools(
        self,
        agent: Any,
        *,
        request_id: str,
        server_name: str,
        request_scoped_server_id: str,
        tool_defs: list[dict[str, Any]],
        get_params: Any,
        pool_params: dict[str, Any],
        seen_names: set[str],
        kind: str,
        session_key: str = "",
    ) -> list[str]:
        """把单个连接器的工具定义注册为池化工具（PooledRequestMcpTool）。

        返回成功注册的 qualified name 列表；重名/非法名仅跳过该工具。
        单个工具 add_tool 失败只 warning + skip（对齐 dev-stable 的
        per-tool 语义），不再 raise 中断整次注册。
        """
        tool_ids: list[str] = []
        tool_names: list[str] = []
        for td in tool_defs:
            tname = str(td.get("name") or "").strip()
            if not tname:
                logger.warning(
                    "[ToolManager] 请求级 MCP 连接器 '%s' 存在空工具名，跳过",
                    server_name,
                )
                continue
            # 不同连接器下可能存在同名工具（如多个 Supabase MCP 共享 execute_sql）；
            # 在 LLM 可见名上叠加 server 前缀做命名空间隔离，避免 ability_manager._tools
            # 后注册覆盖前注册。资源侧的 full id 不变，仍由 server_id 唯一定位。
            qualified_name = f"{server_name}__{tname}"
            if qualified_name in seen_names:
                logger.warning(
                    "[ToolManager] 请求级 MCP 工具名重复，跳过: name=%s "
                    "(已被先前连接器/office-claw 注册): request_id=%s",
                    qualified_name, request_id,
                )
                continue
            seen_names.add(qualified_name)

            tool_id = f"{request_scoped_server_id}.{server_name}.{tname}"
            card = ToolCard(
                id=tool_id,
                name=qualified_name,
                description=str(td.get("description") or ""),
                input_params=td.get("input_params") or {},
            )
            pooled = PooledRequestMcpTool(
                card,
                get_params,
                raw_tool_name=tname,
                request_id=request_id,
                server_name=server_name,
                session_key=session_key,
            )
            add_res = Runner.resource_mgr.add_tool(pooled, tag=server_name)
            if add_res is not None and hasattr(add_res, "is_ok") and not add_res.is_ok():
                err = _mcp_add_result_error_text(add_res)
                if "already exist" not in err.lower():
                    logger.warning(
                        "[ToolManager] 请求级 MCP 工具 add_tool 失败，跳过: "
                        "name=%s tool=%s error=%s: request_id=%s",
                        server_name, tname, err, request_id,
                    )
                    continue
            agent.ability_manager.add(card)
            tool_ids.append(tool_id)
            tool_names.append(qualified_name)

        if tool_ids:
            reg = {
                "kind": kind,
                "server_name": server_name,
                "server_id": request_scoped_server_id,
                "tool_ids": tool_ids,
                "tool_names": tool_names,
                "pool_params": pool_params,
            }
            async with self._request_registrations_lock:
                self._request_registrations.setdefault(request_id, []).append(reg)
        return tool_names

    async def unregister_request_scoped_mcp(self, request_id: str) -> None:
        async with self._request_registrations_lock:
            registrations = self._request_registrations.pop(request_id, None)
        if not registrations:
            return

        agent = self._get_agent() if self._get_agent else None
        has_stdio = False

        for reg in registrations:
            try:
                if reg.get("kind") == "stdio":
                    has_stdio = True
                    for tool_id in reg.get("tool_ids", []):
                        try:
                            Runner.resource_mgr.remove_tool(tool_id)
                        except Exception as exc:
                            logger.warning("[ToolManager] remove_tool 失败 tool_id=%s: %s", tool_id, exc)
                    if agent is not None:
                        # tool_names 已经是带 server 前缀的 qualified name，
                        # 与 add() 时写入 ability_manager._tools 的 key 一致。
                        for tool_name in reg.get("tool_names", []):
                            try:
                                agent.ability_manager.remove(tool_name)
                            except Exception as exc:
                                logger.warning(
                                    "[ToolManager] ability_manager.remove 失败 name=%s: %s",
                                    tool_name, exc,
                                )
                elif reg.get("kind") == "shared":
                    server_id = reg.get("server_id")
                    if server_id:
                        try:
                            await Runner.resource_mgr.remove_mcp_server(
                                server_id=server_id,
                                tag=reg.get("server_name"),
                                ignore_exception=True,
                            )
                        except Exception as exc:
                            logger.warning("[ToolManager] remove_mcp_server 失败 server_id=%s: %s", server_id, exc)
                    if agent is not None:
                        server_name = reg.get("server_name")
                        if server_name:
                            try:
                                agent.ability_manager.remove(server_name)
                            except Exception as exc:
                                logger.warning("[ToolManager] ability_manager.remove 失败 name=%s: %s", server_name, exc)
                logger.info(
                    "[ToolManager] 已清理请求级 MCP request_id=%s kind=%s name=%s",
                    request_id, reg.get("kind"), reg.get("server_name"),
                )
            except Exception as exc:
                logger.warning("[ToolManager] 清理请求级 MCP 失败 request_id=%s: %s", request_id, exc)

        if has_stdio:
            _REQUEST_STDIO_PARAMS.set(None)

        # 长生命周期 worker 不再随请求销毁（session 级池化：HITL 审批拆出的
        # 后续请求复用同一 stdio 进程/浏览器）。仅回收"只被本请求用过且不再
        # 被复用"的孤儿（如参数漂移后遗留的旧指纹 worker）——正常路径由
        # interface 层的 close-on-final / cancel / session 逐出 / 空闲 TTL
        # sweep 驱动释放。best-effort，不阻塞清理。
        try:
            await release_request_scoped_mcp_sessions(request_id)
        except Exception as exc:
            logger.warning(
                "[ToolManager] 请求级 MCP 孤儿 worker 回收失败: request_id=%s error=%s",
                request_id, exc,
            )

    async def release_session_scoped_mcp(self, session_key: str) -> None:
        """释放该 session 名下全部池化 MCP worker（关闭 stdio 子进程/浏览器、
        remote 长连接）。

        触发点（interface 层驱动）：
        - 用户轮次正常结束（close-on-final：流终态非 paused）；
        - 用户主动 cancel（chat.interrupt intent=cancel）；
        - session 逐出 / Agent 销毁（cleanup）。
        """
        if not session_key:
            return
        try:
            await release_session_mcp_workers(session_key)
            logger.info(
                "[ToolManager] 已释放 session 级 MCP worker: session_key=%s",
                session_key,
            )
        except Exception as exc:
            logger.warning(
                "[ToolManager] session 级 MCP worker 释放失败: session_key=%s error=%s",
                session_key, exc,
            )

    async def unregister_all_request_scoped_mcp(self) -> None:
        request_ids = list(self._request_registrations.keys())
        for request_id in request_ids:
            try:
                await self.unregister_request_scoped_mcp(request_id)
            except Exception as exc:
                logger.warning("[ToolManager] 兜底清理失败 request_id=%s: %s", request_id, exc)
        # 兜底：Agent 销毁时释放本 session 名下全部残留池化 worker（stdio
        # 子进程/浏览器），防其活过 Agent 生命周期。池是进程级全局，其它
        # session 的 worker 不在此清（各自 Agent 实例的 cleanup 负责）。
        session_key = self._resolve_session_key()
        if session_key:
            try:
                await release_session_mcp_workers(session_key)
            except Exception as exc:
                logger.warning(
                    "[ToolManager] 池化 worker session 级兜底清理失败: session_key=%s error=%s",
                    session_key, exc,
                )

    async def register_request_scoped_office_claw_mcp(self, cfg: dict[str, Any]) -> dict[str, Any]:
        from uuid import uuid4
        request_id = f"legacy_office_claw_{uuid4().hex[:12]}"
        payload = {"mcpServers": {_OFFICE_CLAW_SERVER_NAME_PREFIX: cfg}}
        return await self.register_request_scoped_mcp(payload, request_id=request_id)
