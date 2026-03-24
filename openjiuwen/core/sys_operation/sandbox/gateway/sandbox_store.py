# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import abc
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SandboxStatus(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"


@dataclass
class SandboxRecord:
    sandbox_id: str
    base_url: str
    status: SandboxStatus
    launcher_type: str
    sandbox_type: str
    container_config_hash: str
    created_ts: float = field(default_factory=time.time)
    last_used_ts: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class AbstractSandboxStore(abc.ABC):
    @abc.abstractmethod
    async def get(self, key: str) -> Optional[SandboxRecord]:
        pass

    @abc.abstractmethod
    async def set(self, key: str, record: SandboxRecord) -> None:
        pass

    @abc.abstractmethod
    async def hdel(self, key: str) -> Optional[SandboxRecord]:
        pass

    @abc.abstractmethod
    async def flushdb(self) -> List[SandboxRecord]:
        pass

    @abc.abstractmethod
    async def evict_expired(self, idle_ttl_seconds: int, now: float) -> List[SandboxRecord]:
        pass


class InMemorySandboxStore(AbstractSandboxStore):
    def __init__(self) -> None:
        self._records: Dict[str, SandboxRecord] = {}

    async def get(self, key: str) -> Optional[SandboxRecord]:
        return self._records.get(key)

    async def set(self, key: str, record: SandboxRecord) -> None:
        self._records[key] = record

    async def hdel(self, key: str) -> Optional[SandboxRecord]:
        return self._records.pop(key, None)

    async def flushdb(self) -> List[SandboxRecord]:
        records = list(self._records.values())
        self._records.clear()
        return records

    async def evict_expired(self, idle_ttl_seconds: int, now: float) -> List[SandboxRecord]:
        expired = [k for k, r in self._records.items() if (now - r.last_used_ts) > idle_ttl_seconds]
        return [self._records.pop(k) for k in expired]
