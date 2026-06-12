
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ToolManager - tools.add 等 RPC：落盘用户 MCP 工具配置并交给 mcp_toolkits 生成 McpServerConfig。"""

from __future__ import annotations

import json
import os
import re
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Literal

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner

from jiuwenclaw.utils import get_agent_tools_dir, logger

from jiuwenclaw.agentserver.tools.ephemeral_stdio_mcp_tool import (
    EphemeralStdioMcpTool,
    list_stdio_mcp_tool_defs,
    stdio_params_from_mcp_config,
)
from jiuwenclaw.agentserver.tools.mcp_toolkits import _normalize_stdio_command_kind, create_mcp_tool

_OFFICE_CLAW_SERVER_NAME_PREFIX = "office-claw"
_REQUEST_SCOPED_OFFICE_CLAW_SERVER_ID = "office-claw-request"

# 已存在的 MCP 注册错误标识；与 Runner.resource_mgr.add_mcp_server 的错误消息约定一致
_MCP_ALREADY_EXIST_TOKEN = "already exist"

# 请求级 stdio 参数：每个异步上下文独立，避免并发请求间配置串台
# 注意：default 使用工厂函数避免在多个上下文之间共享同一可变字典
_OFFICE_CLAW_STDIO_PARAMS: ContextVar[dict[str, Any]] = ContextVar("_OFFICE_CLAW_STDIO_PARAMS")


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


def _get_office_claw_stdio_params() -> dict[str, Any]:
    """回调函数：供 EphemeralStdioMcpTool 在 invoke 时获取当前请求级的 stdio 参数。

    若当前上下文未设置参数，返回新的空 dict（避免共享可变默认值）。
    """
    try:
        return _OFFICE_CLAW_STDIO_PARAMS.get()
    except LookupError:
        return {}


def _resolve_path_safe(value: str) -> Path | None:
    """解析路径，失败时记录 debug 日志并返回 None。"""
    try:
        return Path(value).expanduser().resolve()
    except OSError as exc:
        logger.debug("[ToolManager] 路径解析失败 value=%s error=%s", value, exc)
        return None


def _trusted_cat_cafe_stdio_roots() -> list[Path]:
    """请求级 stdio MCP 允许加载的脚本/工作目录须落在这些根路径之下。"""
    candidates: list[str] = []
    for env_key in ("CAT_CAFE_MCP_CWD", "OFFICE_CLAW_MCP_CWD"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            candidates.append(raw)
    candidates.append(str(Path.home() / ".office-claw"))
    candidates.append(str(Path(sys.executable).parent))

    lp = (os.getenv("LOCALAPPDATA") or "").strip()
    if lp:
        candidates.append(str(Path(lp) / "Programs" / "OfficeClaw"))
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = (os.getenv(env_key) or "").strip()
        if base:
            candidates.append(str(Path(base) / "OfficeClaw"))

    roots: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        resolved = _resolve_path_safe(raw)
        if resolved is None:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


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
    """限制请求级 stdio：禁止内联代码执行面，脚本路径须在受信根目录下。"""
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


def _mcp_result_is_ok(result: Any) -> bool:
    """解析 ``add_mcp_server`` 返回值。

    异常视为非 OK 并记录日志，避免内部缺陷被静默成 False。
    """
    if result is None:
        return True
    is_ok = getattr(result, "is_ok", None)
    if not callable(is_ok):
        return False
    try:
        return bool(is_ok())
    except Exception as exc:
        logger.warning("[ToolManager] is_ok() 调用异常: %s", exc)
        return False


def _mcp_result_error_text(result: Any) -> str:
    """提取 MCP 操作结果的错误信息文本。"""
    if result is None:
        return ""
    for attr in ("error", "msg"):
        fn = getattr(result, attr, None)
        if not callable(fn):
            continue
        try:
            value = fn()
        except Exception as exc:
            logger.debug("[ToolManager] %s() 取错误信息失败: %s", attr, exc)
            continue
        if value is not None:
            return str(value)
    value = getattr(result, "_error", None)
    if value is not None:
        return str(value)
    return str(result)


def _is_already_exist_error(err: str) -> bool:
    """判断 MCP 错误信息是否表示"资源已存在"。

    目前底层只暴露字符串错误，未来如改为结构化错误码可在此集中替换。
    """
    return _MCP_ALREADY_EXIST_TOKEN in (err or "").lower()


async def _add_mcp_server_and_ability(agent: Any, mcp_cfg: Any, *, tag: str) -> None:
    """调用 ``add_mcp_server``，按返回值决定是否 ``ability_manager.add``；失败抛 ``RuntimeError``。"""
    result = await Runner.resource_mgr.add_mcp_server(mcp_cfg, tag=tag)
    if _mcp_result_is_ok(result):
        agent.ability_manager.add(mcp_cfg)
        return
    err = _mcp_result_error_text(result)
    if _is_already_exist_error(err):
        agent.ability_manager.add(mcp_cfg)
        logger.info("[ToolManager] add_mcp_server 已存在，仍加入 ability_manager: %s", err)
        return
    raise RuntimeError(f"add_mcp_server 失败: {err}" if err else "add_mcp_server 失败")


# ---------------------------------------------------------------------------
# 落盘 JSON 模板：列表顺序即写入顺序；每项为 (disk_key, default, kind)。
# kind:
#   tool_name — 使用 mcpServers 的 key，忽略 cfg 里的同名键
#   text      — 字符串；cfg 缺省用 default，非 str 则 str()
#   list      — 列表；cfg 非 list 则用 default 的拷贝
#   any       — cfg.get(disk_key, default)；default 为 dict/list 时做浅拷贝
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
    if kind == "tool_name":
        return (tool_name or "").strip()
    if kind == "text":
        raw = cfg.get(disk_key, default)
        if raw is None:
            return ""
        return raw if isinstance(raw, str) else str(raw)
    if kind == "list":
        raw = cfg.get(disk_key, default)
        return list(raw) if isinstance(raw, list) else _mutable_default_copy(default)
    if kind == "any":
        if disk_key not in cfg:
            return _mutable_default_copy(default)
        return cfg[disk_key]
    raise ValueError(f"TOOL_DISK_SCHEMA 未知 kind={kind!r}，字段={disk_key!r}")


def _tool_record_for_disk(tool_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """按 TOOL_DISK_SCHEMA 生成落盘对象，再合并 cfg 中未消费的键。"""
    record: dict[str, Any] = {}
    consumed: set[str] = set()

    for disk_key, default, kind in TOOL_DISK_SCHEMA:
        consumed.add(disk_key)
        record[disk_key] = _coerce_tool_disk_value(disk_key, default, kind, tool_name, cfg)

    for key, val in cfg.items():
        if key in consumed:
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
            except OSError as exc:
                logger.debug("[ToolManager] 清理临时文件失败 path=%s error=%s", tmp, exc)
        raise


def _build_registered_payload(server_name: str, server_id: str) -> dict[str, Any]:
    """统一构造单工具注册成功的返回结构。"""
    return {
        "registered": True,
        "name": server_name,
        "server_id": server_id,
    }


class ToolManager:
    """管理 tools 相关 RPC（与 SkillManager 的 handler 命名风格一致）。"""

    def __init__(
            self,
            get_agent: Callable[[], Any] | None = None,
            *,
            get_tools_dir: Callable[[], Path] | None = None,
    ) -> None:
        """get_agent: 返回当前底层 Agent，用于 ``Runner.resource_mgr`` / ``ability_manager``。

        get_tools_dir: 可选；返回 MCP 配置落盘目录。多租户下由 ``JiuWenClaw`` 传入
        ``jiuwenclaw_workspace/tools``；缺省回退 ``utils.get_agent_tools_dir()``。
        """
        self._get_agent = get_agent
        self._get_tools_dir = get_tools_dir
        # (tool_id, tool_name)，请求级 Office Claw stdio 走 ephemeral 注册时用于下次替换前卸载
        self._office_claw_ephemeral_tools: list[tuple[str, str]] = []

    def _resolve_tools_dir(self) -> Path:
        if self._get_tools_dir is not None:
            return self._get_tools_dir()
        return get_agent_tools_dir()

    def _require_agent(self) -> Any:
        """获取已初始化的底层 Agent；未就绪时抛 RuntimeError。"""
        agent = self._get_agent() if self._get_agent else None
        if agent is None:
            raise RuntimeError("JiuWenClaw 未初始化，请先调用 create_instance()")
        return agent

    async def _unregister_ephemeral_office_claw_tools(self, agent: Any) -> None:
        """清理上一次注册的请求级 Office Claw ephemeral 工具，防止内存泄漏。"""
        if not self._office_claw_ephemeral_tools:
            return
        for tool_id, tname in self._office_claw_ephemeral_tools:
            try:
                remove_tool = getattr(Runner.resource_mgr, "remove_tool", None)
                if callable(remove_tool):
                    remove_tool(tool_id, ignore_not_exist=True)
            except Exception as exc:
                logger.warning(
                    "[ToolManager] 清理 ephemeral Office Claw 工具失败 tool=%s id=%s: %s",
                    tname, tool_id, exc,
                )
            try:
                agent.ability_manager.remove(tname)
            except Exception as exc:
                logger.debug(
                    "[ToolManager] ability_manager 移除 ephemeral 工具失败 tool=%s: %s",
                    tname, exc,
                )
        self._office_claw_ephemeral_tools.clear()

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

    async def handle_tools_add(
            self,
            params: dict,
            *,
            source: Literal["rpc", "local"] = "rpc",
    ) -> dict[str, Any]:
        """按工具名拆分落盘；对每个工具调用 ``create_mcp_tool`` 注册。

        params:
            mcp_json: str，根对象须含 ``mcpServers``，每个 key 为工具名。
        source:
            "rpc"   — 来自外部 RPC（如浏览器），强制安全拦截
            "local" — 来自宿主项目本地配置，跳过安全拦截
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

        agent = self._require_agent()

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
                # 注册失败时回滚刚写入的磁盘文件，避免下次启动加载到无效配置
                logger.error(
                    "[ToolManager] 注册工具失败 name=%s: %s",
                    tool_name, exc, extra={'user_visible': 'critical'},
                )
                try:
                    if out_path.exists():
                        out_path.unlink()
                        saved.pop()
                        logger.warning("[ToolManager] 已回滚落盘文件 name=%s path=%s", tool_name, out_path)
                except OSError as rollback_exc:
                    logger.warning(
                        "[ToolManager] 回滚落盘文件失败 name=%s path=%s: %s",
                        tool_name, out_path, rollback_exc,
                    )
                raise
            registered.append({"name": mcp_cfg.server_name, "id": mcp_cfg.server_id})
            logger.info(
                "[ToolManager] 已注册工具 name=%s id=%s",
                mcp_cfg.server_name, mcp_cfg.server_id,
                extra={'user_visible': 'critical'},
            )

        return {
            "saved": saved,
            "tools_dir": str(tools_dir.resolve()),
            "registered_tools": registered,
        }

    async def load_tools_from_disk(self, skip_server_names: set[str] | None = None) -> dict[str, Any]:
        """启动时扫描 ``tools/*.json``，按落盘记录注册 MCP 工具（MR !379）。"""
        agent = self._require_agent()

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

    async def register_request_scoped_office_claw_mcp(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """按请求注册 Office Claw MCP；stdio 工具以单次 invoke 模式执行。"""
        if not isinstance(cfg, dict):
            raise ValueError("office_claw_mcp 必须是对象")

        agent = self._require_agent()

        mcp_servers = getattr(agent.ability_manager, "_mcp_servers", {})
        names_to_remove = [
            name for name in mcp_servers
            if isinstance(name, str) and name.startswith(_OFFICE_CLAW_SERVER_NAME_PREFIX)
        ]
        for server_name in names_to_remove:
            get_server_ids = getattr(Runner.resource_mgr, "get_mcp_server_ids", None)
            server_ids = list(get_server_ids(server_name) or []) if callable(get_server_ids) else []
            for server_id in server_ids:
                try:
                    await Runner.resource_mgr.remove_tool_server(server_id, ignore_not_exist=True)
                except Exception as exc:
                    logger.warning("[ToolManager] 移除旧的 Office Claw MCP 失败 name=%s id=%s: %s",
                        server_name, server_id, exc)
            agent.ability_manager.remove(server_name)

        # 清理上一次的 ephemeral 工具，避免 _office_claw_ephemeral_tools 永久累积
        await self._unregister_ephemeral_office_claw_tools(agent)

        record = {
            "name": _OFFICE_CLAW_SERVER_NAME_PREFIX,
            "server_id": _REQUEST_SCOPED_OFFICE_CLAW_SERVER_ID,
            **cfg,
        }
        single_json = json.dumps(record, ensure_ascii=False)
        mcp_cfg = create_mcp_tool(single_json)

        # stdio：不经过 add_mcp_server，每工具每次 invoke 单独起停子进程，避免会话间状态串台
        if getattr(mcp_cfg, "client_type", "") == "stdio":
            return await self._register_ephemeral_stdio_tools(agent, mcp_cfg)

        await _add_mcp_server_and_ability(agent, mcp_cfg, tag=mcp_cfg.server_name)
        logger.info(
            "[ToolManager] 已注册请求级 Office Claw MCP name=%s id=%s",
            mcp_cfg.server_name, mcp_cfg.server_id,
        )
        return _build_registered_payload(mcp_cfg.server_name, mcp_cfg.server_id)

    async def _register_ephemeral_stdio_tools(self, agent: Any, mcp_cfg: Any) -> dict[str, Any]:
        """注册请求级 Office Claw stdio ephemeral 工具集合。

        校验失败抛 ValueError（参数非法），列举/注册失败抛 RuntimeError（运行时故障），
        便于上层区分两类错误。
        """
        _validate_cat_cafe_request_scoped_stdio(mcp_cfg.params or {})

        stdio_sp = stdio_params_from_mcp_config(mcp_cfg.params or {})
        _OFFICE_CLAW_STDIO_PARAMS.set(stdio_sp)

        try:
            tool_defs = await list_stdio_mcp_tool_defs(mcp_cfg.params or {})
        except Exception as exc:
            raise RuntimeError(f"列举 Office Claw stdio MCP 工具失败: {exc}") from exc

        for td in tool_defs:
            tname = td["name"]
            tool_id = f"{mcp_cfg.server_id}.{mcp_cfg.server_name}.{tname}"
            card = ToolCard(
                id=tool_id,
                name=tname,
                description=td.get("description") or "",
                input_params=td.get("input_params") or {},
            )
            ephemeral = EphemeralStdioMcpTool(card, _get_office_claw_stdio_params)
            add_res = Runner.resource_mgr.add_tool(ephemeral, tag=mcp_cfg.server_name)
            if add_res is not None and hasattr(add_res, "is_ok") and not add_res.is_ok():
                err = _mcp_result_error_text(add_res)
                if not _is_already_exist_error(err):
                    raise RuntimeError(f"注册 ephemeral Office Claw 工具失败 {tname}: {err}")
                logger.info(
                    "[ToolManager] ephemeral Office Claw 工具已存在，复用现有资源 tool=%s id=%s err=%s",
                    tname, tool_id, err,
                )
            agent.ability_manager.add(card)
            self._office_claw_ephemeral_tools.append((tool_id, tname))

        logger.info(
            "[ToolManager] 已注册请求级 Office Claw MCP（stdio 每调用隔离）name=%s id=%s tools=%s",
            mcp_cfg.server_name,
            mcp_cfg.server_id,
            [t[1] for t in self._office_claw_ephemeral_tools],
        )
        return _build_registered_payload(mcp_cfg.server_name, mcp_cfg.server_id)
