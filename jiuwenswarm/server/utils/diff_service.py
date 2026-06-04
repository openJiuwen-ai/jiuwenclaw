# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Turn-based diff service for /diff command."""

from __future__ import annotations

import difflib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import get_agent_sessions_dir, get_agent_workspace_dir, get_user_workspace_dir


logger = logging.getLogger(__name__)


class DiffService:
    """提供 turn-based diff 查询服务."""

    def __init__(self) -> None:
        self._agent_id = "jiuwenswarm"

    def get_turn_diffs(self, session_id: str, project_dir: str | None = None) -> list[dict[str, Any]]:
        """获取 session 的所有 turn diff（完整信息）.

        Args:
            session_id: 会话 ID
            project_dir: 项目目录路径（可选，若不提供则从 session metadata 读取）

        Returns:
            turn diff 列表，按时间倒序排列（most recent first）
        """
        turns = self._compute_turn_diffs(session_id, project_dir)
        return list(reversed(turns))

    def _compute_turn_diffs(self, session_id: str, project_dir: str | None = None) -> list[dict[str, Any]]:
        """计算 turn-based diffs."""
        history = self._read_history(session_id)
        agent_history = self._read_agent_history(session_id, project_dir)

        if not history:
            return []

        turns: list[dict[str, Any]] = []

        i = 0
        while i < len(history):
            record = history[i]

            if record["role"] == "user":
                turn_start = record["timestamp"]
                # Use next user message timestamp as turn end boundary.
                # A turn logically spans from one user message to the next,
                # so this captures all file edits within the turn's scope
                # (including those after chat.final but before the next user msg).
                turn_end = self._find_next_user_time(history, i)

                turns.append({
                    "turnIndex": len(turns) + 1,
                    "userPromptPreview": record.get("content", "")[:30],
                    "timestamp": self._timestamp_to_iso(record["timestamp"]),
                    "start_timestamp": turn_start,
                    "end_timestamp": turn_end,
                    "files": {},
                    "stats": {
                        "filesChanged": 0,
                        "linesAdded": 0,
                        "linesRemoved": 0,
                    },
                })

            i += 1

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
                        "linesAdded": 0,
                        "linesRemoved": 0,
                        "lastEditTime": None,
                    }

                for op in edit_info["operations"]:
                    hunks = self._compute_hunks(
                        op["old_content"],
                        op["new_content"],
                    )
                    turn["files"][file_path]["hunks"].extend(hunks)
                    turn["files"][file_path]["lastEditTime"] = op["timestamp"]

                    if op["action"] == "write" and op["old_content"] is None:
                        turn["files"][file_path]["isNewFile"] = True

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

        turns_with_files = [t for t in turns if t["files"]]
        # Keep original turnIndex (aligned with user_count in history)
        # instead of renumbering — allows list_session_turns to correctly
        # map stats by the actual turn position.
        return turns_with_files

    @staticmethod
    def _is_turn_end(record: dict[str, Any]) -> bool:
        """判断一条记录是否是 turn 的结束."""
        event_type = record.get("event_type")
        if event_type == "chat.final":
            return True
        if event_type == "chat.evolution_status" and record.get("status") == "end":
            return True
        return False

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
    def _read_history(session_id: str) -> list[dict[str, Any]]:
        """读取 session history."""
        history_file = get_agent_sessions_dir() / session_id / "history.json"
        if not history_file.exists():
            return []
        try:
            return json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def _get_project_dir_from_metadata(session_id: str) -> str | None:
        """从 session metadata.json 中读取项目目录."""
        metadata_file = get_agent_sessions_dir() / session_id / "metadata.json"
        if not metadata_file.exists():
            return None
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            # 从 channel_metadata.cwd 或 delivery_context.route_metadata.cwd 获取
            channel_meta = metadata.get("channel_metadata", {})
            if isinstance(channel_meta, dict):
                cwd = channel_meta.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    return cwd.strip()
            delivery_ctx = metadata.get("delivery_context", {})
            if isinstance(delivery_ctx, dict):
                route_meta = delivery_ctx.get("route_metadata", {})
                if isinstance(route_meta, dict):
                    cwd = route_meta.get("cwd")
                    if isinstance(cwd, str) and cwd.strip():
                        return cwd.strip()
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read metadata file %s: %s", metadata_file, e)
        return None

    def _is_valid_file_ops_file(
        self, name: str, session_id: str | None, require_session: bool = False
    ) -> bool:
        """检查文件名是否是有效的 file_ops 文件."""
        if not name.startswith(f"file_ops_{self._agent_id}_"):
            return False
        if not name.endswith(".json"):
            return False
        if require_session:
            return session_id is not None and session_id in name
        return session_id is None or session_id in name

    def _read_agent_history(self, session_id: str | None = None, project_dir: str | None = None) -> dict[str, Any]:
        """读取 .agent_history（同时读取全局与 session-specific 文件并合并）.

        Args:
            session_id: 若提供，额外扫描匹配该 session 的 file_ops 文件。
            project_dir: 项目目录路径，若提供则也从项目目录读取 .agent_history。
        """
        result: dict[str, Any] = {}

        # 1. 从 Agent Workspace 和 User Workspace 读取（公共位置）
        paths = [
            get_agent_workspace_dir() / ".agent_history" / f"file_ops_{self._agent_id}.json",
            get_user_workspace_dir() / ".agent_history" / f"file_ops_{self._agent_id}.json",
        ]

        # 2. session-specific file_ops（如 file_ops_jiuwenswarm_tui_xxx.json）
        if session_id:
            for base_dir in (get_agent_workspace_dir(), get_user_workspace_dir()):
                hist_dir = base_dir / ".agent_history"
                if not hist_dir.is_dir():
                    continue
                for f in hist_dir.iterdir():
                    name = f.name
                    if self._is_valid_file_ops_file(name, session_id, require_session=True):
                        paths.append(f)

        # 3. 从项目目录读取（实际写入位置）
        # 如果未传入 project_dir，尝试从 session metadata 获取
        if project_dir is None and session_id:
            project_dir = self._get_project_dir_from_metadata(session_id)
        if project_dir:
            project_hist_dir = Path(project_dir) / ".agent_history"
            if project_hist_dir.is_dir():
                # 读取 session-specific file_ops 文件
                for f in project_hist_dir.iterdir():
                    name = f.name
                    if self._is_valid_file_ops_file(name, session_id):
                        paths.append(f)
                # 也读取全局 file_ops 文件（不带 session_id 后缀的）
                global_file = project_hist_dir / f"file_ops_{self._agent_id}.json"
                if global_file.exists():
                    paths.append(global_file)

        # 用于规范化路径，避免大小写差异导致的重复
        def normalize_path(p: str) -> str:
            """规范化路径：统一大小写和斜杠方向"""
            # 使用 pathlib.Path 规范化路径
            try:
                return str(Path(p).resolve())
            except Exception:
                return p.replace("\\", "/").lower()

        for history_file in paths:
            if history_file.exists():
                try:
                    data = json.loads(history_file.read_text(encoding="utf-8"))
                    for file_path, entries in data.items():
                        # 规范化路径，避免大小写差异导致的重复
                        normalized_path = normalize_path(file_path)
                        if normalized_path not in result:
                            result[normalized_path] = []
                        # 合并条目，避免时间戳相近的重复记录
                        for entry in entries:
                            # 检查是否已存在相同时间戳（±1秒）的相同操作
                            ts = entry.get("timestamp", "")
                            action = entry.get("action", "")
                            is_duplicate = False
                            for existing in result[normalized_path]:
                                existing_ts = existing.get("timestamp", "")
                                existing_action = existing.get("action", "")
                                if action == existing_action:
                                    # 比较时间戳是否相近（同一秒内）
                                    try:
                                        t1 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                        t2 = datetime.fromisoformat(existing_ts.replace("Z", "+00:00"))
                                        if abs((t1 - t2).total_seconds()) < 2:
                                            is_duplicate = True
                                            break
                                    except (ValueError, TypeError):
                                        # 时间戳格式无效，无法比较，跳过此条目比较
                                        continue
                            if not is_duplicate:
                                result[normalized_path].append(entry)
                except Exception as e:
                    logger.warning(f"Failed to read agent history file {history_file}: {e}")

        return result

    def _find_file_edits_by_time_range(
        self,
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
                edit_time = self._iso_to_timestamp(entry["timestamp"])

                if edit_time >= start_time:
                    if end_time is None or edit_time < end_time:
                        if file_path not in file_edits:
                            file_edits[file_path] = {
                                "file_path": file_path,
                                "operations": [],
                            }
                        file_edits[file_path]["operations"].append({
                            "action": entry["action"],
                            "timestamp": entry["timestamp"],
                            "old_content": entry["old_content"],
                            "new_content": entry["new_content"],
                        })

        return file_edits

    @staticmethod
    def _iso_to_timestamp(iso_str: str) -> float:
        """将 ISO 8601 字符串转换为 Unix timestamp."""
        dt = datetime.fromisoformat(iso_str)
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
    ) -> list[dict[str, Any]]:
        """计算结构化 diff hunks."""
        # 处理删除文件的情况：new_content 为 None
        if new_content is None:
            if old_content is None:
                return []
            # 文件被删除：显示所有行被移除
            lines = old_content.splitlines()
            return [{
                "oldStart": 1,
                "oldLines": len(lines),
                "newStart": 0,
                "newLines": 0,
                "lines": [f"-{line}" for line in lines],
            }]

        # 处理新建文件的情况：old_content 为 None
        if old_content is None:
            lines = new_content.splitlines()
            return [{
                "oldStart": 0,
                "oldLines": 0,
                "newStart": 1,
                "newLines": len(lines),
                "lines": [f"+{line}" for line in lines],
            }]

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        if not old_lines and not new_lines:
            return []

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        hunks: list[dict[str, Any]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            old_start = i1 + 1
            old_count = i2 - i1
            new_start = j1 + 1
            new_count = j2 - j1

            lines: list[str] = []

            for k in range(i1, i2):
                lines.append(f"-{old_lines[k].rstrip()}")

            for k in range(j1, j2):
                lines.append(f"+{new_lines[k].rstrip()}")

            hunks.append({
                "oldStart": old_start,
                "oldLines": old_count,
                "newStart": new_start,
                "newLines": new_count,
                "lines": lines,
            })

        return hunks

    @staticmethod
    def _finalize_turn(turn: dict[str, Any]) -> None:
        """完成 turn 的统计信息计算."""
        turn["stats"]["filesChanged"] = len(turn["files"])
        turn["stats"]["linesAdded"] = sum(
            f["linesAdded"] for f in turn["files"].values()
        )
        turn["stats"]["linesRemoved"] = sum(
            f["linesRemoved"] for f in turn["files"].values()
        )

    def get_files_to_restore(
        self, session_id: str, turn_index: int, project_dir: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """返回需要恢复的文件及其目标内容.

        对于在 turn_index 及之后所有 turn 中被修改的文件，
        找到它们在 turn_index 开始前的状态（old_content of the first
        edit at/after the target turn），以便恢复操作将文件写回。

        Args:
            session_id: 会话 ID
            turn_index: 目标回退轮次（1-based，即 /rewind 使用的编号）
            project_dir: 项目目录路径（可选，若不提供则从 session metadata 读取）

        Returns:
            { file_path: { "restore_content": str | None, "action": "write" | "delete" } }
            restore_content 为 None 表示文件在目标 turn 之前不存在，应删除。
        """
        history = self._read_history(session_id)
        if not history:
            return {}

        # 1. 找到目标 turn 的起始时间（第 N 条 user 消息的 timestamp）
        user_count = 0
        target_timestamp: float | None = None
        for record in history:
            if record.get("role") == "user":
                user_count += 1
                if user_count == turn_index:
                    target_timestamp = record.get("timestamp")
                    break

        if target_timestamp is None:
            return {}

        # 2. 读取 file_ops 日志
        agent_history = self._read_agent_history(session_id, project_dir)

        # 3. 对于每个文件，找到第一条 timestamp >= target_timestamp 的 entry
        #    该 entry 的 old_content 即为目标 turn 开始前的文件状态
        files_to_restore: dict[str, dict[str, Any]] = {}
        for file_path, entries in agent_history.items():
            # entries 按 timestamp 排序（写入时序）
            for entry in entries:
                edit_time = self._iso_to_timestamp(entry["timestamp"])
                if edit_time >= target_timestamp:
                    if entry.get("old_content") is not None:
                        files_to_restore[file_path] = {
                            "restore_content": entry["old_content"],
                            "action": "write",
                        }
                    else:
                        # 文件由 agent 创建，恢复时应删除
                        files_to_restore[file_path] = {
                            "restore_content": None,
                            "action": "delete",
                        }
                    break  # 只需要第一条匹配的 entry

        return files_to_restore


_diff_service: DiffService | None = None


def get_diff_service() -> DiffService:
    """获取 DiffService 单例实例."""
    global _diff_service
    if _diff_service is None:
        _diff_service = DiffService()
    return _diff_service
