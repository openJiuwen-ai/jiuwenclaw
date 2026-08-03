# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Turn-based diff service for /diff command.

数据链路（与 git 无关）：

1. openjiuwen harness 的 write_file / edit_file / bash 工具在每次文件操作后
   把 ``{old_content, new_content, action, timestamp}`` 追加到
   ``{workspace}/.agent_history/file_ops_{card_id}_{session_id}.json``。
   企业侧 ``card_id = jiuwenclaw_{session_id}``（见 agent_card_id.py），因此
   实际文件名形如 ``file_ops_jiuwenclaw_{sid}_{sid}.json``。
2. 本服务按「用户消息时间窗」把 file_ops 聚合成 turn diff。

读取端合并多个候选位置（租户 agent workspace、租户根、project_dir），并对
时间戳相近的重复条目去重；与 develop 分支
``jiuwenswarm/server/utils/diff_service.py`` 的 file_ops 读取链路对齐（不含
git / worktree / change-set 部分）。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.session_id_safe import resolve_session_dir_under_root
from jiuwenclaw.utils import (
    get_agent_sessions_dir,
    get_agent_workspace_relative_dir,
    get_multi_tenant_user_workspace_dir,
    normalize_tenant_scope_id,
    resolve_tenant_sessions_dir,
)

logger = logging.getLogger(__name__)

# 单文件 diff 输出的最大行数（超出则截断并标记 isTruncated）
MAX_LINES_PER_FILE = 400

# 多位置合并时的条目优先级（数值越小越优先，重复条目保留高优先级来源）
HISTORY_PRIORITY_PROJECT_ROOT = 0
HISTORY_PRIORITY_SHARED_WORKSPACE = 10
HISTORY_PRIORITY_UNKNOWN = 50

# file_ops 条目上的软删除标记（develop 的回退/丢弃机制写入；企业侧当前不产生
# 这些标记，但读取端保持兼容：带标记的条目对 turn diff 显示层不可见）
_REWOUND_KEY = "rewound_out"
_DISCARDED_KEY = "discarded_out"


def _resolve_sessions_root(
    *,
    service_id: str | None = None,
    agent_id: str | None = None,
    sessions_root: str | Path | None = None,
) -> Path:
    """Resolve sessions root from optional override or explicit tenant ids.

    Prefer the module-level ``get_multi_tenant_user_workspace_dir`` so tests can
    monkeypatch a single symbol and cover both history.json and file_ops roots.
    """
    if sessions_root is not None:
        return Path(sessions_root)
    if service_id is not None or agent_id is not None:
        sid = normalize_tenant_scope_id(service_id)
        aid = normalize_tenant_scope_id(agent_id)
        workspace = get_multi_tenant_user_workspace_dir(sid, aid)
        if workspace is not None:
            return workspace / "agent" / "sessions"
        return resolve_tenant_sessions_dir(service_id, agent_id)
    return get_agent_sessions_dir()


class DiffService:
    """提供 turn-based diff 查询服务."""

    def __init__(self) -> None:
        self._agent_id = "jiuwenclaw"

    def get_turn_diffs(
        self,
        session_id: str,
        *,
        service_id: str | None = None,
        agent_id: str | None = None,
        sessions_root: str | Path | None = None,
        project_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取 session 的所有 turn diff（完整信息）.

        Args:
            session_id: 会话 ID
            service_id / agent_id: 租户身份；用于解析 sessions / workspace
            sessions_root: 可选覆盖 sessions 根目录；不从 path 反推身份
            project_dir: 项目目录（可选；不提供时从 session metadata 读取）

        Returns:
            turn diff 列表，按时间倒序排列（most recent first）。

            每个 turn 的 ``turnIndex`` 为 session history 中用户消息的原始
            序号（1-based），过滤无变更轮次后**不重新编号**（例如仅第 2、5
            轮有改动则返回 ``[5, 2]`` 倒序）。客户端应按该序号展示，勿假定连续。
        """
        turns = self._compute_turn_diffs(
            session_id,
            service_id=service_id,
            agent_id=agent_id,
            sessions_root=sessions_root,
            project_dir=project_dir,
        )
        return list(reversed(turns))

    def get_turn_diff(
        self,
        session_id: str,
        *,
        turn_index: int,
        service_id: str | None = None,
        agent_id: str | None = None,
        sessions_root: str | Path | None = None,
        project_dir: str | None = None,
    ) -> dict[str, Any] | None:
        """获取指定轮次的 turn diff（turn_index 与 turnIndex 对齐，1-based）。"""
        turns = self._compute_turn_diffs(
            session_id,
            service_id=service_id,
            agent_id=agent_id,
            sessions_root=sessions_root,
            project_dir=project_dir,
        )
        for turn in turns:
            if int(turn.get("turnIndex", 0) or 0) == turn_index:
                return turn
        return None

    def get_turn_diff_summaries(
        self,
        session_id: str,
        *,
        service_id: str | None = None,
        agent_id: str | None = None,
        sessions_root: str | Path | None = None,
        project_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取 turn diff 摘要（不含 hunks 正文），用于轻量列表展示。"""
        turns = self._compute_turn_diffs(
            session_id,
            service_id=service_id,
            agent_id=agent_id,
            sessions_root=sessions_root,
            project_dir=project_dir,
        )
        summaries: list[dict[str, Any]] = []
        for turn in turns:
            summary = {k: v for k, v in turn.items() if k != "files"}
            summary["files"] = {
                path: {k: v for k, v in info.items() if k != "hunks"}
                for path, info in turn["files"].items()
            }
            summaries.append(summary)
        return list(reversed(summaries))

    def _compute_turn_diffs(
        self,
        session_id: str,
        *,
        service_id: str | None = None,
        agent_id: str | None = None,
        sessions_root: str | Path | None = None,
        project_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        """计算 turn-based diffs."""
        history = self._read_history(
            session_id,
            service_id=service_id,
            agent_id=agent_id,
            sessions_root=sessions_root,
        )
        if not history:
            return []

        if project_dir is None:
            project_dir = self._get_project_dir_from_metadata(
                session_id,
                sessions_root=_resolve_sessions_root(
                    service_id=service_id,
                    agent_id=agent_id,
                    sessions_root=sessions_root,
                ),
            )

        agent_history = self._read_agent_history(
            session_id,
            project_dir,
            service_id=service_id,
            agent_id=agent_id,
        )

        turns: list[dict[str, Any]] = []

        for i, record in enumerate(history):
            if record["role"] != "user":
                continue
            turn_start = record["timestamp"]
            # 用下一条用户消息时间作为 turn 结束边界：一个 turn 逻辑上覆盖
            # 到下一条用户消息为止（含 chat.final 之后、下一条用户消息之前
            # 的文件编辑）。
            turn_end = self._find_next_user_time(history, i)

            turns.append({
                "turnIndex": len(turns) + 1,
                "userPromptPreview": record.get("content", "")[:30],
                "timestamp": self._timestamp_to_iso(record["timestamp"]),
                "start_timestamp": turn_start,
                "end_timestamp": turn_end,
                "request_id": record.get("request_id", ""),
                "user_message_id": record.get("id", ""),
                "assistant_message_id": self._find_assistant_message_id(history, i),
                "files": {},
                "stats": {
                    "filesChanged": 0,
                    "linesAdded": 0,
                    "linesRemoved": 0,
                },
            })

        for turn in turns:
            file_edits = self._find_file_edits_by_time_range(
                agent_history,
                start_time=turn["start_timestamp"],
                end_time=turn["end_timestamp"],
            )

            for file_path, edit_info in file_edits.items():
                if file_path not in turn["files"]:
                    turn["files"][file_path] = {
                        "filePath": file_path,
                        "hunks": [],
                        "isNewFile": False,
                        "isDeletedFile": False,
                        "isTruncated": False,
                        "linesAdded": 0,
                        "linesRemoved": 0,
                        "lastEditTime": None,
                    }

                for op in edit_info["operations"]:
                    hunks, truncated = self._compute_hunks(
                        op["old_content"],
                        op["new_content"],
                    )
                    turn["files"][file_path]["hunks"].extend(hunks)
                    turn["files"][file_path]["lastEditTime"] = op["timestamp"]
                    if truncated:
                        turn["files"][file_path]["isTruncated"] = True

                    if op["action"] == "write" and op["old_content"] is None:
                        turn["files"][file_path]["isNewFile"] = True
                    if op["new_content"] is None and op["old_content"] is not None:
                        turn["files"][file_path]["isDeletedFile"] = True

                    for hunk in hunks:
                        for line in hunk["lines"]:
                            if line.startswith("+") and not line.startswith("+++"):
                                turn["files"][file_path]["linesAdded"] += 1
                            elif line.startswith("-") and not line.startswith("---"):
                                turn["files"][file_path]["linesRemoved"] += 1

            turn["stats"]["filesChanged"] = len(turn["files"])
            turn["stats"]["linesAdded"] = sum(
                f["linesAdded"] for f in turn["files"].values()
            )
            turn["stats"]["linesRemoved"] = sum(
                f["linesRemoved"] for f in turn["files"].values()
            )

        # 保留原始 turnIndex（与 history 中的用户消息序号对齐），只过滤无
        # 变更的轮次，不重新编号。这是有意行为：turnIndex 标识「第几次用户
        # 提问」，而非「第几次有文件变更」；get_turn_diff(turn_index=N) 也按
        # 原始序号查找。
        return [t for t in turns if t["files"]]

    # ------------------------------------------------------------------
    # history / metadata 读取
    # ------------------------------------------------------------------

    @staticmethod
    def _find_next_user_time(
        history: list[dict[str, Any]], user_index: int
    ) -> float | None:
        """查找下次用户消息时间."""
        for j in range(user_index + 1, len(history)):
            if history[j]["role"] == "user":
                return history[j]["timestamp"]
        return None

    @staticmethod
    def _find_assistant_message_id(
        history: list[dict[str, Any]], user_index: int
    ) -> str:
        """查找当前 user turn 后第一条 assistant 消息 ID。"""
        request_id = str(history[user_index].get("request_id", "") or "")
        for j in range(user_index + 1, len(history)):
            record = history[j]
            if record.get("role") == "user":
                break
            if record.get("role") != "assistant":
                continue
            if request_id:
                assistant_request_id = str(record.get("request_id", "") or "")
                if assistant_request_id and assistant_request_id != request_id:
                    continue
            return str(record.get("id", "") or "")
        return ""

    @staticmethod
    def _read_history(
        session_id: str,
        *,
        service_id: str | None = None,
        agent_id: str | None = None,
        sessions_root: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """读取 session history."""
        root = _resolve_sessions_root(
            service_id=service_id,
            agent_id=agent_id,
            sessions_root=sessions_root,
        )
        session_dir = resolve_session_dir_under_root(root, session_id)
        if session_dir is None:
            return []
        history_file = session_dir / "history.json"
        if not history_file.exists():
            return []
        try:
            return json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def _get_project_dir_from_metadata(
        session_id: str,
        *,
        sessions_root: Path,
    ) -> str | None:
        """从 session metadata.json 只读获取项目目录（无写副作用）。

        读取顺序（任一命中即返回）：
          1. 顶层 ``project_dir``（interface._effective_project_dir_for_session 写入）
          2. ``channel_metadata.cwd``（显式传 cwd 的通道，向后兼容）
        """
        session_dir = resolve_session_dir_under_root(sessions_root, session_id)
        if session_dir is None:
            return None
        metadata_file = session_dir / "metadata.json"
        if not metadata_file.exists():
            return None
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read metadata file %s: %s", metadata_file, e)
            return None
        top_level = metadata.get("project_dir")
        if isinstance(top_level, str) and top_level.strip():
            return top_level.strip()
        channel_meta = metadata.get("channel_metadata", {})
        if isinstance(channel_meta, dict):
            cwd = channel_meta.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                return cwd.strip()
        return None

    @staticmethod
    def resolve_trusted_project_dir(
        session_id: str,
        requested: str | None,
        *,
        sessions_root: Path,
    ) -> str | None:
        """解析 command.diff 可用的 project_dir（防任意路径读取）.

        可信来源仅为 session metadata（chat.send 首次绑定）。客户端
        ``params.project_dir`` 仅当与 metadata 解析为同一路径时才接受；
        否则忽略请求值并回退 metadata。

        注意：Coding 场景下 project_dir 本就可在 agent workspace 之外，
        因此**不能**用「必须落在 workspace_root 下」做边界校验，否则会误杀
        合法工程目录。正确模型是「会话绑定」，不是「workspace 子路径」。
        """
        trusted = DiffService._get_project_dir_from_metadata(
            session_id, sessions_root=sessions_root
        )
        raw = (requested or "").strip() if isinstance(requested, str) else ""
        if not raw:
            return trusted

        try:
            req_resolved = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            logger.warning(
                "[DiffService] ignore invalid params.project_dir=%r session_id=%s",
                raw,
                session_id,
            )
            return trusted

        if not trusted:
            logger.warning(
                "[DiffService] reject unbound params.project_dir=%r session_id=%s "
                "(no session metadata project_dir)",
                raw,
                session_id,
            )
            return None

        try:
            trusted_resolved = Path(trusted).expanduser().resolve()
        except (OSError, ValueError):
            return trusted

        if req_resolved == trusted_resolved:
            return str(trusted_resolved)

        logger.warning(
            "[DiffService] reject params.project_dir outside session binding: "
            "session_id=%s requested=%r trusted=%r",
            session_id,
            raw,
            trusted,
        )
        return trusted

    # ------------------------------------------------------------------
    # file_ops 读取合并
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_file_ops_file(
        name: str, session_id: str | None, require_session: bool = False
    ) -> bool:
        """检查文件名是否是有效的 file_ops 文件.

        文件名约定: ``file_ops_{agent_id}_{session_id}.json``，其中 session_id
        始终是 ``.json`` 前的最后一段。使用 ``_{session_id}.json`` 后缀匹配替代
        子串匹配，避免短 session_id 误匹配其他 agent 的 file_ops 文件。

        当 session_id 对应的是父会话时，也接受子 agent 会话（后缀形如
        ``_sub_{type}_{suffix}``）的 file_ops 文件，使 diff 统计能覆盖子 agent
        的文件变更。
        """
        if not name.startswith("file_ops_"):
            return False
        if not name.endswith(".json"):
            return False
        if not session_id:
            return not require_session
        suffix = f"_{session_id}.json"
        if name.endswith(suffix):
            return True
        sub_marker = f"_{session_id}_sub_"
        marker_pos = name.find(sub_marker, len("file_ops_"))
        if marker_pos < 0:
            return False
        agent_id = name[len("file_ops_"):marker_pos]
        return bool(agent_id) and "_" not in agent_id

    @staticmethod
    def _candidate_history_dirs(
        *,
        service_id: str | None,
        agent_id: str | None,
    ) -> list[Path]:
        """租户侧 ``.agent_history`` 候选目录（agent workspace 为实际写入位置）。"""
        sid = normalize_tenant_scope_id(service_id)
        aid = normalize_tenant_scope_id(agent_id)
        tenant_root = get_multi_tenant_user_workspace_dir(sid, aid)
        if tenant_root is None:
            tenant_root = get_multi_tenant_user_workspace_dir("default", "default")
        if tenant_root is None:
            return []
        agent_workspace = tenant_root / get_agent_workspace_relative_dir()
        return [
            agent_workspace / ".agent_history",
            tenant_root / ".agent_history",
        ]

    def _read_agent_history(
        self,
        session_id: str | None = None,
        project_dir: str | None = None,
        *,
        service_id: str | None = None,
        agent_id: str | None = None,
        include_rewound: bool = False,
    ) -> dict[str, Any]:
        """读取 .agent_history（合并全局与 session-specific 文件）.

        Args:
            session_id: 若提供，额外扫描匹配该 session 的 file_ops 文件。
            project_dir: 项目目录路径，若提供则也从项目目录读取 .agent_history。
            service_id / agent_id: 租户身份。
            include_rewound: 是否包含被标记为 ``rewound_out`` / ``discarded_out``
                的条目（软删除快照）。显示层（turn diff）不应看到它们。
        """
        result: dict[str, Any] = {}
        history_file_priorities: dict[str, int] = {}
        paths: list[Path] = []

        def path_key(path: Path) -> str:
            try:
                return os.path.normcase(str(path.resolve()))
            except OSError:
                return os.path.normcase(str(path))

        def add_history_file(path: Path, priority: int) -> None:
            paths.append(path)
            history_file_priorities.setdefault(path_key(path), priority)

        def scan_history_dir(hist_dir: Path, priority: int) -> None:
            # 旧命名的全局文件（无 session 后缀）
            add_history_file(hist_dir / f"file_ops_{self._agent_id}.json", priority)
            if session_id and hist_dir.is_dir():
                try:
                    children = list(hist_dir.iterdir())
                except OSError:
                    return
                for f in children:
                    if self._is_valid_file_ops_file(
                        f.name, session_id, require_session=True
                    ):
                        add_history_file(f, priority)

        # 1. 租户 workspace（公共位置，工具的默认写入处）
        for hist_dir in self._candidate_history_dirs(
            service_id=service_id, agent_id=agent_id
        ):
            scan_history_dir(hist_dir, HISTORY_PRIORITY_SHARED_WORKSPACE)

        # 2. 项目目录（会话绑定 project_dir 时的可能写入位置）
        if project_dir:
            scan_history_dir(
                Path(project_dir) / ".agent_history",
                HISTORY_PRIORITY_PROJECT_ROOT,
            )

        # 用于规范化路径，避免大小写/斜杠方向差异导致的重复
        def normalize_path(p: str) -> str:
            try:
                return str(Path(p).resolve())
            except OSError:
                return p.replace("\\", "/").lower()

        result_entry_priorities: dict[str, list[int]] = {}
        result_path_by_comparable_key: dict[str, str] = {}
        # path -> content_key -> [(entry_index, epoch_seconds)]，O(1) 定位近重复
        dedup_index: dict[str, dict[tuple[str, str], list[tuple[int, float]]]] = {}

        for history_file in paths:
            if not history_file.exists():
                continue
            try:
                data = json.loads(history_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read agent history file %s: %s", history_file, e)
                continue
            history_priority = history_file_priorities.get(
                path_key(history_file),
                HISTORY_PRIORITY_UNKNOWN,
            )
            if not isinstance(data, dict):
                continue
            for file_path, entries in data.items():
                normalized_path = normalize_path(file_path)
                comparable_key = normalized_path.lower()
                normalized_path = result_path_by_comparable_key.setdefault(
                    comparable_key,
                    normalized_path,
                )
                if normalized_path not in result:
                    result[normalized_path] = []
                    result_entry_priorities[normalized_path] = []
                    dedup_index[normalized_path] = {}
                path_dedup = dedup_index[normalized_path]
                for entry in entries:
                    # 软删除的快照默认对显示层不可见（见 include_rewound）
                    if not include_rewound and (
                        entry.get(_REWOUND_KEY) or entry.get(_DISCARDED_KEY)
                    ):
                        continue
                    # 多来源合并去重：相同操作 + 相同内容且时间戳相近（<2s）
                    # 视为重复，保留高优先级来源的条目
                    action = str(entry.get("action", ""))
                    content_key = DiffService._entry_content_key(action, entry)
                    entry_epoch = DiffService._entry_epoch_seconds(entry.get("timestamp", ""))
                    is_duplicate = False
                    duplicate_index: int | None = None
                    if entry_epoch is not None:
                        for idx, existing_epoch in path_dedup.get(content_key, []):
                            if abs(entry_epoch - existing_epoch) < 2:
                                is_duplicate = True
                                duplicate_index = idx
                                break
                    if is_duplicate:
                        priorities = result_entry_priorities[normalized_path]
                        if (
                            duplicate_index is not None
                            and duplicate_index < len(priorities)
                            and history_priority < priorities[duplicate_index]
                        ):
                            result[normalized_path][duplicate_index] = entry
                            priorities[duplicate_index] = history_priority
                            if entry_epoch is not None:
                                bucket = path_dedup.setdefault(content_key, [])
                                for i, (b_idx, _) in enumerate(bucket):
                                    if b_idx == duplicate_index:
                                        bucket[i] = (duplicate_index, entry_epoch)
                                        break
                    else:
                        new_idx = len(result[normalized_path])
                        result[normalized_path].append(entry)
                        result_entry_priorities[normalized_path].append(
                            history_priority
                        )
                        if entry_epoch is not None:
                            path_dedup.setdefault(content_key, []).append(
                                (new_idx, entry_epoch)
                            )

        return result

    @staticmethod
    def _entry_content_key(action: str, entry: dict[str, Any]) -> tuple[str, str]:
        """去重用内容指纹：(action, sha256(old||new))."""
        digest = hashlib.sha256()
        for value in (entry.get("old_content"), entry.get("new_content")):
            if value is None:
                digest.update(b"\x00")
            else:
                digest.update(b"\x01")
                digest.update(str(value).encode("utf-8", errors="replace"))
            digest.update(b"\xff")
        return (action, digest.hexdigest())

    @staticmethod
    def _entry_epoch_seconds(ts: Any) -> float | None:
        if not isinstance(ts, str) or not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, OSError):
            return None

    @staticmethod
    def _find_file_edits_by_time_range(
        agent_history: dict[str, Any],
        start_time: float,
        end_time: float | None,
    ) -> dict[str, dict[str, Any]]:
        """根据时间范围查找文件编辑记录.

        时间区间：[start_time, end_time) 左闭右开
        """
        file_edits: dict[str, dict[str, Any]] = {}

        for file_path, entries in agent_history.items():
            for entry in entries:
                try:
                    edit_time = DiffService._iso_to_timestamp(entry["timestamp"])
                except (KeyError, ValueError, TypeError):
                    continue

                if edit_time >= start_time:
                    if end_time is None or edit_time < end_time:
                        if file_path not in file_edits:
                            file_edits[file_path] = {
                                "file_path": file_path,
                                "operations": [],
                            }
                        file_edits[file_path]["operations"].append({
                            "action": entry.get("action"),
                            "timestamp": entry.get("timestamp"),
                            "old_content": entry.get("old_content"),
                            "new_content": entry.get("new_content"),
                        })

        return file_edits

    # ------------------------------------------------------------------
    # diff 计算
    # ------------------------------------------------------------------

    @staticmethod
    def _iso_to_timestamp(iso_str: str | float | int) -> float:
        """将 ISO 8601 字符串或数值时间戳转换为 Unix timestamp."""
        if isinstance(iso_str, (int, float)):
            return float(iso_str)
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.timestamp()

    @staticmethod
    def _timestamp_to_iso(timestamp: float) -> str:
        """将 Unix timestamp 转换为 ISO 8601 字符串."""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.isoformat()

    @staticmethod
    def _compute_hunks(
        old_content: str | None,
        new_content: str | None,
        max_lines: int = MAX_LINES_PER_FILE,
    ) -> tuple[list[dict[str, Any]], bool]:
        """计算结构化 diff hunks.

        Returns:
            (hunks, truncated): hunks 列表和是否被截断的标志。
        """
        # 删除文件：new_content 为 None
        if new_content is None:
            if old_content is None:
                return [], False
            lines = old_content.splitlines()
            truncated = len(lines) > max_lines
            if truncated:
                lines = lines[:max_lines]
            return [{
                "oldStart": 1,
                "oldLines": len(lines),
                "newStart": 0,
                "newLines": 0,
                "lines": [f"-{line}" for line in lines],
            }], truncated

        # 新建文件：old_content 为 None
        if old_content is None:
            lines = new_content.splitlines()
            truncated = len(lines) > max_lines
            if truncated:
                lines = lines[:max_lines]
            return [{
                "oldStart": 0,
                "oldLines": 0,
                "newStart": 1,
                "newLines": len(lines),
                "lines": [f"+{line}" for line in lines],
            }], truncated

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        if not old_lines and not new_lines:
            return [], False

        # 输出带 context_lines 行上下文的 unified hunks，并合并上下文窗口
        # 重叠的相邻变更，对齐 `git diff --unified=3` / jsdiff structuredPatch
        # 的行为。旧实现完全跳过 equal 段，产生无上下文的孤立 hunks。
        context_lines = 3
        opcodes = difflib.SequenceMatcher(
            None, old_lines, new_lines
        ).get_opcodes()
        n_old = len(old_lines)
        n_new = len(new_lines)

        hunks: list[dict[str, Any]] = []
        total_lines = 0
        truncated = False

        i = 0
        while i < len(opcodes):
            tag, i1, i2, j1, j2 = opcodes[i]
            if tag == "equal":
                i += 1
                continue

            # 本 hunk 的首个变更是 opcodes[i]；吸收后续「间隔 equal 段足够短
            # （<= 2*context_lines）」的变更，使其上下文窗口彼此桥接。
            o_lo = max(0, i1 - context_lines)
            n_lo = max(0, j1 - context_lines)
            last_i2 = i2
            last_j2 = j2
            k = i + 1
            while k < len(opcodes):
                ntag, ni1, ni2, nj1, nj2 = opcodes[k]
                if ntag == "equal":
                    if (ni2 - ni1) > 2 * context_lines:
                        break
                    k += 1
                    continue
                last_i2 = ni2
                last_j2 = nj2
                k += 1

            o_hi = min(n_old, last_i2 + context_lines)
            n_hi = min(n_new, last_j2 + context_lines)

            # 把前导 equal 段（i-1）与尾随 equal 段（k）纳入输出范围，以便
            # 产出首尾上下文行；均被上面的窗口裁剪。
            start_idx = (
                i - 1 if i - 1 >= 0 and opcodes[i - 1][0] == "equal" else i
            )
            end_idx = (
                k + 1
                if k < len(opcodes) and opcodes[k][0] == "equal"
                else k
            )

            lines: list[str] = []
            for idx in range(start_idx, end_idx):
                tag2, ii1, ii2, jj1, jj2 = opcodes[idx]
                if tag2 == "equal":
                    for m in range(max(ii1, o_lo), min(ii2, o_hi)):
                        if total_lines >= max_lines:
                            truncated = True
                            break
                        lines.append(f" {old_lines[m].rstrip()}")
                        total_lines += 1
                elif tag2 == "delete":
                    for m in range(max(ii1, o_lo), min(ii2, o_hi)):
                        if total_lines >= max_lines:
                            truncated = True
                            break
                        lines.append(f"-{old_lines[m].rstrip()}")
                        total_lines += 1
                elif tag2 == "insert":
                    for m in range(max(jj1, n_lo), min(jj2, n_hi)):
                        if total_lines >= max_lines:
                            truncated = True
                            break
                        lines.append(f"+{new_lines[m].rstrip()}")
                        total_lines += 1
                else:  # replace
                    for m in range(max(ii1, o_lo), min(ii2, o_hi)):
                        if total_lines >= max_lines:
                            truncated = True
                            break
                        lines.append(f"-{old_lines[m].rstrip()}")
                        total_lines += 1
                    for m in range(max(jj1, n_lo), min(jj2, n_hi)):
                        if total_lines >= max_lines:
                            truncated = True
                            break
                        lines.append(f"+{new_lines[m].rstrip()}")
                        total_lines += 1
                if truncated:
                    break

            hunks.append({
                "oldStart": o_lo + 1,
                "oldLines": o_hi - o_lo,
                "newStart": n_lo + 1,
                "newLines": n_hi - n_lo,
                "lines": lines,
            })
            if truncated:
                break
            i = k

        return hunks, truncated


_diff_service: DiffService | None = None


def get_diff_service() -> DiffService:
    """获取 DiffService 单例实例."""
    global _diff_service
    if _diff_service is None:
        _diff_service = DiffService()
    return _diff_service
