# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session 存储抽象层。

提供统一的存储接口，屏蔽 Redis 和本地文件的实现差异。
"""
from __future__ import annotations

import asyncio
import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jiuwenclaw.utils import get_checkpoint_dir, logger

if TYPE_CHECKING:
    from jiuwenclaw.gateway.session_map import Session

_lock = threading.Lock()


class SessionStorage(ABC):
    """Session 存储接口抽象类."""

    @abstractmethod
    def load(self) -> None:
        """加载所有数据到内存."""
        ...

    @abstractmethod
    def save(self, session: "Session") -> None:
        """保存单条 Session 数据到存储."""
        ...

    @abstractmethod
    def get(self, identity_key: str) -> "Session | None":
        """根据 identity_key 获取 Session."""
        ...

    @abstractmethod
    def set(self, identity_key: str, session: "Session") -> None:
        """存储 identity_key -> Session 映射（仅内存）."""
        ...

    @abstractmethod
    def remove(self, identity_key: str) -> None:
        """删除指定的映射."""
        ...

    @abstractmethod
    def get_all(self) -> dict[str, "Session"]:
        """获取所有映射."""
        ...


class LocalSessionStorage(SessionStorage):
    """本地文件存储实现（同步）."""

    def __init__(self, store_path: Path | None = None) -> None:
        if store_path:
            self._store_path = store_path
        else:
            self._store_path = get_checkpoint_dir() / "session_map.json"
        self._mapping: dict[str, "Session"] = {}  # identity_key -> Session
        self._lock = threading.Lock()
        self.load()

    @property
    def store_path(self) -> Path:
        """存储文件路径（只读）。"""
        return self._store_path

    def load(self) -> None:
        """从文件加载所有数据到内存."""
        from jiuwenclaw.gateway.session_map import _session_from_stored_value
        try:
            if not self._store_path.exists():
                return
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    key = str(k)
                    sess = _session_from_stored_value(v)
                    if sess:
                        self._mapping[key] = sess
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LocalSessionStorage] 加载失败: %s", exc)

    def save(self, session: "Session") -> None:
        """全量保存 _mapping 到文件."""
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                out = {k: {"session_id": s.session_id, "service_id": s.service_id, "agent_id": s.agent_id}
                       for k, s in self._mapping.items()}
                with open(self._store_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LocalSessionStorage] 保存失败: %s", exc)

    def get(self, identity_key: str) -> "Session | None":
        with self._lock:
            return self._mapping.get(identity_key)

    def set(self, identity_key: str, session: "Session") -> None:
        with self._lock:
            self._mapping[identity_key] = session
        self.save(session)

    def remove(self, identity_key: str) -> None:
        with self._lock:
            if identity_key in self._mapping:
                del self._mapping[identity_key]

    def get_all(self) -> dict[str, "Session"]:
        with self._lock:
            return dict(self._mapping)


class RedisSessionStorage(SessionStorage):
    """Redis 存储实现（异步）."""

    def __init__(self, ttl: int = 604800) -> None:
        self._ttl = ttl
        self._redis: Any | None = None
        self._mapping: dict[str, "Session"] = {}  # identity_key -> Session
        self._lock = threading.Lock()
        self._claw_id = self._get_claw_id()

    @staticmethod
    def _get_claw_id() -> str:
        """获取 claw_id，从配置文件 instance_id 字段读取。"""
        try:
            from jiuwenclaw.config import get_config
            val = (get_config().get("gateway") or {}).get("instance_id")
            if val and str(val).strip():
                return str(val).strip()
        except Exception:  # noqa: BLE001
            pass
        return "default"

    def _get_redis(self) -> Any | None:
        if self._redis is None:
            from jiuwenclaw.extensions.redis.redis_runtime import get_gateway_redis_client
            self._redis = get_gateway_redis_client()
        return self._redis

    def _redis_key(self, identity_key: str) -> str:
        return f"session_map:{self._claw_id}:{identity_key}"

    def _session_to_json(self, session: "Session") -> str:
        """将 Session 序列化为 JSON 字符串。"""
        return json.dumps({"session_id": session.session_id, "service_id": session.service_id, "agent_id": session.agent_id})

    def _session_from_json(self, raw: str | None) -> "Session | None":
        """从 JSON 字符串反序列化为 Session。"""
        if not raw:
            return None
        from jiuwenclaw.gateway.session_map import Session
        try:
            data = json.loads(raw)
            sid = str(data.get("session_id") or "").strip()
            svc = str(data.get("service_id") or "").strip()
            aid_raw = data.get("agent_id")
            aid = str(aid_raw).strip() if aid_raw else None
            if sid and svc:
                return Session(session_id=sid, service_id=svc, agent_id=aid)
        except Exception:  # noqa: BLE001
            pass
        return None

    def load(self) -> None:
        """从 Redis 加载所有数据到内存."""
        redis = self._get_redis()
        if redis is None:
            return
        try:
            keys = asyncio.run(redis.scan_iter(f"session_map:{self._claw_id}:*"))
            if keys:
                values = asyncio.run(redis.mget(keys))
                prefix_len = len(f"session_map:{self._claw_id}:")
                with self._lock:
                    for key, value in zip(keys, values):
                        if value:
                            identity_key = key[prefix_len:]
                            sess = self._session_from_json(value)
                            if sess:
                                self._mapping[identity_key] = sess
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RedisSessionStorage] load 失败: %s", exc)

    def save(self, session: "Session") -> None:
        """保存单条 Session 数据到 Redis."""
        redis = self._get_redis()
        if redis is None:
            return
        identity_key = None
        with self._lock:
            for k, v in self._mapping.items():
                if v.session_id == session.session_id:
                    identity_key = k
                    break
        if identity_key is None:
            return
        try:
            asyncio.run(redis.set(self._redis_key(identity_key), self._session_to_json(session), ttl_seconds=self._ttl))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RedisSessionStorage] save 失败: %s", exc)

    def get(self, identity_key: str) -> "Session | None":
        with self._lock:
            return self._mapping.get(identity_key)

    def set(self, identity_key: str, session: "Session") -> None:
        with self._lock:
            self._mapping[identity_key] = session
        self.save(session)

    def remove(self, identity_key: str) -> None:
        redis = self._get_redis()
        with self._lock:
            if identity_key in self._mapping:
                del self._mapping[identity_key]
        if redis is not None:
            try:
                asyncio.run(redis.delete(self._redis_key(identity_key)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RedisSessionStorage] remove 失败: %s", exc)

    def get_all(self) -> dict[str, "Session"]:
        with self._lock:
            return dict(self._mapping)