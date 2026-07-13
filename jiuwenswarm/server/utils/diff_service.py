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
from jiuwenswarm.server.runtime.session.session_history import load_history_records


logger = logging.getLogger(__name__)


MAX_FILES = 50
MAX_DIFF_SIZE_BYTES = 1_000_000
MAX_LINES_PER_FILE = 400
MAX_FILES_FOR_DETAILS = 500


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

        if not history:
            return []

        agent_history = self._read_agent_history(session_id, project_dir)

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
                        "isBinary": False,
                        "isLargeFile": False,
                        "isTruncated": False,
                        "isUntracked": False,
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
        try:
            return load_history_records(session_id)
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
        max_lines: int = MAX_LINES_PER_FILE,
    ) -> tuple[list[dict[str, Any]], bool]:
        """计算结构化 diff hunks.

        Returns:
            (hunks, truncated): hunks 列表和是否被截断的标志。
        """
        # 处理删除文件的情况：new_content 为 None
        if new_content is None:
            if old_content is None:
                return [], False
            # 文件被删除：显示所有行被移除
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

        # 处理新建文件的情况：old_content 为 None
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

        # Emit unified hunks with context_lines of surrounding context and
        # merge adjacent changes whose context windows overlap, matching
        # `git diff --unified=3` / jsdiff's structuredPatch. The previous
        # implementation skipped equal opcodes entirely, producing context-less
        # isolated hunks that showed far less content than `git diff`.
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

            # First change of this hunk is at opcodes[i]; absorb following
            # changes whose separating equal run is short enough that their
            # context windows bridge the gap (run length <= 2*context_lines).
            change_start = i
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

            # Include the leading equal opcode (i-1) and the trailing equal
            # opcode (k, when present) so leading/trailing context lines are
            # emitted; both are clamped to the window below.
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

    @staticmethod
    def _decode_c_escaped(inner: str) -> str:
        """Decode git's C-style escapes in an already-unquoted path segment.

        Handles ``\\t \\n \\r \\a \\b \\v \\f \\" \\\\`` , octal ``\\NNN`` and
        hex ``\\xNN``; unknown escapes keep the backslash literally.
        """
        simple = {
            "a": 0x07, "b": 0x08, "t": 0x09, "n": 0x0A,
            "v": 0x0B, "f": 0x0C, "r": 0x0D, '"': 0x22, "\\": 0x5C,
        }
        out = bytearray()
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch != "\\":
                out.extend(ch.encode("utf-8"))
                i += 1
                continue
            i += 1
            if i >= len(inner):
                break
            esc = inner[i]
            if esc in simple:
                out.append(simple[esc])
                i += 1
                continue
            if esc == "x":
                hexd = inner[i + 1:i + 3]
                if len(hexd) == 2 and all(c in "0123456789abcdefABCDEF" for c in hexd):
                    out.append(int(hexd, 16) & 0xFF)
                    i += 3
                    continue
            if esc in "01234567":
                j = i
                oct_digits = ""
                while j < len(inner) and inner[j] in "01234567" and len(oct_digits) < 3:
                    oct_digits += inner[j]
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            # Unknown escape: keep backslash and the char literally.
            out.extend(b"\\")
            out.extend(esc.encode("utf-8"))
            i += 1
        return out.decode("utf-8", errors="replace")

    @staticmethod
    def _unquote_git_path(path: str) -> str:
        """Decode a git-quoted path back to its raw bytes.

        git wraps paths containing control chars / quotes / backslashes in
        double quotes and C-escapes the offending bytes. This quoting is
        independent of ``core.quotepath`` (which only governs non-ASCII bytes),
        so a literal TAB in a filename is emitted as ``"dir\\tfile.txt"``
        regardless of that setting. Feeding the quoted form straight into
        ``Path(repo) / path`` resolves to a non-existent file, so numstat,
        diff headers and ls-files paths must be unquoted here to match the
        real on-disk relative path. Unquoted paths are returned verbatim.
        """
        if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
            return path
        return DiffService._decode_c_escaped(path[1:-1])

    @staticmethod
    def _extract_diff_header_path(token: str) -> str | None:
        """Extract the on-disk relative path from a ``--- a/`` / ``+++ b/`` token.

        git quotes the whole ``a/<path>`` / ``b/<path>`` form when the path
        contains control chars (e.g. ``+++ "b/dir\\tfile.txt"``), so the prefix
        lives inside the quotes. Strip the prefix and decode, returning the
        real relative path, or ``None`` for ``/dev/null`` (deleted/new file
        counterpart).
        """
        if token == "/dev/null":
            return None
        quoted = len(token) >= 2 and token.startswith('"') and token.endswith('"')
        inner = token[1:-1] if quoted else token
        for prefix in ("b/", "a/"):
            if inner.startswith(prefix):
                rel = inner[len(prefix):]
                return DiffService._decode_c_escaped(rel) if quoted else rel
        return DiffService._decode_c_escaped(inner) if quoted else inner

    @staticmethod
    def _run_git_command(project_dir: str, args: list[str]) -> str | None:
        """在 project_dir 中运行 git 命令，返回 stdout 或 None."""
        import subprocess

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            if result.returncode != 0:
                return None
            return result.stdout
        except Exception:
            return None

    @staticmethod
    def _get_git_toplevel(project_dir: str) -> str | None:
        """返回 git 仓库根目录；project_dir 可以是仓库内任意子目录."""
        import subprocess

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            if result.returncode != 0:
                return None
            root = result.stdout.strip()
            return str(Path(root).resolve()) if root else None
        except Exception:
            return None

    @staticmethod
    def _is_in_transient_git_state(project_dir: str) -> bool:
        """检测是否处于 merge/rebase/cherry-pick/revert 等瞬态 git 状态.

        这些状态下工作区包含 incoming 改动（非用户意图编辑），
        应跳过 diff 计算以避免显示误导性内容。
        """
        import subprocess

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            if result.returncode != 0:
                return False
            git_dir = Path(result.stdout.strip())
            if not git_dir.is_absolute():
                git_dir = Path(project_dir) / git_dir
        except Exception:
            return False

        transient_files = [
            "MERGE_HEAD",
            "REBASE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
        ]
        return any((git_dir / name).exists() for name in transient_files)

    @staticmethod
    def _parse_git_numstat(output: str) -> dict[str, dict[str, int | bool]]:
        """解析 git diff --numstat 输出为 per-file 统计.

        输入格式:
            3\t2\tpath/to/file.py
            -\t-\tbinary_file.png

        返回:
            { "/abs/path/file.py": {"added": 3, "removed": 2, "isBinary": false}, ... }
        """
        import re

        result: dict[str, dict[str, int | bool]] = {}
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added_str, removed_str = parts[0], parts[1]
            file_path = "\t".join(parts[2:])
            # rename 的 numstat 路径需归一化为新路径，否则与 _parse_git_diff_hunks
            # 从 "+++ b/new" 提取的 key 不一致，导致 hunks 丢失。
            # 两种形式:
            #   - brace 简写(有共同前/后缀): a/{b => c}/d.txt  -> a/c/d.txt
            #     (可能嵌套或多段: a/{b => c}/{d => e}.txt)
            #   - 裸形式(无共同前/后缀): old => new  -> new
            while True:
                m = re.search(r"\{([^{}]*)\s=>\s([^{}]*)\}", file_path)
                if not m:
                    break
                file_path = file_path[:m.start()] + m.group(2) + file_path[m.end():]
            if " => " in file_path:
                file_path = file_path.rsplit(" => ", 1)[-1].strip()
            # 控制字符路径（如含 TAB）会被 git 加引号并 C 转义（与
            # core.quotepath 无关），解码回原始字节串才能对应磁盘真实文件。
            file_path = DiffService._unquote_git_path(file_path)
            is_binary = added_str == "-" and removed_str == "-"
            result[file_path] = {
                "added": 0 if is_binary else int(added_str),
                "removed": 0 if is_binary else int(removed_str),
                "isBinary": is_binary,
            }
        return result

    @staticmethod
    def _parse_shortstat(output: str) -> dict[str, int] | None:
        """解析 git diff --shortstat 输出.

        格式: " N files changed, N insertions(+), N deletions(-)"
        用于在加载完整 diff 前快速探测规模。
        """
        import re

        match = re.match(
            r"(\d+)\s+files?\s+changed(?:,\s+(\d+)\s+insertions?\(\+\))?(?:,\s+(\d+)\s+deletions?\(-\))?",
            output.strip(),
        )
        if not match:
            return None
        return {
            "filesChanged": int(match.group(1) or "0"),
            "linesAdded": int(match.group(2) or "0"),
            "linesRemoved": int(match.group(3) or "0"),
        }

    @staticmethod
    def _parse_git_diff_hunks(output: str) -> dict[str, list[dict[str, Any]]]:
        """解析 git diff 输出为按文件分组的 hunk 列表.

        每个 hunk 格式与 _compute_hunks() 一致:
            {
                "oldStart": int, "oldLines": int,
                "newStart": int, "newLines": int,
                "lines": ["-removed line", "+added line", " context line"],
            }
        """
        import re

        files: dict[str, list[dict[str, Any]]] = {}
        current_file: str | None = None
        current_hunk: dict[str, Any] | None = None
        line_counts: dict[str, int] = {}
        truncated: set[str] = set()

        # 匹配 diff 头部: --- a/path, +++ b/path
        # 控制字符路径会被整体加引号（如 +++ "b/dir\tfile.txt"），b/ 前缀在引号内，
        # 所以捕获整个 token（含引号）再用 _extract_diff_header_path 剥前缀+解码。
        # 限定 b//a/ 前缀或两端引号，避免把以 "++ " 开头的 hunk 内容行（会变成
        # "+++ ..."，无 b/ 前缀也非两端引号）误判为文件头。
        # 对于删除文件，+++ b/ 行是 +++ /dev/null 不会匹配，需要回退到 --- a/ 行
        file_header_new_re = re.compile(r'^\+\+\+ (b/.*|".*")$')
        file_header_old_re = re.compile(r'^--- (a/.*|".*")$')
        # 匹配 hunk 头部: @@ -oldStart,oldLines +newStart,newLines @@
        hunk_header_re = re.compile(
            r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@"
        )

        for line in output.splitlines():
            # 检测文件头（优先 +++ b/ 行）
            file_match = file_header_new_re.match(line)
            if file_match:
                resolved = DiffService._extract_diff_header_path(file_match.group(1))
                if resolved is not None:
                    current_file = resolved
                    if current_file not in files:
                        files[current_file] = []
                        line_counts[current_file] = 0
                    current_hunk = None
                continue

            # 回退：对于删除文件，+++ b/ 不匹配（是 +++ /dev/null），
            # 从 --- a/ 行提取文件路径
            file_match = file_header_old_re.match(line)
            if file_match:
                resolved = DiffService._extract_diff_header_path(file_match.group(1))
                if resolved is not None:
                    current_file = resolved
                    if current_file not in files:
                        files[current_file] = []
                        line_counts[current_file] = 0
                    current_hunk = None
                continue

            if current_file is None:
                continue

            # 检测 hunk 头
            hunk_match = hunk_header_re.match(line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_lines = int(hunk_match.group(2) or "1")
                new_start = int(hunk_match.group(3))
                new_lines = int(hunk_match.group(4) or "1")

                current_hunk = {
                    "oldStart": old_start,
                    "oldLines": old_lines,
                    "newStart": new_start,
                    "newLines": new_lines,
                    "lines": [],
                }
                files[current_file].append(current_hunk)
                continue

            if current_hunk is None:
                continue

            # 收集 hunk 行（+, -, 空格前缀的上下文行）
            if line.startswith("+") or line.startswith("-") or line.startswith(" "):
                if line_counts[current_file] >= MAX_LINES_PER_FILE:
                    truncated.add(current_file)
                    continue
                current_hunk["lines"].append(line)
                line_counts[current_file] += 1

        return files, truncated

    @staticmethod
    def _split_large_file_diffs(
        output: str,
    ) -> tuple[str, set[str]]:
        """将 git diff 输出按文件切分，跳过超过 MAX_DIFF_SIZE_BYTES 的文件块.

        返回 (过滤后的 diff 输出, 被跳过的大文件路径集合)。
        被跳过的文件不参与 hunk 解析，但 numstat 统计仍会保留。
        """
        import re

        if not output:
            return "", set()
        # 以 "diff --git " 为分隔切分（首段通常为空）
        chunks = output.split("diff --git ")
        kept: list[str] = []
        large_files: set[str] = set()
        for chunk in chunks:
            if not chunk:
                continue
            full = "diff --git " + chunk
            if len(full.encode("utf-8", errors="replace")) > MAX_DIFF_SIZE_BYTES:
                # 提取文件路径用于标记。路径可能被引号包裹（控制字符），
                # 需用 _extract_diff_header_path 解码以与 numstat key 对齐。
                m = re.search(r'^\+\+\+ (b/.*|".*")$', full, re.MULTILINE)
                if m:
                    resolved = DiffService._extract_diff_header_path(m.group(1))
                    if resolved is not None:
                        large_files.add(resolved)
                else:
                    m2 = re.search(r'^--- (a/.*|".*")$', full, re.MULTILINE)
                    if m2:
                        resolved = DiffService._extract_diff_header_path(m2.group(1))
                        if resolved is not None:
                            large_files.add(resolved)
                continue
            kept.append(full)
        return "".join(kept), large_files

    def _get_untracked_files(
        self, project_dir: str, max_files: int = MAX_FILES
    ) -> dict[str, dict[str, Any]]:
        """获取未跟踪文件列表，仅记录文件名和状态."""
        # core.quotepath=false 让 git 对非 ASCII 字节直接输出原始 UTF-8 文件名
        # （而非八进制转义串），否则中文路径无法对应磁盘真实路径。但 ASCII 控制字符
        # （如 TAB）无论该设置如何都会被加引号并 C 转义（如 "dir\tfile.txt"），
        # 仍需 _unquote_git_path 解码才能对应磁盘真实文件。
        output = self._run_git_command(
            project_dir,
            ["-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard"],
        )
        if not output or not output.strip():
            return {}

        files: dict[str, dict[str, Any]] = {}
        for rel_path in output.strip().splitlines():
            if len(files) >= max_files:
                break
            rel_path = rel_path.strip()
            if not rel_path:
                continue
            rel_path = DiffService._unquote_git_path(rel_path)
            abs_path = str(Path(project_dir) / rel_path)

            files[abs_path] = {
                "filePath": abs_path,
                "hunks": [],
                "isNewFile": True,
                "isBinary": False,
                "isLargeFile": False,
                "isTruncated": False,
                "isUntracked": True,
                "linesAdded": 0,
                "linesRemoved": 0,
                "lastEditTime": None,
            }

        return files

    def get_git_diff(self, project_dir: str | None) -> dict[str, Any] | None:
        """获取工作区相对于 HEAD 的 git diff（仅已跟踪文件的修改）.

        Args:
            project_dir: 项目目录路径.

        Returns:
            {
                "stats": {"filesChanged": int, "linesAdded": int, "linesRemoved": int},
                "files": { file_path: { "filePath": str, "hunks": [...],
                    "isNewFile": bool, "linesAdded": int, "linesRemoved": int } }
            }
            如果不是 git 仓库或没有任何改动，返回 None.
        """
        if not project_dir:
            return None
        repo_dir = self._get_git_toplevel(project_dir)
        if not repo_dir:
            return None
        if self._is_in_transient_git_state(repo_dir):
            return None

        files: dict[str, dict[str, Any]] = {}
        total_files_changed = 0
        total_added = 0
        total_removed = 0

        # 1. 已跟踪文件的改动: git diff HEAD
        # 先用 --shortstat 快速探测规模，避免对超大 diff 加载完整内容
        shortstat = self._run_git_command(repo_dir, ["diff", "HEAD", "--shortstat"])
        has_tracked_changes = shortstat and shortstat.strip() != ""

        # 解析 shortstat 取得准确的文件/行数总计
        shortstat_stats = self._parse_shortstat(shortstat) if has_tracked_changes else None
        if shortstat_stats and shortstat_stats["filesChanged"] > MAX_FILES_FOR_DETAILS:
            # 文件数过多，仅返回统计以避免加载数百 MB 内容
            return {
                "stats": {
                    "filesChanged": shortstat_stats["filesChanged"],
                    "linesAdded": shortstat_stats["linesAdded"],
                    "linesRemoved": shortstat_stats["linesRemoved"],
                },
                "files": {},
            }

        if has_tracked_changes:
            numstat_output = self._run_git_command(repo_dir, ["diff", "HEAD", "--numstat"])
            diff_output = self._run_git_command(repo_dir, ["diff", "HEAD"])
            if numstat_output and diff_output:
                per_file_stats = self._parse_git_numstat(numstat_output)
                total_files_changed += len(per_file_stats)
                total_added += sum(int(stats["added"]) for stats in per_file_stats.values())
                total_removed += sum(int(stats["removed"]) for stats in per_file_stats.values())
                filtered_output, large_files = self._split_large_file_diffs(diff_output)
                all_hunks, truncated_files = self._parse_git_diff_hunks(filtered_output)

                for rel_path, stats in list(per_file_stats.items())[:MAX_FILES]:
                    abs_path = str(Path(repo_dir) / rel_path)
                    is_binary = bool(stats.get("isBinary", False))
                    is_large = rel_path in large_files
                    is_truncated = rel_path in truncated_files
                    if is_binary or is_large:
                        hunks = []
                    else:
                        hunks = all_hunks.get(rel_path, [])
                    lines_added = stats["added"]
                    lines_removed = stats["removed"]

                    files[abs_path] = {
                        "filePath": abs_path,
                        "hunks": hunks,
                        "isNewFile": False,
                        "isBinary": is_binary,
                        "isLargeFile": is_large,
                        "isTruncated": is_truncated,
                        "isUntracked": False,
                        "linesAdded": lines_added,
                        "linesRemoved": lines_removed,
                        "lastEditTime": None,
                    }

        if not files:
            return None

        return {
            "stats": {
                "filesChanged": total_files_changed,
                "linesAdded": total_added,
                "linesRemoved": total_removed,
            },
            "files": files,
        }

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


    def truncate_file_ops_by_timestamp(self, session_id: str, cutoff_ts: float) -> None:
        """截断 session-specific file_ops 日志，移除 timestamp >= cutoff_ts 的条目.

        在 rewind 操作后调用，确保 file_ops 日志与截断后的 history.json 一致。
        仅处理 session-specific 文件（文件名包含 session_id），不动全局 file_ops。

        Args:
            session_id: 会话 ID
            cutoff_ts: 截断阈值（Unix timestamp），>= 此时间的条目将被移除
        """

        # 收集所有 session-specific file_ops 文件
        file_ops_paths: list[Path] = []
        for base_dir in (get_agent_workspace_dir(), get_user_workspace_dir()):
            hist_dir = base_dir / ".agent_history"
            if not hist_dir.is_dir():
                continue
            for f in hist_dir.iterdir():
                if self._is_valid_file_ops_file(f.name, session_id, require_session=True):
                    file_ops_paths.append(f)

        # 也从项目目录扫描
        project_dir = self._get_project_dir_from_metadata(session_id)
        if project_dir:
            project_hist_dir = Path(project_dir) / ".agent_history"
            if project_hist_dir.is_dir():
                for f in project_hist_dir.iterdir():
                    if self._is_valid_file_ops_file(f.name, session_id, require_session=True):
                        if f not in file_ops_paths:
                            file_ops_paths.append(f)

        for file_ops_path in file_ops_paths:
            try:
                data = json.loads(file_ops_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue

                truncated = False
                new_data: dict[str, Any] = {}
                for file_path, entries in data.items():
                    if not isinstance(entries, list):
                        continue
                    filtered = []
                    for e in entries:
                        try:
                            entry_ts = self._iso_to_timestamp(e.get("timestamp", ""))
                        except (ValueError, TypeError):
                            filtered.append(e)  # 无法解析的条目保留
                            continue
                        if entry_ts < cutoff_ts:
                            filtered.append(e)
                    if len(filtered) != len(entries):
                        truncated = True
                    if filtered:
                        new_data[file_path] = filtered

                if truncated:
                    file_ops_path.write_text(
                        json.dumps(new_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    logger.info(
                        "truncate_file_ops: cleaned %s (cutoff_ts=%s)",
                        file_ops_path.name, cutoff_ts,
                    )
            except Exception as exc:
                logger.warning(
                    "truncate_file_ops: failed to process %s: %s",
                    file_ops_path, exc,
                )


_diff_service: DiffService | None = None


def get_diff_service() -> DiffService:
    """获取 DiffService 单例实例."""
    global _diff_service
    if _diff_service is None:
        _diff_service = DiffService()
    return _diff_service
