# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from abc import ABC, abstractmethod
import time
from typing import Dict, Generic, Tuple, TypeVar


class RefCountedResource(ABC):
    def __init__(self):
        self._ref_count: int = 1
        self._closed: bool = False
        self._created_at: float = time.time()
        self._last_used: float = self._created_at

    @property
    def ref_count(self) -> int:
        return self._ref_count

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def age(self) -> float:
        return time.time() - self._created_at if not self._closed else 0

    def increment_ref(self) -> int:
        if self._closed:
            raise RuntimeError("Cannot increment ref on closed resource")
        self._ref_count += 1
        self._last_used = asyncio.get_event_loop().time()
        return self._ref_count

    def decrement_ref(self) -> bool:
        if self._closed:
            return False

        self._ref_count -= 1
        return self._ref_count <= 0

    @abstractmethod
    async def _do_close(self, **kwargs):
        pass

    async def close(self, **kwargs):
        if self._closed:
            return

        try:
            self.decrement_ref()
            await self._do_close(**kwargs)
        finally:
            self._closed = True

    def get_stats(self) -> Dict:
        return {
            'ref_count': self._ref_count,
            'closed': self._closed,
            'created_at': self._created_at,
            'last_used': self._last_used,
            'age': self.age
        }


ResourceType = TypeVar('ResourceType', bound=RefCountedResource)


class BaseRefResourceMgr(ABC, Generic[ResourceType]):
    """资源管理器基类"""

    def __init__(self):
        self._resources: Dict[str, ResourceType] = {}
        self._lock = asyncio.Lock()

    @abstractmethod
    def _get_resource_key(self, config) -> str:
        """获取资源键值"""
        pass

    @abstractmethod
    async def _create_resource(self, config) -> ResourceType:
        """创建资源"""
        pass

    async def acquire(self, config) -> Tuple[ResourceType, bool]:
        """
        获取资源
        返回：(资源对象, 是否是新创建的)
        """
        key = self._get_resource_key(config)

        async with self._lock:
            if key in self._resources:
                resource = self._resources[key]
                if not resource.closed:
                    resource.increment_ref()
                    return resource, False
                else:
                    del self._resources[key]

            resource = await self._create_resource(config)
            self._resources[key] = resource
            return resource, True

    async def release(self, config):
        key = self._get_resource_key(config)

        async with self._lock:
            resource = self._resources[key]
            if resource.closed:
                return
            should_close = resource.decrement_ref()

            if should_close:
                if key in self._resources:
                    del self._resources[key]
                await resource.close()

    async def close(self, key: str):
        async with self._lock:
            if key in self._resources:
                resource = self._resources.pop(key)
                await resource.close()

    async def close_all(self):
        async with self._lock:
            for key, resource in list(self._resources.items()):
                await resource.close()
            self._resources.clear()

    async def get_stats(self) -> Dict:
        async with self._lock:
            resources_stats = {}
            for key, resource in self._resources.items():
                resources_stats[key] = resource.get_stats()

            return {
                'total_resources': len(self._resources),
                'resources': resources_stats,
            }
