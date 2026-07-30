# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 沙箱运行时 policy 副本渲染与读写.

officeAce 经 WS 接口 (sandbox.files.set / sandbox.network.set) 配置的文件白/黑名单、
网络域名白/黑名单, **直接写进 windows-policy 运行时副本的对应字段**, 不存 config.yaml.
config.yaml 仅保留基础配置 (sandbox.enabled / startup_mode / url / type / policy_file).

副本结构 (``<OFFICE_CLAW_DATA_ROOT>/windows-policy.runtime.yaml``):
  - 顶层 ``user_overrides`` 段: 存用户原始配置 (files/network), 便于 get 返回 + 取消不丢基底.
    **注意**: box-server 加载副本时走 ``SecurityPolicy.model_validate``; 该模型未设
    ``model_config = ConfigDict(extra="forbid")``, Pydantic v2 默认 ``extra="ignore"`` →
    ``user_overrides`` 段被静默忽略, 不报错 (已实测验证). 若将来给 SecurityPolicy 加
    ``extra="forbid"``, 需把 user_overrides 从 ``windows`` 段移到副本外的独立存储.
  - ``windows`` 段: box-server 实际读的部分; 每次 render 从干净基底 (打包
    windows-policy.yaml) deepcopy 重建, 再把 user_overrides 合并进去, 保证用户取消某配置
    后 windows 干净回落基底原值 (pypi/npmmirror egress / workspace allow_write).

生效语义 (关键):
  - 文件 ACL: 沙箱创建时读 (process.py:_create_windows) → 销毁重建沙箱即生效.
  - 网络 egress: box-server 启动时读 (app.py lifespan → EgressFilter) → 重启 box-server 生效.
  - disable_all (总开关只压不删): true 时 default=deny + 运行时旁路 allow (不把 allow_domains
    写进 egress.allowed_domains → EgressFilter 全拒), 但 user_overrides.network.allow_domains
    原样保留, 关掉总开关即恢复写入.

设计依据: docs/windows_sandbox_officeace_integration_design.md §1.3 配置生效机制;
win_proxy.py:EgressFilter.allow (deny 优先, 有 allow 规则时命中的放行, 无则按 default).
"""

from __future__ import annotations

import copy
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_BASE_POLICY_NAME = "windows-policy.yaml"
_RUNTIME_COPY_NAME = "windows-policy.runtime.yaml"
_USER_OVERRIDES_KEY = "user_overrides"
_FILES_DEFAULTS: dict[str, Any] = {"allow": [], "deny": []}
_NETWORK_DEFAULTS: dict[str, Any] = {
    "disable_all": False,
    "allow_domains": [],
    "deny_domains": [],
}


def _config_dir() -> Path:
    """副本所在目录: 与 config.yaml 同目录 (<workspace>/config/).

    照搬 config.yaml 机制: 跟随 ``JIUWENCLAW_DATA_DIR`` / workspace 解析
    (jiuwenclaw.utils.get_config_dir), 不引入新的 OFFICE_CLAW_DATA_DIR 依赖,
    与 config.yaml 同根、随 workspace 走. agent-server 启动时 workspace 已初始化,
    该目录必然存在 (init_user_workspace 创建).
    """
    from jiuwenclaw.utils import get_config_dir  # lazy import, 避免 agentserver 启动期耦合
    return get_config_dir()


def _jiuwenbox_configs_dir() -> Path | None:
    """探测 jiuwenbox/configs/ 目录 (基底 windows-policy.yaml 所在).

    与 jiuwenclaw.config._jiuwenbox_configs_dir 同实现 (本地复制, 避免私有函数跨模块引用).
    """
    here = Path(__file__).resolve()
    for ancestor in here.parents[1:7]:
        for candidate in (
            ancestor / "jiuwenbox" / "src" / "jiuwenbox" / "configs",
            ancestor / "jiuwenbox" / "configs",
        ):
            if candidate.is_dir():
                return candidate
    try:
        import jiuwenbox  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pkg_dir = Path(jiuwenbox.__file__).resolve().parent
    except Exception:  # noqa: BLE001
        return None
    direct = pkg_dir / "configs"
    if direct.is_dir():
        return direct
    for steps_up in (2, 3):
        candidate = pkg_dir
        for _ in range(steps_up):
            candidate = candidate.parent
        candidate = candidate / "configs"
        if candidate.is_dir():
            return candidate
    return None


def _base_policy_path() -> Path | None:
    """打包基底 windows-policy.yaml 路径; 不存在返回 None."""
    configs = _jiuwenbox_configs_dir()
    if configs is None:
        return None
    p = configs / _BASE_POLICY_NAME
    return p if p.is_file() else None


def _runtime_copy_path() -> Path:
    """运行时副本落点: <config_dir>/windows-policy.runtime.yaml (与 config.yaml 同目录)."""
    return _config_dir() / _RUNTIME_COPY_NAME


def _ensure_copy_exists() -> Path:
    """副本不存在时从基底复制一份 (含空 user_overrides). 返回副本路径.

    无基底 (非 Windows / 未安装 jiuwenbox) 也返回路径, 但文件不会创建 (后续 _load_copy 返回 {}).
    """
    copy_p = _runtime_copy_path()
    copy_p.parent.mkdir(parents=True, exist_ok=True)
    if not copy_p.is_file():
        base_p = _base_policy_path()
        if base_p is None:
            return copy_p
        try:
            base = yaml.safe_load(base_p.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            logger.warning("读基底 policy %s 失败: %s", base_p, exc)
            return copy_p
        if not isinstance(base, dict):
            base = {}
        base[_USER_OVERRIDES_KEY] = {
            "files": copy.deepcopy(_FILES_DEFAULTS),
            "network": copy.deepcopy(_NETWORK_DEFAULTS),
        }
        try:
            copy_p.write_text(
                yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("已从基底创建运行时 policy 副本: %s", copy_p)
        except OSError as exc:
            logger.warning("写运行时 policy 副本 %s 失败: %s", copy_p, exc)
    return copy_p


def _load_copy() -> dict[str, Any]:
    """读副本 (不存在则返回空 dict)."""
    p = _ensure_copy_exists()
    if not p.is_file():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("读副本 %s 失败: %s", p, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_copy(data: dict[str, Any]) -> None:
    p = _runtime_copy_path()
    try:
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("写副本 %s 失败: %s", p, exc)


def _ensure_user_overrides(data: dict[str, Any]) -> dict[str, Any]:
    ov = data.get(_USER_OVERRIDES_KEY)
    if not isinstance(ov, dict):
        ov = {
            "files": copy.deepcopy(_FILES_DEFAULTS),
            "network": copy.deepcopy(_NETWORK_DEFAULTS),
        }
        data[_USER_OVERRIDES_KEY] = ov
    else:
        if not isinstance(ov.get("files"), dict):
            ov["files"] = copy.deepcopy(_FILES_DEFAULTS)
        if not isinstance(ov.get("network"), dict):
            ov["network"] = copy.deepcopy(_NETWORK_DEFAULTS)
    return ov


# ----------------------------------------------------------------------------
# get / set: 读写 user_overrides 段 (用户配置原始值)
# ----------------------------------------------------------------------------

def get_sandbox_files_config() -> dict[str, Any]:
    """返回用户文件白/黑名单 (user_overrides.files, 不含基底必需集)."""
    data = _load_copy()
    ov = _ensure_user_overrides(data)
    files = ov.get("files") or {}
    return {
        "allow": [str(p) for p in (files.get("allow") or []) if str(p).strip()],
        "deny": [str(p) for p in (files.get("deny") or []) if str(p).strip()],
    }


def set_sandbox_files_config(allow: list[Any], deny: list[Any]) -> dict[str, Any]:
    """整体替换用户文件白/黑名单, 写副本 user_overrides.files, 再 render 合并进 windows.filesystem.

    allow/deny 都可为空 list (表示清空用户段 → 副本 windows 回落基底必需集).
    """
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise ValueError("allow and deny must be lists")
    allow_norm = [str(p) for p in allow if str(p).strip()]
    deny_norm = [str(p) for p in deny if str(p).strip()]
    data = _load_copy()
    ov = _ensure_user_overrides(data)
    ov["files"] = {"allow": allow_norm, "deny": deny_norm}
    _save_copy(data)
    render_runtime_policy()
    return {"allow": allow_norm, "deny": deny_norm}


def get_sandbox_network_config() -> dict[str, Any]:
    """返回用户网络配置 (user_overrides.network)."""
    data = _load_copy()
    ov = _ensure_user_overrides(data)
    net = ov.get("network") or {}
    return {
        "disable_all": bool(net.get("disable_all", False)),
        "allow_domains": [str(d) for d in (net.get("allow_domains") or []) if str(d).strip()],
        "deny_domains": [str(d) for d in (net.get("deny_domains") or []) if str(d).strip()],
    }


def set_sandbox_network_config(
    disable_all: bool,
    allow_domains: list[Any],
    deny_domains: list[Any],
) -> dict[str, Any]:
    """整体替换用户网络配置, 写副本 user_overrides.network, 再 render 合并进 windows.network.egress.

    disable_all=true: 总开关只压不删 (运行时旁路 allow 等价断网, 但 allow_domains 原样保留).
    """
    if not isinstance(disable_all, bool):
        raise ValueError("disable_all must be boolean")
    if not isinstance(allow_domains, list) or not isinstance(deny_domains, list):
        raise ValueError("allow_domains and deny_domains must be lists")
    net = {
        "disable_all": disable_all,
        "allow_domains": [str(d) for d in allow_domains if str(d).strip()],
        "deny_domains": [str(d) for d in deny_domains if str(d).strip()],
    }
    data = _load_copy()
    ov = _ensure_user_overrides(data)
    ov["network"] = net
    _save_copy(data)
    render_runtime_policy()
    return dict(net)


# ----------------------------------------------------------------------------
# render: 把 user_overrides 合并进 windows 段 (box-server 实际读的部分)
# ----------------------------------------------------------------------------

def render_runtime_policy() -> Path | None:
    """把 user_overrides 合并进 windows 段, 落地最终 policy 值. 返回副本路径.

    box-server 读副本的 windows 段; user_overrides 段只是存储, 运行时不读.

    关键: 每次 render 从干净基底 deepcopy 重建 windows 段 (而非在副本旧 windows 上累积),
    保证用户取消某配置后 windows 干净回落基底原值 (pypi/npmmirror egress, 不留渲染残留).
    """
    base_p = _base_policy_path()
    if base_p is None:
        # 无基底 (非 Windows / 未装 jiuwenbox): 不渲染, ensure_running 自行回落默认 policy.
        return None
    try:
        base = yaml.safe_load(base_p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("读基底 %s 失败, 跳过 render: %s", base_p, exc)
        return None
    if not isinstance(base, dict):
        base = {}
    data = _load_copy()
    ov = _ensure_user_overrides(data)
    # 用干净基底 windows 重新构建 (覆盖副本里上次 render 累积的 windows), 保证回落干净.
    data["windows"] = copy.deepcopy(base.get("windows") or {})
    win = data["windows"]
    fs = win.setdefault("filesystem", {})
    net_block = win.setdefault("network", {})
    egress = net_block.setdefault("egress", {})
    files = ov.get("files") or {}
    network = ov.get("network") or {}

    # --- 文件白名单 → 合并 allow_read + allow_write (保留基底必需集, 去重) ---
    for key in ("allow_read", "allow_write"):
        existing = list(fs.get(key) or [])
        for p in (files.get("allow") or []):
            if p and p not in existing:
                existing.append(p)
        fs[key] = existing
    # --- 文件黑名单 → 合并 deny_read + deny_write (Deny 优先) ---
    for key in ("deny_read", "deny_write"):
        existing = list(fs.get(key) or [])
        for p in (files.get("deny") or []):
            if p and p not in existing:
                existing.append(p)
        fs[key] = existing

    # --- 网络 ---
    if network.get("disable_all"):
        # 总开关只压不删: default=deny + 运行时旁路 allow (不把 allow_domains 写进
        # egress.allowed_domains → EgressFilter 无 allow 规则 → 按 default=deny 全拒, 等价断网).
        # user_overrides.network.allow_domains 仍原样存在副本里, 不删; 关掉总开关 → 重新渲染
        # → 走 else 分支把 allow_domains 写回 egress → 恢复生效.
        egress["default"] = "deny"
        egress.pop("allowed_domains", None)
        egress["blocked_domains"] = list(network.get("deny_domains") or [])
    else:
        allow = network.get("allow_domains") or []
        deny = network.get("deny_domains") or []
        if not allow and not deny:
            # 都空 → windows 已是干净基底, egress 自动是基底原值 (pypi/npmmirror), 装包正常.
            pass
        else:
            egress["default"] = "deny"  # 基底本就是 deny
            egress["allowed_domains"] = list(allow)  # 直接写入对应字段
            egress["blocked_domains"] = list(deny)  # 黑名单优先

    _save_copy(data)
    return _runtime_copy_path()


def fingerprint_runtime_policy() -> str | None:
    """副本内容指纹 (sha256), 供 JiuwenBoxRunner 判断是否需重 spawn.

    副本不存在返回 None. 用于网络配置变更后, runner 检测 path 不变但内容变 → 重 spawn.
    """
    p = _runtime_copy_path()
    if not p.is_file():
        return None
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as exc:
        logger.debug("计算副本指纹失败: %s", exc)
        return None


__all__ = [
    "render_runtime_policy",
    "fingerprint_runtime_policy",
    "get_sandbox_files_config",
    "set_sandbox_files_config",
    "get_sandbox_network_config",
    "set_sandbox_network_config",
]
