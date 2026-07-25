# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Skill 白名单：按租户同步 SkillHub URL 并产出 enabled_skills 目录名列表."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.skill_manager import SkillManager, _safe_rmtree
from jiuwenclaw.utils import _require_tenant_ids, get_tenant_agent_jiuwenclaw_workspace_dir

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".skill_whitelist_manifest.json"
_RESERVED_SKILL_DIR_NAMES = frozenset({MANIFEST_FILENAME, "_marketplace"})

# 同一 skills 物理目录上的白名单 sync 串行，避免多 session 并发下载/落盘
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
    """Gateway/DB 白名单项。同步判定仅看 ``source`` + ``version``."""

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


@dataclass
class _ManifestEntry:
    """sync 状态条目；内存索引与落盘 manifest 共用结构."""

    db_skill_id: str
    installed_dir: str
    skill_source: str
    skill_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "db_skill_id": self.db_skill_id,
            "installed_dir": self.installed_dir,
            "skill_source": self.skill_source,
            "skill_version": self.skill_version,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _ManifestEntry | None:
        db_id = str(raw.get("db_skill_id", "")).strip()
        installed = str(raw.get("installed_dir", "")).strip()
        source = str(raw.get("skill_source", "")).strip()
        version = str(raw.get("skill_version", "")).strip()
        if not db_id or not installed:
            return None
        return cls(
            db_skill_id=db_id,
            installed_dir=installed,
            skill_source=source,
            skill_version=version,
        )


def is_skill_whitelist_tenant(agent_id: str | None, service_id: str | None) -> bool:
    """ACP/default 或 ID 缺失的租户不启用白名单逻辑."""
    try:
        sid, aid = _require_tenant_ids(service_id, agent_id)
    except ValueError:
        return False
    if aid == "acp" and sid == "global_acp":
        return False
    if aid == "default" and sid == "default":
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
    """将白名单 SkillHub URL 同步到租户 jiuwenclaw_workspace/skills."""

    def __init__(self, service_id: str, agent_id: str) -> None:
        workspace = get_tenant_agent_jiuwenclaw_workspace_dir(service_id, agent_id)
        self._skills_dir = workspace / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._skills_dir / MANIFEST_FILENAME
        self._manager = SkillManager(workspace_dir=str(workspace))

    def load_manifest_entries(self) -> list[_ManifestEntry]:
        """读取并解析 manifest 文件中的条目列表（供外部查询使用）."""
        return self._load_manifest_entries()

    def _load_manifest_entries(self) -> list[_ManifestEntry]:
        if not self._manifest_path.is_file():
            return []
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[SkillWhitelist] load manifest failed: %s", exc)
            return []
        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        entries: list[_ManifestEntry] = []
        if not isinstance(raw_entries, list):
            return entries
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            entry = _ManifestEntry.from_dict(raw)
            if entry is not None:
                entries.append(entry)
        return entries

    def _save_manifest_entries(self, entries: list[_ManifestEntry]) -> None:
        payload = {"entries": [e.to_dict() for e in entries]}
        self._manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _source_version_key(skill_source: str, skill_version: str) -> tuple[str, str]:
        return skill_source.strip(), skill_version.strip()

    def _remove_installed_dir(self, installed_dir: str) -> None:
        if not installed_dir or installed_dir in _RESERVED_SKILL_DIR_NAMES:
            return
        target = self._skills_dir / installed_dir
        if target.is_dir():
            _safe_rmtree(target)

    @staticmethod
    def _find_entry_by_source_version(
        entries: list[_ManifestEntry],
        skill_source: str,
        skill_version: str,
    ) -> _ManifestEntry | None:
        key = SkillWhitelistSynchronizer._source_version_key(skill_source, skill_version)
        for entry in entries:
            if SkillWhitelistSynchronizer._source_version_key(entry.skill_source, entry.skill_version) == key:
                return entry
        return None

    @staticmethod
    def _upsert_entry(
        entries: list[_ManifestEntry],
        *,
        db_skill_id: str,
        installed_dir: str,
        skill_source: str,
        skill_version: str,
    ) -> list[_ManifestEntry]:
        """按 ``db_skill_id`` 追加或更新；同 ``(source, version)`` 可有多条 ``id``."""
        updated: list[_ManifestEntry] = []
        replaced = False
        for entry in entries:
            if entry.db_skill_id == db_skill_id:
                updated.append(
                    _ManifestEntry(
                        db_skill_id=db_skill_id,
                        installed_dir=installed_dir,
                        skill_source=skill_source,
                        skill_version=skill_version,
                    )
                )
                replaced = True
            else:
                updated.append(entry)
        if not replaced:
            updated.append(
                _ManifestEntry(
                    db_skill_id=db_skill_id,
                    installed_dir=installed_dir,
                    skill_source=skill_source,
                    skill_version=skill_version,
                )
            )
        return updated

    @staticmethod
    def _sync_installed_dir_for_source_version(
        entries: list[_ManifestEntry],
        *,
        skill_source: str,
        skill_version: str,
        installed_dir: str,
    ) -> list[_ManifestEntry]:
        """同键多 id 共享落盘目录：重下成功后统一更新 ``installed_dir``."""
        key = SkillWhitelistSynchronizer._source_version_key(skill_source, skill_version)
        return [
            _ManifestEntry(
                db_skill_id=entry.db_skill_id,
                installed_dir=installed_dir,
                skill_source=entry.skill_source,
                skill_version=entry.skill_version,
            )
            if SkillWhitelistSynchronizer._source_version_key(entry.skill_source, entry.skill_version) == key
            else entry
            for entry in entries
        ]

    @staticmethod
    def _installed_dir_ref_count(
        entries: list[_ManifestEntry], installed_dir: str
    ) -> int:
        return sum(1 for entry in entries if entry.installed_dir == installed_dir)

    def _should_remove_stale_installed_dir(
        self,
        entries: list[_ManifestEntry],
        existing: _ManifestEntry | None,
        new_installed_dir: str,
    ) -> bool:
        if existing is None:
            return False
        if not existing.installed_dir:
            return False
        if existing.installed_dir == new_installed_dir:
            return False
        if self._installed_dir_ref_count(entries, existing.installed_dir) > 1:
            return False
        return True

    async def sync(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult:
        """按 skills 物理目录串行同步：同一落盘目录同时仅一个 sync 在飞."""
        lock = await _skills_dir_sync_lock_for(self._skills_dir)
        async with lock:
            return await self._sync_locked(config)

    async def _sync_locked(self, config: AgentSkillWhitelistConfig) -> SkillWhitelistSyncResult:
        result = SkillWhitelistSyncResult()
        entries = self._load_manifest_entries()
        enabled_dirs: list[str] = []
        seen_dirs: set[str] = set()

        for item in config.items_with_source:
            existing = self._find_entry_by_source_version(
                entries, item.source, item.version
            )
            need_download = True
            installed_dir = existing.installed_dir if existing else ""

            if existing is not None:
                dir_path = self._skills_dir / existing.installed_dir
                if dir_path.is_dir() and (dir_path / "SKILL.md").is_file():
                    need_download = False
                    installed_dir = existing.installed_dir

            if need_download:
                try:
                    install_result = await asyncio.to_thread(
                        self._manager.install_skill_sync,
                        item.source,
                        True,
                        None,
                    )
                except Exception as exc:
                    msg = (
                        f"sync failed id={item.id} source={item.source}: {exc}"
                    )
                    logger.warning("[SkillWhitelist] %s", msg)
                    result.errors.append(msg)
                    if existing is not None:
                        installed_dir = existing.installed_dir
                    else:
                        continue
                else:
                    if not install_result.get("ok"):
                        detail = install_result.get("detail") or "install failed"
                        msg = (
                            f"sync failed id={item.id} source={item.source}: {detail}"
                        )
                        logger.warning("[SkillWhitelist] %s", msg)
                        result.errors.append(msg)
                        if existing is not None:
                            installed_dir = existing.installed_dir
                        else:
                            continue
                    else:
                        installed_dir = str(install_result.get("skill_name", "")).strip()
                        if not installed_dir:
                            msg = f"sync failed id={item.id}: empty skill_name"
                            logger.warning("[SkillWhitelist] %s", msg)
                            result.errors.append(msg)
                            continue
                        if self._should_remove_stale_installed_dir(entries, existing, installed_dir):
                            self._remove_installed_dir(existing.installed_dir)

                entries = self._sync_installed_dir_for_source_version(
                    entries,
                    skill_source=item.source,
                    skill_version=item.version,
                    installed_dir=installed_dir,
                )
                entries = self._upsert_entry(
                    entries,
                    db_skill_id=item.id,
                    installed_dir=installed_dir,
                    skill_source=item.source,
                    skill_version=item.version,
                )
            else:
                entries = self._upsert_entry(
                    entries,
                    db_skill_id=item.id,
                    installed_dir=installed_dir,
                    skill_source=item.source,
                    skill_version=item.version,
                )

            dir_path = self._skills_dir / installed_dir
            if dir_path.is_dir() and (dir_path / "SKILL.md").is_file():
                if installed_dir not in seen_dirs:
                    enabled_dirs.append(installed_dir)
                    seen_dirs.add(installed_dir)
            else:
                msg = (
                    f"installed dir missing after sync: id={item.id} dir={installed_dir}"
                )
                logger.warning("[SkillWhitelist] %s", msg)
                result.errors.append(msg)

        try:
            self._save_manifest_entries(entries)
        except OSError as exc:
            logger.warning("[SkillWhitelist] save manifest failed: %s", exc)
            result.errors.append(f"save manifest failed: {exc}")

        result.enabled_skill_dirs = enabled_dirs
        return result