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

import ctypes
import json
import os
import re
import sys
import datetime
import shutil
import socket
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal, Optional
import logging
from logging.handlers import BaseRotatingHandler
from ruamel.yaml import YAML

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


def _load_logging_config_from_yaml() -> dict[str, Any]:
    """读取 ~/.jiuwenswarm/config/config.yaml 中的 logging 段（无则空）。"""
    try:
        cf = get_config_file()
        if not cf.exists():
            return {}
        rt = YAML()
        with open(cf, "r", encoding="utf-8") as f:
            data = rt.load(f) or {}
        raw = data.get("logging")
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
    2. JIUWENSWARM_DATA_DIR environment variable (for multi-instance isolation)
    3. get_user_home() / ".jiuwenswarm" (default instance)

    Also performs one-time migration from ~/.jiuwenclaw/ to ~/.jiuwenswarm/ if needed.
    """
    global _workspace_base_dir
    if _workspace_base_dir is not None:
        return _workspace_base_dir
    env_workspace = os.getenv("JIUWENSWARM_DATA_DIR")
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
    """确定初始化使用的语言：显式参数优先，否则读已复制的 config，默认 zh。"""
    if explicit is not None:
        lang = str(explicit).strip().lower()
        return lang if lang in ("zh", "en") else "zh"
    if config_yaml_dest.exists():
        try:
            rt = YAML()
            with open(config_yaml_dest, "r", encoding="utf-8") as f:
                data = rt.load(f) or {}
            lang = str(data.get("preferred_language") or "zh").strip().lower()
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
    builtin_skills_dir = get_builtin_skills_dir()
    if not builtin_skills_dir.exists():
        return set()
    return {item.name for item in builtin_skills_dir.iterdir() if item.is_dir()}


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
        with TrackCopyDiff(
            dest=config_yaml_dest,
            is_file=True,
            cumulative=cumulative_diff,
            overwrite=overwrite,
        ):
            shutil.copy2(config_yaml_src, config_yaml_dest)

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

    # ----- copy runtime dirs (new layout) -----
    agent_root = workspace_dir / "agent"
    agent_sessions = agent_root / "sessions"
    (agent_root / ".checkpoint").mkdir(parents=True, exist_ok=True)
    (agent_root / ".logs").mkdir(parents=True, exist_ok=True)

    # ----- DeepAgent workspace (standard DeepAgents schema) -----
    deepagent_workspace = agent_root / "workspace"
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

    from jiuwenswarm.common.config import migrate_config_from_template, set_preferred_language_in_config_file

    migrate_config_from_template(config_yaml_src, config_yaml_dest)
    set_preferred_language_in_config_file(config_yaml_dest, resolved_lang)

    # ----- 默认安装内置技能: skill-creator 和 swarmskill-creator -----
    _install_default_builtin_skills(
        builtin_dir=get_builtin_skills_dir(),
        user_skills_dir=agent_skills,
        overwrite=overwrite,
        cumulative_diff=cumulative_diff,
    )

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


def _resolve_paths() -> None:
    """Resolve and cache all paths."""
    global _initialized, _config_dir, _workspace_dir, _root_dir

    if _initialized:
        return

    workspace_dir = get_user_workspace_dir()

    # Migrate from legacy jiuwenclaw_workspace directory name to workspace
    _migrate_jiuwenclaw_workspace_to_workspace(workspace_dir)

    # 优先使用已初始化的用户工作区 (~/.jiuwenswarm)，
    # 保证源码运行与安装包运行后的读写路径完全一致。
    user_config_dir = workspace_dir / "config"
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
        Path to agent workspace: ~/.jiuwenswarm/agent/workspace
    """
    return get_agent_root_dir() / "workspace"


def get_prompt_attachment_dir() -> Path:
    """Get the jiuwenswarm prompt attachment directory path."""

    return get_agent_workspace_dir() / "prompt_attachment"


def get_agent_root_dir() -> Path:
    return get_user_workspace_dir() / "agent"


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


def get_interactions_dir() -> Path:
    """Get the interactions directory for pending interaction contexts.

    Returns:
        Path to interactions directory: {workspace}/agent/workspace/interactions
    """
    return get_agent_workspace_dir() / "interactions"


def get_cron_jobs_path() -> Path:
    """Canonical path for cron_jobs.json shared by gateway and agentserver."""
    return get_user_workspace_dir() / "agent" / "home" / "cron_jobs.json"


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
    return get_agent_root_dir() / "sessions"


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


_legacy_migration_done: bool = False


def _migrate_legacy_checkpoint_and_logs() -> None:
    """One-time migration: move ~/.jiuwenswarm/.checkpoint and .logs to ~/.jiuwenswarm/agent/."""
    global _legacy_migration_done
    if _legacy_migration_done:
        return
    _legacy_migration_done = True

    workspace = get_user_workspace_dir()
    agent_root = workspace / "agent"

    for name in (".checkpoint", ".logs"):
        legacy = workspace / name
        new_path = agent_root / name
        if legacy.exists() and not new_path.exists():
            agent_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(new_path))


def get_checkpoint_dir() -> Path:
    _migrate_legacy_checkpoint_and_logs()
    return get_agent_root_dir() / ".checkpoint"


def get_logs_dir() -> Path:
    _migrate_legacy_checkpoint_and_logs()
    return get_agent_root_dir() / ".logs"


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
# 1) 敏感键名；2) 分隔符及两侧空白（: 或 =）；3/4) 可选引号（当前替换逻辑未直接使用）
_KV_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|user[_-]?id|userid)"
    r"(?![A-Za-z0-9])(\s*[:=]\s*)([\"']?)[^,\s\"'\]\}]+([\"']?)"
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
_BEARER_SENSITIVE_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9\-._~+/]+=*")
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


def _sanitize_log_text(text: str) -> str:
    if not text:
        return text

    masked = text
    masked = _DATA_IMAGE_PATTERN.sub("data:image/*;base64,******", masked)
    masked = _KV_SENSITIVE_PATTERN.sub(r"\1\2" f"{_SENSITIVE_MASK}", masked)
    masked = _NAMED_SENSITIVE_KV_PATTERN.sub(r"\1\2" f"{_SENSITIVE_MASK}" r"\2", masked)
    masked = _BEARER_SENSITIVE_PATTERN.sub(r"\1" f"{_SENSITIVE_MASK}", masked)
    for pattern in _SENSITIVE_PATTERNS:
        masked = pattern.sub(_SENSITIVE_MASK, masked)
    return masked


class SensitiveDataFilter(logging.Filter):
    """Mask sensitive data in all log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            record.msg = _sanitize_log_text(message)
            record.args = ()
        except Exception:
            # Never block logging because of desensitization failure.
            pass
        return True


class JsonOnlyFormatter(logging.Formatter):
    """只输出message内容，不添加任何前缀（时间戳、级别、logger名）"""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def setup_logger(log_level: Optional[str] = None) -> logging.Logger:
    """配置 ``jiuwenswarm`` 根日志：控制台 + 分组件文件 + 汇总 full.log。

    各模块应使用 ``logging.getLogger(__name__)``，分文件规则：
    - ``jiuwenswarm.channel.*`` → channel.log
    - ``jiuwenswarm.agents.*`` 或 ``jiuwenswarm.server.*`` → agent_server.log
    - 其余 ``jiuwenswarm.*``（含 ``jiuwenswarm.app``、gateway、evolution、utils 等）→ gateway.log

    所有分类日志同时写入 ``full.log``。输出目录：``~/.jiuwenswarm/agent/.logs/``。

    级别由 ``config.yaml`` 的 ``logging`` 段控制；环境变量 ``LOG_LEVEL`` 仅覆盖**控制台**级别
    （``log_level`` 参数为 ``None`` 时）。若传入 ``log_level``（如单测），则控制台与各文件级别均为该值。
    """
    logs_root = get_logs_dir()
    logs_root.mkdir(parents=True, exist_ok=True)

    levels = _resolve_logging_levels(log_level)

    root = logging.getLogger("jiuwenswarm")
    root.setLevel(levels.logger)
    root.propagate = False
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    privacy_filter = SensitiveDataFilter()

    def _add_rotating(
        filename: str,
        level: int,
        name_filter: Optional[_ComponentNameFilter] = None,
        custom_formatter: Optional[logging.Formatter] = None,
    ) -> None:
        h = SafeRotatingFileHandler(
            filename=logs_root / filename,
            maxBytes=_LOG_FILE_MAX_BYTES,
            backupCount=_LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(level)
        h.setFormatter(custom_formatter if custom_formatter is not None else formatter)
        h.addFilter(privacy_filter)
        if name_filter is not None:
            h.addFilter(name_filter)
        root.addHandler(h)

    _add_rotating("gateway.log", levels.gateway, _ComponentNameFilter("gateway"))
    _add_rotating("channel.log", levels.channel, _ComponentNameFilter("channel"))
    _add_rotating("agent_server.log", levels.agent_server,
        _CompositeFilter([_ComponentNameFilter("agent_server"), _ComponentNameFilter("permissions")]))
    _add_rotating("full.log", levels.full, None)
    json_formatter = JsonOnlyFormatter()
    _add_rotating("permissions.log", levels.agent_server, _ComponentNameFilter("permissions"), json_formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(levels.console)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(privacy_filter)
    root.addHandler(stream_handler)
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


logger = logging.getLogger(__name__)
setup_logger()
