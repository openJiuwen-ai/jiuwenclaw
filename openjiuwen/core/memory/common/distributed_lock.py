# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import uuid
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class DistributedLock:
    """
    Async multiprocess safe distributed lock
    """
    def __init__(self, store, lock_name: str):
        self.store = store
        self.lock_key = "_lock/" + lock_name
        self.ttl = 10
        self.retry_delay = 0.01
        self.lock_value = None

    async def acquire(self):
        self.lock_value = str(uuid.uuid4())
        while True:
            success = await self.store.exclusive_set(self.lock_key, self.lock_value, expiry=self.ttl)
            if success:
                return True
            await asyncio.sleep(self.retry_delay)

    async def release(self):
        try:
            lock_key = await self.store.get(self.lock_key)
            if lock_key == self.lock_value:
                await self.store.delete(self.lock_key)
        except Exception as e:
            memory_logger.error(
                "Error releasing lock",
                exception=str(e),
                event_type=LogEventType.MEMORY_STORE,
            )

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
