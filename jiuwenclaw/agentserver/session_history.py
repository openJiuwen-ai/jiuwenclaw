from __future__ import annotations

import atexit
import datetime
import logging
import json
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Tuple

from jiuwenclaw.utils import get_agent_sessions_dir


logger = logging.getLogger(__name__)
_FILE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[tuple[str, dict[str, Any], str | None]] = queue.Queue(maxsize=20000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()

# 缓冲层分两组：普通缓冲层（_session_buffer，合并后批量写）与暂留层（_session_pending，
# tool_calls.delta 等对应 chat.tool_call 命中后再决定丢弃/落盘）。
BUFFERABLE_EVENT_TYPES = {
    "chat.delta",
    "chat.reasoning",
    "chat.tool_update",
    "chat.tool_calls.delta",
}
NORMAL_BUFFER_EVENT_TYPES = BUFFERABLE_EVENT_TYPES - {"chat.tool_calls.delta"}
PENDING_EVENT_TYPE = "chat.tool_calls.delta"
BUFFER_FLUSH_INTERVAL = 5.0      # 普通缓冲定时刷新间隔（秒，兜底）
BUFFER_MAX_SIZE = 100            # 合并上限：delta_count / per-call 条目数
PENDING_MAX_SECONDS = 2.0        # 暂留绝对超时（秒，兜底）

# ── 契约 ────────────────────────────────────────────────────
# append_history_record 必须在 event-loop 线程内调用（同步函数，无 await）。
# 协程切换只在 await 发生，故 _route_event 内多段临界区之间无插入窗口，
# 步骤1检查与步骤2创建无 TOCTOU。_buffer_lock 的真正职责是抵御定时刷新线程
# 与 event-loop 线程之间的线程级并发。
# ⚠ 一旦出现从工作线程直接调用 append_history_record 的路径，上述前提破坏，
# TOCTOU 窗口将真实打开，届时须改 recheck 或合并锁内操作。
_buffer_lock = threading.Lock()
# 普通缓冲层：每 session 一条当前合并记录 + 类型 + request_id + root
_session_buffer: dict[str, dict[str, Any]] = {}
_session_buffer_type: dict[str, str] = {}
_session_buffer_request_id: dict[str, str] = {}
_session_buffer_root: dict[str, str | None] = {}
# tool_update 的 per-call_id 合并缓冲（_session_buffer_type 为 chat.tool_update 时生效）
_session_tool_update_buffer: dict[str, "OrderedDict[str, dict[str, Any]]"] = {}
_session_tool_update_root: dict[str, str | None] = {}
# 暂留层：每 session 最多一个 tool_calls.delta 暂留条目
_session_pending: dict[str, "_PendingState"] = {}
_pending_raw_counter: int = 0  # 非缓冲事件进 pending_queue 的唯一键计数器
_FLUSH_THREAD_STARTED = False
_FLUSH_THREAD_LOCK = threading.Lock()
_flush_stop_event = threading.Event()  # 测试可控启停；生产由 atexit/shutdown() 收尾


def _serialize_value(obj: Any) -> Any:
    """递归把 datetime/date 转为 ISO 字符串，其余原样返回（JSON 序列化用）。"""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_value(item) for item in obj]
    return obj


# ════════════════════════════════════════════════════════
# 缓冲层：_PendingState 与合并函数
# ════════════════════════════════════════════════════════

@dataclass
class _PendingState:
    """tool_calls.delta 暂留状态。

    暂留期间整个 session 不落盘（其它事件进 pending_queue 候着），等对应的
    chat.tool_call 命中丢弃（条件 A）或超时落盘（条件 B）。
    """
    item: dict[str, Any]
    request_id: str
    pending_queue: "OrderedDict[Tuple[str, str], dict[str, Any]]" = field(default_factory=OrderedDict)
    start_time: float = 0.0          # 暂留起点（time.monotonic，超时判定）
    sessions_root: str | None = None  # 落盘根目录（超时时由定时线程使用）


def _is_empty_value(v: Any) -> bool:
    """None / 空串 / 空集合为"空"。注意数值 0、False 不算空（避免误判）。"""
    if v is None:
        return True
    if isinstance(v, str) and v == "":
        return True
    if isinstance(v, (list, dict, tuple, set)) and len(v) == 0:
        return True
    return False


def _merge_delta_events(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    merged["content"] = existing.get("content", "") + new.get("content", "")
    merged.setdefault("start_ts", existing.get("timestamp"))
    merged["timestamp"] = new.get("timestamp", merged.get("timestamp"))
    merged["delta_count"] = existing.get("delta_count", 1) + 1
    if _is_empty_value(merged.get("source_chunk_type")) and not _is_empty_value(new.get("source_chunk_type")):
        merged["source_chunk_type"] = new["source_chunk_type"]
    return merged


def _merge_reasoning_events(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    merged["content"] = existing.get("content", "") + new.get("content", "")
    merged.setdefault("start_ts", existing.get("timestamp"))
    merged["timestamp"] = new.get("timestamp", merged.get("timestamp"))
    merged["delta_count"] = existing.get("delta_count", 1) + 1
    return merged


def _merge_tool_update_events(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    if "status" in new:
        merged["status"] = new["status"]
    if "arguments" in new:
        merged["arguments"] = new["arguments"]  # 覆盖而非拼接（与 delta 累加不同）
    for k in ("tool_name", "tool_call_id"):
        if _is_empty_value(merged.get(k)) and not _is_empty_value(new.get(k)):
            merged[k] = new[k]
    merged.setdefault("start_ts", existing.get("timestamp"))
    merged["timestamp"] = new.get("timestamp", merged.get("timestamp"))
    merged["delta_count"] = existing.get("delta_count", 1) + 1
    return merged


def _get_tool_call_key(call: dict) -> tuple[str, int]:
    call_id = call.get("id", "") or call.get("tool_call_id", "") or ""
    index = call.get("index", 0)
    return (call_id, index)


def _merge_tool_call(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    merged["arguments"] = existing.get("arguments", "") + new.get("arguments", "")
    for k in ("id", "tool_call_id", "name", "type"):
        if _is_empty_value(merged.get(k)) and not _is_empty_value(new.get(k)):
            merged[k] = new[k]
    if "index" not in merged and "index" in new:
        merged["index"] = new["index"]
    return merged


def _merge_tool_calls_delta_events(existing: dict, new: dict) -> dict:
    merged = dict(existing)
    calls_by_key: dict[tuple, dict] = {}  # 已有 calls 按 (call_id, index) 索引
    by_index: dict[int, tuple] = {}       # index → 对应键（仅记首个有 id 的，空 id 匹配用）
    for call in existing.get("tool_calls", []):
        key = _get_tool_call_key(call)
        calls_by_key[key] = call
        idx = call.get("index", 0)
        if idx not in by_index:
            by_index[idx] = key

    for call in new.get("tool_calls", []):
        key = _get_tool_call_key(call)
        call_id = call.get("id", "") or call.get("tool_call_id", "") or ""
        if key in calls_by_key:
            calls_by_key[key] = _merge_tool_call(calls_by_key[key], call)
        elif not call_id:
            idx = call.get("index", 0)
            matched_key = by_index.get(idx)
            if matched_key is not None and matched_key in calls_by_key:
                calls_by_key[matched_key] = _merge_tool_call(calls_by_key[matched_key], call)
            else:
                calls_by_key[key] = dict(call)
                by_index.setdefault(idx, key)
        else:
            calls_by_key[key] = dict(call)
            by_index.setdefault(call.get("index", 0), key)

    merged["tool_calls"] = list(calls_by_key.values())
    merged.setdefault("start_ts", existing.get("timestamp"))
    merged["timestamp"] = new.get("timestamp", merged.get("timestamp"))
    merged["delta_count"] = existing.get("delta_count", 1) + 1
    return merged


_MERGE = {
    "chat.delta": _merge_delta_events,
    "chat.reasoning": _merge_reasoning_events,
    "chat.tool_update": _merge_tool_update_events,
}


# ════════════════════════════════════════════════════════
# 缓冲层：批量写与普通缓冲层操作
# ════════════════════════════════════════════════════════

def _history_file(session_id: str, sessions_root: str | None = None) -> Path:
    root = Path(sessions_root) if sessions_root else get_agent_sessions_dir()
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "history.json"


def _parse_history_text(raw_text: str) -> list[dict[str, Any]] | None:
    if not raw_text.strip():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        entry = line.strip()
        if not entry:
            continue
        try:
            parsed = json.loads(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 history.json JSONL 第%d 行失败: %s", line_no, exc)
            return None
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def read_history_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        parsed = _parse_history_text(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 history.json 失败，已忽略并重建: %s", exc)
        return []
    if parsed is None:
        logger.warning("读取 history.json 失败，已忽略并重建")
        return []
    return parsed


def read_history_records_for_frontend(path: Path) -> list[dict[str, Any]]:
    """读取并过滤被撤回的 delta/reasoning（用于前端历史恢复）。
    
    过滤规则：
    - 找出所有 chat.retract 的 request_id
    - 过滤掉相同 request_id 的 chat.delta、chat.reasoning、chat.tool_call、chat.tool_result
    - 保留其他所有事件（包括 chat.retract 本身）
    """
    records = read_history_records(path)
    
    revoked_rids = {
        r.get("request_id")
        for r in records
        if r.get("event_type") == "chat.retract" and r.get("request_id") is not None
    }
    
    filtered_event_types = (
        "chat.delta",
        "chat.reasoning",
        "chat.tool_call",
        "chat.tool_result",
    )
    result: list[dict[str, Any]] = []
    for r in records:
        rid = r.get("request_id")
        event_type = r.get("event_type")
        if rid not in revoked_rids:
            result.append(r)
        elif event_type not in filtered_event_types:
            result.append(r)
    return result


def enrich_history_messages_session_id(
    messages: Iterable[dict[str, Any]],
    resolved_session_id: str,
) -> list[dict[str, Any]]:
    """为缺少 session_id 的历史记录做浅拷贝补全（兼容旧数据）。"""
    sid = resolved_session_id.strip()
    out: list[dict[str, Any]] = []
    for m in messages:
        if "session_id" not in m:
            out.append({**m, "session_id": sid})
        else:
            out.append(m)
    return out


def _write_item(session_id: str, item: dict[str, Any], sessions_root: str | None = None) -> None:
    fpath = _history_file(session_id, sessions_root)
    with _FILE_LOCK:
        with fpath.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(item, ensure_ascii=False))
            fh.write("\n")


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                sid, item, sessions_root = _WRITE_QUEUE.get()
                try:
                    _write_item(sid, item, sessions_root)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("history 异步写入失败: %s", exc)
                finally:
                    _WRITE_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-history-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


# ════════════════════════════════════════════════════════
# 缓冲层：批量写
# ════════════════════════════════════════════════════════

def _batch_write_items(session_id: str, items: list[dict], sessions_root: str | None) -> None:
    """批量写入 history.json（一次 open 写多行）。_FILE_LOCK 串行化磁盘写。"""
    if not items:
        return
    fpath = _history_file(session_id, sessions_root)
    lines = [json.dumps(item, ensure_ascii=False) for item in items]
    with _FILE_LOCK:
        with fpath.open("a", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write(line)
                fh.write("\n")


# ════════════════════════════════════════════════════════
# 缓冲层：普通缓冲层操作
# ════════════════════════════════════════════════════════

def _flush_buffer_unlocked(session_id: str) -> tuple[list[dict], str | None]:
    """落盘并清空普通缓冲层（调用方已持锁）。返回 (items, recorded_root)，
    调用方锁外执行 IO。tool_update 走 per-call 缓冲可返回多条。"""
    # tool_update per-call 缓冲优先（此时 _session_buffer 该 sid 为空）
    per_call = _session_tool_update_buffer.pop(session_id, None)
    if per_call is not None:
        _session_buffer_type.pop(session_id, None)
        _session_buffer_request_id.pop(session_id, None)
        _session_buffer_root.pop(session_id, None)
        recorded_root = _session_tool_update_root.pop(session_id, None)
        return list(per_call.values()), recorded_root
    item = _session_buffer.pop(session_id, None)
    if item is None:
        return [], None
    _session_buffer_type.pop(session_id, None)
    _session_buffer_request_id.pop(session_id, None)
    recorded_root = _session_buffer_root.pop(session_id, None)
    return [item], recorded_root


def _flush_buffer(session_id: str, sessions_root: str | None) -> None:
    """落盘并清空普通缓冲层。sessions_root 为 None 时回退到缓冲时记录的 root
    （定时刷新线程无 root 上下文时用）。"""
    with _buffer_lock:
        items, recorded_root = _flush_buffer_unlocked(session_id)
    if items:
        root = sessions_root if sessions_root is not None else recorded_root
        _batch_write_items(session_id, items, root)


def _flush_on_request_switch(session_id: str, request_id: str, sessions_root: str | None) -> None:
    """新 request_id 到达：落盘旧请求的缓冲 + 激活的暂留层（按条件 B）。

    暂留激活时普通缓冲层为空，旧 request_id 须同时看暂留层。
    buf_items / pending 均是旧请求数据，各自用其缓冲时记录的 root 落盘
    （sessions_root 是新请求 root，不能覆盖旧记录落盘目录）。
    """
    with _buffer_lock:
        current_rid = _session_buffer_request_id.get(session_id, "")
        pending = _session_pending.get(session_id)
        pending_rid = pending.request_id if pending is not None else ""
        old_rid = current_rid or pending_rid
        switched = bool(request_id and old_rid and request_id != old_rid)
        if switched:
            buf_items, buf_root = _flush_buffer_unlocked(session_id)
            _session_pending.pop(session_id, None)
        else:
            buf_items = []
            pending = None
    if buf_items:
        _batch_write_items(session_id, buf_items, buf_root)
    if pending is not None:
        pending_items = [pending.item] + list(pending.pending_queue.values())
        _batch_write_items(session_id, pending_items, pending.sessions_root)


def _flush_on_type_switch_unlocked(session_id: str, event_type: str, request_id: str) -> tuple[list[dict], str | None]:
    """类型/请求切换判定（调用方已持锁）。返回 (待落盘 items, recorded_root)。"""
    current_type = _session_buffer_type.get(session_id)
    current_rid = _session_buffer_request_id.get(session_id, "")
    type_switch = current_type is not None and current_type != event_type
    request_switch = request_id and current_rid and request_id != current_rid
    if type_switch or request_switch:
        return _flush_buffer_unlocked(session_id)
    return [], None


# ════════════════════════════════════════════════════════
# 缓冲层：tool_calls.delta 暂留机制
# ════════════════════════════════════════════════════════

def _extract_tool_call_id(item: dict) -> str:
    """从 chat.tool_call 提取 id（嵌套 item["tool_call"]["tool_call_id"]）。"""
    tc = item.get("tool_call") or {}
    if not isinstance(tc, dict):
        return ""
    return (tc.get("tool_call_id") or tc.get("id")
            or item.get("tool_call_id") or item.get("id") or "")


def _extract_pending_call_ids(pending_item: dict) -> set[str]:
    ids: set[str] = set()
    for call in pending_item.get("tool_calls", []):
        if not isinstance(call, dict):
            continue
        cid = call.get("id") or call.get("tool_call_id") or ""
        if cid:
            ids.add(cid)
    return ids


def _get_buffer_key(item: dict) -> Tuple[str, str]:
    return (item.get("request_id", ""), item.get("event_type", ""))


def _buffer_into(pending_queue: "OrderedDict[Tuple[str, str], dict]", item: dict, event_type: str) -> None:
    """合并事件进 pending_queue（同类型合并，非缓冲事件按到达顺序原样进）。"""
    global _pending_raw_counter
    if event_type in NORMAL_BUFFER_EVENT_TYPES:
        key = _get_buffer_key(item)
        pending_queue[key] = _MERGE[event_type](pending_queue[key], item) if key in pending_queue else dict(item)
    else:
        # 非缓冲事件原样进队列，自增计数器作唯一键保持顺序
        _pending_raw_counter += 1
        pending_queue[(item.get("request_id", ""), f"__raw_{_pending_raw_counter}")] = dict(item)


def _route_event(sid: str, item: dict, event_type: str, sessions_root_s: str | None) -> None:
    """事件分发：暂留层 / 普通缓冲层 / 非缓冲事件。

    每分支持 _buffer_lock 完成"判定+合并+取出落盘快照"，IO 在锁外执行，
    避免与定时刷新线程并发重复落盘。"""
    rid = item.get("request_id", "")

    # ── 1. 暂留激活：所有事件走暂留管理（保序不落盘）──
    flush_items: list[dict] | None = None
    flush_root: str | None = sessions_root_s
    with _buffer_lock:
        pending = _session_pending.get(sid)
        if pending is not None:
            if event_type == PENDING_EVENT_TYPE:
                pending.item = _merge_tool_calls_delta_events(pending.item, item)
                return
            if event_type == "chat.tool_call" and _extract_tool_call_id(item) in \
               _extract_pending_call_ids(pending.item):
                # 条件 A：目标 tool_call 命中 → 丢弃 delta，落盘积压事件 + tool_call
                _session_pending.pop(sid, None)
                flush_items = list(pending.pending_queue.values()) + [item]
                flush_root = pending.sessions_root
            else:
                _buffer_into(pending.pending_queue, item, event_type)
                return
    if flush_items is not None:
        _batch_write_items(sid, flush_items, flush_root)
        return

    # ── 2. 无暂留且为 tool_calls.delta：开启暂留（先 flush 普通缓冲保序）──
    if event_type == PENDING_EVENT_TYPE:
        with _buffer_lock:
            flush_items, _ = _flush_on_type_switch_unlocked(sid, event_type, rid)
            _session_pending[sid] = _PendingState(
                item=dict(item),
                request_id=rid,
                pending_queue=OrderedDict(),
                start_time=time.monotonic(),
                sessions_root=sessions_root_s,
            )
        if flush_items:
            _batch_write_items(sid, flush_items, sessions_root_s)
        return

    # ── 3. 普通缓冲：tool_update 走 per-call_id 分组，delta/reasoning 单条合并 ──
    if event_type in NORMAL_BUFFER_EVENT_TYPES:
        with _buffer_lock:
            switch_items, switch_root = _flush_on_type_switch_unlocked(sid, event_type, rid)
            if event_type == "chat.tool_update":
                # 不同 call_id 各成一条，同 call_id 内合并（status/arguments 取最新）
                call_id = (item.get("tool_call_id") or item.get("id") or "")
                per_call = _session_tool_update_buffer.setdefault(sid, OrderedDict())
                existing_call = per_call.get(call_id)
                per_call[call_id] = _merge_tool_update_events(existing_call, item) if existing_call else dict(item)
                _session_buffer_type[sid] = event_type
                _session_buffer_request_id[sid] = rid
                _session_buffer_root[sid] = sessions_root_s
                _session_tool_update_root[sid] = sessions_root_s
                cap_items, cap_root = ([], None)
                if len(per_call) >= BUFFER_MAX_SIZE:
                    cap_items, cap_root = _flush_buffer_unlocked(sid)
            else:
                existing = _session_buffer.get(sid)
                if existing is not None and _session_buffer_type.get(sid) == event_type:
                    merged = _MERGE[event_type](existing, item)
                else:
                    merged = dict(item)
                _session_buffer[sid] = merged
                _session_buffer_type[sid] = event_type
                _session_buffer_request_id[sid] = rid
                _session_buffer_root[sid] = sessions_root_s
                cap_items, cap_root = ([], None)
                if merged.get("delta_count", 1) >= BUFFER_MAX_SIZE:
                    cap_items, cap_root = _flush_buffer_unlocked(sid)
        # switch_items（切换前旧记录）用 switch_root、cap_items（达上限的当前记录）用 cap_root；
        # root 一致则合并一次 open，否则各落各文件（防多租户 root 混用落错文件）。
        if switch_items and cap_items and switch_root == cap_root:
            _batch_write_items(sid, switch_items + cap_items, switch_root if switch_root is not None else cap_root)
        else:
            if switch_items:
                _batch_write_items(sid, switch_items, switch_root)
            if cap_items:
                _batch_write_items(sid, cap_items, cap_root)
        return

    # ── 4. 非缓冲事件：先 flush 普通缓冲（类型切换），再与本事件批量写入 ──
    with _buffer_lock:
        switch_items, switch_root = _flush_on_type_switch_unlocked(sid, event_type, rid)
    # switch_items 是旧缓冲记录，按 recorded_root 落盘；本事件按传入 root。两者同属
    # 同一非 None root 才合并一次 open；否则各落各文件（防多租户 root 混用）。
    if not switch_items:
        _batch_write_items(sid, [item], sessions_root_s)
    elif switch_root is not None and sessions_root_s is not None and switch_root == sessions_root_s:
        _batch_write_items(sid, switch_items + [item], sessions_root_s)
    else:
        _batch_write_items(sid, switch_items, switch_root if switch_root is not None else sessions_root_s)
        _batch_write_items(sid, [item], sessions_root_s)


# ════════════════════════════════════════════════════════
# 缓冲层：定时刷新线程
# ════════════════════════════════════════════════════════

_FLUSH_THREAD: threading.Thread | None = None


def _ensure_flush_thread_started() -> None:
    """启动定时刷新线程：每 BUFFER_FLUSH_INTERVAL 秒刷新普通缓冲层 + 检查暂留超时。"""
    global _FLUSH_THREAD_STARTED, _FLUSH_THREAD
    if _FLUSH_THREAD_STARTED and _FLUSH_THREAD is not None and _FLUSH_THREAD.is_alive():
        return
    with _FLUSH_THREAD_LOCK:
        if _FLUSH_THREAD_STARTED and _FLUSH_THREAD is not None and _FLUSH_THREAD.is_alive():
            return

        def _flush_worker() -> None:
            while not _flush_stop_event.wait(BUFFER_FLUSH_INTERVAL):
                try:
                    _periodic_flush()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("history 定时刷新失败: %s", exc)

        t = threading.Thread(target=_flush_worker, name="session-history-flusher", daemon=True)
        t.start()
        _FLUSH_THREAD = t
        _FLUSH_THREAD_STARTED = True


def _force_flush_all_pending() -> None:
    """强制落盘所有剩余暂留（忽略超时），shutdown 收尾兜底。

    _periodic_flush 只落已超时的 pending；shutdown 时未超时的 pending（tool_call 未命中）
    的 item + pending_queue 会丢，此函数在 _periodic_flush 之后兜底全落盘。"""
    with _buffer_lock:
        remaining = list(_session_pending.items())
        _session_pending.clear()
    for sid, pending in remaining:
        try:
            _batch_write_items(sid, [pending.item] + list(pending.pending_queue.values()), pending.sessions_root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history shutdown 暂留落盘失败 sid=%s: %s", sid, exc)


_SHUTDOWN_DONE: bool = False  # shutdown 是否已完成（幂等防重入）


def shutdown() -> None:
    """进程退出前收尾：停定时线程 + flush 落盘（含强制落剩余暂留）+ 排空异步写队列。

    atexit 注册 + app_agentserver._run finally 显式调用可能叠加，故幂等：第二次直接返回。
    排空队列后再补一次 flush：捕获 shutdown 进行中由 event-loop 残留协程投递的缓冲事件。
    """
    global _FLUSH_THREAD_STARTED, _FLUSH_THREAD, _SHUTDOWN_DONE
    with _buffer_lock:
        if _SHUTDOWN_DONE:
            return
        _SHUTDOWN_DONE = True
    _flush_stop_event.set()
    # 等 flush 线程退出（最多 2s），避免与本函数 flush 并发。若本函数恰好跑在 flush
    # 线程上则跳过 self-join：_flush_stop_event 保持 set，worker 本轮结束即退出，不复活。
    if _FLUSH_THREAD is not None and _FLUSH_THREAD is not threading.current_thread():
        _FLUSH_THREAD.join(timeout=2.0)
    try:
        _periodic_flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("history shutdown flush 失败: %s", exc)
    try:
        _force_flush_all_pending()
    except Exception as exc:  # noqa: BLE001
        logger.warning("history shutdown 强制暂留落盘失败: %s", exc)
    # 排空异步写队列（带超时轮询，避免 atexit 卡在磁盘 IO）
    deadline = time.monotonic() + 5.0
    try:
        while _WRITE_QUEUE.unfinished_tasks > 0 and time.monotonic() < deadline:
            time.sleep(0.01)
    except Exception:  # noqa: BLE001
        pass
    # 末次复扫：捕获上面两次 flush 之后、进程退出之前由残留协程投递的缓冲事件
    try:
        _periodic_flush()
        _force_flush_all_pending()
    except Exception as exc:  # noqa: BLE001
        logger.warning("history shutdown 末次复扫失败: %s", exc)
    # 复位标志 + 线程句柄，允许后续重启（测试隔离、长生命周期进程重启历史写入）
    _flush_stop_event.clear()
    _FLUSH_THREAD_STARTED = False
    _FLUSH_THREAD = None


atexit.register(shutdown)


def _periodic_flush() -> None:
    """定时刷新：普通缓冲层落盘 + 暂留层超时检查（条件 B）。

    已知限制（跨线程竞态，窗口极小）：暂留超时在此锁内 pop、锁外落盘，若恰在 pop 后
    落盘前 event-loop 线程收到命中该 pending 的 chat.tool_call，_route_event 查 pending
    为 None → 条件 A 不触发，tool_call 直接落盘，随后本线程也落 delta，导致本应丢弃的
    delta 残留落盘。仅残留、不丢数据、不崩；完全消除需把 pending 落盘原子化进锁内，代价过高。
    """
    with _buffer_lock:
        buffer_sids = list(set(_session_buffer.keys()) | set(_session_tool_update_buffer.keys()))
        pending_sids = [
            (sid, p) for sid, p in _session_pending.items()
            if time.monotonic() - p.start_time >= PENDING_MAX_SECONDS
        ]
        for sid, _ in pending_sids:
            _session_pending.pop(sid, None)

    for sid in buffer_sids:
        _flush_buffer(sid, None)

    # 条件 B：暂留超时 → 落盘 delta + pending_queue，用暂留时记录的 root
    for sid, pending in pending_sids:
        try:
            _batch_write_items(sid, [pending.item] + list(pending.pending_queue.values()), pending.sessions_root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history 暂留超时落盘失败 sid=%s: %s", sid, exc)


def append_history_record(
    *,
    session_id: str,
    request_id: str,
    channel_id: str,
    role: str,
    content: Any,
    timestamp: float,
    event_type: str | None = None,
    extra: dict[str, Any] | None = None,
    channel_metadata: dict[str, Any] | None = None,
    mode: str | None = None,
    sessions_root: str | Path | None = None,
    task_id: str | None = None,
) -> None:
    """向指定 session 的 history.json 异步追加一条 JSONL 记录."""
    sid = (session_id or "default").strip() or "default"
    rid = str(request_id or "").strip()
    cid = str(channel_id or "").strip()
    role_norm = "assistant" if role == "assistant" else "user"
    content_text = content if isinstance(content, str) else str(content)

    item: dict[str, Any] = {
        "id": f"{rid}:{role_norm}",
        "role": role_norm,
        "request_id": rid,
        "channel_id": cid,
        "timestamp": float(timestamp),
        "content": content_text,
    }
    if role_norm == "assistant" and event_type:
        item["event_type"] = event_type
    if task_id:
        item["task_id"] = task_id
    if isinstance(extra, dict) and extra:
        serialized_extra = _serialize_value(extra)
        if isinstance(serialized_extra, dict):
            item.update(serialized_extra)
    item["session_id"] = sid

    sessions_root_s = str(sessions_root) if sessions_root else None
    et = event_type if (role_norm == "assistant" and event_type) else None

    _ensure_flush_thread_started()
    try:
        _flush_on_request_switch(sid, rid, sessions_root_s)
        _route_event(sid, item, et, sessions_root_s)
    except Exception as exc:  # noqa: BLE001
        # 缓冲失败 → 降级异步队列逐条写，不丢数据
        logger.warning("history 缓冲写入失败，降级直写: %s", exc)
        _ensure_worker_started()
        try:
            _WRITE_QUEUE.put_nowait((sid, item, sessions_root_s))
        except queue.Full:
            _write_item(sid, item, sessions_root_s)

    try:
        from jiuwenclaw.agentserver.session_metadata import update_session_metadata
        update_session_metadata(
            session_id=sid,
            channel_id=cid,
            increment_message_count=True,
            user_content=content_text if role_norm == "user" else None,
            channel_metadata=channel_metadata,
            mode=mode,
            sessions_root=sessions_root_s,
        )
    except Exception as exc:
        logger.warning("更新会话元数据失败: %s", exc)
