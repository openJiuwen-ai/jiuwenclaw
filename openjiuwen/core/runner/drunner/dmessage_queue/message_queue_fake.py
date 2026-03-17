# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import serialize_message, deserialize_message
from openjiuwen.core.runner.message_queue_base import SubscriptionBase, AsyncMessageHandler, QueueMessage, \
    MessageQueueBase


class FakeSubscription(SubscriptionBase):
    """
    In-memory subscription for FakeMQ.
    """

    def __init__(self, topic: str):
        self.topic = topic
        self._handler: Optional[AsyncMessageHandler] = None
        self._queue = asyncio.Queue(10000)
        self._task: asyncio.Task | None = None
        self._active = False

    def set_message_handler(self, handler: AsyncMessageHandler):
        self._handler = handler

    def activate(self):
        if not self._active:
            self._active = True
            self._task = asyncio.create_task(self._consume_loop())

    async def deactivate(self):
        self._active = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None  # Clean reference

    async def _consume_loop(self):
        try:
            while self._active:
                try:
                    raw = await asyncio.wait_for(self._queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue

                if not self._active:
                    break

                payload = deserialize_message(raw)
                if self._handler:
                    await self._handler(payload)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"[FakeSubscription] consume_loop error: {e}")

    async def push(self, msg):
        if self._active:
            await self._queue.put(msg)


class FakeMQ(MessageQueueBase):
    """
    In-memory message queue for testing.
    """

    def __init__(self):
        self._topics: Dict[str, List[FakeSubscription]] = {}
        self._lock = asyncio.Lock()
        self._is_running = False

    def start(self):
        self._is_running = True
        logger.info("[FakeMQ] started")

    async def stop(self):
        """Stop MQ & deactivate all subscriptions."""
        self._is_running = False

        topics = list(self._topics.keys())
        for t in topics:
            await self.unsubscribe(t)

        logger.info("[FakeMQ] stopped")

    def subscribe(self, topic: str) -> FakeSubscription:
        if not self._is_running:
            raise build_error(StatusCode.MESSAGE_QUEUE_TOPIC_SUBSCRIPTION_ERROR, reason="message queue is not running")
        sub = FakeSubscription(topic)
        self._topics.setdefault(topic, []).append(sub)
        logger.info(f"[FakeMQ] new subscription for topic={topic}")
        return sub

    async def unsubscribe(self, topic: str):
        if topic in self._topics:
            for sub in self._topics[topic]:
                await sub.deactivate()
            del self._topics[topic]
            logger.info(f"[FakeMQ] unsubscribed topic={topic}")

    async def produce_message(self, topic: str, message: QueueMessage):
        data = serialize_message(message)

        async with self._lock:
            subs = list(self._topics.get(topic, []))

        for sub in subs:
            asyncio.create_task(sub.push(data))
