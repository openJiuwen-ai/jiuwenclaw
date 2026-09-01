# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Skill 白名单：按租户同步预制技能到盘；启用集以磁盘为准（暂不依赖 installed_skill 表）."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.common.local_env_config import is_enterprise
from jiuwenswarm.common.utils import _require_tenant_ids
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager, _safe_rmtree

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".skill_whitelist_manifest.json"
_RESERVED_SKILL_DIR_NAMES = frozenset({MANIFEST_FILENAME, "_marketplace", "skills_state.json"})

_SKILLS_DIR_SYNC_LOCKS: dict[str, asyncio.Lock] = {}
_SKILLS_DIR_SYNC_LOCKS_META = threading.Lock()


async def _skills_dir_sync_lock_for(skills_dir: Path) -> asyncio.Lock:
    key = str(skills_dir.resolve())
    with _SKILLS_DIR_SYNC_LOCKS_META:
        lock = _SKILLS_DIR_SYNC_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SKILLS_DIR_SYNC_LOCKS[key] = lock
        return lock


@dataclass
class SkillWhitelistItem:
    """Gateway/DB 白名单项。增量判定看 ``skill_name`` 定位后比 version / skill_id."""

    id: str
    version: str
    source: str


@dataclass
class AgentSkillWhitelistConfig:
    agent_id: str
    service_id: str
    skills: list[SkillWhitelistItem] = field(default_factory=list)

    @property
    def items_with_source(self) -> list[SkillWhitelistItem]:
        out: list[SkillWhitelistItem] = []
        for item in self.skills:
            if str(item.source or "").strip():
                out.append(item)
        return out


@dataclass
class SkillWhitelistSyncResult:
    enabled_skill_dirs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    ok: bool = True


@dataclass
class _PrebuiltSyncState:
    """单次 sync 过程中共享的 manifest / 保留集 / 结果."""

    manifest: dict[str, dict[str, str]]
    kept_ids: set[str]
    kept_names: set[str]
    result: SkillWhitelistSyncResult


def is_skill_whitelist_tenant(agent_id: str | None, service_id: str | None) -> bool:
    """ACP 或 ID 缺失的租户不启用白名单逻辑；仅企业版下生效."""
    if not is_enterprise():
        return False
    try:
        sid, aid = _require_tenant_ids(service_id, agent_id)
    except ValueError:
        return False
    if aid == "acp" and sid == "global_acp":
        return False
    return True


def parse_agent_skill_whitelist(
    agent_id: str,
    service_id: str,
    skills: list[dict[str, Any]] | None,
) -> AgentSkillWhitelistConfig:
    """解析 gateway 返回的 skills 列表（字段：``id``、``version``、``source``）."""
    items: list[SkillWhitelistItem] = []
    for raw in skills or []:
        if not isinstance(raw, dict):
            continue
        skill_id = str(raw.get("skill_id", "")).strip()
        if not skill_id:
            continue
        items.append(
            SkillWhitelistItem(
                id=skill_id,
                version=str(raw.get("skill_version", "")).strip(),
                source=str(raw.get("skill_source", "")).strip(),
            )
        )
    return AgentSkillWhitelistConfig(agent_id=agent_id, service_id=service_id, skills=items)


class SkillWhitelistSynchronizer:
    """将预制技能同步到租户 skills/；以本地 manifest + 磁盘为准，不读写 installed_skill."""

    def __init__(
        self,
        workspace_dir: str | Path,
        service_id: str,
        agent_id: str,
        *,
        group_id: str | None = None,
        bot_id: str | None = None,
    ) -> None:
        workspace = Path(workspace_dir)
        self._service_id = service_id
        self._agent_id = agent_id
        self._group_id = group_id
        self._bot_id = bot_id
        self._skills_dir = workspace / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._skills_dir / MANIFEST_FILENAME
        self._manager = SkillManager(
            workspace_dir=str(workspace),
            persist_skills_state=False,
            service_id=service_id,
            agent_id=agent_id,
        )

    def _remove_installed_dir(self, installed_dir: str) -> None:
        if not installed_dir or installed_dir in _RESERVED_SKILL_DIR_NAMES:
            return
        target = self._skills_dir / installed_dir
        if target.is_dir():
            _safe_rmtree(target)

    @staticmethod
    def _skill_dir_ready(skills_dir: Path, skill_name: str) -> bool:
        if not skill_name:
            return False
        path = skills_dir / skill_name
        return path.is_dir() and (path / "SKILL.md").is_file()

    def _load_manifest(self) -> dict[str, dict[str, str]]:
        """manifest: skill_id -> {skill_name, version, source}."""
        if not self._manifest_path.is_file():
            return {}
        try:
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[SkillWhitelist] load manifest failed: %s", exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for skill_id, meta in raw.items():
            sid = str(skill_id or "").strip()
            if not sid or not isinstance(meta, dict):
                continue
            out[sid] = {
                "skill_name": str(meta.get("skill_name") or "").strip(),
                "version": str(meta.get("version") or "").strip(),
                "source": str(meta.get("source") or "").strip(),
            }
        return out

    def _save_manifest(self, manifest: dict[str, dict[str, str]]) -> None:
        payload = {
            sid: {
                "skill_name": meta.get("skill_name", ""),
                "version": meta.get("version", ""),
                "source": meta.get("source", ""),
            }
            for sid, meta in manifest.items()
            if str(sid).strip()
        }
        try:
            self._manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[SkillWhitelist] save manifest failed: %s", exc)

    def _list_ready_skill_dirs(self) -> list[str]:
        if not self._skills_dir.is_dir():
            return []
        names: list[str] = []
        try:
            children = sorted(self._skills_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            logger.warning("[SkillWhitelist] list skills dir failed: %s", exc)
            return []
        for child in children:
            name = child.name
            if name in _RESERVED_SKILL_DIR_NAMES or not child.is_dir():
                continue
            if self._skill_dir_ready(self._skills_dir, name):
                names.append(name)
        return names

    def _should_download_prebuilt(
        self,
        item: SkillWhitelistItem,
        manifest: dict[str, dict[str, str]],
    ) -> tuple[bool, str]:
        """是否需要下载预制包。返回 (need_download, disk_skill_name)."""
        meta = manifest.get(item.id)
        if meta is None:
            # 同 source 已装过时复用目录名，避免重复下载
            source = str(item.source or "").strip()
            if source:
                for other in manifest.values():
                    if str(other.get("source") or "").strip() == source:
                        name = str(other.get("skill_name") or "").strip()
                        if name and self._skill_dir_ready(self._skills_dir, name):
                            same_version = (
                                str(other.get("version") or "").strip() == item.version.strip()
                            )
                            if same_version:
                                return False, name
                            return True, name
            return True, ""

        disk_skill_name = str(meta.get("skill_name") or "").strip()
        if not disk_skill_name:
            return True, ""
        if not self._skill_dir_ready(self._skills_dir, disk_skill_name):
            return True, disk_skill_name
        same_version = str(meta.get("version") or "").strip() == item.version.strip()
        if same_version:
            return False, disk_skill_name
        return True, disk_skill_name

    @staticmethod
    def _mark_failed(
        result: SkillWhitelistSyncResult,
        *,
        skill_name: str,
        error_code: str,
        error_message: str,
    ) -> None:
        result.ok = False
        result.errors.append(error_message)
        result.failed.append(
            {
                "skill_name": skill_name,
                "error_code": error_code,
                "error_message": error_message,
            }
        )

    async def sync(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult:
        """按 skills 物理目录串行同步：同一落盘目录同时仅一个 sync 在飞."""
        lock = await _skills_dir_sync_lock_for(self._skills_dir)
        async with lock:
            return await self._run_sync(config)

    async def _run_sync(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult:
        """持锁同步：下载落盘 → 更新 manifest → 启用集=磁盘就绪目录."""
        state = _PrebuiltSyncState(
            manifest=self._load_manifest(),
            kept_ids=set(),
            kept_names=set(),
            result=SkillWhitelistSyncResult(),
        )

        for item in config.items_with_source:
            try:
                outcome = await self._ensure_prebuilt_installed(item, state.manifest)
            except Exception as exc:  # noqa: BLE001
                msg = f"sync failed id={item.id} source={item.source}: {exc}"
                logger.warning("[SkillWhitelist] %s", msg)
                self._mark_failed(
                    state.result,
                    skill_name="",
                    error_code="sync_exception",
                    error_message=msg,
                )
                continue
            self._apply_prebuilt_outcome(outcome, item, state)

        self._remove_prebuilt_not_in_template(state)
        self._save_manifest(state.manifest)
        # 启用集：白名单成功落盘的 + 盘上其它就绪目录（用户自装等）
        enabled = list(dict.fromkeys([*state.kept_names, *self._list_ready_skill_dirs()]))
        state.result.enabled_skill_dirs = enabled
        logger.info(
            "[SkillWhitelist] disk sync done agent=%s service=%s enabled=%s succeeded=%s errors=%s",
            self._agent_id,
            self._service_id,
            enabled,
            state.result.succeeded,
            state.result.errors,
        )
        return state.result

    async def reconcile_disk_into_ledger(self) -> SkillWhitelistSyncResult:
        """扫描磁盘就绪技能作为启用集（不再写 installed_skill）."""
        lock = await _skills_dir_sync_lock_for(self._skills_dir)
        async with lock:
            result = SkillWhitelistSyncResult()
            result.enabled_skill_dirs = self._list_ready_skill_dirs()
            return result

    def _apply_prebuilt_outcome(
        self,
        outcome: dict[str, Any],
        item: SkillWhitelistItem,
        state: _PrebuiltSyncState,
    ) -> None:
        if outcome.get("ok"):
            name = str(outcome.get("skill_name") or "").strip()
            if not name:
                return
            state.kept_ids.add(item.id)
            state.kept_names.add(name)
            state.result.succeeded.append(name)
            state.manifest[item.id] = {
                "skill_name": name,
                "version": item.version,
                "source": item.source,
            }
            return

        keep = str(outcome.get("skill_name") or "").strip()
        if keep and self._skill_dir_ready(self._skills_dir, keep):
            state.kept_ids.add(item.id)
            state.kept_names.add(keep)
            if item.id not in state.manifest:
                state.manifest[item.id] = {
                    "skill_name": keep,
                    "version": item.version,
                    "source": item.source,
                }
        self._mark_failed(
            state.result,
            skill_name=str(outcome.get("skill_name") or ""),
            error_code=str(outcome.get("error_code") or "sync_failed"),
            error_message=str(outcome.get("error_message") or "sync failed"),
        )

    def _remove_prebuilt_not_in_template(self, state: _PrebuiltSyncState) -> None:
        """当前模板里没有的预制：删盘 + 清 manifest."""
        for skill_id in list(state.manifest.keys()):
            if skill_id in state.kept_ids:
                continue
            meta = state.manifest.pop(skill_id, {}) or {}
            name = str(meta.get("skill_name") or "").strip()
            if not name or name in state.kept_names:
                continue
            # 仍被其它 id 引用则保留目录
            still_used = any(
                str(m.get("skill_name") or "").strip() == name for m in state.manifest.values()
            )
            if still_used:
                continue
            try:
                self._remove_installed_dir(name)
                state.result.succeeded.append(f"removed:{name}")
            except Exception as exc:  # noqa: BLE001
                msg = f"remove prebuilt failed name={name}: {exc}"
                logger.warning("[SkillWhitelist] %s", msg)
                self._mark_failed(
                    state.result,
                    skill_name=name,
                    error_code="remove_failed",
                    error_message=msg,
                )

    async def _ensure_prebuilt_installed(
        self,
        item: SkillWhitelistItem,
        manifest: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """确保模板项对应的预制已就绪：按需下载落盘 → 校验目录."""
        need_download, disk_skill_name = self._should_download_prebuilt(item, manifest)

        installed_dir = disk_skill_name
        if need_download:
            try:
                install_result = await asyncio.to_thread(
                    self._manager.install_skill_sync,
                    item.source,
                    True,
                    None,
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "skill_name": disk_skill_name,
                    "error_code": "download_failed",
                    "error_message": f"sync failed id={item.id} source={item.source}: {exc}",
                }
            if not install_result.get("ok"):
                detail = install_result.get("detail") or "install failed"
                return {
                    "ok": False,
                    "skill_name": disk_skill_name,
                    "error_code": "install_failed",
                    "error_message": f"sync failed id={item.id} source={item.source}: {detail}",
                }
            installed_dir = str(install_result.get("skill_name", "")).strip()
            if not installed_dir:
                return {
                    "ok": False,
                    "skill_name": "",
                    "error_code": "empty_skill_name",
                    "error_message": f"sync failed id={item.id}: empty skill_name",
                }
            if disk_skill_name and disk_skill_name != installed_dir:
                still_used = any(
                    sid != item.id
                    and str(m.get("skill_name") or "").strip() == disk_skill_name
                    for sid, m in manifest.items()
                )
                if not still_used:
                    self._remove_installed_dir(disk_skill_name)

        if not self._skill_dir_ready(self._skills_dir, installed_dir):
            return {
                "ok": False,
                "skill_name": installed_dir,
                "error_code": "dir_missing",
                "error_message": (
                    f"installed dir missing after sync: id={item.id} dir={installed_dir}"
                ),
            }

        return {"ok": True, "skill_name": installed_dir}
