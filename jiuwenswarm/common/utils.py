# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Path management for JiuWenSwarm.

根目录见 ``JIUWENSWARM_DATA_DIR``（默认 ``~/.jiuwenswarm``；可由环境变量 ``JIUWENSWARM_DATA_DIR`` 指定绝对路径）。

Runtime layout:
- <root>/config/config.yaml
- <root>/config/.env
- <root>/agent/home
- <root>/agent/workspace（DeepAgent 标准工作空间）
  - memory/
  - skills/
  - todo/
  - messages/
  - agents/
  - AGENT.md
  - IDENTITY.md
  - SOUL.md
  - HEARTBEAT.md
  - USER.md
- <root>/agent/sessions
- <root>/agent/workspace/agent-data.json
- <root>/agent/.checkpoint
- <root>/agent/.logs（gateway.log / channel.log / agent_server.log / full.log）

内置模板位于包内 ``jiuwenswarm/resources/``（含 ``agent/`` 下各技能模板以及 ``skills_state.json``）。
"""

import asyncio
import copy
import ctypes
import hashlib
import json
import os
import re
import sys
import datetime
import shutil
import socket
import time
from collections import OrderedDict
from collections.abc import Hashable
from pathlib import Path
from dataclasses import dataclass, replace
from typing import Any, Literal, Optional
import logging
import queue as _queue
from logging.handlers import BaseRotatingHandler, QueueHandler, QueueListener
from collections import OrderedDict
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# 尝试导入 pythonjsonlogger（用于 JSON 格式化输出，缺失时优雅降级为文本 Formatter）
try:
    from pythonjsonlogger import jsonlogger
except ImportError:
    jsonlogger = None

# read_file 工具：False 时不把图片字节注入主模型对话，改走 image_reading / VQA 等视觉工具。
# 用于 create_deep_agent(enable_read_image_multimodal=...) 与 MultimodalImageRail(enable_image_multimodal=...)。
DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL: bool = False

_LOG_FILE_MAX_BYTES = 20 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 20


@dataclass
class CopyDiffResult:
    """Result of copy operation with diff tracking."""
    added_dirs: list[str]
    added_files: list[str]
    overwritten_files: list[str]


class TrackCopyDiff:
    """上下文管理器：自动追踪拷贝前后差异。

    支持文件和目录两种模式：
        # 目录模式（默认）
        with TrackCopyDiff(dest=dst_dir) as diff:
            shutil.copytree(src_dir, dst_dir)

        # 文件模式
        with TrackCopyDiff(dest=dst_file, is_file=True) as diff:
            shutil.copy2(src_file, dst_file)

        # 累积多次结果
        cumulative_diff = CopyDiffResult([], [], [])
        with TrackCopyDiff(dest=dst_dir1, cumulative=cumulative_diff):
            shutil.copytree(src1, dst_dir1)
        with TrackCopyDiff(dest=dst_dir2, cumulative=cumulative_diff):
            shutil.copytree(src2, dst_dir2)

        # overwrite 模式不统计差异
        with TrackCopyDiff(dest=dst_dir, overwrite=True) as diff:
            shutil.copytree(src_dir, dst_dir)

    追踪内容:
    - added_dirs: 新增文件所在的父目录列表（目录模式）
    - added_files: 新增的文件列表
    - overwritten_files: 被覆盖的文件列表（时间戳变化）
    """

    def __init__(
        self,
        dest: Path,
        cumulative: Optional[CopyDiffResult] = None,
        is_file: bool = False,
        overwrite: bool = False,
    ):
        self.dest = dest
        self.overwrite = overwrite
        self.before_files: dict[str, float] = {}
        self._is_file_mode = is_file
        self.diff = cumulative if cumulative else CopyDiffResult([], [], [])

    def __enter__(self) -> CopyDiffResult:
        # overwrite 模式不追踪
        if self.overwrite:
            return self.diff

        # 文件模式：只记录目标文件状态
        if self._is_file_mode:
            if self.dest.exists() and self.dest.is_file():
                self.before_files[""] = self.dest.stat().st_mtime
            return self.diff

        # 目录模式：记录目标目录文件状态
        if self.dest.exists():
            for f in self.dest.rglob("*"):
                if f.is_file():
                    full_path = str(f)
                    self.before_files[full_path] = f.stat().st_mtime
        return self.diff

    def __exit__(self, exc_type, exc_val, exc_tb):
        # overwrite 模式不追踪差异
        if self.overwrite:
            return False

        # 文件模式：统计单个文件变化
        if self._is_file_mode:
            if self.dest.exists() and self.dest.is_file():
                mtime = self.dest.stat().st_mtime
                if "" not in self.before_files:
                    # 新增文件
                    self.diff.added_files.append(str(self.dest))
                elif mtime != self.before_files[""]:
                    # 文件被覆盖
                    self.diff.overwritten_files.append(str(self.dest))
            return False

        # 目录模式：对比差异
        added_files: list[str] = []
        overwritten_files: list[str] = []
        added_dirs: set[str] = set()

        if self.dest.exists():
            for f in self.dest.rglob("*"):
                if f.is_file():
                    full_path = str(f)
                    mtime = f.stat().st_mtime
                    if full_path not in self.before_files:
                        # 新增文件
                        added_files.append(full_path)
                        # 父目录也使用全路径（与其他字段保持一致）
                        added_dirs.add(str(f.parent))
                    elif mtime != self.before_files[full_path]:
                        # 文件被覆盖（时间戳变化）
                        overwritten_files.append(full_path)

        # 累积到现有结果（而非替换）
        self.diff.added_dirs = sorted(set(self.diff.added_dirs) | added_dirs)
        self.diff.added_files = sorted(set(self.diff.added_files) | set(added_files))
        self.diff.overwritten_files = sorted(
            set(self.diff.overwritten_files) | set(overwritten_files)
        )
        return False  # 不抑制异常


@dataclass
class LoggingLevels:
    """Container for logging level configuration."""
    logger: int
    console: int
    gateway: int
    channel: int
    agent_server: int
    full: int


class SafeRotatingFileHandler(BaseRotatingHandler):
    """Safe rotating file handler"""

    def __init__(self, filename, maxBytes=0, backupCount=0, encoding=None,
                 delay=False, errors=None):
        """Initialize the handler."""
        super().__init__(filename, 'a', encoding, errors)
        self.max_bytes = maxBytes
        self.backup_count = backupCount
        self._current_filename = filename

        if delay:
            self.stream = None

    def shouldRollover(self, record):
        """
        Determine if rollover should occur.

        Returns True if the log file size exceeds maxBytes.
        """
        if self.stream is None:
            return False
        if self.max_bytes > 0:
            msg = "%s\n" % self.format(record)
            self.stream.seek(0, 2)  # Seek to end of file
            if self.stream.tell() + len(msg) >= self.max_bytes:
                return True
        return False

    def doRollover(self):
        """
        Perform log rotation to keep app.log as the active log file.
        """
        base_path = Path(self.baseFilename)

        timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_filename = base_path.parent / f"{base_path.stem}_{timestamp}{base_path.suffix}"

        try:
            if base_path.exists():
                shutil.copy2(base_path, backup_filename)
        except OSError as e:
            print(f"WARNING: Could not copy log file to backup: {e}", file=sys.stderr)

        # Clean up old backup files
        self._cleanup_old_backups()

        try:
            if self.stream:
                self.stream.seek(0)  # Seek to beginning
                self.stream.truncate(0)  # Truncate to 0 bytes
        except OSError as e:
            print(f"WARNING: Could not truncate log file: {e}", file=sys.stderr)

    def _cleanup_old_backups(self):
        """
        Remove old backup files if they exceed backupCount.

        Backup files are sorted by modification time (oldest first).
        """
        if self.backup_count <= 0:
            return

        try:
            base_path = Path(self.baseFilename)
            log_dir = base_path.parent

            backup_files = []
            for f in log_dir.glob(f"{base_path.stem}_*{base_path.suffix}"):
                if f.is_file() and f != base_path:
                    backup_files.append(f)

            # Sort by modification time (oldest first)
            backup_files.sort(key=lambda x: x.stat().st_mtime)

            # Remove excess files
            files_to_delete = len(backup_files) - self.backup_count
            if files_to_delete > 0:
                for f in backup_files[:files_to_delete]:
                    try:
                        f.unlink()
                    except OSError as e:
                        print(f"WARNING: Could not delete old log file {f}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Error during backup cleanup: {e}", file=sys.stderr)


def _parse_log_level(name: str, default: int = logging.INFO) -> int:
    """Parse level name to logging module constant."""
    if not name or not isinstance(name, str):
        return default
    return getattr(logging, name.strip().upper(), default)


def _log_component_from_logger_name(name: str) -> str:
    """按 ``logging.getLogger(__name__)`` 的 logger 名划分 gateway / channel / agent_server / permissions（含 security）。"""
    if name.startswith("jiuwenswarm.channels"):
        return "channel"
    if name.startswith("jiuwenswarm.agents.harness.common.rails.permissions"):
        return "permissions"
    if name.startswith("openjiuwen.harness.security") or name.startswith("openjiuwen.harness.rails.security"):
        return "permissions"
    if name.startswith("jiuwenswarm.agents") or name.startswith("jiuwenswarm.server"):
        return "agent_server"
    return "gateway"


class _ComponentNameFilter(logging.Filter):
    """仅放行指定组件（由 logger 名判定）的日志记录。"""

    def __init__(self, component: str) -> None:
        super().__init__()
        self.component = component

    def filter(self, record: logging.LogRecord) -> bool:
        return _log_component_from_logger_name(record.name) == self.component


class _CompositeFilter(logging.Filter):
    """组合多个过滤器，任一通过即放行"""

    def __init__(self, filters: list[logging.Filter]) -> None:
        super().__init__()
        self.filters = filters

    def filter(self, record: logging.LogRecord) -> bool:
        return any(f.filter(record) for f in self.filters)


# ---------------------------------------------------------------------------
# Sparse-override config merge utilities
# ---------------------------------------------------------------------------

def merge_template_with_override(
    template: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """模板默认值 + 用户 override；用户键覆盖模板。

    - override 中独有的顶层键**不会保留**，会被清理（与 migrate_config_from_template
      的 Remove 规则一致）。
    - 深度递归（上限 4 层）。
    """
    return _deep_merge(template, override, depth=0)


def _deep_merge(
    template: dict[str, Any],
    override: dict[str, Any],
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively merge template with user override, cleaning deprecated fields.

    Rules:
    - Add: fields only in template → use template value
    - Keep: override values for fields that exist in template (preserve user settings)
    - Remove: fields only in override (deprecated config, cleanup)
    - Max recursion depth: 4
    """
    if depth >= 4:
        return override

    result: dict[str, Any] = {}

    for key, tmpl_val in template.items():
        if key not in override:
            result[key] = copy.deepcopy(tmpl_val)
        elif isinstance(tmpl_val, dict) and isinstance(override.get(key), dict):
            result[key] = _deep_merge(tmpl_val, override[key], depth + 1)
        else:
            result[key] = override[key]

    return result


def fill_template_defaults(
    target: dict[str, Any],
    template: dict[str, Any],
    depth: int = 0,
) -> dict[str, Any]:
    """模板补缺型合并：以 target 为主体，模板仅补全 target 缺失的键。

    与 merge_template_with_override 不同：
    - target 独有的键（模板中没有）**原样保留**，不做清理；
    - target 显式设置的值不被模板覆盖；
    - 双方均为 dict 的键递归补缺（上限 4 层，与 merge_template_with_override 一致）。
    适用于外部传入的稀疏配置（如企业同步 spec.config）：补齐模板默认值
    （如 react.subagents）且不丢弃外部配置的任何键。
    """
    if depth >= 4:
        return target
    result = copy.deepcopy(target)
    for key, tmpl_val in template.items():
        if key not in result:
            result[key] = copy.deepcopy(tmpl_val)
        elif isinstance(result[key], dict) and isinstance(tmpl_val, dict):
            result[key] = fill_template_defaults(result[key], tmpl_val, depth + 1)
    return result


def load_yaml_dict(path: Path) -> dict[str, Any]:
    """用 yaml.safe_load 读取 YAML 文件为 dict；不存在或无效时返回空 dict。"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def resolve_shipped_template_config_path() -> Path:
    """包内 shipped 模板：jiuwenswarm/resources/config.yaml。"""
    return Path(__file__).resolve().parent.parent / "resources" / "config.yaml"


def _read_template_version_value(template_path: Path) -> Any:
    """读取模板 config.yaml 顶层的 ``version``（缺省 ``1.0``）。"""
    if not template_path.exists():
        return 1.0
    try:
        rt = YAML()
        rt.preserve_quotes = True
        with open(template_path, "r", encoding="utf-8") as f:
            tpl = rt.load(f)
        if isinstance(tpl, dict) and tpl.get("version") is not None:
            return tpl["version"]
    except OSError as e:
        logger.warning("Failed to read template version from %s: %s", template_path, e)
    return 1.0


def _write_initial_user_override_config(template_src: Path, dest: Path) -> None:
    """首次初始化用户目录时写入稀疏 override（仅 ``version``，取自模板）。"""
    version_val = _read_template_version_value(template_src)
    rt = YAML()
    rt.preserve_quotes = True
    rt.default_flow_style = False
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.width = 4096
    data = CommentedMap()
    data["version"] = version_val
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        rt.dump(data, f)


def migrate_legacy_user_config_if_needed() -> None:
    """无 ``version`` 的旧版完整 config 迁移为稀疏 override：仅保留 permissions + version。

    已有 ``version`` 的用户文件不修改。
    """
    cfg_path = get_config_file()
    if not cfg_path.exists():
        return
    try:
        rt = YAML()
        rt.preserve_quotes = True
        rt.default_flow_style = False
        rt.indent(mapping=2, sequence=4, offset=2)
        rt.width = 4096
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = rt.load(f)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return
        ver = data.get("version")
        if ver is not None and str(ver).strip() != "":
            return

        package_root = _find_package_root()
        tpl_file = (package_root / "resources" / "config.yaml") if package_root else None
        version_val = _read_template_version_value(tpl_file) if tpl_file else 1.0

        new_data = CommentedMap()
        new_data["version"] = version_val
        if "permissions" in data:
            new_data["permissions"] = data["permissions"]

        with open(cfg_path, "w", encoding="utf-8") as f:
            rt.dump(new_data, f)
        logger.info(
            "[jiuwenswarm] migrated legacy config.yaml to sparse override (schema version %s)",
            version_val,
        )
    except OSError as e:
        logger.warning("[jiuwenswarm] legacy config migration failed: %s", e)


def _load_logging_config_from_yaml() -> dict[str, Any]:
    """读取合并后的 logging 段（包内模板 + 用户 override）。"""
    try:
        template = load_yaml_dict(resolve_shipped_template_config_path())
        override = load_yaml_dict(get_config_file())
        merged = merge_template_with_override(template, override)
        raw = merged.get("logging")
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        logger.warning("load logging config failed, caused by=%s", e)
    return {}


def _resolve_logging_levels(
    log_level_override: Optional[str],
) -> LoggingLevels:
    """返回日志级别配置。"""
    cfg = _load_logging_config_from_yaml()
    base = _parse_log_level(str(cfg.get("level", "INFO")))

    def _coerce(key: str) -> int:
        if key in cfg and cfg[key] is not None:
            return _parse_log_level(str(cfg[key]), base)
        return base

    console = _coerce("console_level")
    env_console = os.getenv("LOG_LEVEL")
    if env_console:
        console = _parse_log_level(env_console, console)

    gateway = _coerce("gateway")
    channel = _coerce("channel")
    agent_server = _coerce("agent_server")
    full = _coerce("full")

    if log_level_override is not None:
        v = _parse_log_level(log_level_override)
        console = gateway = channel = agent_server = full = v
        logger_level = v
    else:
        logger_level = min(gateway, channel, agent_server, full)

    return LoggingLevels(logger_level, console, gateway, channel, agent_server, full)


_user_home: Path | None = None
_workspace_base_dir: Path | None = None


def get_user_home() -> Path:
    """Get the current user home directory.

    Priority:
    1. Cached value (if already set via set_user_home or previous call)
    2. JIUWENSWARM_HOME environment variable
    3. System default Path.home()
    """
    global _user_home
    if _user_home is not None:
        return _user_home
    env_home = os.getenv("JIUWENSWARM_HOME")
    if env_home:
        _user_home = Path(env_home)
        return _user_home
    _user_home = Path.home()
    return _user_home


def set_user_home(path: Path, initialized: bool = False) -> None:
    """Set a custom user home directory.

    After calling this function, all path getters will return paths based on the new home directory.

    Args:
        path: The new user home directory path.
        initialized: If True, skip cache reset (use when paths are already initialized elsewhere).
    """
    global _user_home, _initialized, _config_dir, _workspace_dir, _root_dir
    _user_home = Path(path)
    if initialized:
        return
    _initialized = False
    _config_dir = None
    _workspace_dir = None
    _root_dir = None


def get_user_workspace_dir() -> Path:
    """Get the user workspace directory path (~/.jiuwenswarm or custom path).

    Priority:
    1. Cached value (if already set via set_user_workspace_dir or previous call)
    2. ``JIUWENSWARM_DATA_DIR`` (or relay ``JIUWENCLAW_DATA_DIR``) for isolation
    3. get_user_home() / ".jiuwenswarm" (default instance)

    Also performs one-time migration from ~/.jiuwenclaw/ to ~/.jiuwenswarm/ if needed.
    """
    global _workspace_base_dir
    if _workspace_base_dir is not None:
        return _workspace_base_dir
    env_workspace = (
        os.getenv("JIUWENSWARM_DATA_DIR") or os.getenv("JIUWENCLAW_DATA_DIR") or ""
    ).strip()
    if env_workspace:
        _workspace_base_dir = Path(env_workspace)
        return _workspace_base_dir

    # One-time migration from .jiuwenclaw to .jiuwenswarm
    _migrate_from_jiuwenclaw_root()

    _workspace_base_dir = get_user_home() / ".jiuwenswarm"
    return _workspace_base_dir




# Cache for resolved paths
_config_dir: Path | None = None
_workspace_dir: Path | None = None
_root_dir: Path | None = None
_is_package: bool | None = None
_initialized: bool = False


def _detect_installation_mode() -> bool:
    """Detect if running from a package installation (whl) or PyInstaller bundle."""
    global _is_package
    if _is_package is not None:
        return _is_package

    # PyInstaller 打包后使用用户工作区路径
    if getattr(sys, "frozen", False):
        _is_package = True
        return True

    # Check if module is in site-packages
    module_file = Path(__file__).resolve()

    # Check if module file is in any site-packages directory
    for path in sys.path:
        site_packages = Path(path)
        if "site-packages" in str(site_packages) and site_packages in module_file.parents:
            _is_package = True
            return True

    _is_package = False
    return False


def _find_source_root() -> Path:
    """Find the repository root in development mode (contains jiuwenswarm/ package)."""
    current = Path(__file__).resolve().parent.parent
    jw_pkg = current / "jiuwenswarm"
    if (jw_pkg / "resources" / "agent").exists():
        return current
    parent = current.parent
    jw_pkg2 = parent / "jiuwenswarm"
    if (jw_pkg2 / "resources" / "agent").exists():
        return parent
    return current


def _find_package_root() -> Path | None:
    """Best-effort detection of the jiuwenswarm package root.

    In package mode (whl), __file__ is at site-packages/jiuwenswarm/common/utils.py,
    so parent.parent is site-packages/jiuwenswarm/.
    In editable / source mode, __file__ is at <project>/jiuwenswarm/common/utils.py,
    so parent.parent is <project>/jiuwenswarm/.
    """
    current = Path(__file__).resolve().parent.parent
    jw_pkg = current / "jiuwenswarm"
    if (jw_pkg / "resources").exists():
        return current
    return current


def _resolve_preferred_language(
    config_yaml_dest: Path, explicit: Optional[str]
) -> str:
    """确定初始化使用的语言：显式参数优先，否则读 override + 模板，默认 zh。"""
    if explicit is not None:
        lang = str(explicit).strip().lower()
        return lang if lang in ("zh", "en") else "zh"
    # 稀疏 override 模式：先读 override，再读模板
    for cfg_path in (config_yaml_dest, resolve_shipped_template_config_path()):
        if cfg_path.exists():
            try:
                rt = YAML()
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = rt.load(f) or {}
                lang = str(data.get("preferred_language") or "").strip().lower()
                if lang in ("zh", "en"):
                    return lang
            except Exception as e:
                logger.error(f"Failed to load config.yaml: {e}")
    return "zh"


def _is_interactive() -> bool:
    """Check if stdin is connected to a terminal (interactive mode)."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def prompt_preferred_language() -> Optional[Literal["zh", "en"]]:
    """交互询问语言偏好。仅接受明确选项；空输入、不在列表或取消用语 → 返回 None（调用方应终止 init）。
    非交互环境（stdin非TTY）默认返回 'zh'。
    """
    if not _is_interactive():
        print("[jiuwenswarm-init] Non-interactive mode: using default language 'zh'")
        return "zh"
    print()
    print("[jiuwenswarm-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenswarm-init]  请选择默认语言 / Choose your default language")
    print("[jiuwenswarm-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenswarm-init]   [1] 中文（简体）")
    print("[jiuwenswarm-init]       → config: preferred_language: zh")
    print("[jiuwenswarm-init]   ────────────────────────────────────────────")
    print("[jiuwenswarm-init]   [2] English")
    print("[jiuwenswarm-init]       → config: preferred_language: en")
    print("[jiuwenswarm-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenswarm-init]  须明确选择：1 / 2 / zh / en（无默认语言）")
    print("[jiuwenswarm-init]  取消：no / n / q / cancel / 取消")
    print("[jiuwenswarm-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    raw = input(
        "[jiuwenswarm-init] 请输入选项 (1, 2, zh, en) 或 no 取消: "
    ).strip().lower()
    if raw in ("no", "n", "q", "quit", "cancel", "取消"):
        return None
    if raw in ("1", "zh", "中文", "chinese"):
        return "zh"
    if raw in ("2", "en", "english", "e", "英文"):
        return "en"
    print("[jiuwenswarm-init] 无效选项；未选择有效语言，初始化已取消（与拒绝 yes/no 相同）。")
    return None


def _get_builtin_skill_names() -> set[str]:
    """Get the set of built-in skill names from package resources."""
    return get_builtin_skill_names()


def get_builtin_skill_names() -> set[str]:
    """Return official package builtin skill directory names."""
    builtin_skills_dir = get_builtin_skills_dir()
    if not builtin_skills_dir.exists():
        return set()
    return {item.name for item in builtin_skills_dir.iterdir() if item.is_dir()}


def is_builtin_skill(skill_name: str) -> bool:
    """Whether *skill_name* is an official package builtin skill."""
    normalized = (skill_name or "").strip().lower()
    if not normalized:
        return False
    return normalized in {name.lower() for name in get_builtin_skill_names()}


def _update_skills_state_for_builtin(
    user_skills_dir: Path,
    skill_names: list[str],
) -> None:
    """更新 skills_state.json，记录默认安装的内置技能.

    Args:
        user_skills_dir: 用户技能目录路径
        skill_names: 已安装的技能名称列表
    """
    state_file = user_skills_dir / "skills_state.json"

    # 加载现有状态
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取技能状态文件失败，将创建新文件: {e}")
            state = {"marketplaces": [], "installed_plugins": [], "local_skills": []}
    else:
        state = {"marketplaces": [], "installed_plugins": [], "local_skills": []}

    # 确保必要的字段存在
    if "installed_plugins" not in state:
        state["installed_plugins"] = []
    if not isinstance(state["installed_plugins"], list):
        state["installed_plugins"] = []

    # 获取已记录的技能名称
    existing_names = {
        item.get("name") for item in state["installed_plugins"]
        if isinstance(item, dict) and item.get("name")
    }

    # 添加新安装的技能记录
    installed_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    for skill_name in skill_names:
        if skill_name not in existing_names:
            state["installed_plugins"].append({
                "name": skill_name,
                "marketplace": "builtin",
                "version": "",
                "commit": "",
                "source": "builtin",
                "installed_at": installed_at,
            })
            logger.info(f"已将默认技能记录到状态文件: {skill_name}")

    # 保存状态文件
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"技能状态文件已更新: {state_file}")
    except Exception as e:
        logger.error(f"保存技能状态文件失败: {e}")


def _install_default_builtin_skills(
    builtin_dir: Path,
    user_skills_dir: Path,
    overwrite: bool,
    cumulative_diff: CopyDiffResult,
) -> None:
    """安装默认的内置技能到用户技能目录.

    默认安装的技能：
    - skill-creator: 技能创建助手
    - swarmskill-creator: Swarm技能创建助手

    Args:
        builtin_dir: 内置技能目录路径
        user_skills_dir: 用户技能目录路径
        overwrite: 是否覆盖已存在的技能
        cumulative_diff: 累积的文件变更追踪结果
    """
    # 定义默认安装的技能列表
    default_skills = ["skill-creator", "swarmskill-creator"]

    if not builtin_dir.exists() or not builtin_dir.is_dir():
        logger.warning(f"内置技能目录不存在，跳过默认技能安装: {builtin_dir}")
        return

    user_skills_dir.mkdir(parents=True, exist_ok=True)

    # 记录成功安装的技能，用于后续更新状态文件
    installed_skills = []

    for skill_name in default_skills:
        builtin_skill_path = builtin_dir / skill_name
        user_skill_path = user_skills_dir / skill_name

        # 检查内置技能是否存在
        if not builtin_skill_path.exists() or not builtin_skill_path.is_dir():
            logger.warning(f"内置技能不存在，跳过安装: {skill_name}")
            continue

        # 如果用户目录已存在该技能且不是覆盖模式，则跳过
        if user_skill_path.exists() and not overwrite:
            logger.info(f"技能已存在，跳过安装: {skill_name}")
            continue

        # 复制技能到用户目录
        try:
            with TrackCopyDiff(
                dest=user_skill_path,
                cumulative=cumulative_diff,
                overwrite=overwrite,
            ):
                if user_skill_path.exists() and overwrite:
                    shutil.rmtree(user_skill_path)
                shutil.copytree(builtin_skill_path, user_skill_path)
            logger.info(f"已安装默认技能: {skill_name}")
            installed_skills.append(skill_name)
        except Exception as e:
            logger.error(f"安装默认技能失败 {skill_name}: {e}")

    # 更新 skills_state.json，记录已安装的技能
    if installed_skills:
        _update_skills_state_for_builtin(user_skills_dir, installed_skills)


def _migrate_from_jiuwenclaw_root() -> bool:
    """Migrate from legacy ~/.jiuwenclaw/ to ~/.jiuwenswarm/.

    This is a one-time migration that moves the entire root directory.
    Called at startup before any workspace operations.

    Returns:
        True if migration was performed, False otherwise.
    """
    user_home = get_user_home()
    old_root = user_home / ".jiuwenclaw"
    new_root = user_home / ".jiuwenswarm"

    # No migration needed if old doesn't exist or new already exists
    if not old_root.exists():
        return False
    if new_root.exists():
        # New workspace exists, don't migrate
        print(f"[migration] Both .jiuwenclaw and .jiuwenswarm exist, skipping migration")
        return False

    print(f"[migration] Migrating from {old_root} to {new_root}")

    try:
        shutil.move(str(old_root), str(new_root))
        print(f"[migration] Migration completed: {old_root} -> {new_root}")
        return True
    except OSError as e:
        print(f"[migration] ERROR: Failed to migrate from .jiuwenclaw to .jiuwenswarm: {e}")
        return False


def _migrate_jiuwenclaw_workspace_to_workspace(workspace_dir: Path) -> None:
    """Migrate from legacy jiuwenclaw_workspace directory name to workspace.

    Migration:
    - Old: ~/.jiuwenswarm/agent/jiuwenclaw_workspace/
    - New: ~/.jiuwenswarm/agent/workspace/

    Args:
        workspace_dir: Path to workspace root (~/.jiuwenswarm).
    """
    old_workspace = workspace_dir / "agent" / "jiuwenclaw_workspace"
    new_workspace = workspace_dir / "agent" / "workspace"

    if not old_workspace.exists():
        return
    if new_workspace.exists():
        # Both exist - merge carefully
        print(f"[migration] Both jiuwenclaw_workspace and workspace exist, merging...")
        for item in old_workspace.iterdir():
            dest = new_workspace / item.name
            if item.is_dir():
                if dest.exists():
                    # Merge directories
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copytree(item, dest)
            else:
                if not dest.exists():
                    shutil.copy2(item, dest)
        # Remove old after successful merge
        shutil.rmtree(old_workspace)
        print(f"[migration] Merged and removed: {old_workspace}")
    else:
        # Simple rename
        shutil.move(str(old_workspace), str(new_workspace))
        print(f"[migration] Renamed: {old_workspace} -> {new_workspace}")


def _migrate_legacy_workspace(
    workspace_dir: Path,
    preferred_language: Optional[str] = None,
) -> None:
    """Migrate from legacy layout to new DeepAgent workspace layout.

    This handles VERY old layouts where skills, memory, and home were
    separate directories outside of the workspace.

    Migration:
    - Old: ~/.jiuwenswarm/agent/home/ (PRINCIPLE.md, TONE.md, HEARTBEAT.md)
    - Old: ~/.jiuwenswarm/agent/skills/
    - Old: ~/.jiuwenswarm/agent/memory/

    - New: ~/.jiuwenswarm/agent/workspace/ (DeepAgent standard)

    Mapping:
    - agent/home/HEARTBEAT.md -> agent/workspace/HEARTBEAT.md
    - agent/skills/ -> agent/workspace/skills/
    - agent/memory/ -> agent/workspace/memory/

    Note: jiuwenclaw_workspace -> workspace renaming is handled separately by
    _migrate_jiuwenclaw_workspace_to_workspace.

    Args:
        workspace_dir: Path to workspace root (~/.jiuwenswarm).
        preferred_language: Preferred language for config (zh/en).
    """
    logger.info(f"Migrating from legacy layout: {workspace_dir}")

    old_home = workspace_dir / "agent" / "home"
    old_skills = workspace_dir / "agent" / "skills"
    old_memory = workspace_dir / "agent" / "memory"

    new_workspace = workspace_dir / "agent" / "workspace"
    new_workspace.mkdir(parents=True, exist_ok=True)

    # 1. Migrate old home files
    if old_home.exists():
        # HEARTBEAT.md -> HEARTBEAT.md (if not exists in new location)
        old_heartbeat = old_home / "HEARTBEAT.md"
        new_heartbeat = new_workspace / "HEARTBEAT.md"
        if old_heartbeat.exists() and not new_heartbeat.exists():
            shutil.copy2(old_heartbeat, new_heartbeat)
            logger.info("Migrated HEARTBEAT.md from home")

        # Merge PRINCIPLE.md and TONE.md into SOUL.md
        old_principle = old_home / "PRINCIPLE.md"
        old_tone = old_home / "TONE.md"
        new_soul = new_workspace / "SOUL.md"
        if not new_soul.exists() and (old_principle.exists() or old_tone.exists()):
            soul_content = ["# Agent Soul\n\n"]
            if old_principle.exists():
                principle_text = old_principle.read_text(encoding="utf-8")
                soul_content.append("## Principles\n\n")
                soul_content.append(principle_text)
                soul_content.append("\n\n")
            if old_tone.exists():
                tone_text = old_tone.read_text(encoding="utf-8")
                soul_content.append("## Tone\n\n")
                soul_content.append(tone_text)
                soul_content.append("\n\n")
            new_soul.write_text("".join(soul_content), encoding="utf-8")
            logger.info("Merged PRINCIPLE.md and TONE.md into SOUL.md")

    new_skills = new_workspace / "skills"
    if old_skills.exists():
        if new_skills.exists():
            shutil.rmtree(new_skills)
        shutil.copytree(old_skills, new_skills)
        logger.info(f"Migrated skills: {old_skills} -> {new_skills}")

        builtin_skill_names = _get_builtin_skill_names()
        for skill_dir in new_skills.iterdir():
            if skill_dir.is_dir() and (skill_dir.name in builtin_skill_names \
                 or skill_dir.name in ["daily-report", "skill-creation"]):
                shutil.rmtree(skill_dir)

    # 4. Migrate memory
    new_memory = new_workspace / "memory"
    new_memory.mkdir(parents=True, exist_ok=True)

    if old_memory.exists():
        # 4.1 Migrate USER.md to workspace root (not in memory/)
        old_user = old_memory / "USER.md"
        new_user = new_workspace / "USER.md"
        if old_user.exists() and not new_user.exists():
            shutil.copy2(old_user, new_user)
            logger.info("Migrated USER.md from memory/ to workspace root")

        # 4.2 Create daily_memory directory
        daily_memory = new_memory / "daily_memory"
        daily_memory.mkdir(parents=True, exist_ok=True)

        # 4.3 Merge memory files (skip if already exists)
        # Date pattern: YYYY-MM-DD.md (e.g., 2026-04-14.md)
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

        for item in old_memory.iterdir():
            if item.name == "USER.md":
                continue  # Already handled above
            if item.name == "MEMORY.md":
                dest = new_memory / "MEMORY.md"
                if not dest.exists():
                    shutil.copy2(item, dest)
                    logger.info("Migrated MEMORY.md")
            elif item.is_file():
                # Date-based memory files (YYYY-MM-DD.md) -> daily_memory/
                # Other files -> new_memory/ root
                dest = daily_memory / item.name if date_pattern.match(item.name) else new_memory / item.name
                if not dest.exists():
                    shutil.copy2(item, dest)
                    logger.info(f"Migrated memory file: {item.name}")
            elif item.is_dir():
                # Other directories (e.g., specific memory categories)
                dest = new_memory / item.name
                if not dest.exists():
                    shutil.copytree(item, dest)
                    logger.info(f"Migrated memory directory: {item.name}")

        logger.info(f"Migrated memory: {old_memory} -> {new_memory}")

    # 5. Migrate cron_jobs.json from old_home to gateway
    # This ensures cron jobs are not lost during migration
    old_cron_jobs = old_home / "cron_jobs.json"
    gateway_dir = workspace_dir / "gateway"
    new_cron_jobs = gateway_dir / "cron_jobs.json"
    if old_cron_jobs.exists():
        gateway_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Read old cron jobs data
            old_data = json.loads(old_cron_jobs.read_text(encoding="utf-8"))
            # Add 'expired': false to each job if not present (schema migration)
            if "jobs" in old_data and isinstance(old_data["jobs"], list):
                for job in old_data["jobs"]:
                    if isinstance(job, dict) and "expired" not in job:
                        job["expired"] = False
            if not new_cron_jobs.exists():
                # Write migrated data to new location
                new_cron_jobs.write_text(
                    json.dumps(old_data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                logger.info(f"Migrated cron_jobs.json: {old_cron_jobs} -> {new_cron_jobs}")
            else:
                # Both exist - backup old, log warning
                backup_cron = gateway_dir / f"cron_jobs.json.backup.{int(time.time())}"
                shutil.copy2(old_cron_jobs, backup_cron)
                logger.warning(
                    f"Both old and new cron_jobs.json exist. "
                    f"Kept new version, backed up old to {backup_cron}"
                )
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to migrate cron_jobs.json: {e}")

    # 6. Clean up old directories after successful migration
    try:
        if old_home.exists():
            shutil.rmtree(old_home)
            logger.info(f"Removed old home: {old_home}")
        if old_skills.exists():
            shutil.rmtree(old_skills)
            logger.info(f"Removed old skills: {old_skills}")
        if old_memory.exists():
            shutil.rmtree(old_memory)
            logger.info(f"Removed old memory: {old_memory}")
    except OSError as e:
        logger.warning(f"Failed to remove some old directories: {e}")

    logger.info(f"Migration completed: {new_workspace}")


def cleanup_team_files(workspace_dir: Path) -> None:
    """清理 Team 旧版本遗留的文件和目录.

    Legacy cleanup:
    - Old: {workspace_dir}/workspace/ (旧版本 team workspace)
    - Old: {workspace_dir}/agent/team_data/ (旧版本 team 数据库目录)
    - Old: {workspace_dir}/team.db (旧版本 team 数据库文件)
    - Old: {workspace_dir}/team.db-wal (旧版本 team WAL 文件)
    - Old: {workspace_dir}/team.db-shm (旧版本 team SHM 文件)
    - Old: {workspace_dir}/agent/team.db (旧版本 team 数据库文件)
    - Old: {workspace_dir}/agent/team.db-wal (旧版本 team WAL 文件)
    - Old: {workspace_dir}/agent/team.db-shm (旧版本 team SHM 文件)

    Args:
        workspace_dir: JiuWenSwarm 用户工作空间根目录 (~/.jiuwenswarm)
    """
    agent_dir = workspace_dir / "agent"

    # 清理 {workspace_dir}/workspace/ (旧版本 team workspace)
    legacy_workspace = workspace_dir / "workspace"
    if legacy_workspace.exists():
        try:
            shutil.rmtree(legacy_workspace)
            logger.info(f"[Cleanup] Removed legacy workspace directory: {legacy_workspace}")
        except OSError as e:
            logger.warning(f"[Cleanup] Failed to remove legacy workspace directory: {e}")

    # 清理 {workspace_dir}/agent/team_data/ (旧版本 team 数据库目录)
    legacy_team_data = agent_dir / "team_data"
    if legacy_team_data.exists():
        try:
            shutil.rmtree(legacy_team_data)
            logger.info(f"[Cleanup] Removed legacy team_data directory: {legacy_team_data}")
        except OSError as e:
            logger.warning(f"[Cleanup] Failed to remove legacy team_data directory: {e}")

    # 清理 {workspace_dir}/team.db* (旧版本 team 数据库文件)
    legacy_team_db_root = workspace_dir / "team.db"
    for suffix in ["", "-wal", "-shm"]:
        db_file = legacy_team_db_root.with_suffix(".db" + suffix)
        if db_file.exists():
            try:
                db_file.unlink()
                logger.info(f"[Cleanup] Removed legacy team database file: {db_file}")
            except OSError as e:
                logger.warning(f"[Cleanup] Failed to remove legacy team database file: {e}")

    # 清理 {workspace_dir}/agent/team.db* (旧版本 team 数据库文件)
    legacy_team_db_agent = agent_dir / "team.db"
    for suffix in ["", "-wal", "-shm"]:
        db_file = legacy_team_db_agent.with_suffix(".db" + suffix)
        if db_file.exists():
            try:
                db_file.unlink()
                logger.info(f"[Cleanup] Removed legacy team database file: {db_file}")
            except OSError as e:
                logger.warning(f"[Cleanup] Failed to remove legacy team database file: {e}")


def update_config() -> None:
    """稀疏 override 模式：迁移旧版全量 config（无 version 字段）并清理 override 中模板已删除的字段。

    - migrate_legacy_user_config_if_needed: 旧版全量 config → 稀疏 override
    - 清理 override 中模板已不存在的字段（Remove 规则）
    """
    migrate_legacy_user_config_if_needed()

    package_root = _find_package_root()
    if not package_root:
        raise RuntimeError("package root not found")

    workspace_dir = get_user_workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    resources_dir = package_root / "resources"
    config_yaml_src_candidates = [
        resources_dir / "config.yaml",
        package_root / "config" / "config.yaml",
    ]
    config_yaml_src = next((p for p in config_yaml_src_candidates if p.exists()), None)
    if not config_yaml_src:
        raise RuntimeError(
            "config.yaml template not found; tried: "
            + ", ".join(str(p) for p in config_yaml_src_candidates)
        )

    config_yaml_dest = workspace_dir / "config" / "config.yaml"
    # 稀疏 override 模式：清理 override 中模板已删除的废弃字段
    from jiuwenswarm.common.config import cleanup_override_against_template

    cleanup_override_against_template(config_yaml_src, config_yaml_dest)


def prepare_workspace(
    overwrite: bool = True,
    preferred_language: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
) -> CopyDiffResult:
    package_root = _find_package_root()
    if not package_root:
        raise RuntimeError("package root not found")

    if workspace_dir is None:
        workspace_dir = get_user_workspace_dir()
    else:
        workspace_dir = Path(workspace_dir)

    # 初始化累积结果（用于追踪所有复制操作）
    cumulative_diff = CopyDiffResult([], [], [])
    workspace_dir.mkdir(parents=True, exist_ok=True)
    migrate_legacy_user_config_if_needed()

    # Migrate from legacy jiuwenclaw_workspace directory name to workspace
    _migrate_jiuwenclaw_workspace_to_workspace(workspace_dir)

    # Check for legacy workspace migration or cleanup (pre-DeepAgent layout)
    # These are even older layouts: agent/workspace, agent/home, agent/skills, agent/memory
    old_workspace = workspace_dir / "agent" / "workspace"
    old_home = workspace_dir / "agent" / "home"
    old_skills = workspace_dir / "agent" / "skills"
    old_memory = workspace_dir / "agent" / "memory"

    # Check for legacy directory migration (for start command, overwrite=False)
    # Migration triggers when ANY legacy directory exists, not just old_workspace
    legacy_dirs_exist = (
        old_home.exists() or old_skills.exists() or old_memory.exists()
    )

    if legacy_dirs_exist and not overwrite:
        _migrate_legacy_workspace(workspace_dir, preferred_language)
    # If overwrite (init command), clean up old legacy directories first
    elif overwrite:
        try:
            if old_home.exists():
                shutil.rmtree(old_home)
                logger.info(f"Removed old home: {old_home}")
            if old_skills.exists():
                shutil.rmtree(old_skills)
                logger.info(f"Removed old skills: {old_skills}")
            if old_memory.exists():
                shutil.rmtree(old_memory)
                logger.info(f"Removed old memory: {old_memory}")
        except OSError as e:
            logger.warning(f"Failed to remove some old directories: {e}")

    # ----- config: copy config.yaml -----
    resources_dir = package_root / "resources"
    config_yaml_src_candidates = [
        resources_dir / "config.yaml",
        package_root / "config" / "config.yaml",
    ]

    config_yaml_src = next((p for p in config_yaml_src_candidates if p.exists()), None)

    if not config_yaml_src:
        raise RuntimeError(
            "config.yaml template not found; tried: "
            + ", ".join(str(p) for p in config_yaml_src_candidates)
        )

    config_dest_dir = workspace_dir / "config"
    config_dest_dir.mkdir(parents=True, exist_ok=True)
    config_yaml_dest = config_dest_dir / "config.yaml"

    if overwrite or not config_yaml_dest.exists():
        _write_initial_user_override_config(config_yaml_src, config_yaml_dest)

    builtin_rules_src = resources_dir / "builtin_rules.yaml"
    builtin_rules_dest = config_dest_dir / "builtin_rules.yaml"
    if builtin_rules_src.is_file() and (overwrite or not builtin_rules_dest.exists()):
        with TrackCopyDiff(
            dest=builtin_rules_dest,
            is_file=True,
            cumulative=cumulative_diff,
            overwrite=overwrite,
        ):
            shutil.copy2(builtin_rules_src, builtin_rules_dest)

    resolved_lang = _resolve_preferred_language(config_yaml_dest, preferred_language)

    # ----- 内置模板根目录：<package>/resources（含 agent/、skills_state.json）-----
    template_root = resources_dir
    template_agent_dir = template_root / "agent"
    if not template_agent_dir.is_dir():
        raise RuntimeError(f"resources template missing agent dir: {template_agent_dir}")

    # ----- .env: copy from template to config/.env -----
    env_template_src_candidates = [
        resources_dir / ".env.template",
        package_root / ".env.template",
    ]
    env_template_src = next((p for p in env_template_src_candidates if p.exists()), None)
    if not env_template_src:
        raise RuntimeError(
            "env template source not found; tried: "
            + ", ".join(str(p) for p in env_template_src_candidates)
        )
    env_dest = workspace_dir / "config" / ".env"
    if overwrite or not env_dest.exists():
        with TrackCopyDiff(
            dest=env_dest,
            is_file=True,
            cumulative=cumulative_diff,
            overwrite=overwrite,
        ):
            shutil.copy2(env_template_src, env_dest)

    # ----- copy runtime dirs (multi-tenant layout) -----
    service_root = get_service_root_dir()
    service_root.mkdir(parents=True, exist_ok=True)
    (service_root / ".logs").mkdir(parents=True, exist_ok=True)

    agent_workspace = get_multi_tenant_user_workspace_dir("default", "default")
    if agent_workspace is None:
        raise RuntimeError("failed to resolve default multi-tenant workspace")
    agent_workspace.mkdir(parents=True, exist_ok=True)
    (agent_workspace / ".checkpoint").mkdir(parents=True, exist_ok=True)
    agent_root = agent_workspace / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    agent_sessions = agent_root / "sessions"

    # ----- DeepAgent workspace (standard DeepAgents schema) -----
    deepagent_workspace = agent_root / "workspace"
    default_project_workspace = deepagent_workspace / "projects"
    agent_skills = deepagent_workspace / "skills"
    agent_memory = deepagent_workspace / "memory"

    template_agent_workspace = template_agent_dir / "workspace"
    template_agent_memory = template_agent_dir / "workspace" / "memory"

    def copy_if_missing(src: str | Path, dst: str | Path) -> str | Path:
        """增量复制：目标已存在则跳过。

        Args:
            src: 源文件路径
            dst: 目标文件路径

        Returns:
            目标文件路径（已存在则直接返回，否则返回复制后的路径）
        """
        if os.path.exists(dst):
            return dst
        return shutil.copy2(src, dst)

    def _copy_dir(
        src_dir: Path,
        dst_dir: Path,
        ignore_patterns: tuple[str, ...] | None = None,
    ) -> None:
        if not src_dir.exists():
            return
        if overwrite and dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.parent.mkdir(parents=True, exist_ok=True)

        if ignore_patterns:
            ignore = shutil.ignore_patterns(*ignore_patterns)
        else:
            ignore = None

        if not dst_dir.exists():
            shutil.copytree(src_dir, dst_dir, ignore=ignore)
        else:
            shutil.copytree(
                src_dir, dst_dir, dirs_exist_ok=True, ignore=ignore,
                copy_function=copy_if_missing,
            )

    # Copy DeepAgent workspace template (includes agent-data.json, memory, skills)
    # Ignore _ZH.md and _EN.md files - they are handled separately
    if template_agent_workspace.exists():
        with TrackCopyDiff(
            dest=deepagent_workspace,
            cumulative=cumulative_diff,
            overwrite=overwrite,
        ):
            _copy_dir(
                template_agent_workspace,
                deepagent_workspace,
                ignore_patterns=("*_ZH.md", "*_EN.md", "skills"),
            )
    else:
        deepagent_workspace.mkdir(parents=True, exist_ok=True)
    with TrackCopyDiff(
        dest=agent_memory,
        cumulative=cumulative_diff,
        overwrite=overwrite,
    ):
        _copy_dir(template_agent_memory, agent_memory, ignore_patterns=("*_ZH.md", "*_EN.md"))

    # Copy multi-language files based on resolved language
    # Files with _ZH/_EN suffix are copied to the workspace without suffix
    suffix = "_ZH" if resolved_lang == "zh" else "_EN"
    multilang_files = [
        (f"AGENT{suffix}.md", "AGENT.md"),
        (f"HEARTBEAT{suffix}.md", "HEARTBEAT.md"),
        (f"IDENTITY{suffix}.md", "IDENTITY.md"),
        (f"SOUL{suffix}.md", "SOUL.md"),
        (f"memory/MEMORY{suffix}.md", "memory/MEMORY.md"),
    ]
    for src_name, dst_name in multilang_files:
        src_path = template_agent_workspace / src_name
        dst_path = deepagent_workspace / dst_name
        if src_path.exists() and not dst_path.exists():
            with TrackCopyDiff(
                dest=dst_path,
                is_file=True,
                cumulative=cumulative_diff,
                overwrite=overwrite,
            ):
                shutil.copy2(src_path, dst_path)

    # skills state: shipped under resources/
    skills_state_src = template_root / "skills_state.json"
    if skills_state_src.exists():
        agent_skills.mkdir(parents=True, exist_ok=True)
        dest_skill_state = agent_skills / "skills_state.json"
        if not dest_skill_state.exists():
            with TrackCopyDiff(
                dest=dest_skill_state,
                is_file=True,
                cumulative=cumulative_diff,
                overwrite=overwrite,
            ):
                shutil.copy2(skills_state_src, dest_skill_state)

    # sessions is runtime-only (template may not include it)
    agent_sessions.mkdir(parents=True, exist_ok=True)
    default_project_workspace.mkdir(parents=True, exist_ok=True)

    from jiuwenswarm.common.config import set_preferred_language_in_config_file

    set_preferred_language_in_config_file(config_yaml_dest, resolved_lang)

    # ----- 默认安装内置技能: skill-creator 和 swarmskill-creator -----
    _install_default_builtin_skills(
        builtin_dir=get_builtin_skills_dir(),
        user_skills_dir=agent_skills,
        overwrite=overwrite,
        cumulative_diff=cumulative_diff,
    )

    _resolve_paths(force=True)

    return cumulative_diff


def _close_log_handlers() -> None:
    """Close all jiuwenswarm log handlers to release file locks.

    This is needed before deleting workspace directory in init -f mode,
    because setup_logger() runs at module import time and opens log files.
    """
    root = logging.getLogger("jiuwenswarm")
    for handler in root.handlers[:]:
        try:
            handler.close()
            root.removeHandler(handler)
        except Exception:
            pass  # Ignore errors during cleanup


def _print_diff_summary(diff_result: CopyDiffResult, overwrite: bool) -> None:
    """打印文件变更统计摘要。

    Args:
        diff_result: 包含 added_dirs, added_files, overwritten_files 的结果
        overwrite: 是否为覆盖模式（True 时不显示统计）
    """
    if overwrite:
        return

    total_files = len(diff_result.added_files) + len(diff_result.overwritten_files)
    if total_files == 0:
        print("[jiuwenswarm-init] 初始化完成：工作区已就绪，无新文件需创建 / Init complete: workspace ready, no new files needed")
        return

    print("[jiuwenswarm-init] 初始化完成，文件变更如下：/ Init complete, file changes:")
    if diff_result.added_files:
        print(f"  新增文件 / New files: {len(diff_result.added_files)}")
        for f in diff_result.added_files[:10]:
            print(f"    + {f}")
        if len(diff_result.added_files) > 10:
            print(f"    ...等 {len(diff_result.added_files) - 10} 个 / ...and {len(diff_result.added_files) - 10} more")
    if diff_result.overwritten_files:
        print(f"  更新文件 / Updated files: "
              f"{len(diff_result.overwritten_files)}")
        for f in diff_result.overwritten_files[:10]:
            print(f"    ~ {f}")
        if len(diff_result.overwritten_files) > 10:
            print(f"    ...等 {len(diff_result.overwritten_files) - 10} 个 / "
              f"...and {len(diff_result.overwritten_files) - 10} more")


def init_user_workspace(
    overwrite: bool = True, workspace_dir: Optional[Path] = None
) -> Path | Literal["cancelled"]:
    """Initialize ~/.jiuwenswarm from package or source resources.

    资源布局:
    - 模板配置:   <package_root>/resources/config.yaml
    - .env 模板: <package_root>/resources/.env.template
    - 数据模板:   <package_root>/resources/agent（含各技能模板）、skills_state.json

    上述内容会被复制到:
    - ~/.jiuwenswarm/config/config.yaml（含 preferred_language）
    - ~/.jiuwenswarm/config/builtin_rules.yaml（内置 shell 安全规则模板，与 config 同目录）
    - ~/.jiuwenswarm/config/.env
    - ~/.jiuwenswarm/agent/...

    注意：PRINCIPLE.md、TONE.md、HEARTBEAT.md 已被 SOUL.md 和新的心跳机制替代，
    不再由 JiuwenSwarm 复制到用户工作区。

    交互式 init 会先询问语言；首次启动 app 时非交互 prepare_workspace 则沿用模板 config 中的语言。

    Args:
        overwrite: True 时强制清理整个工作空间目录后初始化；
                   False 时保留原有数据，执行迁移合并逻辑。
        workspace_dir: 工作空间目录路径，若不指定则使用 get_user_workspace_dir() 获取。
    """
    if workspace_dir is None:
        workspace_dir = get_user_workspace_dir()
    else:
        workspace_dir = Path(workspace_dir)
    if workspace_dir.exists():
        if overwrite:
            # Force mode: explain both modes and ask for confirmation
            print(
                f"[jiuwenswarm-init] With -f/--force flag, "
                f"entire {workspace_dir} will be deleted for clean initialization."
            )
            print("[jiuwenswarm-init] WARNING: This will delete all historical configuration and memory information.")
            print("[jiuwenswarm-init] This action cannot be undone.")
            if _is_interactive():
                confirmation = input(
                    "[jiuwenswarm-init] Do you want to confirm reinitialization? (yes/no): "
                ).strip().lower()

                if confirmation not in ("yes", "y"):
                    print("[jiuwenswarm-init] Initialization cancelled. Exiting.")
                    return "cancelled"
            else:
                print("[jiuwenswarm-init] Non-interactive mode: proceeding with reinitialization.")

            # Close all log handlers to release file locks before deleting
            _close_log_handlers()

            # Delete entire workspace directory for clean initialization
            try:
                shutil.rmtree(workspace_dir)
                print(f"[jiuwenswarm-init] Removed workspace directory: {workspace_dir}")
            except OSError as e:
                print(f"[jiuwenswarm-init] ERROR: Failed to remove "
                  f"workspace: {e}")
                return "cancelled"
        else:
            # Merge mode: inform about preservation
            print("[jiuwenswarm-init] 增量初始化：只添加缺失文件，不覆盖已有文件 / "
              "Incremental init: only adds missing files, preserves existing")
            print("[jiuwenswarm-init] 此操作不可撤销 / This action cannot be undone.")
            if _is_interactive():
                confirmation = input("[jiuwenswarm-init] Do you want to continue? (yes/no): ").strip().lower()

                if confirmation not in ("yes", "y"):
                    print("[jiuwenswarm-init] Initialization cancelled. Exiting.")
                    return "cancelled"
            else:
                print("[jiuwenswarm-init] Non-interactive mode: proceeding with merge initialization.")

    lang = prompt_preferred_language()
    if lang is None:
        print("[jiuwenswarm-init] Initialization cancelled. Exiting.")
        return "cancelled"
    print(f"[jiuwenswarm-init] 将使用语言 / Language: {lang}")
    diff_result = prepare_workspace(overwrite, preferred_language=lang, workspace_dir=workspace_dir)
    _print_diff_summary(diff_result, overwrite)

    return workspace_dir


def _resolve_paths(force=False) -> None:
    """Resolve and cache all paths."""
    global _initialized, _config_dir, _workspace_dir, _root_dir

    if not force and _initialized:
        return

    workspace_dir = get_user_workspace_dir()

    # Migrate from legacy jiuwenclaw_workspace directory name to workspace
    _migrate_jiuwenclaw_workspace_to_workspace(workspace_dir)

    # 优先使用已初始化的用户工作区 (~/.jiuwenswarm)，
    # 保证源码运行与安装包运行后的读写路径完全一致。
    user_config_dir = workspace_dir / "config"
    # 多租户路径：service_default/agent_default/agent/workspace
    multi_tenant_workspace = get_multi_tenant_user_workspace_dir("default", "default")
    if multi_tenant_workspace is not None:
        user_workspace_dir = multi_tenant_workspace / "agent" / "workspace"
    else:
        user_workspace_dir = workspace_dir / "agent" / "workspace"
    if user_config_dir.exists():
        _root_dir = workspace_dir
        _config_dir = user_config_dir
        _workspace_dir = user_workspace_dir
    else:
        # 尚未初始化 ~/.jiuwenswarm：从包内 resources 直读配置，工作区指向包内 agent/workspace
        package_root = _find_package_root()
        if package_root and (package_root / "resources" / "config.yaml").exists():
            res = package_root / "resources"
            _root_dir = package_root.parent
            _config_dir = res
            _workspace_dir = res / "agent" / "workspace"
            _workspace_dir.mkdir(parents=True, exist_ok=True)
        else:
            source_root = _find_source_root()
            pkg = source_root / "jiuwenswarm"
            res = pkg / "resources"
            _root_dir = source_root
            _config_dir = res if (res / "config.yaml").exists() else source_root / "config"
            _workspace_dir = res / "agent" / "workspace"
            _workspace_dir.mkdir(parents=True, exist_ok=True)

    _initialized = True


def get_config_dir() -> Path:
    """Get the config directory path."""
    _resolve_paths()
    return _config_dir


def get_runtime_state_path(session_id: str | None = None) -> Path:
    """Per-session runtime_state.yaml path under config dir.

    每个 session 独占一份文件，避免心跳/定时/并发 session 共用单文件互相覆盖
    channel/mode/model/git 等字段。session_id 为空时回退到 ``default``。
    """
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", (session_id or "").strip())[:128] or "default"
    return get_config_dir() / "runtime_state" / f"{sid}.yaml"


def get_workspace_dir() -> Path:
    """Get the workspace directory path."""
    _resolve_paths()
    return _workspace_dir


def get_root_dir() -> Path:
    """Get the root directory path."""
    _resolve_paths()
    return _root_dir


def get_agent_workspace_dir() -> Path:
    """Get the agent workspace directory path.

    This is the DeepAgent standard workspace directory under the agent root.
    It contains standard nodes like skills, memory, todo, messages, etc.

    Returns:
        Path to agent workspace:
        ``~/.jiuwenswarm/service_default/agent_default/agent/workspace``
        (or the request-bound tenant workspace when ContextVar is set).
    """
    try:
        from jiuwenswarm.server.runtime.tenant_context import get_bound_jiuwenclaw_workspace

        bound = get_bound_jiuwenclaw_workspace()
        if bound is not None:
            return bound
    except ImportError:
        logger.debug("tenant_context unavailable for workspace bind", exc_info=True)
    return get_agent_root_dir() / "workspace"


def get_default_project_workspace_dir() -> Path:
    """Get the fallback task workspace used when no project is bound.

    Agent-owned data such as memory, skills, todo, and sessions stays directly
    under ``get_agent_workspace_dir()``. This directory is only the default cwd
    / workspace boundary for user task artifacts when no project is selected.
    """
    return get_agent_workspace_dir() / "projects"


def get_default_project_session_workspace_dir(session_id: str | None = None) -> Path:
    """Get the no-project task workspace for a single conversation session.

    Layout:
        <agent-workspace>/projects/<session_id>

    ``session_id`` is used when available so the same conversation keeps a stable
    workspace. If it is missing during early adapter initialization, the shared
    projects root is returned so no throwaway session directory is created.
    """
    base = get_default_project_workspace_dir()
    raw_session = str(session_id or "").strip()
    if not raw_session:
        base.mkdir(parents=True, exist_ok=True)
        return base
    safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_session).strip("._-")
    if not safe_session:
        base.mkdir(parents=True, exist_ok=True)
        return base
    workspace = base / safe_session
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def get_prompt_attachment_dir() -> Path:
    """Get the jiuwenswarm prompt attachment directory path."""

    return get_agent_workspace_dir() / "prompt_attachment"


def get_service_root_dir(service_id: str = "default") -> Path:
    """Get the service-level directory path.

    多租户架构下，service 级别存放共享数据（如日志）。
    Path: ``~/.jiuwenswarm/service_{service_id}/``
    """
    sid = str(service_id or "default").strip() or "default"
    return get_user_workspace_dir() / f"service_{sid}"


def get_agent_root_dir() -> Path:
    """Get the agent root directory path (multi-tenant default).

    Path: ``~/.jiuwenswarm/service_default/agent_default/agent/``
    (or the request-bound agent root when ContextVar is set).
    """
    try:
        from jiuwenswarm.server.runtime.tenant_context import get_bound_agent_root

        bound = get_bound_agent_root()
        if bound is not None:
            return bound
    except ImportError:
        logger.debug("tenant_context unavailable for agent root bind", exc_info=True)
    return get_multi_tenant_user_workspace_dir("default", "default") / "agent"


def get_agent_root_relative_dir() -> Path:
    """Get the agent root relative path under a tenant workspace root."""
    return Path("agent")


def get_agent_workspace_relative_dir() -> Path:
    """Get the agent workspace relative path under a tenant workspace root."""
    return get_agent_root_relative_dir() / "workspace"


_AGENT_WORKSPACE_DIR_NAMES = frozenset({"workspace", "jiuwenclaw_workspace"})


def collapse_nested_agent_workspace_dir(path: Path | str) -> Path:
    """Collapse ``.../workspace/workspace`` back to the agent workspace.

    The agent workspace is ``.../agent/workspace`` (this project) or
    ``.../agent/jiuwenclaw_workspace`` (upstream). PPT tooling historically
    used ``{cwd}/workspace`` as the session parent, which nests a second
    ``workspace`` directory when cwd is already the agent workspace.
    """
    resolved = Path(path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        resolved = resolved.absolute()
    parent_name = resolved.parent.name.lower()
    if resolved.name.lower() == "workspace" and parent_name in _AGENT_WORKSPACE_DIR_NAMES:
        return resolved.parent
    return resolved


def get_agent_sessions_relative_dir() -> Path:
    """Get the agent sessions relative path under a tenant workspace root."""
    return get_agent_root_relative_dir() / "sessions"


def _normalize_tenant_id(value: str | None) -> str:
    return str(value or "").strip()


def _require_tenant_ids(service_id: str | None, agent_id: str | None) -> tuple[str, str]:
    """Require non-empty ``service_id`` and ``agent_id`` for path construction."""
    sid = _normalize_tenant_id(service_id)
    aid = _normalize_tenant_id(agent_id)
    if not sid or not aid:
        raise ValueError(
            f"tenant id required: agent_id={agent_id!r}, service_id={service_id!r}"
        )
    return sid, aid


def get_multi_tenant_user_workspace_dir(
    service_id: str | None,
    agent_id: str | None = None,
) -> Path | None:
    """Get multi-tenant user workspace directory path.

    Path format: ``~/.jiuwenswarm/service_{service_id}/agent_{agent_id}``

    Aligns with test/jiuwenclaw and OfficeClaw on-disk layout
    (e.g. ``service_default/agent_office``).
    """
    if not service_id and not agent_id:
        return None
    workspace_dir = get_user_workspace_dir()
    workspace_dir = (
        workspace_dir / f"service_{service_id}" if service_id else workspace_dir / "service"
    )
    workspace_dir = (
        workspace_dir / f"agent_{agent_id}" if agent_id else workspace_dir / "agents"
    )
    return workspace_dir


def resolve_tenant_env_ns(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[str, str]:
    """Resolve ``(service_id, agent_id)``: explicit pair > bound env_ns > TypeError."""
    from jiuwenswarm.common.local_env_config import (
        get_bound_agent_env_ns,
        normalize_env_ns_id,
    )

    if service_id is not None or agent_id is not None:
        if service_id is None or agent_id is None:
            raise TypeError(
                "tenant scope requires both service_id and agent_id when either is passed"
            )
        sid = str(service_id).strip()
        aid = str(agent_id).strip()
        if not sid or not aid:
            raise TypeError("tenant service_id/agent_id must be non-empty strings")
        return normalize_env_ns_id(sid), normalize_env_ns_id(aid)
    bound = get_bound_agent_env_ns()
    if bound is not None:
        return normalize_env_ns_id(bound[0]), normalize_env_ns_id(bound[1])
    raise TypeError(
        "tenant scope is required: pass service_id=... and agent_id=..., "
        "or bind_agent_env_ns before resolving tenant paths"
    )


def get_tenant_agent_workspace_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """多租户 DeepAgent 工作区：``service_{sid}/agent_{aid}/agent/workspace``."""
    sid, aid = resolve_tenant_env_ns(service_id, agent_id)
    workspace = get_multi_tenant_user_workspace_dir(sid, aid)
    if workspace is None:
        raise TypeError(
            f"invalid tenant for workspace path: service_id={sid!r}, agent_id={aid!r}"
        )
    return workspace / get_agent_workspace_relative_dir()


# 兼容旧命名（上游 jiuwenclaw_workspace）
get_tenant_agent_jiuwenclaw_workspace_dir = get_tenant_agent_workspace_dir


def get_tenant_agent_skills_dirs(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> list[Path]:
    """多租户 skills 目录（与 ``JiuWenSwarm`` / ``SkillManager`` 落盘路径一致）."""
    workspace = get_tenant_agent_workspace_dir(service_id, agent_id)
    return [workspace / "skills"]


def get_multi_tenant_skill_dirs(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> list[Path]:
    """Resolve the skills directory list for multi-tenant / single-tenant mode.

    - Multi-tenant（提供 ``service_id`` / ``agent_id``）: returns
      ``[service_{sid}/agent_{aid}/agent/workspace/skills]``.
    - Single-tenant（均未提供）: returns ``[get_agent_skills_dir()]``.
    """
    if service_id or agent_id:
        return get_tenant_agent_skills_dirs(service_id, agent_id)
    return [get_agent_skills_dir()]


def get_agent_home_dir() -> Path:
    return get_agent_root_dir() / "home"


def get_agent_memory_dir() -> Path:
    """Get the agent memory directory path.

    Uses DeepAgent standard workspace location for unified workspace.

    Returns:
        Path to memory directory: ~/.jiuwenswarm/agent/workspace/memory
    """
    return get_agent_workspace_dir() / "memory"


def get_agent_skills_dir() -> Path:
    """Get the agent skills directory path.

    Uses DeepAgent standard workspace location for unified workspace.

    Returns:
        Path to skills directory: ~/.jiuwenswarm/agent/workspace/skills
    """
    return get_agent_workspace_dir() / "skills"


JIUWENSWARM_SHARED_SKILLS_DIRS_ENV = "JIUWENSWARM_SHARED_SKILLS_DIRS"
# Relay still emits the legacy name; tip ingest remaps it to the canonical key.
JIUWENCLAW_SHARED_SKILLS_DIRS_ENV = "JIUWENCLAW_SHARED_SKILLS_DIRS"


def parse_shared_skills_dirs_raw(raw: str) -> list[Path]:
    """Parse SHARED_SKILLS_DIRS value into deduplicated absolute paths."""
    text = (raw or "").strip()
    if not text:
        return []

    dirs: list[Path] = []
    seen: set[str] = set()
    for part in [part.strip() for part in text.split(os.pathsep) if part.strip()]:
        path = Path(part).expanduser().resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(path)
    return dirs


def get_shared_agent_skills_dirs() -> list[Path]:
    """Read shared skill roots from tip/env (OfficeClaw ``office-claw-skills`` etc.).

    Reads canonical ``JIUWENSWARM_SHARED_SKILLS_DIRS``; ``read_env`` also
    resolves relay ``JIUWENCLAW_SHARED_SKILLS_DIRS`` via product-key aliasing.
    """
    from jiuwenswarm.common.local_env_config import read_env

    raw = read_env(JIUWENSWARM_SHARED_SKILLS_DIRS_ENV, "")
    if not raw or not raw.strip():
        return []
    return parse_shared_skills_dirs_raw(raw.strip())


def resolve_agent_registered_skill_dirs() -> list[Path]:
    """Resolve skill dirs: request-bound override, shared tip dirs, else workspace."""
    try:
        from jiuwenswarm.server.runtime.agent_adapter.session_skill_dirs import (
            get_session_registered_skill_dirs,
        )

        bound = get_session_registered_skill_dirs()
    except Exception:
        bound = None
    if bound:
        return [Path(p) for p in bound]
    shared = get_shared_agent_skills_dirs()
    if shared:
        return shared
    return [get_agent_skills_dir()]


def get_interactions_dir() -> Path:
    """Get the interactions directory for pending interaction contexts.

    Returns:
        Path to interactions directory: {workspace}/agent/workspace/interactions
    """
    return get_agent_workspace_dir() / "interactions"


def get_cron_jobs_path() -> Path:
    """Legacy global cron_jobs.json (pre-tenant Gateway). Prefer per-tenant helpers."""
    return get_agent_home_dir() / "cron_jobs.json"


def resolve_gateway_cron_jobs_path(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Gateway per-tenant cron store: ``gateway/cron/service_{sid}/agent_{aid}/cron_jobs.json``."""
    sid = str(service_id or "default").strip() or "default"
    aid = str(agent_id or "default").strip() or "default"
    return (
        get_user_workspace_dir()
        / "gateway"
        / "cron"
        / f"service_{sid}"
        / f"agent_{aid}"
        / "cron_jobs.json"
    )


def resolve_tenant_agent_root_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Resolve ``service_{sid}/agent_{aid}/agent``."""
    sid, aid = resolve_tenant_env_ns(service_id, agent_id)
    workspace = get_multi_tenant_user_workspace_dir(sid, aid)
    if workspace is None:
        raise TypeError(
            f"invalid tenant for agent root: service_id={sid!r}, agent_id={aid!r}"
        )
    return workspace / "agent"


def resolve_tenant_agent_workspace_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Resolve ``service_{sid}/agent_{aid}/agent/workspace``."""
    return resolve_tenant_agent_root_dir(service_id, agent_id) / "workspace"


def resolve_tenant_sessions_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Resolve ``service_{sid}/agent_{aid}/agent/sessions`` for a tenant pair."""
    return resolve_tenant_agent_root_dir(service_id, agent_id) / "sessions"


def resolve_cron_tenant_scope(
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
    metadata: dict | None = None,
    params: dict | None = None,
    log_prefix: str = "[Cron]",
) -> tuple[str, str]:
    """Resolve cron tenant ids; missing values fall back to default/default."""
    sid = service_id
    aid = agent_id
    if sid is None and isinstance(metadata, dict):
        sid = metadata.get("service_id")
    if aid is None and isinstance(metadata, dict):
        aid = metadata.get("agent_id")
    if sid is None and isinstance(params, dict):
        sid = params.get("service_id")
    if aid is None and isinstance(params, dict):
        aid = params.get("agent_id")
    sid_s = str(sid).strip() if sid is not None else ""
    aid_s = str(aid).strip() if aid is not None else ""
    if not sid_s or not aid_s:
        logger.warning(
            "%s missing service_id/agent_id; fallback to default/default (sid=%r aid=%r)",
            log_prefix,
            sid,
            aid,
        )
    return sid_s or "default", aid_s or "default"


def get_deepagent_todo_dir() -> Path:
    """Get the DeepAgent todo directory path.

    Returns:
        Path to todo directory: ~/.jiuwenswarm/agent/workspace/todo
    """
    return get_agent_workspace_dir() / "todo"


def get_deepagent_messages_dir() -> Path:
    """Get the DeepAgent messages directory path.

    Returns:
        Path to messages directory: ~/.jiuwenswarm/agent/workspace/messages
    """
    return get_agent_workspace_dir() / "messages"


def get_deepagent_agents_dir() -> Path:
    """Get the DeepAgent agents (sub-agent) directory path.

    Returns:
        Path to agents directory: ~/.jiuwenswarm/agent/workspace/agents
    """
    return get_agent_workspace_dir() / "agents"


def get_deepagent_heartbeat_path() -> Path:
    """Get the DeepAgent HEARTBEAT.md file path.

    Returns:
        Path to HEARTBEAT.md: ~/.jiuwenswarm/agent/workspace/HEARTBEAT.md
    """
    return get_agent_workspace_dir() / "HEARTBEAT.md"


def get_deepagent_agent_md_path() -> Path:
    """Get the DeepAgent AGENT.md file path.

    Returns:
        Path to AGENT.md: ~/.jiuwenswarm/agent/workspace/AGENT.md
    """
    return get_agent_workspace_dir() / "AGENT.md"


def get_deepagent_soul_md_path() -> Path:
    """Get the DeepAgent SOUL.md file path.

    Returns:
        Path to SOUL.md: ~/.jiuwenswarm/agent/workspace/SOUL.md
    """
    return get_agent_workspace_dir() / "SOUL.md"


def get_deepagent_identity_md_path() -> Path:
    """Get the DeepAgent IDENTITY.md file path.

    Returns:
        Path to IDENTITY.md: ~/.jiuwenswarm/agent/workspace/IDENTITY.md
    """
    return get_agent_workspace_dir() / "IDENTITY.md"


def get_deepagent_user_md_path() -> Path:
    """Get the DeepAgent USER.md file path.

    Returns:
        Path to USER.md: ~/.jiuwenswarm/agent/workspace/USER.md
    """
    return get_agent_workspace_dir() / "USER.md"


def get_builtin_skills_dir() -> Path:
    """Get the built-in skills directory from package resources."""
    package_root = _find_package_root()
    # 优先检查 workspace/skills 目录（标准布局）
    primary_path = package_root / "resources" / "agent" / "workspace" / "skills"
    if primary_path.exists() and primary_path.is_dir():
        return primary_path
    # 回退到 skills 目录
    fallback_path = package_root / "resources" / "agent" / "skills"
    return fallback_path


def get_agent_sessions_dir() -> Path:
    """Get sessions directory (bound tenant or ``service_default/agent_default``).

    Path: ``~/.jiuwenswarm/service_default/agent_default/agent/sessions``
    """
    return get_agent_root_dir() / "sessions"


def get_agent_evolution_trajectories_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Get the evolution execution trajectories directory.

    Path: ``service_{sid}/agent_{aid}/agent/evolution_trajectories``
    """
    if service_id is not None or agent_id is not None:
        return resolve_tenant_agent_root_dir(service_id, agent_id) / "evolution_trajectories"
    return get_agent_root_dir() / "evolution_trajectories"


# 当前 git 分支解析（带短 TTL 缓存），用于 /resume 按分支过滤会话。
# 对齐 Claude Code：非 git 目录 / detached HEAD / 任何失败一律返回 "HEAD" 哨兵值。
_GIT_BRANCH_CACHE: dict[str, tuple[float, str]] = {}
_GIT_BRANCH_TTL_SECONDS = 5.0


def resolve_git_branch(project_dir: str | None) -> str:
    """返回 ``project_dir`` 当前 git 分支，取不到时返回哨兵 ``"HEAD"``。

    结果按 ``project_dir`` 缓存数秒，避免在 session.list / 每次聊天请求时
    频繁 spawn git 进程。
    """
    if not project_dir or not os.path.isdir(project_dir):
        return "HEAD"
    now = time.time()
    cached = _GIT_BRANCH_CACHE.get(project_dir)
    if cached and now - cached[0] < _GIT_BRANCH_TTL_SECONDS:
        return cached[1]
    branch = "HEAD"
    git_bin = shutil.which("git")
    if git_bin:
        try:
            import subprocess

            result = subprocess.run(
                [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=project_dir,
            )
            if result.returncode == 0:
                branch = result.stdout.strip() or "HEAD"
        except Exception:
            branch = "HEAD"
    _GIT_BRANCH_CACHE[project_dir] = (now, branch)
    return branch


def get_checkpoint_dir() -> Path:
    """Get the default checkpoint directory (agent_default).

    Path: ``~/.jiuwenswarm/service_default/agent_default/.checkpoint``

    Per-agent isolation uses ``set_checkpoint`` / ``get_multi_tenant_user_workspace_dir``.
    """
    workspace = get_multi_tenant_user_workspace_dir("default", "default")
    if workspace:
        return workspace / ".checkpoint"
    return get_agent_root_dir().parent / ".checkpoint"


def _resolve_logs_service_id(service_id: str | None = None) -> str:
    """Resolve service_id for logs: explicit > bound env_ns > default."""
    if service_id is not None:
        return str(service_id).strip() or "default"
    try:
        from jiuwenswarm.common.local_env_config import get_bound_agent_env_ns

        ns = get_bound_agent_env_ns()
        if ns is not None:
            return str(ns[0]).strip() or "default"
    except Exception:
        logger.debug("resolve logs service_id from bound env_ns failed", exc_info=True)
    return "default"


def get_logs_dir(service_id: str | None = None) -> Path:
    """Get the logs directory path (service-level).

    Path: ``~/.jiuwenswarm/service_{sid}/.logs``

    ``service_id`` 解析顺序：显式参数 > 当前 ``bind_agent_env_ns`` 的 sid > ``default``。
    进程启动时的 FileHandler（``setup_logger``）通常无 bind，仍落在 ``service_default/.logs``。
    """
    log_root_path = os.getenv("LOG_ROOT_PATH", "").strip()
    if log_root_path:
        return Path(log_root_path).expanduser().resolve()
    sid = _resolve_logs_service_id(service_id)
    return get_service_root_dir(sid) / ".logs"


def get_xy_tmp_dir() -> Path:
    workspace_dir = get_user_workspace_dir()
    xy_tmp_dir = workspace_dir / "tmp" / "xiaoyi"
    xy_tmp_dir.mkdir(parents=True, exist_ok=True)
    return xy_tmp_dir


def get_env_file() -> Path:
    return get_config_dir() / ".env"


def reset_free_search_runtime_flags() -> None:
    """Start each process with free-search engines disabled unless reopened via config UI."""
    os.environ["FREE_SEARCH_DDG_ENABLED"] = "false"
    os.environ["FREE_SEARCH_BING_ENABLED"] = "false"


def get_config_file() -> Path:
    """Get the config.yaml file path."""
    return get_config_dir() / "config.yaml"


def is_package_installation() -> bool:
    """Check if running from package installation."""
    return _detect_installation_mode()


# 统一敏感信息掩码值。
_SENSITIVE_MASK = "******"
_DATA_IMAGE_PATTERN = re.compile(
    r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+"
)
# 匹配常见敏感字段键值对（不要求值必须带引号），用于覆盖:
# - token=abc
# - api_key: sk-xxx
# - authorization = Bearer ...
# 分组说明：
# 1) 敏感键名；2) 分隔符及两侧空白（: 或 =）；3) 可选起始引号；
# 4) 值本体（用于脱敏后附指纹）；5) 可选结束引号。
_KV_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|user[_-]?id|userid)"
    r"(?![A-Za-z0-9])(\s*[:=]\s*)([\"']?)([^,\s\"'\]\}]+)([\"']?)"
)
# 匹配“键名包含敏感关键词”且“值被引号包裹”的场景，覆盖:
# - 'CAT_CAFE_CALLBACK_TOKEN': 'xxxx'
# - 'CAT_CAFE_USER_ID': 'CSDN-weixin'
# - "my_private_key"="xxxx"
# 分组说明：
# 1) 完整的 key + 分隔符（含可选引号）
# 2) 值的起始引号（' 或 "）
# 3) 值内容（非贪婪）
# 4) 结束引号（通过 (\2) 强制与起始引号一致）
_NAMED_SENSITIVE_KV_PATTERN = re.compile(
    r"(?i)([\"']?[A-Za-z0-9_.-]*"
    r"(?:token|secret|password|passwd|pwd|api[_-]?key|authorization|"
    r"credential|private[_-]?key|user[_-]?id|userid)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
# 匹配 Authorization Bearer 令牌，保留 "Bearer " 前缀，仅掩码后面的令牌值。
# 分组：1) "Bearer " 前缀；2) 令牌值本体（用于算指纹）。
_BEARER_SENSITIVE_PATTERN = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)")
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    # 匹配 JWT（header.payload.signature 三段式，常见以 eyJ 开头）。
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # 匹配 OpenAI 风格 key（sk- 前缀）。
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    # 匹配 GitHub Personal Access Token（ghp_ 前缀）。
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    # 匹配 GitLab Personal Access Token（glpat- 前缀）。
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    # 匹配邮箱地址（避免日志中泄露个人身份信息）。
    re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
    # 匹配中国大陆手机号（可带 +86 或 86 前缀，支持空格/短横线分隔）。
    re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    # 匹配中国身份证号（18 位，最后一位可为 X/x）。
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
]
# PII / 非凭证类 pattern：掩码但不附指纹（关联意义不大，且避免引入额外可逆性顾虑）。
_SENSITIVE_PII_PATTERNS: tuple[re.Pattern[str], ...] = tuple(_SENSITIVE_PATTERNS[-3:])
# 凭证类 prefix pattern：掩码并附指纹（同 key 指纹一致可关联、不可逆）。
_SENSITIVE_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(_SENSITIVE_PATTERNS[:4])


def _fingerprint(value: str) -> str:
    """返回 value 的 SHA256 前 4 字节（8 位 hex）指纹，用于脱敏后的关联。

    不可逆：拿到 ``fp:7f3a2c19`` 无法还原原值。同一 key 每次指纹一致，
    可在日志中把同一账号/会话的多次请求串起来排查；key 轮换后指纹自然变化。
    """
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]


# 已脱敏产物形态：纯 ****** 或 ******(fp:xxxxxxxx)。
# 用于在二次脱敏时识别"已是脱敏值"，跳过重算指纹，避免产生"指纹的指纹"
# 导致跨日志关联失效（如 stream_logger._mask_secrets 先脱敏，_write_raw 再脱敏）。
_ALREADY_MASKED_PATTERN = re.compile(rf"^{re.escape(_SENSITIVE_MASK)}(\(fp:[0-9a-f]{{8}}\))?$")

# LogMaskingEngine 回退失败计数（避免在日志 Filter 热路径上静默吞异常）。
_sanitize_engine_fallback_failures = 0


def _is_already_masked(value: Any) -> bool:
    """判断 value 是否已是脱敏产物（纯掩码或带指纹），避免重复脱敏。"""
    try:
        v = str(value) if value is not None else ""
    except Exception:
        return False
    return bool(v) and bool(_ALREADY_MASKED_PATTERN.match(v))


def _masked_with_fp(value: Any) -> str:
    """脱敏并附指纹：``******(fp:xxxxxxxx)``。value 为空或失败时退化为纯掩码。

    若 value 本身已是脱敏产物（``******`` 或 ``******(fp:..)``），原样返回，
    不重算指纹——避免对"指纹值"再算指纹导致跨日志关联失效。
    """
    try:
        v = str(value) if value is not None else ""
    except Exception:
        return _SENSITIVE_MASK
    if _is_already_masked(v):
        return v
    fp = _fingerprint(v)
    if not fp:
        return _SENSITIVE_MASK
    return f"{_SENSITIVE_MASK}(fp:{fp})"


def _sanitize_log_text(text: str) -> str:
    if not text:
        return text

    # 企业版：若已从 Gateway DB 下发脱敏规则，优先走 LogMaskingEngine。
    if os.getenv("AGENT_RUNTIME", "").strip():
        try:
            from jiuwenswarm.infrastructure.log_masking.engine import LogMaskingEngine

            engine = LogMaskingEngine.get_instance()
            if engine.uses_external_rules:
                return engine.sanitize(text)
        except Exception as exc:
            # 回退到本地正则脱敏。不能走 logging：本函数会被 SensitiveDataFilter 调用，
            # 写日志会递归进脱敏路径。仅首次 stderr 提示，避免静默吞掉异常。
            global _sanitize_engine_fallback_failures
            _sanitize_engine_fallback_failures += 1
            if _sanitize_engine_fallback_failures == 1:
                print(
                    "[jiuwenswarm] LogMaskingEngine sanitize failed, "
                    f"falling back to local masking: {exc!r}",
                    file=sys.stderr,
                )

    masked = text
    masked = _DATA_IMAGE_PATTERN.sub("data:image/*;base64,******", masked)
    # _KV_SENSITIVE_PATTERN: 组1=键名, 组2=分隔符, 组4=值（组3/5 为可选引号）。
    masked = _KV_SENSITIVE_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_masked_with_fp(m.group(4))}", masked
    )
    # _NAMED_SENSITIVE_KV_PATTERN: 组1=键+分隔符, 组2=起始引号, 组3=值, 组4=结束引号。
    masked = _NAMED_SENSITIVE_KV_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_masked_with_fp(m.group(3))}{m.group(4)}", masked
    )
    # _BEARER_SENSITIVE_PATTERN: 组1=Bearer 前缀, 组2=令牌值。
    masked = _BEARER_SENSITIVE_PATTERN.sub(
        lambda m: f"{m.group(1)}{_masked_with_fp(m.group(2))}", masked
    )
    # 凭证类 prefix key（JWT/sk-/ghp_/glpat-）：掩码并附指纹。
    for pattern in _SENSITIVE_CREDENTIAL_PATTERNS:
        masked = pattern.sub(lambda m, _p=pattern: _masked_with_fp(m.group(0)), masked)
    # PII（邮箱/手机/身份证）：纯掩码，不附指纹。
    for pattern in _SENSITIVE_PII_PATTERNS:
        masked = pattern.sub(_SENSITIVE_MASK, masked)
    return masked


def mask_sensitive(text: Any) -> str:
    """对任意文本做敏感信息脱敏，返回脱敏后的字符串。

    作为对外稳定接口：调用方（如 ``agent_ws_server`` 打印模型配置/环境变量）
    应统一走本函数，避免各自硬编码键名匹配（例如只命中 ``API_KEY``、漏掉
    ``OPENAI_API_KEY`` / ``VISION_API_KEY`` 等带前缀变体）造成明文泄露。
    """
    if text is None:
        return ""
    return _sanitize_log_text(str(text))


class SensitiveDataFilter(logging.Filter):
    """Mask sensitive data in all log messages and tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            record.msg = _sanitize_log_text(message)
            record.args = ()
        except Exception:
            # Never block logging because of desensitization failure.
            pass

        # Traceback 由 Formatter.formatException() 在 record.exc_text 中单独渲染，
        # 不经过 record.getMessage()，因此 message 脱敏覆盖不到。这里提前把
        # traceback 文本脱敏写入 record.exc_text 并清空 record.exc_info，
        # 使 logger.exception()/exc_info=True 的异常栈也不会泄露 api_key 等。
        try:
            exc_info = record.exc_info
            if exc_info and not record.exc_text:
                import traceback as _traceback

                # exc_info 是 (type, value, tb) 三元组。Python 3.10+ 的
                # traceback.format_exception 新签名只接受单个异常实例：
                # format_exception(exc, /, limit=None, chain=True)。
                # 旧的 format_exception(*exc_info)（拆包成 3 个位置参数）依赖
                # 兼容层，未来版本可能移除；改用 exc_info[1]（异常实例）是
                # 官方推荐写法，面向未来且行为等价（None 时输出 "NoneType: None"）。
                formatted = "".join(_traceback.format_exception(exc_info[1]))
                record.exc_text = _sanitize_log_text(formatted)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = _sanitize_log_text(record.exc_text)
        except Exception:
            # 同样不因脱敏失败而阻断日志输出。
            pass
        return True


class JsonOnlyFormatter(logging.Formatter):
    """只输出message内容，不添加任何前缀（时间戳、级别、logger名）"""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


# 源头脱敏是否已安装（全局，避免重复设置 LogRecordFactory）。
_source_record_masking_installed = False
# 源头脱敏失败计数（脱敏异常时递增；运维可通过此值监控脱敏失效，避免静默泄露）。
_source_masking_failures = 0


def install_source_record_masking() -> None:
    """在 LogRecord 创建层（``logging.setLogRecordFactory``）安装源头脱敏。

    这是比 handler 上挂 ``SensitiveDataFilter`` 更彻底的兜底层：无论哪个 logger
    发出的 record——包括 **jiuwenswarm 命名空间之外**的第三方库（openjiuwen /
    openai / httpx / urllib3 等，它们自带 handler、不 propagate 到 jiuwenswarm
    根 logger），在 LogRecord **创建瞬间**就被脱敏 message 与 traceback，
    保证任何来源的 api_key 都不明文落盘。

    覆盖场景：
    - ``app_agentserver.py`` 用 ``logging.getLogger("openjiuwen.harness.security")``
      等 openjiuwen 命名空间 logger；
    - clawee（yuanrong faas）拉起的 AgentServer 内 openjiuwen SDK 自有 logger；
    - httpx/openai SDK 在 DEBUG 级别打印请求头（含 Authorization）。

    复用带指纹的 ``_sanitize_log_text``，与 handler 层 ``SensitiveDataFilter``
    共存为双保险：若 record 已在源头脱敏，handler 层的 ``_is_already_masked``
    会跳过重算，不破坏指纹。幂等，重复调用安全。
    """
    global _source_record_masking_installed
    if _source_record_masking_installed:
        return

    old_factory = logging.getLogRecordFactory()

    def _sanitizing_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        try:
            # message 脱敏（含 %s/format 格式化后的最终文本）。
            msg = record.getMessage()
            record.msg = _sanitize_log_text(msg)
            record.args = ()
            # traceback 脱敏：traceback 由 Formatter.formatException 从
            # record.exc_text 单独渲染，getMessage 覆盖不到。此处提前渲染并脱敏，
            # 清空 exc_info 使 Formatter 复用已脱敏的 exc_text。
            exc_info = record.exc_info
            if exc_info and not record.exc_text:
                import traceback as _tb

                formatted = "".join(_tb.format_exception(exc_info[1]))
                record.exc_text = _sanitize_log_text(formatted)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = _sanitize_log_text(record.exc_text)
        except Exception:
            # 永不因脱敏失败而阻断日志输出。但记录失败（计数 + 首次 stderr 提示），
            # 避免静默吞掉异常导致 api_key 在无感知下明文泄露。
            global _source_masking_failures
            _source_masking_failures += 1
            if _source_masking_failures == 1:
                # 仅首次打 stderr（不用 logging，避免自循环），提示运维脱敏失效。
                # 后续仅靠计数器累积，避免高频失败刷屏。
                print(
                    "[jiuwenswarm] source record masking failed — secrets may be "
                    "exposed in logs; check _source_masking_failures counter",
                    file=sys.stderr,
                )
        return record

    logging.setLogRecordFactory(_sanitizing_record_factory)
    _source_record_masking_installed = True


def _resolve_logging_format() -> str:
    """解析日志格式（text/json/dual）。优先级 env > config.yaml > default(text)。"""
    env_format = os.getenv("JIUWENSWARM_LOG_FORMAT")
    if env_format:
        v = env_format.strip().lower()
        if v in ("text", "json", "dual"):
            return v
        logger.warning("无效的 JIUWENSWARM_LOG_FORMAT: '%s'，使用默认 'text'", env_format)
    try:
        cfg = _load_logging_config_from_yaml()
        if cfg:
            v = cfg.get("format")
            if v and v in ("text", "json", "dual"):
                return v
            # 向后兼容：dual_output.enabled=true -> dual
            dual = cfg.get("dual_output")
            if isinstance(dual, dict) and dual.get("enabled") is True:
                logger.warning("检测到旧配置 dual_output.enabled=true，已映射为 format=dual")
                return "dual"
    except Exception as e:
        logger.warning("加载 config.yaml 的 logging.format 失败: %s", e)
    return "text"


def _resolve_output_switches() -> dict[str, bool]:
    """解析 console_enabled/file_enabled。优先级 env > config.yaml > default(True)。"""
    result = {"console_enabled": True, "file_enabled": True}

    def _env_bool(env_name: str) -> bool | None:
        raw = os.getenv(env_name)
        if raw is None:
            return None
        v = raw.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
        logger.warning("无效的 %s: '%s'，使用默认 true", env_name, raw)
        return None

    ce = _env_bool("JIUWENSWARM_LOG_CONSOLE_ENABLED")
    fe = _env_bool("JIUWENSWARM_LOG_FILE_ENABLED")
    try:
        cfg = _load_logging_config_from_yaml()
    except Exception:
        cfg = {}
    if ce is None and cfg:
        ce = cfg.get("console_enabled")
    if fe is None and cfg:
        fe = cfg.get("file_enabled")
    if isinstance(ce, bool):
        result["console_enabled"] = ce
    elif ce is not None:
        logger.warning("config.yaml logging.console_enabled 非布尔: %s，使用默认 true", ce)
    if isinstance(fe, bool):
        result["file_enabled"] = fe
    elif fe is not None:
        logger.warning("config.yaml logging.file_enabled 非布尔: %s，使用默认 true", fe)
    return result


def _validate_json_config(config: dict) -> dict:
    """验证 logging.json.* 字段。"""
    if config.get("timestamp_format") not in ("text", "iso8601"):
        logger.warning("无效 logging.json.timestamp_format，使用默认 'text'")
        config["timestamp_format"] = "text"
    if "include_component" in config and not isinstance(config["include_component"], bool):
        logger.warning("无效 logging.json.include_component，使用默认 True")
        config["include_component"] = True
    if "sanitize_sensitive_data" in config and not isinstance(config["sanitize_sensitive_data"], bool):
        logger.warning("无效 logging.json.sanitize_sensitive_data，使用默认 True")
        config["sanitize_sensitive_data"] = True
    if config.get("exc_info_style", "simple") not in ("simple", "full"):
        config["exc_info_style"] = "simple"
    return config


def _resolve_json_config() -> dict[str, Any]:
    """解析 logging.json 子段（带默认值 + 验证）。"""
    default = {
        "timestamp_format": "text",
        "include_component": True,
        "sanitize_sensitive_data": True,
        "exc_info_style": "simple",
    }
    cfg = _load_logging_config_from_yaml()
    if not cfg:
        return default
    json_cfg = cfg.get("json", {})
    if not isinstance(json_cfg, dict):
        return default
    result = default.copy()
    result.update(json_cfg)
    return _validate_json_config(result)


class JsonUserVisibleFormatter(jsonlogger.JsonFormatter if jsonlogger else logging.Formatter):
    """JSON 格式化日志输出。

    继承 pythonjsonlogger.JsonFormatter（缺失时降级为 logging.Formatter）。
    字段顺序：timestamp → process → level → user_tag → user_id/domain_id/app_id →
    logger → lineno → message → component → user_visible。
    身份字段始终输出（null 便于聚合）。复用 dev-stable 的 _log_component_from_logger_name 与 _sanitize_log_text。
    """

    _FIELD_RENAME_MAP = {"asctime": "timestamp", "levelname": "level", "name": "logger"}

    def __init__(self, timestamp_format: str = "text", include_component: bool = True,
                 sanitize_sensitive_data: bool = True, exc_info_style: str = "simple", *args, **kwargs):
        if jsonlogger is None:
            fmt = kwargs.pop("fmt", "%(asctime)s %(levelname)s %(name)s %(message)s")
            datefmt = kwargs.pop("datefmt", "%Y-%m-%d %H:%M:%S")
            super().__init__(fmt, datefmt)
        else:
            fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
            datefmt = "%Y-%m-%d %H:%M:%S"
            kwargs["json_ensure_ascii"] = False
            super().__init__(fmt, datefmt, *args, **kwargs)
        self.timestamp_format = timestamp_format
        self.include_component = include_component
        self.sanitize_sensitive_data = sanitize_sensitive_data
        self.exc_info_style = exc_info_style

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        if jsonlogger is None:
            return
        super().add_fields(log_record, record, message_dict)
        if "user_visible" in log_record:
            del log_record["user_visible"]
        for old, new in self._FIELD_RENAME_MAP.items():
            if old in log_record:
                log_record[new] = log_record.pop(old)
        if "timestamp" in log_record and self.timestamp_format == "text":
            ts = log_record["timestamp"]
            if isinstance(ts, str):
                try:
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    log_record["timestamp"] = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                except (ValueError, TypeError):
                    log_record["timestamp"] = ts  # 解析失败保留原 ISO 字符串
        ordered: OrderedDict = OrderedDict()
        if "timestamp" in log_record:
            ordered["timestamp"] = log_record["timestamp"]
        ordered["process"] = record.process
        if "level" in log_record:
            ordered["level"] = log_record["level"]
        user_tag = getattr(record, "user_tag", None)
        if user_tag:
            ordered["user_tag"] = user_tag
        ordered["user_id"] = getattr(record, "user_id", None)
        ordered["domain_id"] = getattr(record, "domain_id", None)
        ordered["app_id"] = getattr(record, "app_id", None)
        if "logger" in log_record:
            ordered["logger"] = log_record["logger"]
        ordered["lineno"] = record.lineno
        if "message" in log_record:
            ordered["message"] = log_record["message"]
        for k, v in log_record.items():
            if k not in ordered:
                ordered[k] = v
        log_record.clear()
        log_record.update(ordered)
        if self.include_component:
            log_record["component"] = _log_component_from_logger_name(record.name)
        uv = getattr(record, "user_visible", None)
        if uv in ("critical", "progress"):
            log_record["user_visible"] = uv
        elif uv is not None:
            logger.warning("无效 user_visible 值: '%s'，期望 'critical'/'progress'", uv)
        if self.sanitize_sensitive_data and "message" in log_record:
            log_record["message"] = _sanitize_log_text(log_record["message"])
        if "exc_info" in log_record and log_record["exc_info"] and self.exc_info_style == "simple":
            ei = log_record["exc_info"]
            if isinstance(ei, tuple) and len(ei) >= 2:
                log_record["exc_info"] = f"{ei[0].__name__}: {ei[1]}"


class LoggingTagConfig:
    """用户可见性 Tag 配置。env > config.yaml > default(True)。"""

    user_visible: bool = True
    user_progress_visible: bool = True
    _env_prefix: str = "JIUWENSWARM_LOG_"
    _skip_env_load: bool = False

    def __init__(self, skip_env_load: bool = False):
        self._skip_env_load = skip_env_load
        self._load_config()

    def _load_config(self) -> None:
        if self._skip_env_load:
            self.user_visible = True
            self.user_progress_visible = True
            return
        base_uv = True
        base_upv = True
        self.user_visible = self._load_from_env("USER_VISIBLE", base_uv)
        self.user_progress_visible = self._load_from_env("USER_PROGRESS_VISIBLE", base_upv)
        if os.getenv(f"{self._env_prefix}USER_VISIBLE") is None:
            self.user_visible = self._load_from_yaml("user_visible", self.user_visible)
        if os.getenv(f"{self._env_prefix}USER_PROGRESS_VISIBLE") is None:
            self.user_progress_visible = self._load_from_yaml("user_progress_visible", self.user_progress_visible)

    def _load_from_env(self, key: str, default: bool) -> bool:
        raw = os.getenv(f"{self._env_prefix}{key}")
        if raw is None:
            return default
        v = raw.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
        logger.warning("无效的 env %s: '%s'，使用默认 %s", f"{self._env_prefix}{key}", raw, default)
        return default

    @staticmethod
    def _load_from_yaml(key: str, default: bool) -> bool:
        try:
            cfg = _load_logging_config_from_yaml()
            if not cfg:
                return default
            tags = cfg.get("tags")
            v = tags.get(key) if isinstance(tags, dict) else None
            if v is None:
                return default
            if isinstance(v, bool):
                return v
            logger.warning("config.yaml logging.tags.%s 非布尔: %s，使用默认 %s", key, v, default)
            return default
        except Exception as e:
            logger.warning("加载 logging.tags.%s 失败: %s，使用默认 %s", key, e, default)
            return default

    def is_user_visible_enabled(self) -> bool:
        return self.user_visible

    def is_user_progress_visible_enabled(self) -> bool:
        return self.user_progress_visible


class UserVisibleTagFilter(logging.Filter):
    """按 record.user_visible 设 record.user_tag（[USER]/[USER_PROGRESS]/""）。从不丢日志，幂等。"""

    def __init__(self, tag_config: Optional[LoggingTagConfig] = None):
        super().__init__()
        self.tag_config = tag_config if tag_config is not None else LoggingTagConfig()

    def filter(self, record: logging.LogRecord) -> bool:
        uv = getattr(record, "user_visible", None)
        if uv == "critical" and self.tag_config.is_user_visible_enabled():
            record.user_tag = "[USER] "
        elif uv == "progress" and self.tag_config.is_user_progress_visible_enabled():
            record.user_tag = "[USER_PROGRESS] "
        else:
            record.user_tag = ""
        return True


class IdentityFieldFilter(logging.Filter):
    """从 IdentityStore（contextvar）读身份，塞 record.user_id/domain_id/app_id。始终放行。

    import 链失败时身份降级为 null——日志 filter 绝不因自身 import 失败而中断日志
    （Python logging 不兜 filter 异常，filter 抛会透传到 logger.* 调用方）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from jiuwenswarm.extensions.identity_provider import IdentityStore
            identity = IdentityStore.get_identity()
        except Exception:
            identity = None
        if identity is not None:
            record.user_id = identity.user_id
            record.domain_id = identity.domain_id
            record.app_id = identity.app_id
        else:
            record.user_id = None
            record.domain_id = None
            record.app_id = None
        return True


class IdentityTextFormatter(logging.Formatter):
    """文本 Formatter：构建 record.identity = " user_id=.. domain_id=.. app_id=.. "（null 输出 "null"）。"""

    def format(self, record: logging.LogRecord) -> str:
        parts = []
        for field in ("user_id", "domain_id", "app_id"):
            v = getattr(record, field, None)
            parts.append(f"{field}={v if v is not None else 'null'}")
        record.identity = " " + " ".join(parts) + " "
        return super().format(record)


_log_queue: _queue.SimpleQueue | None = None
_log_listener: QueueListener | None = None
# respect_handler_level is Python 3.12+; cache once for setup_logger.
_SUPPORTS_RESPECT_HANDLER_LEVEL: bool = sys.version_info >= (3, 12)


def _iter_log_output_handlers() -> list[logging.Handler]:
    """Return handlers that actually write logs (listener targets when queued)."""
    if _log_listener is not None:
        return list(_log_listener.handlers)
    return list(logging.getLogger("jiuwenswarm").handlers)


def flush_queued_logs() -> None:
    """Block until queued log records are written (tests / graceful drain).

    Stops the ``QueueListener`` (which drains the queue through the sentinel),
    flushes target handlers, then restarts the listener so logging keeps working.
    """
    global _log_listener
    listener = _log_listener
    if listener is None or _log_queue is None:
        return
    targets = list(listener.handlers)
    listener.stop()
    for handler in targets:
        try:
            handler.flush()
        except OSError as exc:
            logger.warning(
                "[jiuwenswarm] log handler flush failed during drain: %s",
                exc,
            )
    if _SUPPORTS_RESPECT_HANDLER_LEVEL:
        _log_listener = QueueListener(
            _log_queue,
            *targets,
            respect_handler_level=True,
        )
    else:
        _log_listener = QueueListener(_log_queue, *targets)
    _log_listener.start()


def setup_logger(log_level: Optional[str] = None) -> logging.Logger:
    """配置 ``jiuwenswarm`` 根日志：控制台 + 分组件文件 + 汇总 full.log。

    扩展功能（迁移自 enterprise_dev）：
    - format（text/json/dual）：env JIUWENSWARM_LOG_FORMAT 或 config.yaml logging.format
    - console_enabled/file_enabled：输出开关
    - JSON：JsonUserVisibleFormatter（.json 文件）
    - 身份字段：IdentityFieldFilter（每 handler）
    - user_visible Tag：UserVisibleTagFilter（text/dual）
    保留 dev-stable 既有的 SensitiveDataFilter + install_source_record_masking 双层脱敏。

    File/console handlers are served by a ``QueueListener`` thread so emit/flush
    I/O does not block the asyncio event loop. Source-record masking still runs
    on the caller thread (covers third-party loggers outside this root).
    """
    global _log_queue, _log_listener

    # Stop previous listener (supports repeated setup_logger calls).
    if _log_listener is not None:
        _log_listener.stop()
        for old_h in _log_listener.handlers:
            old_h.close()
        _log_listener = None
    _log_queue = _queue.SimpleQueue()
    listener_targets: list[logging.Handler] = []

    log_root_path = os.getenv("LOG_ROOT_PATH", "").strip()
    logs_root = Path(log_root_path).expanduser().resolve() if log_root_path else get_logs_dir()
    logs_root.mkdir(parents=True, exist_ok=True)

    levels = _resolve_logging_levels(log_level)
    log_format = _resolve_logging_format()
    output_switches = _resolve_output_switches()
    console_enabled = output_switches["console_enabled"]
    file_enabled = output_switches["file_enabled"]

    root = logging.getLogger("jiuwenswarm")
    root.setLevel(levels.logger)
    root.propagate = False
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    json_config = _resolve_json_config() if log_format in ("json", "dual") else {}
    # 文本格式串（含 process/identity/user_tag/lineno）
    text_fmt = (
        "%(asctime)s.%(msecs)03d [%(process)d] %(levelname)s "
        "%(identity)s%(user_tag)s%(name)s:%(lineno)d: %(message)s"
    )

    if log_format in ("json", "dual"):
        json_formatter = JsonUserVisibleFormatter(
            timestamp_format=json_config.get("timestamp_format", "text"),
            include_component=json_config.get("include_component", True),
            sanitize_sensitive_data=json_config.get("sanitize_sensitive_data", True),
            exc_info_style=json_config.get("exc_info_style", "simple"),
        )
        text_formatter = IdentityTextFormatter(fmt=text_fmt, datefmt="%Y-%m-%d %H:%M:%S")
    else:
        text_formatter = IdentityTextFormatter(fmt=text_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        json_formatter = None

    privacy_filter = SensitiveDataFilter()
    tag_config = LoggingTagConfig() if log_format in ("text", "dual", "json") else None
    identity_filter = IdentityFieldFilter()

    def _add_rotating(
        filename: str,
        level: int,
        name_filter: Optional[logging.Filter] = None,
        custom_formatter: Optional[logging.Formatter] = None,
        use_json: bool = False,
    ) -> None:
        if not file_enabled:
            return
        h = SafeRotatingFileHandler(
            filename=logs_root / filename,
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(level)
        if custom_formatter is not None:
            h.setFormatter(custom_formatter)
        elif use_json and json_formatter is not None:
            h.setFormatter(json_formatter)
        else:
            h.setFormatter(text_formatter)
        h.addFilter(privacy_filter)
        h.addFilter(identity_filter)
        if tag_config:
            h.addFilter(UserVisibleTagFilter(tag_config))
        if name_filter is not None:
            h.addFilter(name_filter)
        listener_targets.append(h)

    def _component_files(ext: str, use_json: bool) -> None:
        _add_rotating(f"gateway.{ext}", levels.gateway, _ComponentNameFilter("gateway"), use_json=use_json)
        _add_rotating(f"channel.{ext}", levels.channel, _ComponentNameFilter("channel"), use_json=use_json)
        _add_rotating(f"agent_server.{ext}", levels.agent_server,
                      _CompositeFilter([_ComponentNameFilter("agent_server"), _ComponentNameFilter("permissions")]),
                      use_json=use_json)
        _add_rotating(f"full.{ext}", levels.full, None, use_json=use_json)

    if log_format == "text":
        _component_files("log", use_json=False)
    elif log_format == "json":
        _component_files("json", use_json=True)
    elif log_format == "dual":
        _component_files("log", use_json=False)
        _component_files("json", use_json=True)

    # permissions.log 始终用 JsonOnlyFormatter
    _add_rotating("permissions.log", levels.agent_server,
                  _ComponentNameFilter("permissions"), JsonOnlyFormatter())

    # 控制台
    if console_enabled:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(levels.console)
        if log_format == "json":
            stream_handler.setFormatter(json_formatter)
        else:
            stream_handler.setFormatter(text_formatter)
        if tag_config:
            stream_handler.addFilter(UserVisibleTagFilter(tag_config))
        stream_handler.addFilter(identity_filter)
        stream_handler.addFilter(privacy_filter)
        listener_targets.append(stream_handler)

    # QueueHandler keeps file I/O / flush off the asyncio event-loop thread.
    if listener_targets:
        queue_handler = QueueHandler(_log_queue)
        queue_handler.setLevel(logging.NOTSET)
        root.addHandler(queue_handler)
        if _SUPPORTS_RESPECT_HANDLER_LEVEL:
            _log_listener = QueueListener(
                _log_queue,
                *listener_targets,
                respect_handler_level=True,
            )
        else:
            for target in listener_targets:
                target.addFilter(lambda record, handler=target: record.levelno >= handler.level)
            _log_listener = QueueListener(_log_queue, *listener_targets)
        _log_listener.start()

    # 保留 dev-stable 既有的源头脱敏（与 handler 层 SensitiveDataFilter 双保险）
    install_source_record_masking()
    return root


def wait_for_tcp_port(
    host: str,
    port: int,
    *,
    timeout: float = 15.0,
    max_attempts: int | None = None,
    initial_delay: float = 0.1,
    max_delay: float = 2.0,
    connect_timeout: float = 1.0,
    target_state: str = "connected",
) -> bool:
    """Wait for a TCP port to reach the desired state with exponential backoff.

    Args:
        host: Target host.
        port: Target port.
        timeout: Total wall-clock timeout in seconds.
        max_attempts: Maximum number of connection attempts (None = unlimited within timeout).
        initial_delay: Initial sleep interval between attempts (doubles each round).
        max_delay: Maximum sleep interval cap.
        connect_timeout: Per-attempt socket connect timeout.
        target_state: ``"connected"`` — wait until the port accepts a connection;
                      ``"disconnected"`` — wait until the port refuses connections.

    Returns:
        ``True`` if the target state is reached within limits, ``False`` otherwise.
    """
    deadline = time.monotonic() + timeout
    delay = initial_delay
    attempt = 0

    while time.monotonic() < deadline:
        if max_attempts is not None and attempt >= max_attempts:
            return False
        attempt += 1

        try:
            with socket.create_connection((host, port), timeout=connect_timeout):
                if target_state == "connected":
                    return True
        except OSError:
            if target_state == "disconnected":
                return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, max_delay)

    return False


def wait_for_pid_exit(pid: int, timeout: float = 60.0) -> None:
    """Wait for a process to exit, with a timeout and warning on failure.

    On Windows, ``os.kill(pid, 0)`` only checks PID existence via
    ``OpenProcess`` — it does NOT check whether the process is still running.
    We use ``WaitForSingleObject`` with a zero timeout instead, which
    reliably detects exited processes.
    """
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        _synchronize = 0x00100000
        _kernel32 = ctypes.windll.kernel32
        _kernel32.OpenProcess.restype = ctypes.c_void_p
        _kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        _kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        while time.monotonic() < deadline:
            handle = _kernel32.OpenProcess(_synchronize, False, pid)
            if not handle:
                return
            exited = _kernel32.WaitForSingleObject(handle, 0) == 0
            _kernel32.CloseHandle(handle)
            if exited:
                return
            time.sleep(0.5)
    else:
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.5)
    logger.warning("process %d did not exit within %.1f seconds", pid, timeout)



_FILE_HANDLER_LEVEL_MAP: dict[str, str] = {
    "gateway.log": "gateway",
    "gateway.json": "gateway",
    "channel.log": "channel",
    "channel.json": "channel",
    "agent_server.log": "agent_server",
    "agent_server.json": "agent_server",
    "full.log": "full",
    "full.json": "full",
    "permissions.log": "agent_server",
}


def update_log_levels(
    log_level: Optional[str] = None,
    *,
    console_level: Optional[str] = None,
    gateway: Optional[str] = None,
    channel: Optional[str] = None,
    agent_server: Optional[str] = None,
    full: Optional[str] = None,
) -> logging.Logger:
    """运行时动态更新 ``jiuwenswarm`` 根日志及各 handler 的级别，无需重建 handler。"""
    levels = _resolve_logging_levels(log_level)

    if console_level is not None:
        levels = replace(levels, console=_parse_log_level(console_level, levels.console))
    if gateway is not None:
        levels = replace(levels, gateway=_parse_log_level(gateway, levels.gateway))
    if channel is not None:
        levels = replace(levels, channel=_parse_log_level(channel, levels.channel))
    if agent_server is not None:
        levels = replace(levels, agent_server=_parse_log_level(agent_server, levels.agent_server))
    if full is not None:
        levels = replace(levels, full=_parse_log_level(full, levels.full))

    logger_level = min(levels.gateway, levels.channel, levels.agent_server, levels.full)
    levels = replace(levels, logger=logger_level)

    root = logging.getLogger("jiuwenswarm")
    root.setLevel(levels.logger)

    # File/console handlers live on the QueueListener after setup_logger.
    for h in _iter_log_output_handlers():
        if isinstance(h, SafeRotatingFileHandler):
            fname = Path(h.baseFilename).name
            attr = _FILE_HANDLER_LEVEL_MAP.get(fname)
            if attr is not None:
                h.setLevel(getattr(levels, attr))
        elif isinstance(h, logging.StreamHandler):
            h.setLevel(levels.console)

    return root


_LOGGING_CONFIG_TABLE = "logging_config"


def _logging_config_row_to_dict(obj: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return {
            "level": obj.get("level", "INFO"),
            "console_level": obj.get("console_level"),
            "gateway": obj.get("gateway"),
            "channel": obj.get("channel"),
            "agent_server": obj.get("agent_server"),
            "full": obj.get("full"),
        }
    return {
        "level": getattr(obj, "level", "INFO"),
        "console_level": getattr(obj, "console_level", None),
        "gateway": getattr(obj, "gateway", None),
        "channel": getattr(obj, "channel", None),
        "agent_server": getattr(obj, "agent_server", None),
        "full": getattr(obj, "full", None),
    }


def apply_logging_config_payload(payload: dict[str, Any] | None) -> None:
    """将 DB 行 / WS payload 转为 :func:`update_log_levels` 调用。"""
    if not payload or payload.get("op") == "delete":
        update_log_levels()
        return

    kwargs: dict[str, Any] = {}
    if payload.get("level") is not None:
        kwargs["log_level"] = str(payload["level"])
    for key in ("console_level", "gateway", "channel", "agent_server", "full"):
        if payload.get(key) is not None:
            kwargs[key] = str(payload[key])
    update_log_levels(**kwargs)


async def reload_logging_levels_from_gateway_db() -> None:
    """从 Gateway 库加载 ``logging_config`` 并刷新**本进程**日志级别。"""
    if not os.getenv("AGENT_RUNTIME", "").strip():
        update_log_levels()
        return
    try:
        from jiuwenswarm.server.runtime.enterprise_config import gateway_db

        jid = gateway_db.resolve_jiuwenclaw_id()
        if not jid:
            update_log_levels()
            return

        rows = await gateway_db.list_records(
            _LOGGING_CONFIG_TABLE,
            filters={"jiuwenclaw_id": jid},
        )
        row = rows[0] if rows else None
        apply_logging_config_payload(
            _logging_config_row_to_dict(row) if row is not None else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[logging_config_db] logging_config read failed: %s",
            exc,
            exc_info=True,
        )
        update_log_levels()



class AsyncLRUCache:
    """带过期时间的 LRU 缓存（异步并发安全）."""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 600) -> None:
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """获取缓存值，如果不存在或已过期则返回 None."""
        async with self._lock:
            if key not in self._cache:
                return None

            value, timestamp = self._cache[key]
            if time.time() - timestamp > self._ttl:
                self._cache.pop(key, None)
                return None

            self._cache.move_to_end(key)
            return value

    async def put(self, key: str, value: Any) -> None:
        """存入缓存值，如果超过容量则淘汰最久未使用的."""
        async with self._lock:
            if key in self._cache:
                self._cache.pop(key)
            elif len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = (value, time.time())

    async def remove(self, key: str) -> None:
        """删除缓存项."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """清空缓存."""
        async with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def values(self) -> list[Any]:
        """返回当前缓存值快照（同步，不做 TTL 清理）."""
        return [value for value, _ts in self._cache.values()]

    async def keys(self) -> list[str]:
        async with self._lock:
            now = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self._cache.items()
                if now - timestamp > self._ttl
            ]
            for key in expired_keys:
                del self._cache[key]
            return list(self._cache.keys())


logger = logging.getLogger(__name__)
setup_logger()


_TOOL_ARGS_LOG_MAX_DEFAULT = 480


def _truncate_tool_args_log_fragment(text: str, *, full_detail: bool) -> str:
    if full_detail or len(text) <= _TOOL_ARGS_LOG_MAX_DEFAULT:
        return text
    return text[:_TOOL_ARGS_LOG_MAX_DEFAULT] + "..."


def _log_tool_args_repair_stage(
    *,
    stage: str,
    before_raw: str,
    outcome: Literal["success", "failed"],
    after_dict: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    full_detail = logger.isEnabledFor(logging.DEBUG)
    before_shown = _truncate_tool_args_log_fragment(before_raw, full_detail=full_detail)
    if outcome == "success":
        after_raw = (
            json.dumps(after_dict, ensure_ascii=False)
            if isinstance(after_dict, dict)
            else ""
        )
        after_shown = _truncate_tool_args_log_fragment(after_raw, full_detail=full_detail)
        logger.info(
            "[fix_json_arguments] stage=%s outcome=success before=%s after=%s",
            stage,
            before_shown,
            after_shown,
        )
    else:
        err_shown = _truncate_tool_args_log_fragment(error or "", full_detail=full_detail)
        logger.warning(
            "[fix_json_arguments] stage=%s outcome=failed before=%s error=%s",
            stage,
            before_shown,
            err_shown,
        )


def _fix_missing_quotes(json_str: str) -> str:
    s = json_str.strip()

    s = re.sub(
        r':\s+([A-Za-z]:/[^\{\[]*?)(?=\s*[,\}\]])',
        lambda m: f': "{m.group(1)}"',
        s
    )

    s = re.sub(
        r':\s+(?!"|true|false|null|\d+|{|\[|:|"|[A-Za-z]:/)([^\s,\}\[\]""]+?)(?=\s*[,}\]])',
        lambda m: f': "{m.group(1)}"',
        s
    )

    s = re.sub(
        r'{\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r'{"\1":',
        s
    )

    return s


def fix_json_arguments(arguments: str | dict) -> str | dict:
    if not isinstance(arguments, str):
        return arguments

    s = arguments.strip()

    if not s:
        return {}

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    full_detail = logger.isEnabledFor(logging.DEBUG)

    try:
        import json_repair

        repaired = json_repair.loads(s)
    except Exception as exc:
        _log_tool_args_repair_stage(
            stage="json_repair",
            before_raw=s,
            outcome="failed",
            error=str(exc),
        )
    else:
        if isinstance(repaired, dict):
            _log_tool_args_repair_stage(
                stage="json_repair",
                before_raw=s,
                outcome="success",
                after_dict=repaired,
            )
            return repaired
        _log_tool_args_repair_stage(
            stage="json_repair",
            before_raw=s,
            outcome="failed",
            error=f"repaired_not_object:{type(repaired).__name__}",
        )

    fixed = _fix_missing_quotes(s)
    if fixed != s:
        try:
            result = json.loads(fixed)
        except json.JSONDecodeError as exc:
            _log_tool_args_repair_stage(
                stage="rule_fix",
                before_raw=s,
                outcome="failed",
                error=str(exc),
            )
        else:
            _log_tool_args_repair_stage(
                stage="rule_fix",
                before_raw=s,
                outcome="success",
                after_dict=result,
            )
            return result
    else:
        _log_tool_args_repair_stage(
            stage="rule_fix",
            before_raw=s,
            outcome="failed",
            error="no_structural_change_from_rules",
        )

    before_final = _truncate_tool_args_log_fragment(s, full_detail=full_detail)
    logger.warning(
        "[fix_json_arguments] outcome=failed_all_stages before=%s error=all_repair_attempts_exhausted",
        before_final,
    )
    return s


class AsyncLRUCache:
    """带可选过期时间与容量上限的 LRU 缓存（异步并发安全）.

    ``max_size=None`` / ``ttl_seconds=None`` 表示不启用对应限制。
    """

    def __init__(
        self,
        max_size: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._cache: OrderedDict[Hashable, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def _is_expired(self, timestamp: float) -> bool:
        if self._ttl is None:
            return False
        return time.time() - timestamp > self._ttl

    async def get(self, key: Hashable) -> Any | None:
        """获取缓存值，如果不存在或已过期则返回 None.

        命中时会刷新访问时间（滑动过期：自最后一次 get/put 起算 ttl）。
        """
        async with self._lock:
            if key not in self._cache:
                return None

            value, timestamp = self._cache[key]
            if self._is_expired(timestamp):
                self._cache.pop(key, None)
                return None

            # 刷新访问时间并移动到末尾（最近使用）
            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
            return value

    async def put(self, key: Hashable, value: Any) -> None:
        """存入缓存值，如果超过容量则淘汰最久未使用的."""
        async with self._lock:
            if key in self._cache:
                self._cache.pop(key)
            elif self._max_size is not None and len(self._cache) >= self._max_size:
                # 淘汰最久未使用的（头部）
                self._cache.popitem(last=False)

            self._cache[key] = (value, time.time())

    async def touch_if_same(self, key: Hashable, value: Any) -> bool:
        """若 key 存在且缓存值与 value 为同一对象，则刷新访问时间.

        用于请求结束时续约 TTL，避免无条件 put 用旧实例覆盖并发创建的新实例。
        同一对象仍挂在 cache 上时，即使时间戳已过期也会续期（执行期间无 get 触达）。
        """
        async with self._lock:
            if key not in self._cache:
                return False

            cached_value, _timestamp = self._cache[key]
            if cached_value is not value:
                return False

            self._cache[key] = (value, time.time())
            self._cache.move_to_end(key)
            return True

    async def remove(self, key: Hashable) -> None:
        """删除缓存项."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """清空缓存."""
        async with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def snapshot_values_nowait(self) -> list[Any]:
        """Return cached values for sync callers (best-effort, no async lock).

        Entries are stored as ``(value, timestamp)``; malformed entries are skipped.
        """
        values: list[Any] = []
        for entry in self._cache.values():
            if not isinstance(entry, tuple) or len(entry) < 1:
                continue
            values.append(entry[0])
        return values

    async def keys(self) -> list[Hashable]:
        async with self._lock:
            if self._ttl is not None:
                expired_keys = [
                    key
                    for key, (_, timestamp) in self._cache.items()
                    if self._is_expired(timestamp)
                ]
                for key in expired_keys:
                    del self._cache[key]
            return list(self._cache.keys())


def normalize_tenant_scope_id(value: str | None, *, default: str = "default") -> str:
    """Normalize and validate a tenant scope ID (service_id / agent_id)."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if "__" in text:
        raise ValueError(f"tenant scope ID must not contain '__': {text!r}")
    return text
