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
_METADATA_QUEUE: queue.Queue[tuple[str, dict[str, Any], bool]] = queue.Queue(maxsize=5000)
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


def _write_metadata_sync(
    session_id: str,
    metadata: dict[str, Any],
    preserve_pin_fields: bool = False,
) -> dict[str, Any]:
    """同步写入会话元数据(由后台 worker 或 fallback 调用)

    注意: 不更新 _METADATA_CACHE。缓存仅由 _enqueue_write 维护,
    避免 gateway 进程的 init_session_metadata 污染缓存导致后续
    读取不到 agentserver 进程写入的最新数据。
    """
    fpath = _metadata_file(session_id)
    to_write = metadata
    with _FILE_LOCK:
        if preserve_pin_fields and fpath.exists():
            try:
                current = json.loads(fpath.read_text(encoding="utf-8") or "{}")
                if isinstance(current, dict):
                    to_write = _merge_pin_fields(current, metadata)
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取 metadata.json 置顶字段失败: %s", exc)
        fpath.write_text(
            json.dumps(to_write, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return to_write


def _merge_pin_fields(current: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    merged = metadata.copy()
    if "pinned" in current:
        merged["pinned"] = bool(current.get("pinned"))
    if "pin_order" in current:
        merged["pin_order"] = int(current.get("pin_order") or 0)
    return merged


def _merge_pin_fields_from_disk(session_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Preserve latest disk pin state for async writes that do not own pin fields."""
    fpath = get_agent_sessions_dir() / session_id / "metadata.json"
    if not fpath.exists():
        return metadata
    try:
        with _FILE_LOCK:
            current = json.loads(fpath.read_text(encoding="utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 metadata.json 置顶字段失败: %s", exc)
        return metadata
    if not isinstance(current, dict):
        return metadata

    return _merge_pin_fields(current, metadata)


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                sid, metadata, preserve_pin_fields = _METADATA_QUEUE.get()
                try:
                    written = _write_metadata_sync(
                        sid,
                        metadata,
                        preserve_pin_fields=preserve_pin_fields,
                    )
                    if preserve_pin_fields:
                        with _CACHE_LOCK:
                            _METADATA_CACHE[sid] = written.copy()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("metadata 异步写入失败: %s", exc)
                finally:
                    _METADATA_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-metadata-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def _enqueue_write(
    session_id: str,
    metadata: dict[str, Any],
    sync_write: bool = False,
    preserve_pin_fields: bool = False,
) -> None:
    """将写入操作放入异步队列,队列满时退化为同步写。

    ``sync_write=True`` 时跳过异步队列,在更新缓存后直接同步落盘。
    用于跨进程敏感写入(如 ``set_session_pinned``):返回前必须落盘,
    否则只读磁盘的另一进程(AgentServer)会读到陈旧数据。

    注意: ``_write_metadata_sync`` 本身不更新缓存,缓存更新统一在此函数
    顶部完成,与异步路径行为一致,避免 ``init_session_metadata`` 污染缓存。
    """
    # 立即更新缓存,确保后续读取能看到最新状态
    if preserve_pin_fields:
        metadata = _merge_pin_fields_from_disk(session_id, metadata)
    with _CACHE_LOCK:
        _METADATA_CACHE[session_id] = metadata.copy()
    if sync_write:
        written = _write_metadata_sync(
            session_id,
            metadata,
            preserve_pin_fields=preserve_pin_fields,
        )
        if preserve_pin_fields:
            with _CACHE_LOCK:
                _METADATA_CACHE[session_id] = written.copy()
        return
    _ensure_worker_started()
    try:
        _METADATA_QUEUE.put_nowait((session_id, metadata, preserve_pin_fields))
    except queue.Full:
        if preserve_pin_fields:
            metadata = _merge_pin_fields_from_disk(session_id, metadata)
            with _CACHE_LOCK:
                _METADATA_CACHE[session_id] = metadata.copy()
        written = _write_metadata_sync(
            session_id,
            metadata,
            preserve_pin_fields=preserve_pin_fields,
        )
        if preserve_pin_fields:
            with _CACHE_LOCK:
                _METADATA_CACHE[session_id] = written.copy()


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
    project_dir: str = "",
    project_id: str = "",
    model: str = "",
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
        "project_dir": project_dir,
        "project_id": project_id,
        "model": model,
        "last_user_message_at": _current_timestamp(),
        "pinned": False,
        "pin_order": 0,
        "status": "idle",
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
    project_dir: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    last_user_message_at: float | None = None,
    pinned: bool | None = None,
    pin_order: int | None = None,
    touch_last_message_at: bool = True,
    cache_bust: bool = False,
    sync_write: bool = False,
) -> None:
    """更新会话元数据(异步写入,不阻塞调用方)

    title 语义(保持历史防御契约)：
      - title=None  → 不修改（默认）
      - title="x"   → 设置为 "x"
      - title=""    → 忽略（防御意外空值覆盖已有标题）
      - 若需显式清除标题，请设置 clear_title=True

    pinned / pin_order 语义：覆盖式，由 session.pin handler 传入；
    未传(None)时不修改。紧凑重编号由 handler 层统一完成。

    touch_last_message_at：是否刷新 ``last_message_at`` 为当前时刻。默认 ``True``
    (消息追加等场景)。纯状态更新(如 ``set_session_pinned`` 的置顶/重编号)应传
    ``False``,避免腐蚀最后消息时间,破坏 session.list 排序与前端展示。

    cache_bust：是否强制读盘(跳过内存缓存)。默认 ``False``。
    跨进程同步场景(如 ``set_session_pinned`` 的重编号)应传 ``True``,
    避免 Gateway 缓存中的陈旧数据覆盖 AgentServer 的并发更新。

    sync_write：是否在返回前同步落盘。默认 ``False``(走异步队列)。
    跨进程敏感写入(如 ``set_session_pinned`` 的置顶/取消置顶/重编号)应传
    ``True``:返回成功前落盘,否则只读磁盘的另一进程(AgentServer)在窗口期
    内会读到陈旧数据,后续整份 metadata 回写会覆盖刚写入的 ``pinned`` 状态。
    """
    metadata = _read_metadata(session_id, cache_bust=cache_bust)

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
            "project_dir": project_dir or "",
            "project_id": project_id or "",
            "model": model or "",
            "last_user_message_at": last_user_message_at if last_user_message_at is not None else _current_timestamp(),
            "pinned": bool(pinned),
            "pin_order": pin_order if pin_order is not None else 0,
            "status": "idle",
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
        # model：覆盖式——每次请求更新为本次模型
        if model is not None:
            metadata["model"] = model
        # last_user_message_at：覆盖式——仅在用户消息时由调用方传入
        if last_user_message_at is not None:
            metadata["last_user_message_at"] = last_user_message_at
        # pinned / pin_order：覆盖式——由 session.pin handler 传入
        if pinned is not None:
            metadata["pinned"] = bool(pinned)
        if pin_order is not None:
            metadata["pin_order"] = int(pin_order)
        # project_dir：首次锁定——仅当当前值为空时写入，后续不覆盖
        if project_dir and not metadata.get("project_dir"):
            metadata["project_dir"] = project_dir
        # project_id：首次锁定——仅当当前值为空时写入，后续不覆盖
        if project_id and not metadata.get("project_id"):
            metadata["project_id"] = project_id
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

        # 更新最后消息时间(可由 touch_last_message_at=False 关闭,供置顶重编号等
        # 非消息操作复用本函数而不腐蚀 last_message_at 语义)
        if touch_last_message_at:
            metadata["last_message_at"] = _current_timestamp()

    _enqueue_write(
        session_id,
        metadata,
        sync_write=sync_write,
        preserve_pin_fields=pinned is None and pin_order is None,
    )


def sync_session_request_metadata(
    *,
    session_id: str,
    channel_id: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    project_dir: str | None = None,
    project_id: str | None = None,
    last_user_message_at: float | None = None,
) -> str | None:
    """校验请求带来的参数与磁盘 metadata.json 是否需要更新，并按字段语义写入。

    本接口是「请求级参数 → 会话级元数据」的统一校验/同步入口，职责是：
    对比本次请求携带的参数与磁盘已持久化的 metadata，按各字段语义决定写不写。
    不负责参数来源解析（那由渠道层 ``resolve_request_project_dir`` 等纯解析函数完成）。

    字段语义：
      - project_dir：**首次锁定，不可改**。磁盘为空则写入请求值（首次锁定）；
        磁盘已有值且与请求值不一致 → 记 warning（说明会话被换项目目录了，有问题），**不覆盖**。
      - model：**覆盖式**，每次请求刷新为本次模型。
      - last_user_message_at：**覆盖式**，调用方传入则刷新。
      - mode：**覆盖式**（与 append_history_record 联动一致，重复无副作用）。

    Args:
        session_id: 会话 ID（空则直接返回 None，不做任何操作）
        channel_id / mode / model / last_user_message_at: 请求级参数，按上述语义写入
        project_dir: 请求携带的项目目录候选值，用于首次锁定

    Returns:
        本会话**生效**的 project_dir：磁盘已锁定则返回锁定值，否则返回请求候选值
        （首次锁定后即为该值）；无 session_id 或无候选值时返回 None。

    读盘策略：始终 ``cache_bust=True`` 强制读磁盘。本接口由 AgentServer 进程
    调用,而 ``pinned``/``pin_order`` 由 Gateway 进程写入;AgentServer 的内存
    缓存可能陈旧(上一轮聊天留下的 ``pinned=False``)。若用缓存值整份回写,
    会覆盖 Gateway 刚落盘的置顶状态。强制读盘确保本进程只保留磁盘最新值,
    不主动改 ``pinned``/``pin_order``(仅写请求级字段)。
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return None

    metadata = _read_metadata(session_id, cache_bust=True)
    effective_project_dir: str | None = None

    if not metadata:
        # 会话元数据不存在：兜底新建（外部渠道隐式创建 session 的场景）
        now = _current_timestamp()
        metadata = {
            "session_id": session_id,
            "channel_id": channel_id or "",
            "user_id": "",
            "created_at": now,
            "last_message_at": now,
            "title": "",
            "message_count": 0,
            "mode": mode if mode is not None else "unknown",
            "team_name": "",
            "round_id": 0,
            "project_dir": project_dir or "",
            "project_id": project_id or "",
            "model": model or "",
            "last_user_message_at": last_user_message_at if last_user_message_at is not None else now,
            "pinned": False,
            "pin_order": 0,
            "status": "idle",
        }
        effective_project_dir = project_dir or None
    else:
        # 校验 project_dir：首次锁定 / 不一致告警不覆盖
        locked_project = metadata.get("project_dir")
        if isinstance(locked_project, str) and locked_project.strip():
            # 已锁定：以磁盘值为准
            effective_project_dir = locked_project.strip()
            # 请求带了不同值 → 告警（会话被换项目目录，有问题），但不覆盖
            if project_dir and project_dir.strip() and project_dir.strip() != effective_project_dir:
                logger.warning(
                    "会话 %s 的 project_dir 已锁定为 %s，忽略请求带来的不一致值 %s（锁定不可改）",
                    session_id, effective_project_dir, project_dir.strip(),
                )
        elif project_dir and project_dir.strip():
            # 未锁定且请求带了值 → 首次锁定写入
            metadata["project_dir"] = project_dir.strip()
            effective_project_dir = project_dir.strip()

        # project_id：首次锁定，已锁定则忽略请求值（与 project_dir 一致，不可改）
        if project_id and not (isinstance(metadata.get("project_id"), str) and metadata.get("project_id", "").strip()):
            metadata["project_id"] = project_id

        # model：覆盖式
        if model is not None:
            metadata["model"] = model
        # last_user_message_at：覆盖式
        if last_user_message_at is not None:
            metadata["last_user_message_at"] = last_user_message_at
        # mode：覆盖式
        if mode is not None:
            metadata["mode"] = mode
        if channel_id is not None:
            metadata["channel_id"] = channel_id
        metadata["last_message_at"] = _current_timestamp()

    _enqueue_write(session_id, metadata, preserve_pin_fields=True)
    return effective_project_dir


def get_session_metadata(session_id: str, cache_bust: bool = False) -> dict[str, Any]:
    """获取会话元数据

    Args:
        session_id: 会话 ID
        cache_bust: 强制跳过缓存，直接从磁盘读取（用于跨进程同步场景）
    """
    metadata = _read_metadata(session_id, cache_bust)
    if isinstance(metadata, dict) and metadata:
        # 清理已有会话中可能被误写入的系统注入标签标题（<system-reminder>、<file-content> 等）
        if metadata.get("title"):
            sanitized = _sanitize_title(metadata["title"])
            if sanitized != metadata["title"]:
                metadata["title"] = sanitized
        # 兜底：存量会话补默认值，前端永远能拿到稳定 schema
        metadata.setdefault("project_dir", "")
        metadata.setdefault("project_id", "")
        metadata.setdefault("model", "")
        metadata.setdefault("last_user_message_at", metadata.get("created_at", 0.0))
        metadata.setdefault("pinned", False)
        metadata.setdefault("pin_order", 0)
        metadata.setdefault("status", "idle")
    return metadata


# 会话级 pin 重编号全局序列化锁:保障「设置目标 → 收集所有置顶 → 重编号 → 写回」全过程原子性。
# Gateway 为会话级 pin 的唯一写入方(仅经 Web 本地 handler 处理,不转发 AgentServer)。
_SESSION_PIN_LOCK = threading.Lock()


def set_session_pinned(session_id: str, pinned: bool) -> tuple[bool, int] | None:
    """置顶/取消置顶会话,并对所有置顶会话紧凑重编号为 1..N。幂等。

    整个操作在进程内全局锁内完成:
      1. 设置目标会话 ``pinned``(取消时同步清零 ``pin_order``);
      2. 扫描全部会话,收集 ``pinned=True`` 的会话;
      3. 按 ``pin_order`` 升序稳定排序,重新分配 1..N(消除间隙);
      4. 逐个写回。

    新置顶的会话 ``pin_order`` 默认为 0,排序后置于最前(即新置顶会显示在置顶区顶部)。
    非置顶会话 ``pin_order`` 置 0。幂等:对已处于目标状态的会话再次操作视为成功。

    所有写入均以 ``touch_last_message_at=False`` 调用 ``update_session_metadata``:
    置顶不是消息,不应刷新 ``last_message_at``(否则会腐蚀 ``session.list`` 排序与
    ``SessionInfo`` 展示的「最后消息时间」语义)。

    Args:
        session_id: 目标会话 ID
        pinned: ``True``=置顶,``False``=取消置顶

    Returns:
        ``(操作后的 pinned, 操作后的 pin_order)``;会话不存在(metadata 缺失)时返回 ``None``。
        取消置顶时 ``pin_order`` 恒为 0。
    """
    with _SESSION_PIN_LOCK:
        meta = _read_metadata(session_id, cache_bust=True)
        if not meta:
            return None
        # 1. 设置目标会话 pinned 状态(保留原 pin_order 供重编号排序,取消时清零)
        #    全部 sync_write=True:跨进程敏感写入,返回前必须落盘,否则只读磁盘的
        #    AgentServer 在窗口期内读到旧值,后续整份 metadata 回写会覆盖 pinned 状态。
        if pinned:
            update_session_metadata(
                session_id=session_id, pinned=True,
                touch_last_message_at=False, cache_bust=True, sync_write=True,
            )
        else:
            update_session_metadata(
                session_id=session_id, pinned=False, pin_order=0,
                touch_last_message_at=False, cache_bust=True, sync_write=True,
            )

        # 2. 收集所有置顶会话(读缓存:步骤 1 刚把新状态写入缓存,cache_bust=False
        #    能立即看到;且 pinned/pin_order 仅由 Gateway 进程写入,缓存即权威源。
        #    若用 cache_bust=True 读盘,异步写入未落盘时会读到步骤 1 之前的旧状态,
        #    导致取消置顶的会话被重新纳入重编号而又写回 pinned=True。)
        sessions_dir = get_agent_sessions_dir()
        pinned_list: list[tuple[str, int]] = []
        if sessions_dir.is_dir():
            for session_dir in sessions_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                sid = session_dir.name
                if sid.startswith(_HEARTBEAT_SESSION_PREFIX):
                    continue
                m = _read_metadata(sid)
                if not m:
                    continue
                if m.get("pinned"):
                    pinned_list.append((sid, int(m.get("pin_order", 0))))

        # 3. 升序排序 + 4. 紧凑重编号写回(force disk read 避免回滚覆盖)
        pinned_list.sort(key=lambda x: x[1])
        new_orders: dict[str, int] = {}
        for idx, (sid, _old) in enumerate(pinned_list, start=1):
            update_session_metadata(
                session_id=sid, pinned=True, pin_order=idx,
                touch_last_message_at=False, cache_bust=True, sync_write=True,
            )
            new_orders[sid] = idx

        return pinned, new_orders.get(session_id, 0)


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
    _enqueue_write(session_id, metadata, preserve_pin_fields=True)
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
            "project_dir": "",
            "project_id": "",
            "model": "",
            "last_user_message_at": _current_timestamp(),
            "pinned": False,
            "pin_order": 0,
            "status": "idle",
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
    _enqueue_write(session_id, metadata, preserve_pin_fields=True)
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


def migrate_legacy_session_metadata_at_startup() -> None:
    """AgentServer 启动时给老会话的 metadata.json 补全新字段并写回磁盘。

    升级后新增了 project_dir / model / last_user_message_at / status 四个字段，
    老会话的 metadata.json 缺这些字段。本函数在启动时遍历所有会话目录，
    按字段语义补默认值并落盘，保证磁盘上 schema 统一、前端永远拿到稳定结构。

    各字段兜底值来源：
      - project_id：优先按 project_dir 从 project_store 解析;无法匹配则 ""
      - project_dir / model / status：常量默认（""/""/"idle"），老会话本就没存过
      - last_user_message_at：从已有时间字段推算 ——
        last_message_at（agent 最后输出时间）→ created_at（创建时间）→ 目录 mtime
        不能给常量 0.0，否则老会话排序/时间显示错乱
    """
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.is_dir():
        return

    # 构建 project_dir → project_id 映射,用于将存量会话的 project_dir 解析为 project_id
    try:
        from jiuwenswarm.server.runtime.session.project_store import list_projects
        dir_to_id: dict[str, str] = {
            p.project_dir: p.project_id
            for p in list_projects(include_hidden=True, cache_bust=True)
            if p.project_dir
        }
    except Exception:
        dir_to_id = {}

    migrated = 0
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        if session_dir.name.startswith(_HEARTBEAT_SESSION_PREFIX):
            continue
        meta_path = session_dir / "metadata.json"
        if not meta_path.is_file():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as exc:
            # OSError：并发删除/权限致 read_text 失败；ValueError：JSONDecodeError/UnicodeDecodeError
            logger.warning("启动迁移跳过会话 %s: 读取 metadata.json 失败: %s", session_dir.name, exc)
            continue
        if not isinstance(raw, dict):
            continue

        changed = False
        # project_dir / model：常量默认
        if "project_dir" not in raw:
            raw["project_dir"] = ""
            changed = True
        # project_id: 补默认值;若为空且有 project_dir,按路径解析到实际 project_id
        if not str(raw.get("project_id") or "").strip():
            pp = str(raw.get("project_dir") or "")
            resolved = dir_to_id.get(pp, "") if pp else ""
            if raw.get("project_id") != resolved:
                raw["project_id"] = resolved
                changed = True
        if "model" not in raw:
            raw["model"] = ""
            changed = True
        if "status" not in raw:
            raw["status"] = "idle"
            changed = True
        # last_user_message_at：从已有时间字段推算，保证语义合理
        if "last_user_message_at" not in raw:
            # 优先用已有时间字段；不能用 ``or`` 短路——合法的 0.0 时间戳是 falsy
            # 会被跳过。显式 None 判定后回退到目录 mtime（OSError 时 0.0 兜底）。
            fallback = raw.get("last_message_at")
            if fallback is None:
                fallback = raw.get("created_at")
            if fallback is None:
                fallback = session_dir.stat().st_mtime
            try:
                raw["last_user_message_at"] = float(fallback) if fallback is not None else 0.0
            except (TypeError, ValueError):
                raw["last_user_message_at"] = session_dir.stat().st_mtime
            changed = True

        if changed:
            try:
                with _FILE_LOCK:
                    meta_path.write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                # 同步更新内存缓存，避免读到旧值
                with _CACHE_LOCK:
                    _METADATA_CACHE[session_dir.name] = raw.copy()
                migrated += 1
            except (OSError, ValueError, TypeError) as exc:
                # OSError：写盘失败；ValueError/TypeError：json.dumps 序列化失败
                logger.warning("启动迁移写回会话 %s 失败: %s", session_dir.name, exc)

    if migrated:
        logger.info("启动迁移: 已补全 %d 个老会话的 metadata 字段", migrated)


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
                "project_id": "",
                "project_dir": "",
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


def collect_all_sessions_metadata() -> list[dict[str, Any]]:
    """收集全部会话元数据(不分页、不排序),供项目统计与置顶会话聚合使用。

    跳过 heartbeat 会话;强制读盘(``cache_bust=True``)以跨进程拿最新数据。
    无 ``metadata.json`` 的旧会话以目录时间戳构造最小兜底信息
    (``project_id=""``、``project_dir=""``、``pinned=False``),归入默认项目统计。
    返回的每个 dict 已对新增字段应用默认值兜底。
    """
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        if sid.startswith(_HEARTBEAT_SESSION_PREFIX):
            continue
        meta = _read_metadata(sid, cache_bust=True)
        if not meta:
            # 旧会话无 metadata.json: 构造最小兜底,归入默认项目
            try:
                st = session_dir.stat()
            except OSError:
                continue
            meta = {
                "session_id": sid,
                "project_id": "",
                "project_dir": "",
                "pinned": False,
                "pin_order": 0,
                "last_message_at": st.st_mtime,
                # 与 get_session_metadata / 同函数 else 分支一致: 无用户消息时
                # 回退到 created_at(保证排序稳定性,避免空会话全部沉底)
                "last_user_message_at": st.st_ctime,
                "created_at": st.st_ctime,
            }
        else:
            # 兜底默认值,保证新增字段齐全(存量会话无需迁移)
            meta.setdefault("project_id", "")
            meta.setdefault("project_dir", "")
            meta.setdefault("pinned", False)
            meta.setdefault("pin_order", 0)
            meta.setdefault("last_user_message_at", meta.get("created_at", 0.0))
        result.append(meta)

    # 对齐 get_all_sessions_metadata: 清理存量会话标题中残留的系统注入 XML 标签
    # (如 <system-reminder>、<file-content>),避免通过项目接口返给前端
    for s in result:
        if s.get("title"):
            sanitized = _sanitize_title(s["title"])
            if sanitized != s["title"]:
                s["title"] = sanitized
    return result
