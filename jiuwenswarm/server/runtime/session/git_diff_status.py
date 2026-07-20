"""DiffStatusService: 面向 Web 的 diff 状态聚合服务(设计文档 §2.4 / §3.5 / §4.1.16)。

聚合"当前工作区 diff"与"上一轮对话 diff"两路来源,复用
``DiffService.get_git_diff()`` / ``get_turn_diffs()`` 并转换为 snake_case schema,
合并 ``ProjectGitService`` 的 repo 状态。

第一版能力边界(§2.7):untracked 文件、staged/unstaged 分类计数不在范围内;
``DiffFileEntry.is_untracked`` 第一版不序列化。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.session.project_git import GitError, GitOperationError
from jiuwenswarm.server.utils.diff_service import get_diff_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiffStats:
    """Diff 变更统计,``DiffSummary`` 和 ``DiffTurnSummary`` 共用。"""

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_changed": self.files_changed,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }


@dataclass(slots=True)
class DiffHunk:
    """单个 hunk 的结构化表示。"""

    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
            "lines": list(self.lines),
        }


@dataclass(slots=True)
class DiffFileEntry:
    """单个文件的 diff 条目。

    注意: ``is_untracked`` 字段第一版不序列化(设计文档 §3.5 实现要求),
    ``to_dict`` 不写入该 key。后续版本支持 untracked diff 后再统一输出。
    """

    file_path: str = ""
    status: str = "modified"  # modified | added | deleted | renamed | missing
    lines_added: int = 0
    lines_removed: int = 0
    is_binary: bool = False
    is_new_file: bool = False
    is_untracked: bool = False  # 第一版不序列化,仅捕获供后续版本使用
    is_large_file: bool = False
    is_truncated: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)

    def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "file_path": self.file_path,
            "status": self.status,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "is_binary": self.is_binary,
            "is_new_file": self.is_new_file,
            # is_untracked 故意不输出(第一版不支持 untracked 文件分类)
            "is_large_file": self.is_large_file,
            "is_truncated": self.is_truncated,
            "hunks": [h.to_dict() for h in self.hunks] if include_hunks else [],
        }
        return result


@dataclass(slots=True)
class DiffSummary:
    """当前工作区 diff 的摘要对象。"""

    is_dirty: bool = False
    stats: DiffStats = field(default_factory=DiffStats)
    files: dict[str, DiffFileEntry] = field(default_factory=dict)
    kind: str = "working_tree"

    def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "is_dirty": self.is_dirty,
            "stats": self.stats.to_dict(),
            "files": {
                k: v.to_dict(include_hunks=include_hunks)
                for k, v in self.files.items()
            },
        }


@dataclass(slots=True)
class DiffTurnSummary:
    """上一轮对话 diff 的摘要对象。"""

    turn_index: int = 0
    timestamp: str = ""
    user_prompt_preview: str = ""
    stats: DiffStats = field(default_factory=DiffStats)
    files: dict[str, DiffFileEntry] = field(default_factory=dict)
    kind: str = "conversation_turn"

    def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "turn_index": self.turn_index,
            "timestamp": self.timestamp,
            "user_prompt_preview": self.user_prompt_preview,
            "stats": self.stats.to_dict(),
            "files": {
                k: v.to_dict(include_hunks=include_hunks)
                for k, v in self.files.items()
            },
        }


@dataclass(slots=True)
class DiffRepoInfo:
    """Diff 状态中的仓库元信息子对象。"""

    is_git: bool = False
    repo_root: str | None = None
    branch: str | None = None
    head: str | None = None
    transient: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_git": self.is_git,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "head": self.head,
            "transient": self.transient,
        }


@dataclass(slots=True)
class ProjectGitDiffStatus:
    """Diff 状态聚合的顶层返回对象。"""

    project_id: str = ""
    session_id: str | None = None
    work_mode: str = "work"
    repo: DiffRepoInfo = field(default_factory=DiffRepoInfo)
    current: DiffSummary | None = None
    last_turn: DiffTurnSummary | None = None
    generated_at: float = 0.0

    def to_dict(self, *, include_hunks: bool = True) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "work_mode": self.work_mode,
            "repo": self.repo.to_dict(),
            "current": self.current.to_dict(include_hunks=include_hunks) if self.current else None,
            "last_turn": self.last_turn.to_dict(include_hunks=include_hunks) if self.last_turn else None,
            "generated_at": self.generated_at,
        }


def _to_relative_path(file_path: str, repo_root: str | None) -> str:
    """将绝对路径转换为相对 ``repo_root`` 的路径;无法转换时返回原路径。"""
    if not file_path:
        return file_path
    if repo_root:
        try:
            return os.path.relpath(file_path, repo_root)
        except ValueError:
            # Windows 上跨盘符 relpath 会抛 ValueError
            return file_path
    return file_path


def _infer_file_status(entry: dict[str, Any]) -> str:
    """从 DiffService 文件条目推断 ``DiffFileEntry.status`` 字段。

    DiffService 原始返回中没有显式 status 字段,按可用信号映射:
      - ``isUntracked=True`` → ``"added"``(未跟踪文件)
      - ``isNewFile=True`` → ``"added"``(新增已跟踪文件)
      - 其他 → ``"modified"``

    已知局限(设计文档 §2.7): DiffService 不区分 deleted / renamed / missing,
    这些状态在第一版都归为 ``"modified"``。准确区分需增强 DiffService 输出
    (如 git status letter),不在本次实现范围。
    """
    if entry.get("isUntracked") or entry.get("isNewFile"):
        return "added"
    return "modified"


def _convert_stats(raw_stats: dict[str, Any] | None) -> DiffStats:
    """转换 camelCase stats → snake_case DiffStats。"""
    if not raw_stats or not isinstance(raw_stats, dict):
        return DiffStats()
    return DiffStats(
        files_changed=int(raw_stats.get("filesChanged", 0) or 0),
        lines_added=int(raw_stats.get("linesAdded", 0) or 0),
        lines_removed=int(raw_stats.get("linesRemoved", 0) or 0),
    )


def _convert_hunks(raw_hunks: list[dict[str, Any]] | None) -> list[DiffHunk]:
    """转换 camelCase hunk 列表 → snake_case DiffHunk 列表。"""
    if not raw_hunks or not isinstance(raw_hunks, list):
        return []
    result: list[DiffHunk] = []
    for raw in raw_hunks:
        if not isinstance(raw, dict):
            continue
        result.append(DiffHunk(
            old_start=int(raw.get("oldStart", 0) or 0),
            old_lines=int(raw.get("oldLines", 0) or 0),
            new_start=int(raw.get("newStart", 0) or 0),
            new_lines=int(raw.get("newLines", 0) or 0),
            lines=list(raw.get("lines", []) or []),
        ))
    return result


def _convert_file_entry(
    file_path: str,
    entry: dict[str, Any],
    *,
    repo_root: str | None,
    include_hunks: bool,
) -> DiffFileEntry:
    """转换单个 DiffService 文件条目 → DiffFileEntry。

    ``file_path`` 为 DiffService 返回的 key(绝对路径),转换为相对 ``repo_root``
    的路径用于 Web 展示。
    """
    rel_path = _to_relative_path(file_path, repo_root)
    return DiffFileEntry(
        file_path=rel_path,
        status=_infer_file_status(entry),
        lines_added=int(entry.get("linesAdded", 0) or 0),
        lines_removed=int(entry.get("linesRemoved", 0) or 0),
        is_binary=bool(entry.get("isBinary", False)),
        is_new_file=bool(entry.get("isNewFile", False)),
        is_untracked=bool(entry.get("isUntracked", False)),
        is_large_file=bool(entry.get("isLargeFile", False)),
        is_truncated=bool(entry.get("isTruncated", False)),
        hunks=_convert_hunks(entry.get("hunks")) if include_hunks else [],
    )


def _convert_file_map(
    raw_files: dict[str, Any] | None,
    *,
    repo_root: str | None,
    include_files: bool,
    include_hunks: bool,
) -> dict[str, DiffFileEntry]:
    """转换 DiffService files 映射 → DiffFileEntry 映射。"""
    if not include_files or not raw_files or not isinstance(raw_files, dict):
        return {}
    result: dict[str, DiffFileEntry] = {}
    for file_path, entry in raw_files.items():
        if not isinstance(entry, dict):
            continue
        converted = _convert_file_entry(
            file_path, entry, repo_root=repo_root, include_hunks=include_hunks,
        )
        result[converted.file_path] = converted
    return result


def _convert_current_diff(
    raw_diff: dict[str, Any] | None,
    *,
    repo_root: str | None,
    include_files: bool,
    include_hunks: bool,
    repo_is_dirty: bool = False,
) -> DiffSummary:
    """转换 ``DiffService.get_git_diff()`` 返回 → DiffSummary。

    ``is_dirty`` 语义与 ``GitRepoStatus.is_dirty`` 对齐:既包含已跟踪文件的
    改动(``stats.files_changed > 0``),也包含 untracked 文件(``files`` 中
    ``is_untracked=True`` 的条目)。``DiffService.get_git_diff`` 会把 untracked
    文件加入 ``files`` 但不计入 ``stats.files_changed``,仅按 stats 判定会导致
    "工作区只有 untracked 文件时 DiffSummary.is_dirty=False" 与
    ``GitRepoStatus.is_dirty=True`` 矛盾。

    ``include_files=False`` 时 ``files`` 为空,无法通过 ``has_untracked``
    检测 untracked 文件,此时使用 ``repo_is_dirty``(来自 ``GitRepoStatus.is_dirty``)
    兜底。``repo_is_dirty`` 由 ``git status --porcelain`` 直接判定,涵盖 untracked
    文件和已跟踪文件改动,是最权威的 dirty 判定来源。
    """
    if not raw_diff or not isinstance(raw_diff, dict):
        # raw_diff 为空但 repo_is_dirty=True:工作区有 untracked 但 diff 服务未返回
        # (边界场景),以 repo_is_dirty 为准
        return DiffSummary(is_dirty=repo_is_dirty, stats=DiffStats(), files={})
    stats = _convert_stats(raw_diff.get("stats"))
    files = _convert_file_map(
        raw_diff.get("files"),
        repo_root=repo_root,
        include_files=include_files,
        include_hunks=include_hunks,
    )
    # stats.files_changed 不含 untracked,需单独检查 files 中是否存在 untracked 条目。
    # include_files=False 时 files 为空,使用 repo_is_dirty 兜底:
    # summary 首次订阅正是 include_files=False,仅靠 stats 会漏掉 untracked-only 场景。
    has_untracked = any(f.is_untracked for f in files.values()) if include_files else False
    return DiffSummary(
        is_dirty=stats.files_changed > 0 or has_untracked or repo_is_dirty,
        stats=stats,
        files=files,
    )


def _convert_turn_diff(
    turn: dict[str, Any] | None,
    *,
    repo_root: str | None,
    include_files: bool,
    include_hunks: bool,
) -> DiffTurnSummary | None:
    """转换单个 ``get_turn_diffs()`` 返回的 turn → DiffTurnSummary。"""
    if not turn or not isinstance(turn, dict):
        return None
    stats = _convert_stats(turn.get("stats"))
    files = _convert_file_map(
        turn.get("files"),
        repo_root=repo_root,
        include_files=include_files,
        include_hunks=include_hunks,
    )
    return DiffTurnSummary(
        turn_index=int(turn.get("turnIndex", 0) or 0),
        timestamp=str(turn.get("timestamp", "") or ""),
        user_prompt_preview=str(turn.get("userPromptPreview", "") or ""),
        stats=stats,
        files=files,
    )


class DiffStatusService:
    """面向 Web 的 diff 状态聚合服务(设计文档 §2.4 / §4.1.16)。

    复用现有 ``DiffService`` 能力,负责 schema 转换(camelCase → snake_case)、
    空状态与 transient 语义转换、与 ``ProjectGitService`` 的 repo 状态合并。
    不修改 ``DiffService`` 原始返回,避免破坏 TUI ``command.diff`` 等既有消费方。
    """

    @staticmethod
    def get_project_diff_status(
        *,
        project: Any,
        session_id: str | None = None,
        include_files: bool = False,
        include_hunks: bool = False,
    ) -> ProjectGitDiffStatus:
        """聚合当前工作区 diff 和上一轮对话 diff(设计文档 §4.1.16)。

        Args:
            project: 已校验的 Project 对象(由 handler 完成存在性/work_mode 校验)
            session_id: 会话 ID,用于查询上一轮对话 diff;为空时不返回 last_turn
            include_files: 是否返回文件列表;为 false 时 files 为 ``{}``
            include_hunks: 是否返回 hunk;为 true 时隐含 ``include_files=true``

        Returns:
            ProjectGitDiffStatus: 聚合后的 diff 状态对象,调用方调 ``to_dict()``
            作为接口 payload

        说明:
          - transient 状态下 ``current`` 为 ``None``,仍成功返回 ``repo.transient=true``
          - 无 turn diff 时 ``last_turn`` 为 ``None``
          - ``include_hunks=True`` 时隐含 ``include_files=True``
        """
        project_id = getattr(project, "project_id", "")
        project_dir = getattr(project, "project_dir", "")
        work_mode = getattr(project, "work_mode", "work") or "work"

        effective_include_files = include_files or include_hunks

        from jiuwenswarm.server.runtime.session.project_git import (
            get_project_git_service,
        )
        git_service = get_project_git_service()
        repo_status = git_service.status(project)

        if repo_status.error is not None:
            raise GitOperationError(repo_status.error)

        repo_info = DiffRepoInfo(
            is_git=repo_status.is_git,
            repo_root=repo_status.repo_root,
            branch=repo_status.branch,
            head=repo_status.head,
            transient=repo_status.transient,
        )

        current: DiffSummary | None = None
        if repo_status.is_git and not repo_status.transient:
            diff_service = get_diff_service()
            try:
                raw_diff = diff_service.get_git_diff(project_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[DiffStatus] get_git_diff failed (project=%s dir=%s): %s",
                    project_id, project_dir, exc,
                )
                raise
            current = _convert_current_diff(
                raw_diff,
                repo_root=repo_status.repo_root,
                include_files=effective_include_files,
                include_hunks=include_hunks,
                repo_is_dirty=repo_status.is_dirty,
            )

        last_turn: DiffTurnSummary | None = None
        if session_id:
            diff_service = get_diff_service()
            try:
                turns = diff_service.get_turn_diffs(session_id, project_dir)
            except Exception as exc:  # noqa: BLE001
                # 与 get_git_diff 保持对称:错误向上抛,让 handler 感知并触发
                # 订阅状态回滚。否则 source=last_turn 时会静默
                # 返回空数据,客户端误以为订阅成功但拿不到内容。
                logger.warning(
                    "[DiffStatus] get_turn_diffs failed (session=%s): %s",
                    session_id, exc,
                )
                raise
            if turns:
                last_turn = _convert_turn_diff(
                    turns[0],
                    repo_root=repo_status.repo_root,
                    include_files=effective_include_files,
                    include_hunks=include_hunks,
                )

        return ProjectGitDiffStatus(
            project_id=project_id,
            session_id=session_id,
            work_mode=work_mode,
            repo=repo_info,
            current=current,
            last_turn=last_turn,
            generated_at=time.time(),
        )


_service_instance: DiffStatusService | None = None


def get_diff_status_service() -> DiffStatusService:
    """返回 ``DiffStatusService`` 单例。"""
    global _service_instance
    if _service_instance is None:
        _service_instance = DiffStatusService()
    return _service_instance


def reset_diff_status_service() -> None:
    """重置单例(仅供测试)。"""
    global _service_instance
    _service_instance = None


_FILES_EVENT_FIELDS: tuple[str, ...] = (
    "file_path", "status", "lines_added", "lines_removed",
    "is_binary", "is_new_file", "is_large_file", "is_truncated",
)


def file_entry_to_dict_no_hunks(entry: dict[str, Any]) -> dict[str, Any]:
    """将已序列化的文件条目 dict 转换为不含 hunk 的事件格式。

    用于 ``diff_files_changed`` 事件(设计文档 §3.6):
    files 事件只需文件路径/状态/行数统计,不需要 hunk 内容。

    与 ``DiffFileEntry.to_dict(include_hunks=False)`` 输出一致,
    但接受已序列化的 dict 输入(避免反复对象重建)。
    """
    return {
        "file_path": entry.get("file_path", ""),
        "status": entry.get("status", "modified"),
        "lines_added": entry.get("lines_added", 0),
        "lines_removed": entry.get("lines_removed", 0),
        "is_binary": entry.get("is_binary", False),
        "is_new_file": entry.get("is_new_file", False),
        "is_large_file": entry.get("is_large_file", False),
        "is_truncated": entry.get("is_truncated", False),
        "hunks": [],
    }


def file_map_to_dict_no_hunks(
    files_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    """批量转换文件映射:去除 hunk,过滤非 dict 条目。

    用于 ``diff_files_changed`` 事件 payload 的 files 字段构造。
    """
    if not files_dict:
        return {}
    result: dict[str, Any] = {}
    for path, entry in files_dict.items():
        if not isinstance(entry, dict):
            continue
        result[path] = file_entry_to_dict_no_hunks(entry)
    return result


# ── 事件 payload 构造 helper(供 handler 与 registry 共用,避免重复实现) ──

def extract_files_from_status(
    status_dict: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    """从 ``ProjectGitDiffStatus.to_dict()`` 中提取指定 source 的 files 映射。

    Args:
        status_dict: 已序列化的 diff status dict
        source: ``"current"`` 或 ``"last_turn"``

    Returns:
        files 映射;对应分支不存在时返回 ``None``
    """
    if source == "current":
        current = status_dict.get("current")
        return (current or {}).get("files") if current else None
    if source == "last_turn":
        last_turn = status_dict.get("last_turn")
        return (last_turn or {}).get("files") if last_turn else None
    return None


def build_summary_entry(current: dict[str, Any] | None) -> dict[str, Any] | None:
    """构造 summary 事件/快照中的 current 条目(``files`` 固定 ``{}``)。

    summary 层只关心统计信息,文件列表由 files 层负责。

    Returns:
        构造的 summary 条目;``current`` 为空时返回 ``None``。
    """
    if not current:
        return None
    return {
        "kind": current.get("kind", "working_tree"),
        "is_dirty": current.get("is_dirty", False),
        "stats": current.get("stats", {}),
        "files": {},
    }


def build_turn_summary_entry(last_turn: dict[str, Any] | None) -> dict[str, Any] | None:
    """构造 summary 事件/快照中的 last_turn 条目(``files`` 固定 ``{}``)。

    与 ``build_summary_entry`` 对称,仅用于 last_turn 分支。
    """
    if not last_turn:
        return None
    return {
        "kind": last_turn.get("kind", "conversation_turn"),
        "turn_index": last_turn.get("turn_index", 0),
        "stats": last_turn.get("stats", {}),
        "files": {},
    }
