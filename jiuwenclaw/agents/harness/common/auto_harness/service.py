# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AutoHarnessService - Integration service for auto_harness mode.

This service handles:
- Repo URL resolution and cloning/updating
- AutoHarnessConfig construction from JiuwenClaw model config
- Orchestrator creation and session streaming
- Chunk to WebSocket event mapping
- Active run management with cancel support

Design per §5.1-5.6 of auto_harness.md:
- Service is instantiated as member variable of JiuWenClawDeepAdapter
- Base config.yaml loaded once at init
- Per-request: repo_url from request params, clone repo, build config with overrides
"""

from __future__ import annotations

import asyncio
from copy import copy
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional
import uuid
import zipfile
import yaml

from openjiuwen.auto_harness import (
    AutoHarnessConfig,
    AutoHarnessOrchestrator,
    create_auto_harness_orchestrator,
)
from openjiuwen.auto_harness.schema import (
    ExtensionDesign,
    OptimizationTask,
    RuntimeExtensionArtifact,
    StageResult,
    load_auto_harness_config,
)
from openjiuwen.auto_harness.contexts import TaskContext, TaskRuntime
from openjiuwen.auto_harness.pipelines import EXTENDED_EVOLVE_PIPELINE
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline import (
    ExtensionTaskPipeline,
)
from openjiuwen.auto_harness.stages.activate import ExtendActivateStage
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenclaw.agents.harness.common.rails.stream_event_rail import JiuClawStreamEventRail
from jiuwenclaw.common.schema.agent import AgentResponseChunk
from jiuwenclaw.common.utils import get_user_workspace_dir

from .scheduler import Scheduler
from .task_store import TaskStore
from .config_validator import ConfigValidator

logger = logging.getLogger(__name__)

# Data directory for auto-harness runs (per §5.6.1)
_AUTO_HARNESS_DATA_DIR = get_user_workspace_dir() / "auto-harness"
# Packages metadata file for version management
_HARNESS_PACKAGES_FILE = _AUTO_HARNESS_DATA_DIR / "harness-packages.json"
# Default repo URL if not specified in request (per §5.5)
_DEFAULT_REPO_URL = "https://gitcode.com/openJiuwen/agent-core.git"
# Default local repo path
_DEFAULT_LOCAL_REPO = _AUTO_HARNESS_DATA_DIR / "repo" / "openJiuwen--agent-core"
# Default values for ci_gate config
_DEFAULT_CI_GATE_PYTHON_EXECUTABLE = sys.executable
_DEFAULT_CI_GATE_INSTALL_COMMAND = "uv sync --active --group dev --extra cli"


def reset_harness_packages_state() -> None:
    """Reset harness package state to native agent on service startup.

    On service startup, clear active_package_id if there's historical activation info.
    This ensures the agent always starts fresh in native mode.
    """
    try:
        if not _HARNESS_PACKAGES_FILE.exists():
            logger.debug("[AutoHarnessService] No harness-packages.json, skip reset")
            return

        data = json.loads(_HARNESS_PACKAGES_FILE.read_text(encoding="utf-8"))
        active_package_id = data.get("active_package_id")

        if not active_package_id:
            logger.debug("[AutoHarnessService] No active package in metadata, already native")
            return

        extension_name = data.get("active_extension_name", active_package_id)
        logger.info(
            "[AutoHarnessService] Resetting to native agent, clearing previous: %s (%s)",
            extension_name,
            active_package_id,
        )

        # Reset active_package_id to null (native state)
        data["active_package_id"] = None
        data["active_extension_name"] = None
        native_version = data.get("native_version", {})
        if native_version and native_version.get("is_active", False) == False:
            data["native_version"]["is_active"] = True
        for pkg in data.get("packages", []):
            if pkg.get("is_active", False):
                pkg["is_active"] = False
        _HARNESS_PACKAGES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info(
            "[AutoHarnessService] Reset to native agent state, cleared active_package_id"
        )

    except Exception as e:
        logger.warning(
            "[AutoHarnessService] Failed to reset harness package state: %s",
            e,
        )


def _is_safe_zip_path(base_dir: Path, member_path: str) -> bool:
    """Check if zip member path is safe (no path traversal).

    Prevents Zip Slip vulnerability by ensuring the resolved path
    stays within the intended extraction directory.

    Args:
        base_dir: The target extraction directory
        member_path: Path of the zip member entry

    Returns:
        True if path is safe, False if it would escape base_dir
    """
    # Normalize the path and check it doesn't escape base_dir
    resolved = (base_dir / member_path).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


@dataclass
class ActiveAutoHarnessRun:
    """Active auto_harness run metadata (per §5.2)."""

    session_id: str
    request_id: str
    repo_url: str
    local_repo: Path
    task: asyncio.Task
    cancelled: bool = False
    orchestrator: Optional[AutoHarnessOrchestrator] = None
    suspended: bool = False
    stream_queue: Optional[asyncio.Queue] = None
    current_stage_name: str = "assess"
    pending_interaction_id: str = ""
    completed: bool = False


class AutoHarnessService:
    """Service for auto_harness mode integration with JiuwenClaw Web.

    Instantiated as member variable of JiuWenClawDeepAdapter for proper lifecycle management.
    """

    def __init__(self, rail: Any, agent: Any | None = None) -> None:
        """Initialize service - load base config, create data directories.

        Per §5.6.2: config.yaml is bootstrapped if not exists.
        """
        # Directory paths (per §5.6.4)
        self.data_dir = _AUTO_HARNESS_DATA_DIR
        self.repo_cache_dir = self.data_dir / "repo"
        self.experience_dir = self.data_dir / "experience"
        self.runs_dir = self.data_dir / "runs"
        self.config_path = self.data_dir / "config.yaml"

        self._stream_event_rail = rail
        self._agent = agent

        # Active runs tracked by session_id (per §5.2)
        self._active_runs: dict[str, ActiveAutoHarnessRun] = {}

        # Ensure base directories exist
        self._ensure_data_dirs()

        # Load base config once at init (per §5.6.2)
        self._base_config: Optional[AutoHarnessConfig] = None
        self._load_base_config()

        # Scheduler-related properties (for scheduled tasks)
        self._scheduler: Optional[Scheduler] = None
        self._task_store: Optional[TaskStore] = None
        self._config_validator: Optional[ConfigValidator] = None

        # Initialize scheduler components
        self._init_scheduler()

    def update_agent_instance(self, agent: Any):
        self._agent = agent.get_instance()
        try:
            stream_event_rail = JiuClawStreamEventRail()
            logger.info("[AutoHarnessService] JiuClawStreamEventRail create success")
        except Exception as exc:
            logger.warning("[AutoHarnessService] JiuClawStreamEventRail create failed: %s", exc)
            stream_event_rail = None
        self._stream_event_rail = stream_event_rail

    def _ensure_data_dirs(self) -> None:
        """Ensure required data directories exist (per §5.6.1 layout)."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.repo_cache_dir.mkdir(parents=True, exist_ok=True)
            self.experience_dir.mkdir(parents=True, exist_ok=True)
            self.runs_dir.mkdir(parents=True, exist_ok=True)

            # Create temp directories for import/export
            temp_dir = self.data_dir / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            (temp_dir / "exports").mkdir(parents=True, exist_ok=True)
            (temp_dir / "uploads").mkdir(parents=True, exist_ok=True)
            (temp_dir / "extracts").mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(
                "[AutoHarnessService] Failed to create data directories: %s",
                exc,
            )

    @staticmethod
    def _fill_config_defaults(config_path: Path) -> None:
        """Fill default values for missing config items and save to file.

        Args:
            config_path: Path to the config.yaml file

        This function reads the config file, checks for missing defaults,
        fills them in if needed, and saves the updated config.
        """
        config_dict: dict[str, Any] = {}
        if config_path.exists():
            try:
                config_dict = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning("[AutoHarnessService] Failed to parse config.yaml: %s", e)
                config_dict = {}

        needs_save = False

        # Ensure local_repo is a string (not Path object which causes YAML serialization issues)
        local_repo = config_dict.get("local_repo")
        if not local_repo:
            config_dict["local_repo"] = str(_DEFAULT_LOCAL_REPO)
            needs_save = True
        elif hasattr(local_repo, "__fspath__"):  # Path-like object
            config_dict["local_repo"] = str(local_repo)
            needs_save = True

        ci_gate = config_dict.get("ci_gate") or {}
        if not ci_gate.get("python_executable"):
            ci_gate["python_executable"] = _DEFAULT_CI_GATE_PYTHON_EXECUTABLE
            needs_save = True

        if not ci_gate.get("install_command"):
            ci_gate["install_command"] = _DEFAULT_CI_GATE_INSTALL_COMMAND
            needs_save = True

        if needs_save:
            config_dict["ci_gate"] = ci_gate
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                yaml.dump(config_dict, allow_unicode=True, sort_keys=False, default_flow_style=False),
                encoding="utf-8"
            )
            logger.info(
                "[AutoHarnessService] Auto-filled config defaults: " \
                "local_repo=%s, python_executable=%s, install_command=%s",
                config_dict.get("local_repo"),
                ci_gate.get("python_executable"),
                ci_gate.get("install_command"),
            )

    def _load_base_config(self) -> None:
        """Load base config.yaml once at init (per §5.6.2).

        Config is bootstrapped from template if not exists.
        Auto-fills default values for missing config items and saves to file.
        This provides default values; per-request overrides applied in build_auto_harness_config.
        """
        try:
            logger.info(
                "[AutoHarnessService] Loading base config from: %s",
                self.config_path,
            )

            # Check if config file exists before calling load_auto_harness_config
            config_exists = self.config_path.exists()

            # Step 1: Call load_auto_harness_config to bootstrap default config file if not exists
            self._base_config = load_auto_harness_config(str(self.config_path))

            # Step 2: Fill defaults only when config file was newly created
            if not config_exists:
                self._fill_config_defaults(self.config_path)
                # Reload config with updated values
                self._base_config = load_auto_harness_config(str(self.config_path))

            logger.info(
                "[AutoHarnessService] Base config loaded successfully",
            )
        except Exception as exc:
            logger.warning(
                "[AutoHarnessService] Failed to load base config: %s, will bootstrap per-request",
                exc,
            )
            self._base_config = None

    def _init_scheduler(self) -> None:
        """Initialize scheduler components (lazy, not started until needed)."""
        try:
            self._task_store = TaskStore(self.data_dir)
            self._config_validator = ConfigValidator(
                self.config_path,
                base_config=self._base_config
            )
            self._scheduler = Scheduler(
                service=self,
                task_store=self._task_store,
            )
            logger.info("[AutoHarnessService] Scheduler components initialized")
        except Exception as e:
            logger.warning("[AutoHarnessService] Failed to init scheduler: %s", e)

    async def start_scheduler(self) -> None:
        """Start the scheduling loop (called by AgentWebSocketServer)."""
        if self._scheduler is None:
            self._init_scheduler()
        if self._scheduler is not None:
            await self._scheduler.start()
            logger.info("[AutoHarnessService] Scheduler started")

    async def stop_scheduler(self) -> None:
        """Stop the scheduler (called on server shutdown)."""
        if self._scheduler is not None:
            await self._scheduler.stop()
            logger.info("[AutoHarnessService] Scheduler stopped")

    def check_schedule_config(self) -> dict[str, Any]:
        """Check if required git/gitcode config is present.

        Returns:
            {"valid": bool, "missing_fields": list, "config_path": str}
        """
        if self._config_validator is None:
            self._init_scheduler()
        if self._config_validator is None:
            return {"valid": False, "error": "配置校验器未初始化"}
        return self._config_validator.check_config()

    def update_schedule_config(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Update config fields from user input.

        Args:
            fields: Dict like {"git.user_name": "value"}

        Returns:
            {"success": bool, "updated_fields": list}
        """
        if self._config_validator is None:
            return {"success": False, "error": "配置校验器未初始化"}

        result = self._config_validator.update_config(fields)

        # Reload base config after update
        if result.get("success"):
            self._load_base_config()

        return result

    @staticmethod
    def _extract_repo_name(repo_url: str) -> str:
        """Extract repository name from URL.

        Examples:
            https://gitcode.com/openJiuwen/agent-core.git -> openJiuwen--agent-core
            https://github.com/user/project.git -> user--project
        """
        # Remove trailing .git
        url = repo_url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]

        # Extract org/repo part
        parts = url.split("/")
        if len(parts) >= 2:
            org = parts[-2]
            repo = parts[-1]
            return f"{org}--{repo}"

        # Fallback: use last segment
        return parts[-1] if parts else "repository"

    async def _run_git_command(
        self,
        args: list[str],
        cwd: Optional[Path] = None,
    ) -> tuple[int, str, str]:
        """Run a git command asynchronously."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return (
                process.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except Exception as exc:
            logger.error(
                "[AutoHarnessService] Git command failed: git %s, error=%s",
                " ".join(args),
                exc,
            )
            return (1, "", str(exc))

    async def clone_or_update_repo(self, repo_url: str) -> Path:
        """Clone or update remote repository to local cache (per §5.5)."""
        repo_name = self._extract_repo_name(repo_url)
        local_path = self.repo_cache_dir / repo_name

        if local_path.exists() and (local_path / ".git").exists():
            # Update existing repository: fetch + reset (per §5.5)
            logger.info("[AutoHarnessService] Updating existing repo: %s", local_path)
            ret, _, stderr = await self._run_git_command(
                ["fetch", "origin"],
                cwd=local_path,
            )
            if ret != 0:
                logger.warning("[AutoHarnessService] Git fetch failed: %s", stderr)

            ret, _, stderr = await self._run_git_command(
                ["reset", "--hard", "origin/HEAD"],
                cwd=local_path,
            )
            if ret != 0:
                logger.warning("[AutoHarnessService] Git reset failed: %s", stderr)
        else:
            # Clone new repository
            logger.info(
                "[AutoHarnessService] Cloning repo: %s -> %s",
                repo_url,
                local_path,
            )
            if local_path.exists():
                shutil.rmtree(local_path, ignore_errors=True)

            ret, _, stderr = await self._run_git_command(
                ["clone", repo_url, str(local_path)],
            )
            if ret != 0:
                raise RuntimeError(f"Failed to clone repository {repo_url}: {stderr}")

        return local_path

    def build_auto_harness_config(
        self,
        repo_url: str,
        local_repo: Path,
        model: Optional[Model],
        optimization_goal: str = "",
        pipeline_preference: Optional[str] = None
    ) -> AutoHarnessConfig:
        """Build AutoHarnessConfig with per-request overrides (per §5.6.3).

        Override order:
        1. Base config from config.yaml (loaded at init)
        2. Force overrides: data_dir, local_repo, repo_url, experience_dir, model

        Args:
            repo_url: Remote repository URL (from request params per §5.5)
            local_repo: Local path to cloned repository
            model: Model instance from JiuwenClaw

        Returns:
            Configured AutoHarnessConfig instance
        """
        # Start from base config or bootstrap new one with workspace hint
        if self._base_config is not None:
            config = copy(self._base_config)
        else:
            config = load_auto_harness_config(
                str(self.config_path),
                workspace_hint=str(local_repo),
            )

        # Force override paths and settings
        resolved_local_repo = local_repo.expanduser().resolve()
        config.data_dir = str(self.data_dir)
        config.local_repo = str(resolved_local_repo)
        # agent-core still uses config.workspace to scope DeepAgent file tools.
        # Keep it pinned to the repo checkout so assess/plan do not fall back
        # to JiuwenSwarm's user workspace (~/.jiuwenswarm).
        config.workspace = str(resolved_local_repo)
        config.repo_url = repo_url
        config.experience_dir = str(self.experience_dir)
        config.pipeline_preference = pipeline_preference if pipeline_preference else EXTENDED_EVOLVE_PIPELINE
        config.optimization_goal = str(optimization_goal or "")

        # Set model from JiuwenSwarm
        if model is not None:
            config.model = model
        else:
            # Fallback: build from environment
            config.model = self._build_model_from_env()

        # Derive git settings from repo_url. Preserve git.base_branch from
        # config.yaml so local Auto-Harness runs can target feature branches.
        if not config.git_base_branch:
            config.git_base_branch = "develop"
        config.git_remote = repo_url

        # Parse upstream owner/repo from URL
        repo_name = self._extract_repo_name(repo_url)
        parts = repo_name.split("--")
        if len(parts) >= 2:
            config.upstream_owner = parts[0]
            config.upstream_repo = parts[1]

        logger.info(
            "[AutoHarnessService] Built config: data_dir=%s, local_repo=%s, repo_url=%s, model=%s",
            config.data_dir,
            config.local_repo,
            config.repo_url,
            config.model.model_config.model_name if config.model else "None",
        )

        return config

    @staticmethod
    def _build_model_from_env() -> Optional[Model]:
        """Build Model from environment variables as fallback."""
        api_key = os.getenv("API_KEY", "").strip()
        base_url = os.getenv("API_BASE", os.getenv("BASE_URL", "")).strip()
        model_name = os.getenv("MODEL_NAME", os.getenv("MODEL", "")).strip()

        if not api_key or not model_name:
            logger.warning(
                "[AutoHarnessService] Cannot build model from env: missing API_KEY or MODEL_NAME"
            )
            return None

        return Model(
            model_client_config=ModelClientConfig(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
            ),
            model_config=ModelRequestConfig(model=model_name, temperature=0.95),
        )

    @staticmethod
    def is_activate_only_request(request: Any, query: str) -> bool:
        """Return whether request asks for dev-only activate-stage replay."""
        params = getattr(request, "params", {}) or {}
        if bool(params.get("debug_activate_only") or params.get("activate_only")):
            return True
        normalized = query.strip()
        return (
            normalized == "activate-only"
            or normalized.startswith("activate-only ")
            or normalized.startswith("/auto-harness activate-only")
        )

    @staticmethod
    def is_implement_only_request(request: Any, query: str) -> bool:
        """Return whether request asks for dev-only implement-stage replay."""
        params = getattr(request, "params", {}) or {}
        if bool(params.get("debug_implement_only") or params.get("implement_only")):
            return True
        normalized = query.strip()
        return (
            normalized == "implement-only"
            or normalized.startswith("implement-only ")
            or normalized.startswith("/auto-harness implement-only")
        )

    def _latest_extension_design_path(self) -> Path:
        """Find the latest persisted ExtensionDesign artifact."""
        candidates = [
            path
            for path in self.runs_dir.glob("extension_design_*.json")
            if path.is_file()
        ]
        latest = self.runs_dir / "latest_extension_design.json"
        if latest.is_file():
            candidates.append(latest)
        if not candidates:
            raise RuntimeError(
                "未找到可复用的 extension design，请先完整跑到 design_ext 阶段，"
                "或在请求中传 design_path"
            )
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _resolve_implement_only_design_path(
        self,
        request: Any,
        query: str,
    ) -> Path:
        """Resolve ExtensionDesign JSON path from params, command, or latest artifact."""
        params = getattr(request, "params", {}) or {}
        raw_path = (
            params.get("design_path")
            or params.get("extension_design_path")
            or params.get("implement_design_path")
            or ""
        )
        normalized = query.strip()
        if not raw_path and normalized.startswith("/auto-harness implement-only"):
            parts = normalized.split(maxsplit=2)
            if len(parts) >= 3:
                raw_path = parts[2]
        if not raw_path and normalized.startswith("implement-only "):
            parts = normalized.split(maxsplit=1)
            if len(parts) >= 2:
                raw_path = parts[1]

        design_path = (
            Path(str(raw_path)).expanduser()
            if raw_path
            else self._latest_extension_design_path()
        )
        if not design_path.is_file():
            raise RuntimeError(f"implement-only design_path 无效: {design_path}")
        return design_path.resolve()

    @staticmethod
    def _load_extension_designs(design_path: Path) -> list[ExtensionDesign]:
        """Load persisted ExtensionDesign records from JSON."""
        with design_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        records: Any
        if isinstance(data, dict) and isinstance(data.get("designs"), list):
            records = data["designs"]
        elif isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = [data]
        else:
            records = []

        designs: list[ExtensionDesign] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            extension_name = str(item.get("extension_name", "")).strip()
            if not extension_name:
                continue
            designs.append(
                ExtensionDesign(
                    gap_id=str(item.get("gap_id", "")),
                    extension_name=extension_name,
                    components=list(item.get("components") or []),
                    file_plan=dict(item.get("file_plan") or {}),
                    harness_config_patch=dict(item.get("harness_config_patch") or {}),
                )
            )
        if not designs:
            raise RuntimeError(f"design 文件没有可用的 ExtensionDesign: {design_path}")
        return designs

    def _latest_runtime_extension_path(self) -> Path:
        """Find the latest promoted runtime extension for activate-only debug."""
        runtime_root = self.data_dir / "runtime_extensions"
        candidates = [
            path
            for path in runtime_root.glob("*/*")
            if path.is_dir() and (path / "harness_config.yaml").is_file()
        ]
        if not candidates:
            raise RuntimeError(
                "未找到可激活的 runtime extension，请先完整跑到扩展生成/验证阶段，"
                "或在请求中传 runtime_path"
            )
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _resolve_activate_only_runtime_path(
        self,
        request: Any,
        query: str,
    ) -> Path:
        """Resolve runtime extension path from params, command, or latest artifact."""
        params = getattr(request, "params", {}) or {}
        raw_path = (
            params.get("runtime_path")
            or params.get("activate_runtime_path")
            or params.get("extension_runtime_path")
            or ""
        )
        normalized = query.strip()
        if not raw_path and normalized.startswith("/auto-harness activate-only"):
            parts = normalized.split(maxsplit=2)
            if len(parts) >= 3:
                raw_path = parts[2]
        if not raw_path and normalized.startswith("activate-only "):
            parts = normalized.split(maxsplit=1)
            if len(parts) >= 2:
                raw_path = parts[1]

        runtime_path = (
            Path(str(raw_path)).expanduser()
            if raw_path
            else self._latest_runtime_extension_path()
        )
        config_path = runtime_path / "harness_config.yaml"
        if not runtime_path.is_dir() or not config_path.is_file():
            raise RuntimeError(
                f"activate-only runtime_path 无效: {runtime_path}"
            )
        return runtime_path.resolve()

    def _resolve_local_repo_for_debug(self) -> Path:
        """Resolve a lightweight local repo path for activate-only orchestrator setup."""
        base_local_repo = (
            self._base_config.local_repo
            if self._base_config is not None
            else ""
        )
        if base_local_repo:
            candidate = Path(base_local_repo).expanduser()
            if candidate.exists():
                return candidate.resolve()

        for candidate in self.repo_cache_dir.glob("*"):
            if candidate.is_dir() and (candidate / ".git").exists():
                return candidate.resolve()

        return Path.cwd().resolve()

    async def run_activate_only(
        self,
        request: Any,
        session_id: str,
        request_id: str,
        query: str,
        model: Optional[Model] = None,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Dev-only shortcut: run only the activate stage against an existing runtime extension."""
        rid = request_id
        cid = getattr(request, "channel_id", None)

        if session_id in self._active_runs:
            logger.warning(
                "[AutoHarnessService] Session %s already has active run",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": "当前已有 Auto-Harness 任务在运行，请等待完成或取消后再试",
                },
                is_complete=False,
            )
            yield AgentResponseChunk(request_id=rid, channel_id=cid, payload=None, is_complete=True)
            return

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "chat.processing_status",
                "session_id": session_id,
                "is_processing": True,
            },
            is_complete=False,
        )

        try:
            runtime_path = self._resolve_activate_only_runtime_path(request, query)
            local_repo = self._resolve_local_repo_for_debug()
            repo_url = (
                self._base_config.repo_url
                if self._base_config and self._base_config.repo_url
                else _DEFAULT_REPO_URL
            )
            config = self.build_auto_harness_config(
                repo_url,
                local_repo,
                model,
                optimization_goal=query,
            )
            orchestrator = create_auto_harness_orchestrator(
                config,
                agent=self._agent,
                stream_rails=[self._stream_event_rail],
            )
            logger.info(
                "[AutoHarnessService] Activate-only orchestrator agent attached=%s",
                orchestrator.agent is not None,
            )
            stream_queue: asyncio.Queue[Optional[Any]] = asyncio.Queue()
            runtime_ext = RuntimeExtensionArtifact(
                extension_name=runtime_path.name,
                runtime_path=str(runtime_path),
                config_path=str((runtime_path / "harness_config.yaml").resolve()),
            )

            async def stream_producer() -> None:
                task = OptimizationTask(
                    topic=f"activate-only:{runtime_ext.extension_name}",
                    description="Debug activate stage only",
                    status="running",
                )
                ctx = TaskContext(
                    orchestrator=orchestrator,
                    task=task,
                    runtime=TaskRuntime(
                        related=[],
                        wt_path=str(local_repo),
                        edit_safety_rail=None,
                        preexisting_dirty_files=[],
                        task_agent=None,
                        commit_agent=None,
                    ),
                )
                ctx.put_artifact("runtime_extension", runtime_ext)
                ctx.put_artifact("verify_report", {})
                try:
                    await stream_queue.put(
                        OutputSchema(
                            type="message",
                            index=0,
                            payload={
                                "content": (
                                    "Debug activate-only: "
                                    f"{runtime_ext.extension_name}"
                                ),
                                "stage": "activate",
                                "pipeline": "activate_only",
                                "stages": [
                                    {
                                        "slot": "activate",
                                        "display_name": "激活扩展",
                                    }
                                ],
                            },
                        )
                    )
                    async for chunk in ExtendActivateStage().stream(ctx):
                        await stream_queue.put(chunk)
                except asyncio.CancelledError:
                    logger.info("[AutoHarnessService] activate-only stream cancelled")
                except Exception as exc:
                    logger.exception(
                        "[AutoHarnessService] activate-only stream error: %s",
                        exc,
                    )
                    await stream_queue.put(
                        OutputSchema(
                            type="error",
                            index=0,
                            payload={"error": str(exc)},
                        )
                    )
                finally:
                    await stream_queue.put(None)

            producer_task = asyncio.create_task(stream_producer())
            active_run = ActiveAutoHarnessRun(
                session_id=session_id,
                request_id=request_id,
                repo_url=repo_url,
                local_repo=local_repo,
                task=producer_task,
                orchestrator=orchestrator,
                stream_queue=stream_queue,
                current_stage_name="activate",
            )
            self._active_runs[session_id] = active_run

            try:
                async for response_chunk, should_suspend in self._consume_stream(
                    active_run, rid, cid,
                ):
                    if should_suspend:
                        active_run.suspended = True
                        logger.info(
                            "[AutoHarnessService] Activate-only stream waiting for interaction, session=%s",
                            session_id,
                        )
                    yield response_chunk
            except asyncio.CancelledError:
                active_run.cancelled = True
                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass
                raise
        except Exception as exc:
            logger.exception("[AutoHarnessService] activate-only failed: %s", exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": f"Auto-Harness activate-only 失败: {exc}",
                },
                is_complete=False,
            )
        finally:
            self._active_runs.pop(session_id, None)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.processing_status",
                    "session_id": session_id,
                    "is_processing": False,
                },
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=None,
                is_complete=True,
            )
            logger.info(
                "[AutoHarnessService] Final complete chunk yielded, session=%s",
                session_id,
            )

    async def run_implement_only(
        self,
        request: Any,
        session_id: str,
        request_id: str,
        query: str,
        model: Optional[Model] = None,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Dev-only shortcut: run implement_ext and following stages from latest design."""
        rid = request_id
        cid = getattr(request, "channel_id", None)

        if session_id in self._active_runs:
            logger.warning(
                "[AutoHarnessService] Session %s already has active run",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": "当前已有 Auto-Harness 任务在运行，请等待完成或取消后再试",
                },
                is_complete=False,
            )
            yield AgentResponseChunk(request_id=rid, channel_id=cid, payload=None, is_complete=True)
            return

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "chat.processing_status",
                "session_id": session_id,
                "is_processing": True,
            },
            is_complete=False,
        )

        try:
            design_path = self._resolve_implement_only_design_path(request, query)
            designs = self._load_extension_designs(design_path)
            repo_url = (
                self._base_config.repo_url
                if self._base_config and self._base_config.repo_url
                else _DEFAULT_REPO_URL
            )
            local_repo = await self.clone_or_update_repo(repo_url)
            config = self.build_auto_harness_config(
                repo_url,
                local_repo,
                model,
                optimization_goal=query,
            )
            orchestrator = create_auto_harness_orchestrator(
                config,
                agent=self._agent,
                stream_rails=[self._stream_event_rail],
            )
            logger.info(
                "[AutoHarnessService] Implement-only orchestrator agent attached=%s, design_path=%s",
                orchestrator.agent is not None,
                design_path,
            )
            stream_queue: asyncio.Queue[Optional[Any]] = asyncio.Queue()

            async def stream_producer() -> None:
                try:
                    await stream_queue.put(
                        OutputSchema(
                            type="message",
                            index=0,
                            payload={
                                "content": (
                                    "Debug implement-only: "
                                    f"{design_path.name}"
                                ),
                                "stage": "implement_ext",
                                "pipeline": "implement_only",
                                "stages": [
                                    {
                                        "slot": "implement_ext",
                                        "display_name": "实现扩展",
                                    },
                                    {
                                        "slot": "verify_ext",
                                        "display_name": "验证扩展",
                                    },
                                    {
                                        "slot": "activate",
                                        "display_name": "激活扩展",
                                    },
                                ],
                            },
                        )
                    )
                    max_designs = max(1, int(config.max_tasks_per_session or 1))
                    for design in designs[:max_designs]:
                        async for chunk in ExtensionTaskPipeline.run_isolated_stream(
                            orchestrator,
                            design,
                        ):
                            await stream_queue.put(chunk)
                except asyncio.CancelledError:
                    logger.info("[AutoHarnessService] implement-only stream cancelled")
                except Exception as exc:
                    logger.exception(
                        "[AutoHarnessService] implement-only stream error: %s",
                        exc,
                    )
                    await stream_queue.put(
                        OutputSchema(
                            type="error",
                            index=0,
                            payload={"error": str(exc)},
                        )
                    )
                finally:
                    await stream_queue.put(None)

            producer_task = asyncio.create_task(stream_producer())
            active_run = ActiveAutoHarnessRun(
                session_id=session_id,
                request_id=request_id,
                repo_url=repo_url,
                local_repo=local_repo,
                task=producer_task,
                orchestrator=orchestrator,
                stream_queue=stream_queue,
                current_stage_name="implement_ext",
            )
            self._active_runs[session_id] = active_run

            try:
                async for response_chunk, should_suspend in self._consume_stream(
                    active_run, rid, cid,
                ):
                    if should_suspend:
                        active_run.suspended = True
                        logger.info(
                            "[AutoHarnessService] Implement-only stream waiting for interaction, session=%s",
                            session_id,
                        )
                    yield response_chunk
            except asyncio.CancelledError:
                active_run.cancelled = True
                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass
                raise
        except Exception as exc:
            logger.exception("[AutoHarnessService] implement-only failed: %s", exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": f"Auto-Harness implement-only 失败: {exc}",
                },
                is_complete=False,
            )
        finally:
            self._active_runs.pop(session_id, None)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.processing_status",
                    "session_id": session_id,
                    "is_processing": False,
                },
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=None,
                is_complete=True,
            )

    async def run(
        self,
        request: Any,
        session_id: str,
        request_id: str,
        query: str,
        model: Optional[Model] = None,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Run auto_harness session and stream chunks as WebSocket events.

        Args:
            request: AgentRequest object
            session_id: Session identifier
            request_id: Request identifier
            query: User's optimization goal input
            model: JiuwenClaw's current model config

        Yields:
            AgentResponseChunk instances mapped from orchestrator chunks
        """
        rid = request_id
        cid = getattr(request, "channel_id", None)

        # Check for existing active run (per §5.2 - one run per session)
        if session_id in self._active_runs:
            logger.warning(
                "[AutoHarnessService] Session %s already has active run",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": "当前已有 Auto-Harness 任务在运行，请等待完成或取消后再试",
                },
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=None,
                is_complete=True,
            )
            return

        # Resolve repo_url from base config (per §5.6.3)
        # repo_url is configured in config.yaml, not passed in request
        if self._base_config and self._base_config.repo_url:
            repo_url = self._base_config.repo_url
        else:
            # Fallback to default if config not loaded
            repo_url = _DEFAULT_REPO_URL
        logger.info("[AutoHarnessService] Using repo_url from config: %s", repo_url)

        # Emit processing status
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "chat.processing_status",
                "session_id": session_id,
                "is_processing": True,
            },
            is_complete=False,
        )

        active_run: Optional[ActiveAutoHarnessRun] = None
        params = getattr(request, "params", {}) or {}
        pipeline_preference = params.get("pipeline_preference")
        try:
            # Clone/update repository
            local_repo = await self.clone_or_update_repo(repo_url)
            logger.info("[AutoHarnessService] Repo ready at: %s", local_repo)

            # Build config with per-request overrides
            config = self.build_auto_harness_config(
                repo_url,
                local_repo,
                model,
                optimization_goal=query,
                pipeline_preference=pipeline_preference
            )

            # Build optimization task from query
            tasks = [
                OptimizationTask(
                    topic=query,
                    description=query,
                    status="pending",
                )
            ]

            # Create orchestrator
            orchestrator = create_auto_harness_orchestrator(
                config,
                agent=self._agent,
                stream_rails=[self._stream_event_rail],
            )
            logger.info(
                "[AutoHarnessService] Orchestrator agent attached=%s",
                orchestrator.agent is not None,
            )

            # Create streaming task wrapper for cancel support
            stream_queue: asyncio.Queue[Optional[Any]] = asyncio.Queue()
            cancelled = False

            async def stream_producer() -> None:
                """Produce chunks from orchestrator stream."""
                try:
                    async for chunk in orchestrator.run_session_stream(tasks=tasks):
                        if cancelled:
                            logger.info(
                                "[AutoHarnessService] Stream cancelled, stopping producer"
                            )
                            break
                        await stream_queue.put(chunk)
                    logger.info(
                        "[AutoHarnessService] Producer stream finished, session=%s",
                        session_id,
                    )
                except asyncio.CancelledError:
                    logger.info("[AutoHarnessService] Orchestrator stream cancelled")
                except Exception as exc:
                    logger.exception(
                        "[AutoHarnessService] Orchestrator stream error: %s",
                        exc,
                    )
                    await stream_queue.put(
                        OutputSchema(
                            type="error",
                            index=0,
                            payload={"error": str(exc)},
                        )
                    )
                finally:
                    logger.info(
                        "[AutoHarnessService] Producer enqueuing sentinel, session=%s",
                        session_id,
                    )
                    await stream_queue.put(None)  # Signal end

            # Start producer task
            producer_task = asyncio.create_task(stream_producer())

            # Register active run
            active_run = ActiveAutoHarnessRun(
                session_id=session_id,
                request_id=request_id,
                repo_url=repo_url,
                local_repo=local_repo,
                task=producer_task,
                cancelled=False,
                orchestrator=orchestrator,
                stream_queue=stream_queue,
            )
            self._active_runs[session_id] = active_run

            # Consume and map chunks. Activation confirmation is handled as an
            # out-of-band resume signal; this original stream stays open.
            try:
                async for response_chunk, should_suspend in self._consume_stream(
                    active_run, rid, cid,
                ):
                    if should_suspend:
                        active_run.suspended = True
                        logger.info(
                            "[AutoHarnessService] Stream waiting for activate interaction, session=%s",
                            session_id,
                        )
                    yield response_chunk
            except asyncio.CancelledError:
                logger.info(
                    "[AutoHarnessService] Stream consumption cancelled for session %s",
                    session_id,
                )
                cancelled = True
                active_run.cancelled = True

                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except asyncio.CancelledError:
                        pass

                yield AgentResponseChunk(
                    request_id=rid,
                    channel_id=cid,
                    payload={
                        "event_type": "chat.interrupt_result",
                        "session_id": session_id,
                        "intent": "cancel",
                    },
                    is_complete=False,
                )

        except Exception as exc:
            logger.exception("[AutoHarnessService] Run failed: %s", exc)
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.error",
                    "error": f"Auto-Harness 运行失败: {exc}",
                },
                is_complete=False,
            )

        finally:
            if session_id in self._active_runs:
                del self._active_runs[session_id]

            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload={
                    "event_type": "chat.processing_status",
                    "session_id": session_id,
                    "is_processing": False,
                },
                is_complete=False,
            )

            yield AgentResponseChunk(
                request_id=rid,
                channel_id=cid,
                payload=None,
                is_complete=True,
            )

    @staticmethod
    def _map_chunk_to_response(
        chunk: Any,
        request_id: str,
        channel_id: Optional[str],
        current_stage_name: str,
    ) -> list[AgentResponseChunk]:
        """Map orchestrator chunk to AgentResponseChunk(s).

        Handles chunk types per design document mapping:
        - OutputSchema types -> delegated to _parse_stream_chunk (shared with DeepAdapter)
        - StageResult -> harness.stage_result (NEW, auto_harness-specific)

        Per design requirement: reuse _parse_stream_chunk for OutputSchema handling,
        only implement auto_harness-specific StageResult here.
        """
        results: list[AgentResponseChunk] = []

        # Import _parse_stream_chunk from DeepAdapter to reuse OutputSchema parsing logic
        from jiuwenclaw.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter

        # Handle OutputSchema - delegate to shared _parse_stream_chunk
        if isinstance(chunk, OutputSchema):
            parsed = JiuWenClawDeepAdapter._parse_stream_chunk(
                chunk,
                _has_streamed_content=False,
                _stage=current_stage_name,
            )
            if parsed is not None:
                results.append(
                    AgentResponseChunk(
                        request_id=request_id,
                        channel_id=channel_id,
                        payload=parsed,
                        is_complete=False,
                    )
                )
            return results

        # Handle StageResult - auto_harness-specific (per §4.5)
        if isinstance(chunk, StageResult):
            logger.info(
                "[AutoHarnessService] StageResult received: stage=%s, status=%s",
                current_stage_name,
                chunk.status,
            )
            stage_result_payload = {
                "event_type": "harness.stage_result",
                "stage": current_stage_name,
                "status": chunk.status,
                "error": chunk.error,
                "messages": list(chunk.messages) if chunk.messages else [],
                "metrics": dict(chunk.metrics) if chunk.metrics else {},
                # artifacts intentionally not passed to frontend (per §4.5)
            }
            results.append(
                AgentResponseChunk(
                    request_id=request_id,
                    channel_id=channel_id,
                    payload=stage_result_payload,
                    is_complete=False,
                )
            )
            return results

        # Handle dict chunks that may have event_type
        if isinstance(chunk, dict):
            if "event_type" in chunk:
                results.append(
                    AgentResponseChunk(
                        request_id=request_id,
                        channel_id=channel_id,
                        payload=chunk,
                        is_complete=False,
                    )
                )
            elif "type" in chunk:
                # Treat as OutputSchema-like dict, use _parse_stream_chunk
                parsed = JiuWenClawDeepAdapter._parse_stream_chunk(
                    chunk,
                    _has_streamed_content=False,
                    _stage=current_stage_name,
                )
                if parsed is not None:
                    results.append(
                        AgentResponseChunk(
                            request_id=request_id,
                            channel_id=channel_id,
                            payload=parsed,
                            is_complete=False,
                        )
                    )
            else:
                # Unknown dict, log and skip
                logger.debug(
                    "[AutoHarnessService] Unknown dict chunk without type/event_type: %s",
                    list(chunk.keys()),
                )
            return results

        # Unknown chunk type - log and skip (per §4.3: "未知 chunk 仅记日志")
        logger.debug(
            "[AutoHarnessService] Unknown chunk type: %s",
            type(chunk).__name__,
        )
        return results

    async def _consume_stream(
        self,
        active_run: ActiveAutoHarnessRun,
        request_id: str,
        channel_id: Optional[str],
    ) -> AsyncIterator[tuple[AgentResponseChunk, bool]]:
        """Consume stream queue, yielding (chunk, should_suspend) pairs."""
        queue = active_run.stream_queue
        if queue is None:
            return
        producer_task = active_run.task
        while True:
            try:
                chunk = await asyncio.wait_for(
                    queue.get(),
                    timeout=0.1,
                )
            except asyncio.TimeoutError:
                if producer_task.done():
                    logger.info(
                        "[AutoHarnessService] Producer done while queue idle, session=%s",
                        active_run.session_id,
                    )
                    break
                continue

            if chunk is None:
                logger.info(
                    "[AutoHarnessService] Consumer received sentinel, session=%s",
                    active_run.session_id,
                )
                break

            is_terminal = False
            if isinstance(chunk, OutputSchema):
                payload = chunk.payload
                is_terminal = (
                    chunk.type == "harness_session_finished"
                    or (
                        isinstance(payload, dict)
                        and payload.get("is_terminal") is True
                    )
                )

            if isinstance(chunk, OutputSchema) and chunk.type == "message":
                payload = chunk.payload
                if isinstance(payload, dict):
                    stage = payload.get("stage", "")
                    if stage:
                        active_run.current_stage_name = stage

            is_interaction = (
                isinstance(chunk, OutputSchema)
                and chunk.type == "__interaction__"
            )
            if is_interaction:
                payload = chunk.payload
                if isinstance(payload, dict):
                    interaction_id = payload.get("interaction_id")
                    if isinstance(interaction_id, str) and interaction_id:
                        active_run.pending_interaction_id = interaction_id

            for response_chunk in self._map_chunk_to_response(
                chunk,
                request_id,
                channel_id,
                active_run.current_stage_name,
            ):
                yield response_chunk, is_interaction

            if is_terminal:
                active_run.completed = True
                logger.info(
                    "[AutoHarnessService] Consumer received terminal event, session=%s",
                    active_run.session_id,
                )
                break

            if is_interaction:
                continue

    async def resume_activate(
        self,
        session_id: str,
        request_id: str,
        channel_id: Optional[str],
        activate_response: dict,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Resolve pending interaction and stream remaining chunks."""
        active_run = self._active_runs.get(session_id)
        if active_run is None or not active_run.suspended:
            logger.warning(
                "[AutoHarnessService] No suspended run for session=%s",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={
                    "event_type": "chat.error",
                    "error": "没有挂起的 activate 交互",
                },
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload=None,
                is_complete=True,
            )
            return

        orchestrator = active_run.orchestrator
        interaction_id = activate_response.get("interaction_id", "")
        if not interaction_id:
            interaction_id = active_run.pending_interaction_id
            if interaction_id:
                logger.info(
                    "[AutoHarnessService] Reusing stored interaction_id for session=%s",
                    session_id,
                )

        if not interaction_id:
            logger.warning(
                "[AutoHarnessService] Missing interaction_id for suspended session=%s",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={
                    "event_type": "chat.error",
                    "error": "activate 确认缺少 interaction_id",
                },
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload=None,
                is_complete=True,
            )
            return

        if orchestrator is None:
            logger.warning(
                "[AutoHarnessService] Missing orchestrator for session=%s",
                session_id,
            )
            yield AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload=None,
                is_complete=True,
            )
            return

        active_run.suspended = False
        active_run.pending_interaction_id = ""
        orchestrator.run_session_stream(
            message={
                "interaction_id": interaction_id,
                "action": activate_response.get("action", "accept"),
                "feedback": activate_response.get("feedback", ""),
            }
        )

        rid = request_id
        cid = channel_id
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload={
                "event_type": "harness.activate_resume_ack",
                "session_id": session_id,
                "interaction_id": interaction_id,
            },
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=cid,
            payload=None,
            is_complete=True,
        )

    def cancel_session_run(self, session_id: str) -> bool:
        """Cancel active run for a session (per §5.2)."""
        active_run = self._active_runs.get(session_id)
        if active_run is None:
            logger.info("[AutoHarnessService] No active run for session %s", session_id)
            return False

        logger.info("[AutoHarnessService] Cancelling run for session %s", session_id)
        active_run.cancelled = True

        # Cancel the task
        if not active_run.task.done():
            active_run.task.cancel()

        return True

    def get_active_run(self, session_id: str) -> Optional[ActiveAutoHarnessRun]:
        """Get active run metadata for a session."""
        return self._active_runs.get(session_id)

    def has_active_run(self, session_id: str) -> bool:
        """Check if session has an active run."""
        return session_id in self._active_runs

    def cancel_all_runs(self) -> int:
        """Cancel all active runs (for cleanup on adapter shutdown)."""
        count = 0
        for session_id, run in list(self._active_runs.items()):
            if not run.task.done():
                run.cancelled = True
                run.task.cancel()
                count += 1
        self._active_runs.clear()
        return count

    @staticmethod
    def generate_package_id(runtime_path: Path) -> str:
        """Generate unique Package ID.

        Format: pkg_<parent_hash>_<extension_name>_<timestamp>
        Example: pkg_b3fe5044_hermes_context_fence_20250428103000
        """
        parent_dir = runtime_path.parent.name  # e.g., "b3fe5044ecc4"
        ext_name = runtime_path.name  # e.g., "hermes_context_fence"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"pkg_{parent_dir[:8]}_{ext_name}_{timestamp}"

    @staticmethod
    def _get_created_time(path: Path) -> str:
        """Get creation time of directory as ISO 8601 string."""
        try:
            stat = path.stat()
            # Use st_ctime on Windows, st_mtime on Unix
            ts = stat.st_ctime if os.name == "nt" else stat.st_mtime
            return datetime.fromtimestamp(ts).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    def scan_runtime_extensions(self) -> dict[str, Any]:
        """Scan runtime_extensions directory and generate packages info."""
        runtime_root = self.data_dir / "runtime_extensions"
        packages: list[dict[str, Any]] = []

        try:
            for path in runtime_root.glob("*/*"):
                if path.is_dir() and (path / "harness_config.yaml").is_file():
                    package = {
                        "id": self.generate_package_id(path),
                        "extension_name": path.name,
                        "runtime_path": str(path.resolve()),
                        "config_path": str((path / "harness_config.yaml").resolve()),
                        "created_at": self._get_created_time(path),
                        "is_active": False,
                        "version_label": "",
                        "description": "",
                    }
                    packages.append(package)
        except Exception as exc:
            logger.warning("[AutoHarnessService] Failed to scan runtime_extensions: %s", exc)

        return {
            "packages": packages,
            "native_version": {
                "id": "native",
                "extension_name": "Native Agent",
                "is_active": True,
            },
            "active_package_id": None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def load_packages(self) -> dict[str, Any]:
        """Load packages metadata from harness-packages.json.

        If the file doesn't exist, scan runtime_extensions directory,
        save the result to the file, and return the data.
        """
        try:
            if _HARNESS_PACKAGES_FILE.exists():
                data = json.loads(_HARNESS_PACKAGES_FILE.read_text(encoding="utf-8"))
                return data
        except Exception as exc:
            logger.warning("[AutoHarnessService] Failed to load packages file: %s", exc)

        # Fallback: scan directory and save result
        data = self.scan_runtime_extensions()
        self.save_packages(data)
        logger.info("[AutoHarnessService] Created packages metadata from scan")
        return data

    @staticmethod
    def save_packages(data: dict[str, Any]) -> None:
        """Save packages metadata to harness-packages.json."""
        try:
            _HARNESS_PACKAGES_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[AutoHarnessService] Saved packages metadata to %s", _HARNESS_PACKAGES_FILE)
        except Exception as exc:
            logger.warning("[AutoHarnessService] Failed to save packages file: %s", exc)

    def add_package(self, extension_name: str, runtime_path: str, config_path: str) -> dict[str, Any]:
        """Add a new package entry after extension_ready event."""
        data = self.load_packages()

        runtime_path_obj = Path(runtime_path)
        package = {
            "id": self.generate_package_id(runtime_path_obj),
            "extension_name": extension_name,
            "runtime_path": runtime_path,
            "config_path": config_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "is_active": False,
            "version_label": "",
            "description": "",
        }

        data["packages"].append(package)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save_packages(data)

        logger.info(
            "[AutoHarnessService] Added package: id=%s, extension_name=%s",
            package["id"],
            extension_name,
        )
        return package

    @staticmethod
    def find_package_by_id(packages: dict[str, Any], package_id: str) -> Optional[dict[str, Any]]:
        """Find package by ID in packages data."""
        for pkg in packages.get("packages", []):
            if pkg.get("id") == package_id:
                return pkg
        return None

    def update_active_status(self, package_id: str) -> dict[str, Any]:
        """Update active_package_id in packages metadata (Mock implementation)."""
        data = self.load_packages()

        # Update is_active for all packages
        for pkg in data.get("packages", []):
            pkg["is_active"] = pkg.get("id") == package_id

        # Update native_version
        native = data.get("native_version", {})
        native["is_active"] = package_id == "native"
        data["native_version"] = native

        # Update active_package_id
        data["active_package_id"] = package_id if package_id != "native" else None
        data["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Update activated_at for the active package
        if package_id != "native":
            active_pkg = self.find_package_by_id(data, package_id)
            if active_pkg:
                active_pkg["activated_at"] = datetime.now(timezone.utc).isoformat()

        self.save_packages(data)
        logger.info("[AutoHarnessService] Updated active package: %s", package_id)

        return data

    def get_packages_info(self) -> dict[str, Any]:
        """Get packages info for frontend API."""
        return self.load_packages()

    async def activate_package(self, package_id: str) -> dict[str, Any]:
        """Activate a harness package by loading its config or resetting to native agent.

        Simplified activation flow:
        1. Check current active package from metadata
        2. If switching to native: unload current package (if any) via unload_harness_config
        3. If switching to another package: unload current package first, then load new one

        Args:
            package_id: The package ID to activate, or "native" to reset to original agent

        Returns:
            Payload for frontend response with activation details

        Raises:
            ValueError: Package not found or activation failed
        """
        logger.info(
            "[AutoHarnessService] activate_package called: package_id=%s, agent=%s",
            package_id,
            type(self._agent).__name__ if self._agent else None,
        )
        data = self.load_packages()
        current_active_id = data.get("active_package_id")

        # Step 1: Unload current active package if exists and different from target
        if current_active_id and current_active_id != package_id and self._agent is not None:
            current_package = self.find_package_by_id(data, current_active_id)
            if current_package:
                current_config_path = current_package.get("config_path", "")
                if current_config_path and Path(current_config_path).exists():
                    try:
                        unloaded = await self._agent.unload_harness_config(current_config_path)
                        logger.info(
                            "[AutoHarnessService] Unloaded current package %s: %s",
                            current_active_id,
                            unloaded
                        )
                    except FileNotFoundError:
                        logger.warning(
                            "[AutoHarnessService] Current package config not found, skip unload: %s",
                            current_config_path,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[AutoHarnessService] Failed to unload current package %s: %s",
                            current_active_id,
                            exc,
                        )

        # Step 2: Handle native agent (just update status, no load needed)
        if package_id == "native":
            self.update_active_status("native")
            logger.info("[AutoHarnessService] Switched to native agent")
            return {
                "activated_package_id": "native",
                "extension_name": "Native Agent",
                "runtime_path": "",
                "config_path": "",
                "message": "已切换回原生 Agent 模式",
            }

        # Step 3: Handle specific package activation
        package = self.find_package_by_id(data, package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")

        config_path = package.get("config_path", "")
        if not config_path:
            raise ValueError(f"Package {package_id} has no config_path")

        if not Path(config_path).exists():
            raise ValueError(f"Config file not found: {config_path}")

        if self._agent is None:
            logger.warning("[AutoHarnessService] No agent available for activation")
            self.update_active_status(package_id)
            return {
                "activated_package_id": package_id,
                "extension_name": package.get("extension_name", ""),
                "runtime_path": package.get("runtime_path", ""),
                "config_path": config_path,
                "message": "扩展已标记为激活（无 agent 实例）",
            }

        try:
            loaded_resources = await self._agent.load_harness_config(config_path)
            self.update_active_status(package_id)
            logger.info(
                "[AutoHarnessService] Activated package %s, loaded resources: %s",
                package_id,
                loaded_resources,
            )
            return {
                "activated_package_id": package_id,
                "extension_name": package.get("extension_name", ""),
                "runtime_path": package.get("runtime_path", ""),
                "config_path": config_path,
                "loaded_resources": loaded_resources,
                "message": f"扩展已热生效，加载资源: {len(loaded_resources)} 项",
            }
        except FileNotFoundError as exc:
            raise ValueError(f"配置文件不存在: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"激活扩展失败: {exc}") from exc
        except Exception as exc:
            logger.exception("[AutoHarnessService] Activate package %s failed: %s", package_id, exc)
            raise ValueError(f"激活扩展失败: {exc}") from exc

    def delete_package(self, package_id: str) -> dict[str, Any]:
        """Delete a package and optionally switch to native agent if it was active.

        Args:
            package_id: The package ID to delete

        Returns:
            Payload with deletion result and active package switch info if applicable
        """
        if package_id == "native":
            raise ValueError("Cannot delete native agent version")

        data = self.load_packages()
        package = self.find_package_by_id(data, package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")

        # Check if package is currently active
        was_active = package.get("is_active", False)
        active_package_id = data.get("active_package_id")

        # Delete runtime directory if exists
        runtime_path = package.get("runtime_path", "")
        if runtime_path:
            try:
                runtime_dir = Path(runtime_path)
                if runtime_dir.exists() and runtime_dir.is_dir():
                    shutil.rmtree(runtime_dir, ignore_errors=True)
                    logger.info("[AutoHarnessService] Deleted runtime directory: %s", runtime_path)
            except Exception as exc:
                logger.warning("[AutoHarnessService] Failed to delete runtime directory %s: %s", runtime_path, exc)

        # Remove package from list
        packages = data.get("packages", [])
        packages = [p for p in packages if p.get("id") != package_id]
        data["packages"] = packages

        # If deleted package was active, switch to native
        switched_to_native = False
        if was_active or active_package_id == package_id:
            data["active_package_id"] = None
            for pkg in packages:
                pkg["is_active"] = False
            native = data.get("native_version", {})
            native["is_active"] = True
            data["native_version"] = native
            switched_to_native = True
            logger.info("[AutoHarnessService] Deleted active package %s, switched to native agent", package_id)

        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save_packages(data)

        logger.info("[AutoHarnessService] Deleted package: %s", package_id)

        return {
            "deleted_package_id": package_id,
            "extension_name": package.get("extension_name", ""),
            "switched_to_native": switched_to_native,
            "message": f"已删除扩展版本 {package.get('extension_name', package_id)}",
        }

    def export_package(self, package_id: str) -> Path:
        """Create zip archive from selected package directory.

        Args:
            package_id: Package identifier to export

        Returns:
            Path to created zip file

        Raises:
            ValueError: Package not found, is native version, or runtime path missing
            RuntimeError: Zip creation failed
        """
        if package_id == "native":
            raise ValueError("Cannot export native version")

        data = self.load_packages()
        package = self.find_package_by_id(data, package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")

        runtime_path_str = package.get("runtime_path", "")
        if not runtime_path_str:
            raise ValueError(f"Package {package_id} has no runtime_path")

        runtime_path = Path(runtime_path_str)
        if not runtime_path.exists() or not runtime_path.is_dir():
            raise ValueError(f"Package runtime directory not found: {runtime_path}")

        extension_name = package.get("extension_name", "unknown")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_filename = f"{extension_name}_{timestamp}.zip"

        # Create temp exports directory
        exports_dir = self.data_dir / "temp" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        zip_path = exports_dir / zip_filename

        try:
            # Create zip from the package directory
            # The zip will contain the extension directory itself
            shutil.make_archive(
                str(zip_path.with_suffix("")),
                "zip",
                runtime_path.parent,
                runtime_path.name,
            )
            logger.info(
                "[AutoHarnessService] Exported package %s to %s",
                package_id,
                zip_path,
            )
            return zip_path
        except Exception as exc:
            logger.exception("[AutoHarnessService] Failed to create zip archive: %s", exc)
            raise RuntimeError(f"Failed to create export archive: {exc}") from exc

    def import_package(self, zip_path: Path) -> dict[str, Any]:
        """Import package from zip archive.

        Args:
            zip_path: Path to uploaded zip file

        Returns:
            New package info dict

        Raises:
            ValueError: Invalid zip, missing config, or name conflict
            RuntimeError: Import failed
        """
        # Validate zip file
        if not zip_path.exists() or not zip_path.is_file():
            raise ValueError(f"Zip file not found: {zip_path}")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                if not zf.namelist():
                    raise ValueError("Zip file is empty")
                # Check it's a valid zip
                bad_file = zf.testzip()
                if bad_file is not None:
                    raise ValueError(f"Invalid zip file: corrupted file {bad_file}")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid zip file format: {exc}") from exc

        # Extract to temp directory
        temp_extract_dir = self.data_dir / "temp" / "extracts"
        temp_extract_dir.mkdir(parents=True, exist_ok=True)
        extract_target = temp_extract_dir / f"extract_{uuid.uuid4().hex[:8]}"

        try:
            # Validate all paths in zip to prevent path traversal (Zip Slip)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    if not _is_safe_zip_path(extract_target, member):
                        raise ValueError(f"Unsafe path in zip file: {member}")
                zf.extractall(extract_target)
        except Exception as exc:
            logger.exception("[AutoHarnessService] Failed to extract zip: %s", exc)
            raise RuntimeError(f"Failed to extract zip file: {exc}") from exc

        # Find harness_config.yaml (check root and one level deep)
        config_path: Optional[Path] = None
        extracted_ext_dir: Optional[Path] = None

        # Check root level
        root_config = extract_target / "harness_config.yaml"
        if root_config.exists():
            config_path = root_config
            extracted_ext_dir = extract_target
        else:
            # Check one level deep
            for subdir in extract_target.iterdir():
                if subdir.is_dir():
                    sub_config = subdir / "harness_config.yaml"
                    if sub_config.exists():
                        config_path = sub_config
                        extracted_ext_dir = subdir
                        break

        if config_path is None or extracted_ext_dir is None:
            # Cleanup
            shutil.rmtree(extract_target, ignore_errors=True)
            raise ValueError("Zip must contain harness_config.yaml at root or one level deep")

        # Get extension_name from directory name (prefer config if readable)
        extension_name = extracted_ext_dir.name
        try:
            with config_path.open('r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
                if config_data.get("extension_name"):
                    extension_name = str(config_data["extension_name"]).strip()
        except Exception:
            # Use directory name as fallback
            pass

        # Check for name conflict
        data = self.load_packages()
        for existing_pkg in data.get("packages", []):
            if existing_pkg.get("extension_name") == extension_name:
                shutil.rmtree(extract_target, ignore_errors=True)
                raise ValueError(f"Package '{extension_name}' already exists")

        # Generate hash for parent directory
        parent_hash = uuid.uuid4().hex[:8]
        runtime_extensions_root = self.data_dir / "runtime_extensions"
        runtime_extensions_root.mkdir(parents=True, exist_ok=True)

        target_parent_dir = runtime_extensions_root / parent_hash
        target_ext_dir = target_parent_dir / extension_name

        # Move extracted directory to runtime_extensions
        try:
            target_parent_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(extracted_ext_dir), str(target_ext_dir))
        except Exception as exc:
            logger.exception("[AutoHarnessService] Failed to move extracted package: %s", exc)
            shutil.rmtree(extract_target, ignore_errors=True)
            raise RuntimeError(f"Failed to move extracted package: {exc}") from exc

        # Cleanup temp extract directory
        shutil.rmtree(extract_target, ignore_errors=True)

        # Register package
        new_package = {
            "id": self.generate_package_id(target_ext_dir),
            "extension_name": extension_name,
            "runtime_path": str(target_ext_dir.resolve()),
            "config_path": str((target_ext_dir / "harness_config.yaml").resolve()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "is_active": False,
            "version_label": "",
            "description": "",
        }

        data["packages"].append(new_package)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.save_packages(data)

        logger.info(
            "[AutoHarnessService] Imported package: id=%s, extension_name=%s",
            new_package["id"],
            extension_name,
        )

        return new_package

    async def create_scheduled_task(
        self,
        query: str,
        interval_hours: int,
        run_immediately: bool = False,
        model: Optional[Model] = None
    ) -> dict[str, Any]:
        """Create a new scheduled task.

        Args:
            query: The optimization goal/task description
            interval_hours: Execution interval in hours
            run_immediately: If True, trigger immediate execution after creation
            model: Model configuration from JiuwenClaw (model_name stored for execution)

        Returns:
            {"task_id": str, "next_run_time": str, "message": str}
        """
        if self._task_store is None:
            self._init_scheduler()
        if self._task_store is None:
            return {"error": "任务存储未初始化"}

        task_id = f"sch_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        # Set next_run based on run_immediately flag
        next_run = now + timedelta(hours=interval_hours)

        # Store just the model_name (model will be resolved from jiuwenclaw config at execution)
        model_name = None
        if model is not None:
            model_name = model.model_config.model_name

        task_data = {
            "task_id": task_id,
            "query": query,
            "interval_hours": interval_hours,
            "status": "pending",
            "created_at": now.isoformat(),
            "next_run_time": next_run.isoformat(),
            "current_execution_id": None,
            "execution_history": [],
            "model_name": model_name,
        }

        self._task_store.add_task(task_data)

        logger.info(
            "[AutoHarnessService] Created scheduled task: %s, interval=%sh, next_run=%s, run_immediately=%s",
            task_id, interval_hours, next_run.isoformat(), run_immediately
        )

        # Trigger immediate execution if requested
        if run_immediately:
            logger.info("[AutoHarnessService] run_immediately=True, scheduler=%s", self._scheduler)
            if self._scheduler is not None:
                triggered = await self._scheduler.trigger_immediate(task_id)
                logger.info("[AutoHarnessService] trigger_immediate result: %s", triggered)
            else:
                logger.warning("[AutoHarnessService] scheduler not initialized, cannot trigger immediate execution")

        return {
            "task_id": task_id,
            "next_run_time": next_run.isoformat(),
            "message": "定时任务已创建",
        }

    async def run_task(
        self,
        query: str,
        model: Optional[Model] = None
    ) -> dict[str, Any]:
        """Create and immediately execute a one-time task.

        Unlike scheduled tasks, this creates a task that runs once and then
        completes (no rescheduling). Uses the same infrastructure but with
        is_one_time=true flag.

        Args:
            query: The optimization goal/task description
            model: Model configuration from JiuwenClaw

        Returns:
            {"task_id": str, "status": "running", "message": str}
        """
        if self._task_store is None:
            self._init_scheduler()
        if self._task_store is None:
            return {"error": "任务存储未初始化"}

        task_id = f"sch_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        model_name = None
        if model is not None:
            model_name = model.model_config.model_name

        task_data = {
            "task_id": task_id,
            "query": query,
            "interval_hours": 0,  # Not applicable for one-time tasks
            "is_one_time": True,  # Mark as one-time task
            "status": "pending",
            "created_at": now.isoformat(),
            "next_run_time": now.isoformat(),  # Immediate execution
            "current_execution_id": None,
            "execution_history": [],
            "model_name": model_name,
        }

        self._task_store.add_task(task_data)

        logger.info(
            "[AutoHarnessService] Created one-time task: %s, will execute immediately",
            task_id
        )

        # Trigger immediate execution
        if self._scheduler is not None:
            triggered = await self._scheduler.trigger_immediate(task_id)
            logger.info("[AutoHarnessService] run_task trigger_immediate result: %s", triggered)
        else:
            logger.warning(
                "[AutoHarnessService] scheduler not initialized, cannot execute one-time task")

        return {
            "task_id": task_id,
            "status": "running",
            "message": "一次性任务已创建并开始执行",
        }

    async def cancel_scheduled_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a scheduled task.

        Args:
            task_id: Task identifier

        Returns:
            {"task_id": str, "status": str}
        """
        if self._task_store is None or self._scheduler is None:
            return {"error": "调度器未初始化"}

        # Cancel running execution if exists
        self._scheduler.cancel_execution(task_id)

        # Update status
        self._task_store.update_task(task_id, {"status": "cancelled"})

        logger.info("[AutoHarnessService] Cancelled scheduled task: %s", task_id)

        return {"task_id": task_id, "status": "cancelled"}

    async def delete_scheduled_task(self, task_id: str) -> dict[str, Any]:
        """Delete a scheduled task completely.

        Cancels running execution if exists, then removes task and all logs.

        Args:
            task_id: Task identifier

        Returns:
            {"task_id": str} or {"error": str}
        """
        if self._task_store is None:
            return {"error": "任务存储未初始化"}

        # Get task info before deletion
        task = self._task_store.get_task(task_id)
        if task is None:
            return {"error": "任务不存在", "task_id": task_id}

        # Cancel running execution if exists
        if self._scheduler is not None and task.get("status") == "running":
            self._scheduler.cancel_execution(task_id)

        # Delete task from store (includes removing log files)
        deleted = self._task_store.delete_task(task_id)
        if not deleted:
            return {"error": "删除失败", "task_id": task_id}

        logger.info("[AutoHarnessService] Deleted scheduled task: %s", task_id)

        return {"task_id": task_id}

    def get_scheduled_task_status(self, task_id: str) -> Optional[dict[str, Any]]:
        """Get task status and details.

        Returns:
            Task dict or None if not found
        """
        if self._task_store is None:
            return None
        return self._task_store.get_task(task_id)

    def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        """List all scheduled tasks.

        Returns:
            List of task dicts
        """
        if self._task_store is None:
            return []
        return self._task_store.list_tasks()

    def get_scheduled_task_logs(
        self,
        task_id: str,
        log_type: str = "current",
        history_index: int = -1,
        offset: int = 0,
        limit: int = 500
    ) -> dict[str, Any]:
        """Get logs for a task.

        Args:
            task_id: Task identifier
            log_type: "current" or "history"
            history_index: 0=latest completed, 1=second latest, etc.
            offset: Start reading from this line index (for streaming)
            limit: Maximum number of lines to return (default 500)

        Returns:
            Dict with logs or error
        """
        if self._task_store is None:
            return {"error": "任务存储未初始化"}
        return self._task_store.get_logs(task_id, log_type, history_index, offset, limit)
