# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Skill 白名单：按租户同步预制技能到盘 + ``installed_skill``，启用只信 DB."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.harness.common.installed_skill import (
    SOURCE_PREBUILT,
    SOURCE_USER,
    delete_installed_skill,
    list_installed_skills,
    upsert_installed_skill,
)
from jiuwenswarm.common.utils import _require_tenant_ids
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager, _safe_rmtree

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".skill_whitelist_manifest.json"  # 仅占位防误删；不再读写
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


def is_skill_whitelist_tenant(agent_id: str | None, service_id: str | None) -> bool:
    """ACP/default 或 ID 缺失的租户不启用白名单逻辑；仅 AGENT_RUNTIME 下生效."""
    if not os.getenv("AGENT_RUNTIME", "").strip():
        return False
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
    """将预制技能同步到租户 skills/，并写入 ``installed_skill``（按 skill 原子）."""

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

    def _should_download_prebuilt(
        self,
        item: SkillWhitelistItem,
        installed_skills_map: dict[str, dict[str, Any]],
    ) -> tuple[bool, str]:
        """是否需要下载预制包。返回 (need_download, db_skill_name)."""
        by_source: dict[str, Any] | None = None
        source = str(item.source or "").strip()
        db_row: dict[str, Any] | None = None
        for row in installed_skills_map.values():
            if str(row.get("source_type")) != SOURCE_PREBUILT:
                continue
            if str(row.get("skill_id") or "").strip() == item.id:
                db_row = row
                break
            if (
                by_source is None
                and source
                and str(row.get("skill_source") or "").strip() == source
            ):
                by_source = row
        else:
            db_row = by_source

        if db_row is None:
            return True, ""
        db_skill_name = str(db_row.get("skill_name") or "").strip()
        if not db_skill_name:
            return True, ""
        if not self._skill_dir_ready(self._skills_dir, db_skill_name):
            return True, db_skill_name
        same_version = (
            str(db_row.get("skill_version") or "").strip() == item.version.strip()
        )
        if same_version:
            return False, db_skill_name
        return True, db_skill_name

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
        """持锁同步：对齐模板预制 → 剔除多余 → 刷新启用集."""
        result = SkillWhitelistSyncResult()
        installed_skills_map = await self._fetch_installed_skills_map(result)
        if installed_skills_map is None:
            return result

        kept_prebuilt_names: set[str] = set()
        for item in config.items_with_source:
            try:
                outcome = await self._ensure_prebuilt_installed(item, installed_skills_map)
            except Exception as exc:  # noqa: BLE001
                msg = f"sync failed id={item.id} source={item.source}: {exc}"
                logger.warning("[SkillWhitelist] %s", msg)
                self._mark_failed(
                    result,
                    skill_name="",
                    error_code="sync_exception",
                    error_message=msg,
                )
                continue
            self._apply_prebuilt_outcome(
                outcome, installed_skills_map, kept_prebuilt_names, result
            )

        await self._remove_prebuilt_not_in_template(
            installed_skills_map, kept_prebuilt_names, result
        )
        result.enabled_skill_dirs = list(installed_skills_map.keys())
        return result

    async def _fetch_installed_skills_map(
        self, result: SkillWhitelistSyncResult
    ) -> dict[str, dict[str, Any]] | None:
        """查询租户已装技能，返回 skill_name -> 行；失败时写 result 并返回 None."""
        try:
            existing_rows = await list_installed_skills(
                service_id=self._service_id,
                agent_id=self._agent_id,
            )
        except Exception as exc:
            msg = f"list installed_skill failed: {exc}"
            logger.warning("[SkillWhitelist] %s", msg)
            self._mark_failed(
                result,
                skill_name="",
                error_code="db_list_failed",
                error_message=msg,
            )
            return None
        return {
            str(r.get("skill_name") or "").strip(): r
            for r in existing_rows
            if str(r.get("skill_name") or "").strip()
        }

    def _apply_prebuilt_outcome(
        self,
        outcome: dict[str, Any],
        installed_skills_map: dict[str, dict[str, Any]],
        kept_prebuilt_names: set[str],
        result: SkillWhitelistSyncResult,
    ) -> None:
        """根据单项 sync 结果更新 kept 集合与本地索引（不再回读 DB）."""
        if outcome.get("ok"):
            name = str(outcome.get("skill_name") or "").strip()
            if not name:
                return
            kept_prebuilt_names.add(name)
            result.succeeded.append(name)
            row = outcome.get("row")
            if isinstance(row, dict) and row:
                installed_skills_map[name] = row
            return

        keep = str(outcome.get("skill_name") or "").strip()
        if (
            keep
            and keep in installed_skills_map
            and str(installed_skills_map[keep].get("source_type")) == SOURCE_PREBUILT
        ):
            kept_prebuilt_names.add(keep)
        self._mark_failed(
            result,
            skill_name=str(outcome.get("skill_name") or ""),
            error_code=str(outcome.get("error_code") or "sync_failed"),
            error_message=str(outcome.get("error_message") or "sync failed"),
        )

    async def _remove_prebuilt_not_in_template(
        self,
        installed_skills_map: dict[str, dict[str, Any]],
        kept_prebuilt_names: set[str],
        result: SkillWhitelistSyncResult,
    ) -> None:
        """当前模板里没有的预制技能：硬删盘+库（不降回 user）."""
        for name, row in list(installed_skills_map.items()):
            if str(row.get("source_type")) != SOURCE_PREBUILT:
                continue
            if name in kept_prebuilt_names:
                continue
            try:
                self._remove_installed_dir(name)
                await delete_installed_skill(
                    service_id=self._service_id,
                    agent_id=self._agent_id,
                    skill_name=name,
                )
                result.succeeded.append(f"removed:{name}")
                installed_skills_map.pop(name, None)
            except Exception as exc:  # noqa: BLE001
                msg = f"remove prebuilt failed name={name}: {exc}"
                logger.warning("[SkillWhitelist] %s", msg)
                self._mark_failed(
                    result,
                    skill_name=name,
                    error_code="remove_failed",
                    error_message=msg,
                )

    async def _ensure_prebuilt_installed(
        self,
        item: SkillWhitelistItem,
        installed_skills_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """确保模板项对应的预制已就绪：按需下载落盘 → 校验目录 → upsert 账本."""
        need_download, db_skill_name = self._should_download_prebuilt(
            item, installed_skills_map
        )

        installed_dir = db_skill_name
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
                    "skill_name": db_skill_name,
                    "error_code": "download_failed",
                    "error_message": f"sync failed id={item.id} source={item.source}: {exc}",
                }
            if not install_result.get("ok"):
                detail = install_result.get("detail") or "install failed"
                return {
                    "ok": False,
                    "skill_name": db_skill_name,
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
            if db_skill_name and db_skill_name != installed_dir:
                self._remove_installed_dir(db_skill_name)

        if not self._skill_dir_ready(self._skills_dir, installed_dir):
            return {
                "ok": False,
                "skill_name": installed_dir,
                "error_code": "dir_missing",
                "error_message": (
                    f"installed dir missing after sync: id={item.id} dir={installed_dir}"
                ),
            }

        conflict = installed_skills_map.get(installed_dir)
        if conflict is not None and str(conflict.get("source_type")) == SOURCE_USER:
            logger.info(
                "[SkillWhitelist] promote user→prebuilt skill_name=%s",
                installed_dir,
            )

        try:
            row = await upsert_installed_skill(
                service_id=self._service_id,
                agent_id=self._agent_id,
                skill_name=installed_dir,
                source_type=SOURCE_PREBUILT,
                skill_source=item.source,
                skill_version=item.version,
                skill_id=item.id,
                group_id=self._group_id,
                bot_id=self._bot_id,
                user_id=None,
            )
        except Exception as exc:
            if need_download:
                self._remove_installed_dir(installed_dir)
            return {
                "ok": False,
                "skill_name": installed_dir,
                "error_code": "db_write_failed",
                "error_message": f"write DB failed id={item.id} name={installed_dir}: {exc}",
            }

        if db_skill_name and db_skill_name != installed_dir:
            installed_skills_map.pop(db_skill_name, None)

        return {"ok": True, "skill_name": installed_dir, "row": row}
