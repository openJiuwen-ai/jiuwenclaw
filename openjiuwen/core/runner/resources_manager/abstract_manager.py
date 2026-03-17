# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
from typing import Generic, Optional, Callable, TypeVar

from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict

T = TypeVar('T')


class AbstractManager(Generic[T]):
    def __init__(self):
        self._providers: ThreadSafeDict[str, Callable] = ThreadSafeDict()

    def _register_resource_provider(self, resource_id: str, resource: Callable) -> None:
        if self._providers.get(resource_id):
            raise ValueError(f'add resource failed, {resource_id} is already exist')
        self._providers[resource_id] = resource

    async def _get_resource(self, resource_id: str) -> Optional[T]:
        resource_provider = self._providers.get(resource_id)
        if not resource_provider:
            return None
        if inspect.iscoroutinefunction(resource_provider):
            return await resource_provider()
        else:
            return resource_provider()

    def _unregister_resource_provider(self, resource_id: str) -> Optional[T]:
        return self._providers.pop(resource_id, None)
