# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Path management for JiuWenClaw.

Runtime layout:
- ~/.jiuwenclaw/config/config.yaml
- ~/.jiuwenclaw/config/.env
- ~/.jiuwenclaw/agent/home
- ~/.jiuwenclaw/agent/memory
- ~/.jiuwenclaw/agent/skills
- ~/.jiuwenclaw/agent/sessions
- ~/.jiuwenclaw/agent/workspace（运行时文件与 agent-data.json）
- ~/.jiuwenclaw/.checkpoint
- ~/.jiuwenclaw/.logs（gateway.log / channel.log / agent_server.log / full.log）

内置模板位于包内 ``jiuwenclaw/resources/``（含 ``agent/`` 下 HEARTBEAT_ZH/EN、PRINCIPLE、TONE 等，以及 ``skills_state.json``）。
"""

import os
import sys

import time
import mimetypes
from pathlib import Path
from dataclasses import dataclass, field

from typing import Any, Literal, Optional
import logging
import shutil
from ruamel.yaml import YAML


_user_home: Path | None = None


def get_user_home() -> Path:
    """Get the current user home directory."""
    global _user_home
    if _user_home is None:
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
    """Get the user workspace directory path (~/.jiuwenclaw or custom path)."""
    return get_user_home() / ".jiuwenclaw"


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
        if (
            "site-packages" in str(site_packages)
            and site_packages in module_file.parents
        ):
            _is_package = True
            return True

    _is_package = False
    return False


def _find_source_root() -> Path:
    """Find the repository root in development mode (contains jiuwenclaw/ package)."""
    current = Path(__file__).resolve().parent.parent
    jw_pkg = current / "jiuwenclaw"
    if (jw_pkg / "resources" / "agent").exists():
        return current
    parent = current.parent
    jw_pkg2 = parent / "jiuwenclaw"
    if (jw_pkg2 / "resources" / "agent").exists():
        return parent
    return current


def _find_package_root() -> Path | None:
    """Best-effort detection of the jiuwenclaw package root.

    In package mode (whl), __file__ is at site-packages/jiuwenclaw/paths.py,
    so parent is site-packages/jiuwenclaw/.
    In editable / source mode, __file__ is at <project>/jiuwenclaw/paths.py,
    so parent is <project>/jiuwenclaw/.
    """
    current = Path(__file__).resolve().parent
    return current


def _resolve_preferred_language(config_yaml_dest: Path, explicit: Optional[str]) -> str:
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


def prompt_preferred_language() -> Optional[Literal["zh", "en"]]:
    """交互询问语言偏好。仅接受明确选项；空输入、不在列表或取消用语 → 返回 None（调用方应终止 init）。"""
    print()
    print("[jiuwenclaw-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenclaw-init]  请选择默认语言 / Choose your default language")
    print("[jiuwenclaw-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenclaw-init]   [1] 中文（简体）")
    print("[jiuwenclaw-init]       → config: preferred_language: zh")
    print(
        "[jiuwenclaw-init]       → 复制 PRINCIPLE_ZH.md / TONE_ZH.md 为 home/PRINCIPLE.md、TONE.md"
    )
    print("[jiuwenclaw-init]   ────────────────────────────────────────────")
    print("[jiuwenclaw-init]   [2] English")
    print("[jiuwenclaw-init]       → config: preferred_language: en")
    print(
        "[jiuwenclaw-init]       → copy PRINCIPLE_EN.md / TONE_EN.md → home/PRINCIPLE.md, TONE.md"
    )
    print("[jiuwenclaw-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[jiuwenclaw-init]  须明确选择：1 / 2 / zh / en（无默认语言）")
    print("[jiuwenclaw-init]  取消：no / n / q / cancel / 取消")
    print("[jiuwenclaw-init] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    raw = (
        input("[jiuwenclaw-init] 请输入选项 (1, 2, zh, en) 或 no 取消: ")
        .strip()
        .lower()
    )
    if raw in ("no", "n", "q", "quit", "cancel", "取消"):
        return None
    if raw in ("1", "zh", "中文", "chinese"):
        return "zh"
    if raw in ("2", "en", "english", "e", "英文"):
        return "en"
    print(
        "[jiuwenclaw-init] 无效选项；未选择有效语言，初始化已取消（与拒绝 yes/no 相同）。"
    )
    return None


def prepare_workspace(overwrite: bool = True, preferred_language: Optional[str] = None):
    package_root = _find_package_root()
    if not package_root:
        raise RuntimeError("package root not found")

    workspace_dir = get_user_workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

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
        shutil.copy2(config_yaml_src, config_yaml_dest)

    resolved_lang = _resolve_preferred_language(config_yaml_dest, preferred_language)

    # ----- 内置模板根目录：<package>/resources（含 agent/、skills_state.json）-----
    template_root = resources_dir
    template_agent_dir = template_root / "agent"
    if not template_agent_dir.is_dir():
        raise RuntimeError(
            f"resources template missing agent dir: {template_agent_dir}"
        )

    # ----- .env: copy from template to config/.env -----
    env_template_src_candidates = [
        resources_dir / ".env.template",
        package_root / ".env.template",
    ]
    env_template_src = next(
        (p for p in env_template_src_candidates if p.exists()), None
    )
    if not env_template_src:
        raise RuntimeError(
            "env template source not found; tried: "
            + ", ".join(str(p) for p in env_template_src_candidates)
        )
    env_dest = workspace_dir / "config" / ".env"
    if overwrite or not env_dest.exists():
        shutil.copy2(env_template_src, env_dest)

    # ----- copy runtime dirs (new layout) -----
    agent_root = workspace_dir / "agent"
    agent_home = agent_root / "home"
    agent_skills = agent_root / "skills"
    agent_memory = agent_root / "memory"
    agent_sessions = agent_root / "sessions"
    (workspace_dir / ".checkpoint").mkdir(parents=True, exist_ok=True)
    (workspace_dir / ".logs").mkdir(parents=True, exist_ok=True)

    template_agent_workspace = template_agent_dir / "workspace"
    template_agent_memory = template_agent_dir / "memory"
    template_agent_skills = template_agent_dir / "skills"

    agent_workspace = agent_root / "workspace"

    def _copy_dir(src_dir: Path, dst_dir: Path) -> None:
        if not src_dir.exists():
            return
        if overwrite and dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        if not dst_dir.exists():
            shutil.copytree(src_dir, dst_dir)
        else:
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    # agent/workspace 可不在仓库中（agent-data.json 由运行时生成）；无模板子目录时建空目录
    if template_agent_workspace.exists():
        _copy_dir(template_agent_workspace, agent_workspace)
    else:
        if overwrite and agent_workspace.exists():
            shutil.rmtree(agent_workspace)
        agent_workspace.mkdir(parents=True, exist_ok=True)
    _copy_dir(template_agent_memory, agent_memory)
    _copy_dir(template_agent_skills, agent_skills)

    # home: 按语言将 PRINCIPLE/TONE/HEARTBEAT 模板复制为无后缀的 .md
    if overwrite and agent_home.exists():
        shutil.rmtree(agent_home)
    agent_home.mkdir(parents=True, exist_ok=True)
    suffix = "_ZH" if resolved_lang == "zh" else "_EN"
    _principle_src = template_agent_dir / f"PRINCIPLE{suffix}.md"
    _tone_src = template_agent_dir / f"TONE{suffix}.md"
    _heartbeat_src = template_agent_dir / f"HEARTBEAT{suffix}.md"
    if _principle_src.exists():
        shutil.copy2(_principle_src, agent_home / "PRINCIPLE.md")
    if _tone_src.exists():
        shutil.copy2(_tone_src, agent_home / "TONE.md")
    if _heartbeat_src.exists():
        shutil.copy2(_heartbeat_src, agent_home / "HEARTBEAT.md")

    # skills state: shipped under resources/
    skills_state_src = template_root / "skills_state.json"
    if skills_state_src.exists():
        agent_skills.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skills_state_src, agent_skills / "skills_state.json")

    # sessions is runtime-only (template may not include it)
    agent_sessions.mkdir(parents=True, exist_ok=True)

    # 与 home 模板语言一致，写回顶层 preferred_language
    from jiuwenclaw.config import set_preferred_language_in_config_file

    set_preferred_language_in_config_file(config_yaml_dest, resolved_lang)


def init_user_workspace(overwrite: bool = True) -> Path | Literal["cancelled"]:
    """Initialize ~/.jiuwenclaw from package or source resources.

    资源布局:
    - 模板配置:   <package_root>/resources/config.yaml
    - .env 模板: <package_root>/resources/.env.template
    - 数据模板:   <package_root>/resources/agent（含 HEARTBEAT_ZH/EN 等）、skills_state.json

    上述内容会被复制到:
    - ~/.jiuwenclaw/config/config.yaml（含 preferred_language）
    - ~/.jiuwenclaw/config/.env
    - ~/.jiuwenclaw/agent/...（home 下 PRINCIPLE.md / TONE.md / HEARTBEAT.md 由所选语言决定）

    交互式 init 会先询问语言；首次启动 app 时非交互 prepare_workspace 则沿用模板 config 中的语言。
    """
    workspace_dir = get_user_workspace_dir()
    if workspace_dir.exists():
        # Warn user about data loss and ask for confirmation
        print(
            "[jiuwenclaw-init] WARNING: This will delete all historical configuration and memory information."
        )
        print("[jiuwenclaw-init] This action cannot be undone.")
        confirmation = (
            input(
                "[jiuwenclaw-init] Do you want to confirm reinitialization? (yes/no): "
            )
            .strip()
            .lower()
        )

        if confirmation not in ("yes", "y"):
            print("[jiuwenclaw-init] Initialization cancelled. Exiting.")
            return "cancelled"

    lang = prompt_preferred_language()
    if lang is None:
        print("[jiuwenclaw-init] Initialization cancelled. Exiting.")
        return "cancelled"
    print(f"[jiuwenclaw-init] 将使用语言 / Language: {lang}")
    prepare_workspace(overwrite, preferred_language=lang)

    return workspace_dir


def _resolve_paths() -> None:
    """Resolve and cache all paths."""
    global _initialized, _config_dir, _workspace_dir, _root_dir

    if _initialized:
        return

    workspace_dir = get_user_workspace_dir()
    # 优先使用已初始化的用户工作区 (~/.jiuwenclaw)，
    # 保证源码运行与安装包运行后的读写路径完全一致。
    user_config_dir = workspace_dir / "config"
    user_workspace_dir = workspace_dir / "agent" / "workspace"
    if user_config_dir.exists():
        _root_dir = workspace_dir
        _config_dir = user_config_dir
        _workspace_dir = user_workspace_dir
    else:
        # 尚未初始化 ~/.jiuwenclaw：从包内 resources 直读配置，工作区指向包内 agent/workspace
        package_root = _find_package_root()
        if package_root and (package_root / "resources" / "config.yaml").exists():
            res = package_root / "resources"
            _root_dir = package_root.parent
            _config_dir = res
            _workspace_dir = res / "agent" / "workspace"
            _workspace_dir.mkdir(parents=True, exist_ok=True)
        else:
            source_root = _find_source_root()
            pkg = source_root / "jiuwenclaw"
            res = pkg / "resources"
            _root_dir = source_root
            _config_dir = (
                res if (res / "config.yaml").exists() else source_root / "config"
            )
            _workspace_dir = res / "agent" / "workspace"
            _workspace_dir.mkdir(parents=True, exist_ok=True)

    _initialized = True


def get_config_dir() -> Path:
    """Get the config directory path."""
    _resolve_paths()
    return _config_dir


def get_workspace_dir() -> Path:
    """Get the workspace directory path."""
    _resolve_paths()
    return _workspace_dir


def get_root_dir() -> Path:
    """Get the root directory path."""
    _resolve_paths()
    return _root_dir


def get_agent_workspace_dir() -> Path:
    """Get the agent workspace directory path."""
    return get_user_workspace_dir() / "agent" / "workspace"


def get_agent_root_dir() -> Path:
    return get_user_workspace_dir() / "agent"


def get_agent_home_dir() -> Path:
    return get_agent_root_dir() / "home"


def get_agent_memory_dir() -> Path:
    return get_agent_root_dir() / "memory"


def get_agent_skills_dir() -> Path:
    return get_agent_root_dir() / "skills"


def get_builtin_skills_dir() -> Path:
    """Get the built-in skills directory from package resources."""
    package_root = _find_package_root()
    return package_root / "resources" / "agent" / "skills"


def get_agent_sessions_dir() -> Path:
    return get_agent_root_dir() / "sessions"


def get_checkpoint_dir() -> Path:
    return get_user_workspace_dir() / ".checkpoint"


def get_logs_dir() -> Path:
    return get_user_workspace_dir() / ".logs"


def get_xy_tmp_dir() -> Path:
    workspace_dir = get_user_workspace_dir()
    xy_tmp_dir = workspace_dir / "tmp" / "xiaoyi"
    xy_tmp_dir.mkdir(parents=True, exist_ok=True)
    return xy_tmp_dir


def get_env_file() -> Path:
    return get_config_dir() / ".env"


def get_config_file() -> Path:
    """Get the config.yaml file path."""
    return get_config_dir() / "config.yaml"


def is_package_installation() -> bool:
    """Check if running from package installation."""
    return _detect_installation_mode()


def setup_logger(log_level: Optional[str] = None) -> logging.Logger:
    """配置 ``jiuwenclaw`` 根日志：控制台 + 分组件文件 + 汇总 full.log。

    各模块应使用 ``logging.getLogger(__name__)``，分文件规则：
    - ``jiuwenclaw.channel.*`` → channel.log
    - ``jiuwenclaw.agentserver.*`` → agent_server.log
    - 其余 ``jiuwenclaw.*``（含 ``jiuwenclaw.app``、gateway、evolution、utils 等）→ gateway.log

    所有分类日志同时写入 ``full.log``。输出目录：``~/.jiuwenclaw/.logs/``。

    级别由 ``config.yaml`` 的 ``logging`` 段控制；环境变量 ``LOG_LEVEL`` 仅覆盖**控制台**级别
    （``log_level`` 参数为 ``None`` 时）。若传入 ``log_level``（如单测），则控制台与各文件级别均为该值。

    Note: 此函数从 jiuwenclaw.logging.setup 迁移，保持向后兼容。
    """
    # 延迟导入避免循环依赖
    from jiuwenclaw.logging.setup import setup_logger as _setup_logger

    return _setup_logger(log_level)


setup_logger()
logger = logging.getLogger(__name__)


# ===========================================================================
# 文件传输工具函数
# ===========================================================================


@dataclass
class FileTransferStartParams:
    """文件传输开始参数（用于封装多参数方法调用）."""

    transfer_id: str
    filename: str
    file_size: int
    sha256: str
    total_chunks: int
    chunk_size: int
    mime_type: str = ""
    session_id: str = ""
    channel_id: str = ""


@dataclass
class TransferProgress:
    """文件传输进度状态（Gateway 和 AgentServer 共用）."""

    transfer_id: str
    filename: str
    file_size: int
    total_chunks: int
    received_chunks: int = 0
    sha256: str = ""
    chunks: dict[int, bytes] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    mime_type: str = ""
    session_id: str = ""
    channel_id: str = ""  # Gateway 端需要，AgentServer 端可选


def safe_filename(filename: str) -> str:
    """生成安全的文件名.

    移除路径分隔符和可能导致跨平台文件系统问题的字符，
    防止路径遍历攻击。
    """
    # 移除路径分隔符，防止路径遍历
    safe = filename.replace("/", "_").replace("\\", "_")
    # 只保留字母、数字和安全的标点符号
    safe = "".join(c for c in safe if c.isalnum() or c in "._- ")
    return safe or "unnamed_file"


def guess_mime_type(filename: str) -> str:
    """根据文件扩展名猜测 MIME 类型.

    使用 Python 标准 mimetypes 库，支持大多数常见文件类型。
    """
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"



def restart_process(delay: float = 0.0) -> None:
    """重启当前进程.

    Args:
        delay: 延迟重启的秒数，默认 0 立即重启
    """

    def _do_restart() -> None:
        logger.info("[App] 配置已写回，正在重启服务…")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    if delay <= 0:
        _do_restart()
        return

    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.call_later(delay, _do_restart)
    except RuntimeError:
        _do_restart()



async def connect_with_retry(
    client,
    uri: str,
    *,
    max_retries: int = 20,
    interval: float = 3.0,
) -> None:
    """带重试机制的异步连接.

    Args:
        client: WebSocket 客户端实例
        uri: 连接地址
        max_retries: 最大重试次数
        interval: 重试间隔秒数
    """
    import asyncio

    for attempt in range(1, max_retries + 1):
        try:
            await client.connect(uri)
            logger.info("[App] connected to AgentServer: %s", uri)
            return
        except Exception as exc:
            if attempt >= max_retries:
                logger.error(
                    "[App] connect AgentServer failed after %d tries: %s  last=%s",
                    attempt,
                    uri,
                    exc,
                )
                raise
            logger.warning(
                "[App] connect AgentServer failed (%d/%d): %s  retry in %s s…",
                attempt,
                max_retries,
                exc,
                interval,
            )
            await asyncio.sleep(interval)
