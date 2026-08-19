# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""进程内网页正文缓存：键为规范化 URL，值为正文+更新时间+抓取时刻。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目。

    Attributes:
        url: 规范化后的 URL（与缓存键一致）。
        title: 网页标题（可选，仅用于诊断日志）。
        content: 网页正文（markdown 或纯文本）。
        update_time: 网页更新时间 epoch 秒；未知则为 None。
        cached_at: 写入缓存的 epoch 秒。
        source: 来源标记，如 "petal" / "fetch:direct" / "fetch:jina"。
    """
    url: str
    content: str
    update_time: Optional[float] = None
    cached_at: float = field(default_factory=time.time)
    title: str = ""
    source: str = ""


def normalize_url(url: str) -> str:
    """URL 规范化：去除 fragment、去除尾部斜杠、转小写 host。

    用于作为缓存键，保证同一页面不同写法（带 #锚、大小写 host）能命中。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class WebContentCache:
    """进程内单例缓存，并发安全。

    设计要点：
    - 读写均通过 asyncio.Lock 保护，避免并发写覆盖。
    - 缓存命中后是否可信由调用方（模型）根据返回的元数据决策，
      缓存层不做时效性判断。
    - 不持久化、不压缩；上限通过 max_entries 控制，LRU 淘汰。
    """

    def __init__(
        self, *, max_entries: int = 512
    ) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self.bypassed = 0

    async def get(self, url: str) -> Optional[CacheEntry]:
        key = normalize_url(url)
        if not key:
            return None
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            self.hits += 1
            return entry

    async def put(self, entry: CacheEntry) -> None:
        key = normalize_url(entry.url)
        if not key or not (entry.content or "").strip():
            return
        async with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                if entry.update_time is None and existing.update_time is not None:
                    entry.update_time = existing.update_time
                if not (entry.content or "").strip() and (existing.content or "").strip():
                    return
            self._store[key] = entry
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            while len(self._order) > self._max_entries:
                oldest = self._order.pop(0)
                self._store.pop(oldest, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self._order.clear()
            self.hits = 0
            self.misses = 0
            self.bypassed = 0

    def __len__(self) -> int:
        return len(self._store)

    def stats(self) -> dict[str, int]:
        """返回命中率计数器快照。"""
        total = self.hits + self.misses + self.bypassed
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "bypassed": self.bypassed,
            "total": total,
            "hit_rate_pct": round(hit_rate, 1),
            "entries": len(self._store),
        }

    def _evict_locked(self, key: str) -> None:
        self._store.pop(key, None)
        if key in self._order:
            self._order.remove(key)


_DEFAULT_CACHE: WebContentCache | None = None


def get_default_cache() -> WebContentCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = WebContentCache()
    return _DEFAULT_CACHE


def reset_default_cache_for_tests() -> None:
    """仅供测试使用：重置单例。"""
    global _DEFAULT_CACHE
    _DEFAULT_CACHE = None


def parse_update_time(raw: object) -> Optional[float]:
    """从 Petal web_pages[*].update_time 字段解析 epoch 秒。

    支持多种格式：
    - int/float：直接作为 epoch 秒
    - str：ISO8601 或 yyyy-mm-dd HH:MM:SS 或 yyyy-mm-dd
    - 缺失或解析失败：返回 None
    """
    import datetime as _dt

    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                return None
            if s.isdigit():
                return float(s)
            try:
                return _dt.datetime.fromisoformat(s).timestamp()
            except ValueError:
                pass
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%d/%m/%Y",
            ):
                try:
                    return _dt.datetime.strptime(s, fmt).timestamp()
                except ValueError:
                    continue
    except Exception:
        logger.debug("parse_update_time failed for raw=%r", raw, exc_info=True)
    return None
