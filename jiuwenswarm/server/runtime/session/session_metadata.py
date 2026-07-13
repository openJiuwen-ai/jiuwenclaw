"""会话元数据管理模块"""
from __future__ import annotations

import copy
import json
import logging
import queue
import re
import shutil
import threading
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from jiuwenswarm.common.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

# ---------- 异步写入队列(与 session_history 保持一致的模式) ----------
_METADATA_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=5000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()

# 内存缓存: 解决异步写入时读取到陈旧磁盘数据的竞态条件
_METADATA_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

# 会话标题自动生成的截取长度
_TITLE_MAX_LEN = 50
# 心跳任务会话目录前缀，不参与 session.list 等列表展示
_HEARTBEAT_SESSION_PREFIX = "heartbeat_"
_DELIVERY_KIND_SERVER_PUSH = "server_push"

# 匹配所有小写 XML 块:
# 如 <system-reminder>、<file-content>、<command-name> 等系统/工具注入内容
_INJECTED_TAG_RE = re.compile(
    r"<([a-z][\w-]*)(?:\s[^>]*)?>.*?</\1>\n?", re.DOTALL
)
# 匹配截断的 XML 开始标签（标题被 _TITLE_MAX_LEN 截断时可能只剩开始标签）
_INJECTED_TAG_START_RE = re.compile(
    r"<[a-z][\w-]*(?:\s[^>]*)?>?"
)


def _sanitize_title(title: str) -> str:
    """清理标题中的系统注入 XML 标签。

    匹配所有小写 XML 标签（如 <system-reminder>、<file-content>、<command-name>），
    不匹配用户提及的大写 HTML/JSX（如 <Button>、<Component>）。

    处理两种情况：
    1. 完整的 <tag>...</tag> 块（正则移除）
    2. 被 _TITLE_MAX_LEN 截断的 <tag ... 开头（无闭合标签，整段丢弃）
    """
    if not title:
        return title
    cleaned = _INJECTED_TAG_RE.sub("", title).strip()
    if _INJECTED_TAG_START_RE.match(cleaned):
        return ""
    return cleaned


def _current_timestamp() -> float:
    """返回显式使用 UTC 时区的当前时间戳"""
    return datetime.now(timezone.utc).timestamp()


def _metadata_file(session_id: str) -> Path:
    """获取会话元数据文件路径"""
    session_dir = get_agent_sessions_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "metadata.json"


def _read_metadata(session_id: str, cache_bust: bool = False) -> dict[str, Any]:
    """读取会话元数据(优先从内存缓存读取,避免异步写入未落盘时读到陈旧数据)

    读路径不应产生副作用：即便 session 目录不存在，也不触发 mkdir，
    否则会导致仅查询(session.rename 无 title 参数时)隐式创建空 session 目录，
    污染 session.list 结果。

    Args:
        session_id: 会话 ID
        cache_bust: 强制跳过缓存，直接从磁盘读取（用于跨进程同步场景，如 session.list）
    """
    if not cache_bust:
        with _CACHE_LOCK:
            cached = _METADATA_CACHE.get(session_id)
            if cached is not None:
                return cached.copy()
    # cache_bust=True 或缓存没有数据时，强制读磁盘
    fpath = get_agent_sessions_dir() / session_id / "metadata.json"
    if not fpath.exists():
        return {}
    try:
        data = json.loads(fpath.read_text(encoding="utf-8") or '{}')
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("读取 metadata.json 失败: %s", exc)
    return {}


def _write_metadata_sync(session_id: str, metadata: dict[str, Any]) -> None:
    """同步写入会话元数据(由后台 worker 或 fallback 调用)

    注意: 不更新 _METADATA_CACHE。缓存仅由 _enqueue_write 维护,
    避免 gateway 进程的 init_session_metadata 污染缓存导致后续
    读取不到 agentserver 进程写入的最新数据。
    """
    fpath = _metadata_file(session_id)
    with _FILE_LOCK:
        fpath.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                sid, metadata = _METADATA_QUEUE.get()
                try:
                    _write_metadata_sync(sid, metadata)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("metadata 异步写入失败: %s", exc)
                finally:
                    _METADATA_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-metadata-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def _enqueue_write(session_id: str, metadata: dict[str, Any]) -> None:
    """将写入操作放入异步队列,队列满时退化为同步写"""
    # 立即更新缓存,确保后续读取能看到最新状态
    with _CACHE_LOCK:
        _METADATA_CACHE[session_id] = metadata.copy()
    _ensure_worker_started()
    try:
        _METADATA_QUEUE.put_nowait((session_id, metadata))
    except queue.Full:
        _write_metadata_sync(session_id, metadata)


def _auto_title(content: str) -> str:
    """从首条用户消息自动生成会话标题"""
    # 先剥离所有小写 XML 注入标签，
    # 避免将系统提示/文件注入/工具标签误识别为会话标题
    cleaned = _INJECTED_TAG_RE.sub("", content).strip()
    if not cleaned:
        return ""
    title = cleaned.replace("\n", " ")
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_MAX_LEN] + "..."
    return title


def init_session_metadata(
    *,
    session_id: str,
    channel_id: str = "",
    user_id: str = "",
    title: str = "",
    mode: str = "unknown",
    team_name: str = "",
) -> None:
    """初始化会话元数据(同步写,确保创建后立即可读)"""
    metadata = {
        "session_id": session_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "created_at": _current_timestamp(),
        "last_message_at": _current_timestamp(),
        "title": title,
        "message_count": 0,
        "mode": mode,
        "team_name": team_name,
        "round_id": 0,
    }
    _write_metadata_sync(session_id, metadata)


def update_session_metadata(
    *,
    session_id: str,
    channel_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
    clear_title: bool = False,
    increment_message_count: bool = False,
    set_message_count: int | None = None,
    user_content: str | None = None,
    channel_metadata: dict[str, Any] | None = None,
    mode: str | None = None,
    team_name: str | None = None,
    accent_color: str | None = None,
) -> None:
    """更新会话元数据(异步写入,不阻塞调用方)

    title 语义(保持历史防御契约)：
      - title=None  → 不修改（默认）
      - title="x"   → 设置为 "x"
      - title=""    → 忽略（防御意外空值覆盖已有标题）
      - 若需显式清除标题，请设置 clear_title=True
    """
    metadata = _read_metadata(session_id)

    if not metadata:
        # 如果元数据不存在,创建新的(外部渠道隐式创建 session 的兜底)
        # 自动生成标题: 当 title 为空且提供了用户消息内容时
        auto_title = ""
        if not title and user_content:
            auto_title = _auto_title(user_content)
        metadata = {
            "session_id": session_id,
            "channel_id": channel_id or "",
            "user_id": user_id or "",
            "created_at": _current_timestamp(),
            "last_message_at": _current_timestamp(),
            "title": title or auto_title,
            "message_count": 1 if increment_message_count else 0,
            "mode": mode if mode is not None else "unknown",
            "team_name": team_name or "",
            "round_id": 0,
        }
        # 首次创建时写入 channel_metadata
        if channel_metadata:
            metadata["channel_metadata"] = channel_metadata
    else:
        # 更新现有元数据
        if channel_id is not None:
            metadata["channel_id"] = channel_id
        if user_id is not None:
            metadata["user_id"] = user_id
        if mode is not None:
            metadata["mode"] = mode
        if team_name is not None:
            metadata["team_name"] = team_name
        if accent_color is not None:
            metadata["accent_color"] = accent_color
        # 显式清除优先级高于 title 入参
        if clear_title:
            metadata["title"] = ""
        elif title:
            metadata["title"] = title
        if increment_message_count:
            metadata["message_count"] = metadata.get("message_count", 0) + 1
        if set_message_count is not None:
            metadata["message_count"] = set_message_count

        # 自动生成标题: 当 title 为空且提供了用户消息内容时
        if not metadata.get("title") and user_content:
            metadata["title"] = _auto_title(user_content)

        # channel_metadata 仅在首次为空时补充写入（不覆盖）
        if channel_metadata and not metadata.get("channel_metadata"):
            metadata["channel_metadata"] = channel_metadata

        # 总是更新最后消息时间
        metadata["last_message_at"] = _current_timestamp()

    _enqueue_write(session_id, metadata)


def get_session_metadata(session_id: str, cache_bust: bool = False) -> dict[str, Any]:
    """获取会话元数据

    Args:
        session_id: 会话 ID
        cache_bust: 强制跳过缓存，直接从磁盘读取（用于跨进程同步场景）
    """
    metadata = _read_metadata(session_id, cache_bust)
    # 清理已有会话中可能被误写入的系统注入标签标题（<system-reminder>、<file-content> 等）
    if isinstance(metadata, dict) and metadata.get("title"):
        sanitized = _sanitize_title(metadata["title"])
        if sanitized != metadata["title"]:
            metadata["title"] = sanitized
    return metadata


def increment_session_round_count(session_id: str) -> int:
    """递增并持久化 session 的 round_id，返回递增后的值。

    - 首次调用时从 metadata 中读取 round_id（默认 0），先 ++ 再返回。
    - 持久化到 session metadata，确保重启后 round_id 不丢失。
    """
    metadata = _read_metadata(session_id)
    current_round = int(metadata.get("round_id", 0))
    new_round = current_round + 1
    metadata["round_id"] = new_round
    metadata["last_message_at"] = _current_timestamp()
    _enqueue_write(session_id, metadata)
    return new_round


def remove_session_metadata_cache(session_id: str) -> None:
    """Remove cached session metadata after the session directory is deleted."""
    with _CACHE_LOCK:
        _METADATA_CACHE.pop(session_id, None)


def set_session_delivery_context(
    *,
    session_id: str,
    channel_id: str | None,
    source_request_id: str | None,
    route_metadata: dict[str, Any] | None,
    delivery_kind: str = _DELIVERY_KIND_SERVER_PUSH,
) -> dict[str, Any]:
    """刷新 session 级 delivery context，供异步 server_push 恢复路由上下文。"""
    metadata = _read_metadata(session_id)
    current_context_raw = metadata.get("delivery_context")
    current_context = (
        copy.deepcopy(current_context_raw)
        if isinstance(current_context_raw, dict)
        else {}
    )

    normalized_channel_id = str(
        channel_id
        or current_context.get("channel_id")
        or metadata.get("channel_id")
        or ""
    ).strip()
    normalized_request_id = str(
        source_request_id or current_context.get("source_request_id") or ""
    ).strip()

    previous_route_metadata = current_context.get("route_metadata")
    if not isinstance(previous_route_metadata, dict):
        previous_route_metadata = None

    normalized_route_metadata = (
        copy.deepcopy(route_metadata)
        if isinstance(route_metadata, dict) and route_metadata
        else previous_route_metadata
    )

    if not metadata:
        metadata = {
            "session_id": session_id,
            "channel_id": normalized_channel_id,
            "user_id": "",
            "created_at": _current_timestamp(),
            "last_message_at": _current_timestamp(),
            "title": "",
            "message_count": 0,
            "mode": "unknown",
            "round_id": 0,
        }
    else:
        if normalized_channel_id:
            metadata["channel_id"] = normalized_channel_id
        metadata["last_message_at"] = _current_timestamp()

    delivery_context: dict[str, Any] = {
        "delivery_kind": str(delivery_kind or _DELIVERY_KIND_SERVER_PUSH).strip()
        or _DELIVERY_KIND_SERVER_PUSH,
        "session_id": session_id,
        "channel_id": normalized_channel_id,
        "source_request_id": normalized_request_id,
        "updated_at": _current_timestamp(),
    }
    if normalized_route_metadata:
        delivery_context["route_metadata"] = normalized_route_metadata

    metadata["delivery_context"] = delivery_context
    _enqueue_write(session_id, metadata)
    return copy.deepcopy(delivery_context)


def get_session_delivery_context(session_id: str) -> dict[str, Any] | None:
    """读取 session 级 delivery context。"""
    metadata = _read_metadata(session_id)
    context = metadata.get("delivery_context")
    if not isinstance(context, dict):
        return None
    return copy.deepcopy(context)


def build_server_push_message(
    *,
    session_id: str,
    request_id: str,
    payload: dict[str, Any],
    fallback_channel_id: str | None = None,
) -> dict[str, Any]:
    """基于 session delivery context 构造 evolution watcher 的 server_push 消息。"""
    delivery_context = get_session_delivery_context(session_id) or {}
    route_metadata = delivery_context.get("route_metadata")
    channel_id = str(
        delivery_context.get("channel_id") or fallback_channel_id or "default"
    ).strip() or "default"

    message: dict[str, Any] = {
        "request_id": request_id,
        "channel_id": channel_id,
        "session_id": session_id,
        "payload": dict(payload),
    }
    if isinstance(route_metadata, dict) and route_metadata:
        message["metadata"] = copy.deepcopy(route_metadata)
    return message


def remove_team_mode_session_dirs_at_startup() -> None:
    """agentserver 启动时删除 metadata.json 中 mode 为 team 的会话目录。"""
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.is_dir():
        return

    removed = 0
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        meta_path = session_dir / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8") or '{}')
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动清理跳过会话 %s: 读取 metadata.json 失败: %s", session_dir.name, exc)
            continue
        if not isinstance(raw, dict) or raw.get("mode") != "team":
            continue

        session_id = session_dir.name
        try:
            shutil.rmtree(session_dir)
            with _CACHE_LOCK:
                _METADATA_CACHE.pop(session_id, None)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("启动清理删除 team 会话目录失败 %s: %s", session_id, exc)

    if removed:
        logger.info("启动清理: 已删除 %d 个 team 模式会话目录", removed)


def get_all_sessions_metadata(
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    获取所有会话的元数据。

    Returns:
        (sessions, total): 当前页的会话列表 和 会话总数
    """
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return [], 0

    sessions = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue

        session_id = session_dir.name
        if session_id.startswith(_HEARTBEAT_SESSION_PREFIX):
            continue
        metadata = _read_metadata(session_id)

        if not metadata:
            # 没有 metadata.json 的旧会话: 只构造最小信息,不读取 history.json
            # (避免大量旧会话导致接口变慢,完整推断由启动迁移负责)
            metadata = {
                "session_id": session_id,
                "channel_id": "",
                "user_id": "",
                "created_at": session_dir.stat().st_ctime,
                "last_message_at": session_dir.stat().st_mtime,
                "title": "",
                "message_count": 0,
                "mode": "unknown",
            }

        sessions.append(metadata)

    # 清理已有会话中可能被误写入的系统注入标签标题（<system-reminder>、<file-content> 等）
    for s in sessions:
        if s.get("title"):
            sanitized = _sanitize_title(s["title"])
            if sanitized != s["title"]:
                s["title"] = sanitized

    # 按最后消息时间倒序排序
    sessions.sort(key=lambda x: x.get("last_message_at", 0), reverse=True)

    total = len(sessions)
    return sessions[offset: offset + limit], total
