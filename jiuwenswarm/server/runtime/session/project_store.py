"""项目存储模块 — projects.json 的持久化与 CRUD。

存储位置: ``get_agent_root_dir() / "projects.json"``(与 ``sessions/`` 目录同级)。

并发安全:
  - 文件级锁(跨进程): 使用 ``<file>.lock`` 伴生锁文件,Windows 用 ``msvcrt.locking``,
    Unix 用 ``fcntl.flock``。锁文件不被 ``os.replace`` 覆盖,保证跨进程互斥。
  - 原子写: 先写 ``.tmp`` 再 ``os.replace``(配合 ``fsync``),避免断电留下半文件。
  - 内存缓存(进程内): 读走缓存(快路径),``cache_bust=True`` 强制读盘用于跨进程同步;
    写在文件锁内重读磁盘 → 变更 → 原子写回 → 刷新缓存,保证多进程一致。

project_id 格式: ``proj_`` + 8 位 hex(由 ``secrets.token_hex`` 生成)。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from jiuwenswarm.common.utils import get_agent_root_dir

logger = logging.getLogger(__name__)

_VERSION = 1
_PROJECT_ID_PREFIX = "proj_"
_PROJECT_ID_HEX_LEN = 8  # proj_ 后跟 8 位 hex

# 进程内缓存 + 锁
_CACHE: list[dict[str, Any]] | None = None
_CACHE_LOCK = threading.Lock()

# 跨平台文件锁(与 a2x ownership 一致的实现,自包含以避免跨模块耦合)
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_SEC = 10.0

if sys.platform == "win32":
    import msvcrt

    def _acquire(fd: int) -> None:
        # LK_LOCK 自带 ~10s 重试,超时抛 OSError
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _release(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl
    import time

    def _acquire(fd: int) -> None:
        deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    # 保留原始 BlockingIOError 的调用栈,便于排查锁竞争来源
                    raise OSError("timeout acquiring projects.json lock") from exc
                time.sleep(0.05)

    def _release(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def _file_lock(data_path: Path) -> Iterator[None]:
    """跨进程文件锁。锁文件为 ``<data_path>.lock``,与数据文件分离,
    因此数据文件的原子替换不会破坏锁。"""
    data_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = data_path.with_suffix(data_path.suffix + _LOCK_SUFFIX)
    with open(lock_path, "a+b") as f:
        # Windows 需要至少 1 字节才能锁定
        if os.fstat(f.fileno()).st_size == 0:
            f.write(b"\x00")
            f.flush()
        f.seek(0)
        _acquire(f.fileno())
        try:
            yield
        finally:
            _release(f.fileno())


@dataclass
class Project:
    """项目实体(对应 projects.json 中单个项目记录)。"""

    project_id: str
    name: str
    project_path: str
    pinned: bool = False
    pin_order: int = 0
    hidden: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        return cls(
            project_id=str(d.get("project_id", "")),
            name=str(d.get("name", "")),
            project_path=str(d.get("project_path", "")),
            pinned=bool(d.get("pinned", False)),
            pin_order=int(d.get("pin_order", 0)),
            hidden=bool(d.get("hidden", False)),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )


# ── 内部读写(均在已持有文件锁时调用) ─────────────────────────────────────────


def _projects_file() -> Path:
    return get_agent_root_dir() / "projects.json"


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _gen_project_id() -> str:
    # token_hex(n) 返回 2n 位 hex 字符
    return f"{_PROJECT_ID_PREFIX}{secrets.token_hex(_PROJECT_ID_HEX_LEN // 2)}"


def _read_disk_locked(path: Path) -> list[dict[str, Any]]:
    """在文件锁内读取磁盘(调用方须已加锁)。文件缺失/损坏时返回空列表。"""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    projects = raw.get("projects")
    if not isinstance(projects, list):
        return []
    return [p for p in projects if isinstance(p, dict)]


def _fsync_dir(directory: Path) -> None:
    """fsync 父目录,确保 ``os.replace`` 的目录项落盘(断电耐久性)。

    Windows 无法对目录 fsync(``os.open`` 目录语义不同),跳过;
    Unix 下打开目录 fd 并 fsync。
    """
    if sys.platform == "win32":
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _write_disk_locked(path: Path, projects: list[dict[str, Any]]) -> None:
    """在文件锁内原子写入(调用方须已加锁)。"""
    data = {"version": _VERSION, "projects": projects}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    # fsync 父目录,确保 replace 的目录项持久化(断电后不丢文件)
    _fsync_dir(path.parent)


_T = TypeVar("_T")


def _mutate(fn: Callable[[list[dict[str, Any]]], _T]) -> _T:
    """在文件锁保护下: 重读磁盘 → 应用变更 → 原子写回 → 刷新缓存。

    重读磁盘确保拿到其他进程的最新写入,避免基于陈旧缓存做变更而丢失更新。
    """
    global _CACHE
    path = _projects_file()
    with _file_lock(path):
        projects = _read_disk_locked(path)
        result = fn(projects)
        _write_disk_locked(path, projects)
        with _CACHE_LOCK:
            _CACHE = [dict(p) for p in projects]
        return result


def _load_cache(cache_bust: bool = False) -> list[dict[str, Any]]:
    """读取缓存;``cache_bust=True`` 强制读盘(跨进程同步场景)。"""
    global _CACHE
    if not cache_bust:
        with _CACHE_LOCK:
            if _CACHE is not None:
                return [dict(p) for p in _CACHE]
    path = _projects_file()
    with _file_lock(path):
        raw = _read_disk_locked(path)
    with _CACHE_LOCK:
        _CACHE = [dict(p) for p in raw]
    return [dict(p) for p in raw]


# ── 公共 CRUD 原语 ───────────────────────────────────────────────────────────


def get_project_by_id(
    project_id: str, *, cache_bust: bool = False
) -> Project | None:
    """按 project_id 查找项目(默认项目不入库,不会命中)。"""
    for p in _load_cache(cache_bust):
        if p.get("project_id") == project_id:
            return Project.from_dict(p)
    return None


def get_project_by_path(
    project_path: str, *, cache_bust: bool = False
) -> Project | None:
    """按 project_path 查找项目(不限 hidden 状态,由调用方判断)。

    用于 project.create 的冲突检测与隐藏项目自动恢复。
    """
    for p in _load_cache(cache_bust):
        if p.get("project_path") == project_path:
            return Project.from_dict(p)
    return None


def list_projects(
    *, include_hidden: bool = False, cache_bust: bool = False
) -> list[Project]:
    """列出项目。``include_hidden=False``(默认)时排除已软删除项目。"""
    result: list[Project] = []
    for p in _load_cache(cache_bust):
        if not include_hidden and p.get("hidden"):
            continue
        result.append(Project.from_dict(p))
    return result


class ProjectPathConflict(Exception):
    """``project_path`` 与已有可见项目重复(由 ``create_or_restore_project`` 在锁内抛出)。"""


def _gen_unique_project_id(existing_projects: list[dict[str, Any]]) -> str:
    """生成不与现有 ``project_id`` 冲突的 ID(须在文件锁内调用)。

    32 位熵下碰撞概率极低,此处查重+重生成仅为万无一失。
    """
    existing_ids = {p.get("project_id") for p in existing_projects}
    new_id = _gen_project_id()
    while new_id in existing_ids:
        new_id = _gen_project_id()
    return new_id


def create_project(name: str, project_path: str) -> Project:
    """新建项目并持久化(不做 ``project_path`` 去重,供内部/测试使用)。

    本函数不检测 ``project_path`` 是否与已有项目重复,调用方需自行保证;
    ``project_id`` 在锁内查重+重生成,避免碰撞。生产路径请用
    ``create_or_restore_project``(原子完成查重/恢复/新建,无 TOCTOU 窗口)。
    """
    def _do(projects: list[dict[str, Any]]) -> Project:
        now = _now()
        proj = Project(
            project_id=_gen_unique_project_id(projects),
            name=name,
            project_path=project_path,
            created_at=now,
            updated_at=now,
        )
        projects.append(proj.to_dict())
        return proj

    return _mutate(_do)


def create_or_restore_project(name: str, project_path: str) -> tuple[Project, bool]:
    """原子地新建或恢复项目(在文件锁内完成查重/恢复/新建,关闭 TOCTOU 窗口)。

    - ``project_path`` 命中已隐藏项目 → 恢复(置 ``hidden=False``,更新 ``name``),
      返回 ``(proj, True)``;
    - ``project_path`` 命中可见项目 → 抛 ``ProjectPathConflict``;
    - 无匹配 → 新建(``project_id`` 锁内查重+重生成),返回 ``(proj, False)``。

    整个操作在单次 ``_mutate`` 内完成,查重与写入同锁,无 check-then-use 窗口。
    """
    def _do(projects: list[dict[str, Any]]) -> tuple[Project, bool]:
        for p in projects:
            if p.get("project_path") != project_path:
                continue
            if p.get("hidden"):
                # 命中隐藏项目 → 自动恢复
                p["hidden"] = False
                p["name"] = name
                p["updated_at"] = _now()
                return Project.from_dict(p), True
            # 命中可见项目 → 冲突
            raise ProjectPathConflict(project_path)
        # 无匹配 → 新建
        now = _now()
        proj = Project(
            project_id=_gen_unique_project_id(projects),
            name=name,
            project_path=project_path,
            created_at=now,
            updated_at=now,
        )
        projects.append(proj.to_dict())
        return proj, False

    return _mutate(_do)


def save_project(project: Project) -> Project:
    """更新已有项目(upsert: 按 project_id 匹配,命中则替换,未命中则追加)。

    刷新 ``updated_at``。调用方通常先 ``get_project_by_id`` 确认存在后再调用。
    """
    def _do(projects: list[dict[str, Any]]) -> Project:
        d = project.to_dict()
        d["updated_at"] = _now()
        for i, p in enumerate(projects):
            if p.get("project_id") == project.project_id:
                projects[i] = d
                return project
        projects.append(d)
        return project

    return _mutate(_do)


def reindex_project_pin_orders() -> None:
    """对所有置顶(pinned=True)项目紧凑重编号为 1..N,消除间隙。

    按 ``pin_order`` 升序稳定排序后重新分配 1..N;非置顶项目置 ``pin_order=0``。
    保证反复置顶/取消后 ``pin_order`` 不会无限增长。
    """
    def _do(projects: list[dict[str, Any]]) -> None:
        pinned = [p for p in projects if p.get("pinned")]
        pinned.sort(key=lambda p: p.get("pin_order", 0))
        for idx, p in enumerate(pinned, start=1):
            p["pin_order"] = idx
            p["updated_at"] = _now()
        for p in projects:
            if not p.get("pinned"):
                p["pin_order"] = 0

    _mutate(_do)


def invalidate_cache() -> None:
    """清空进程内缓存(测试/特殊场景使用;正常流程下写操作会自动刷新缓存)。"""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None
