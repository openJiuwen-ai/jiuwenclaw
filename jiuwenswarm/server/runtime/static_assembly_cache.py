"""Conservative process caches for immutable agent assembly inputs.

Only directory metadata and parsed ``SKILL.md`` descriptions are shared.
Session history, permissions, prompt attachments, request metadata and complete
skill bodies remain request/session scoped and are never cached here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.harness.rails import SkillUseRail
from watchfiles import awatch


logger = logging.getLogger(__name__)

_ENV_KEY = "JIUWENSWARM_STATIC_ASSEMBLY_CACHE"
_ON_VALUES = frozenset({"1", "true", "yes", "on"})


def static_assembly_cache_enabled() -> bool:
    """Return whether the opt-in static assembly fast path is enabled."""

    return str(os.environ.get(_ENV_KEY, "") or "").strip().lower() in _ON_VALUES


@dataclass(frozen=True, slots=True)
class _SkillDescriptionEntry:
    mtime_ns: int
    size: int
    description: str


_skill_descriptions: dict[str, _SkillDescriptionEntry] = {}
_skill_description_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class _SkillScanItem:
    key: str
    directory: str
    mtime: float
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _SkillScanEntry:
    root_fingerprint: tuple[tuple[str, int, int], ...]
    items: tuple[_SkillScanItem, ...]


_SkillScanKey = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
_skill_scans: dict[_SkillScanKey, _SkillScanEntry] = {}
_skill_scan_lock = threading.RLock()
_skill_scan_generation = 0
_skill_watch_tasks: dict[str, asyncio.Task[None]] = {}


async def _watch_skill_root(root: str) -> None:
    """Invalidate shared scans when the native watcher reports a change."""

    global _skill_scan_generation
    try:
        async for changes in awatch(root, recursive=True):
            # The cached products in this module are derived exclusively from
            # visible SKILL.md files. Runtime artifacts, checkpoints and
            # evolution projections can live below the same broad roots but
            # are refreshed by their own dynamic paths; treating those writes
            # as static-schema invalidations put full directory scans back on
            # the first-token path without changing the eventual prompt.
            if not any(Path(changed_path).name.casefold() == "skill.md" for _, changed_path in changes):
                continue
            with _skill_scan_lock:
                _skill_scan_generation += 1
                _skill_scans.clear()
    except asyncio.CancelledError:
        raise
    except OSError as exc:
        logger.warning("Static skill directory watcher stopped: root=%s error=%s", root, exc)
    finally:
        with _skill_scan_lock:
            if _skill_watch_tasks.get(root) is asyncio.current_task():
                _skill_watch_tasks.pop(root, None)


def _cancel_skill_watchers() -> None:
    with _skill_scan_lock:
        tasks = tuple(_skill_watch_tasks.values())
        _skill_watch_tasks.clear()
    for task in tasks:
        task.cancel()


def _ensure_skill_watchers(roots: tuple[Path, ...]) -> bool:
    """Watch roots once so request-time cache validation is an O(1) read."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with _skill_scan_lock:
        for root in roots:
            resolved = str(root)
            task = _skill_watch_tasks.get(resolved)
            if (task is None or task.done()) and root.is_dir():
                _skill_watch_tasks[resolved] = loop.create_task(_watch_skill_root(resolved))
    return True


def _watch_generation(rail: SkillUseRail) -> int | None:
    roots = getattr(rail, "_static_watch_roots", None)
    if roots is None:
        _, roots = _scan_key(rail)
    if not _ensure_skill_watchers(roots):
        return None
    with _skill_scan_lock:
        return _skill_scan_generation


def clear_static_assembly_cache() -> None:
    """Invalidate process-local immutable assembly products."""

    global _skill_scan_generation
    _cancel_skill_watchers()
    _skill_descriptions.clear()
    _skill_description_locks.clear()
    with _skill_scan_lock:
        _skill_scan_generation += 1
        _skill_scans.clear()


def _scan_key(rail: SkillUseRail) -> tuple[_SkillScanKey, tuple[Path, ...]]:
    roots = tuple(rail._normalize_skill_dirs(rail.skills_dir))  # pylint: disable=protected-access
    key: _SkillScanKey = (
        tuple(str(root) for root in roots),
        tuple(sorted(rail.enabled_skills)),
        tuple(sorted(rail.disabled_skills)),
    )
    return key, roots


def _root_fingerprint(roots: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    fingerprint: list[tuple[str, int, int]] = []
    for root in roots:
        try:
            stat = root.stat()
            is_dir = 1 if root.is_dir() else 0
            fingerprint.append((str(root), stat.st_mtime_ns, is_dir))
        except OSError:
            fingerprint.append((str(root), -1, 0))
    return tuple(fingerprint)


def _scan_entry_is_current(entry: _SkillScanEntry, roots: tuple[Path, ...]) -> bool:
    if entry.root_fingerprint != _root_fingerprint(roots):
        return False
    for item in entry.items:
        try:
            stat = (Path(item.directory) / "SKILL.md").stat()
        except OSError:
            return False
        if stat.st_mtime_ns != item.mtime_ns or stat.st_size != item.size:
            return False
    return True


def _scan_visible_skills(
    rail: SkillUseRail,
    roots: tuple[Path, ...],
) -> _SkillScanEntry:
    items: list[_SkillScanItem] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for directory in sorted(root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir() or not rail._is_skill_name_allowed(directory.name):  # pylint: disable=protected-access
                continue
            skill_md = directory / "SKILL.md"
            try:
                stat = skill_md.stat()
            except OSError:
                continue
            items.append(
                _SkillScanItem(
                    key=str(directory.resolve()),
                    directory=str(directory),
                    mtime=stat.st_mtime,
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                )
            )
    return _SkillScanEntry(root_fingerprint=_root_fingerprint(roots), items=tuple(items))


def _get_skill_scan(rail: SkillUseRail) -> _SkillScanEntry:
    key, roots = _scan_key(rail)
    with _skill_scan_lock:
        cached = _skill_scans.get(key)
        if cached is not None and _scan_entry_is_current(cached, roots):
            return cached
        scanned = _scan_visible_skills(rail, roots)
        _skill_scans[key] = scanned
        logger.debug(
            "Static skill directory cache fill: roots=%d visible=%d",
            len(roots),
            len(scanned.items),
        )
        return scanned


class StaticAssemblyCachedSkillUseRail(SkillUseRail):
    """SkillUseRail variant sharing fingerprinted immutable descriptions.

    Every Rail hook continues to run.  A hit only avoids re-reading and
    re-parsing the same front matter and re-enumerating unchanged skill roots
    in another session-owned Rail instance.
    """

    async def _refresh_skills_incrementally(self) -> None:
        if not static_assembly_cache_enabled():
            await super()._refresh_skills_incrementally()
            return

        if not self.enable_cache:
            self._skill_cache.clear()
            self._skill_update_at.clear()
            self._skill_order.clear()

        _, roots = _scan_key(self)
        self._static_watch_roots = roots
        scan = _get_skill_scan(self)
        discovered_keys: set[str] = set()
        ordered_keys: list[str] = []
        for item in scan.items:
            discovered_keys.add(item.key)
            ordered_keys.append(item.key)
            if (
                item.key not in self._skill_cache
                or self._skill_update_at.get(item.key) != item.mtime
            ):
                self._skill_cache[item.key] = await self._load_skill(
                    Path(item.directory),
                    item.mtime,
                )
                self._skill_update_at[item.key] = item.mtime

        for stale_key in tuple(self._skill_cache):
            if stale_key not in discovered_keys:
                self._skill_cache.pop(stale_key, None)
                self._skill_update_at.pop(stale_key, None)
        self._skill_order = [key for key in ordered_keys if key in self._skill_cache]
        # Do not start background resources merely because a Rail is being
        # built or tested. The first real BEFORE_INVOKE starts the watcher;
        # exact session prewarm records the generation populated here.
        with _skill_scan_lock:
            self._static_scan_generation = _skill_scan_generation

    async def before_invoke(self, ctx: Any) -> None:
        """Run the hook while avoiding unchanged synchronous directory scans."""

        if not static_assembly_cache_enabled():
            await super().before_invoke(ctx)
            return
        generation = _watch_generation(self)
        if generation is None or generation != getattr(self, "_static_scan_generation", None):
            await super().before_invoke(ctx)
            return
        # ``SkillUseRail.before_model_call`` refreshes evolution records again
        # immediately before rendering the skills attachment.  Re-reading the
        # same files here cannot affect the final prompt, but used to put a
        # duplicate filesystem pass on the first-token path.  Keep the Rail
        # hook and its session baseline semantics while leaving the one
        # authoritative dynamic refresh at BEFORE_MODEL_CALL.
        self._ensure_session_baseline(ctx)

    async def _refresh_skill_prompt_if_changed(self, ctx: Any) -> None:
        if not static_assembly_cache_enabled():
            await super()._refresh_skill_prompt_if_changed(ctx)
            return
        generation = _watch_generation(self)
        if generation is not None and generation == getattr(self, "_static_scan_generation", None):
            return
        await super()._refresh_skill_prompt_if_changed(ctx)

    def _build_skills_snapshot_signature(self) -> tuple[tuple[str, float], ...]:
        if not static_assembly_cache_enabled():
            return super()._build_skills_snapshot_signature()
        scan = _get_skill_scan(self)
        return tuple((item.key, item.mtime) for item in scan.items)

    async def _load_description(self, path: Path) -> str:
        if not static_assembly_cache_enabled():
            return await super()._load_description(path)

        resolved = str(path.expanduser().resolve())
        stat = path.stat()
        cached = _skill_descriptions.get(resolved)
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            logger.debug("Static skill description cache hit: path=%s", resolved)
            return cached.description

        lock = _skill_description_locks.setdefault(resolved, asyncio.Lock())
        async with lock:
            stat = path.stat()
            read_mtime_ns = stat.st_mtime_ns
            read_size = stat.st_size
            cached = _skill_descriptions.get(resolved)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
            ):
                logger.debug("Static skill description cache hit: path=%s", resolved)
                return cached.description

            description = await super()._load_description(path)
            # Re-stat after reading.  Do not associate an old parsed value with
            # a newer on-disk fingerprint when a writer races the read.
            stat = path.stat()
            if stat.st_mtime_ns == read_mtime_ns and stat.st_size == read_size:
                _skill_descriptions[resolved] = _SkillDescriptionEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    description=description,
                )
                logger.debug("Static skill description cache fill: path=%s", resolved)
            else:
                _skill_descriptions.pop(resolved, None)
                logger.debug("Static skill description cache skipped after concurrent change: path=%s", resolved)
            return description
