# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid
from typing import Awaitable, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict
from openjiuwen.core.runner.message_queue_base import (
    MessageQueueBase,
    SubscriptionBase,
    QueueMessage,
    InvokeQueueMessage,
    StreamQueueMessage,
    AsyncMessageHandler,
)


class SubscriptionInMemory(SubscriptionBase):

    def __init__(self, max_size=10000, timeout=120000.0):
        """Initialize in-memory subscription

        Args:
            max_size: Maximum queue size
            timeout: Timeout duration (seconds)
        """
        self._queue_max_size = max_size
        self._queue = asyncio.Queue(maxsize=self._queue_max_size)
        self._consume_task = None
        self._handler = None
        self._is_active = False
        self._timeout = timeout

    def set_message_handler(self, handler: AsyncMessageHandler):
        self._handler = handler

    def activate(self):
        if not self._is_active:
            self._is_active = True
            self._consume_task = asyncio.create_task(self._consume_message())

    async def deactivate(self):
        if self._is_active:
            self._is_active = False
            if self._consume_task:
                self._consume_task.cancel()
                try:
                    await self._consume_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._consume_task = None
            self._queue = asyncio.Queue(maxsize=self._queue_max_size)

    def is_active(self):
        return self._is_active

    async def push_message(self, message: QueueMessage):
        if not message.message_id:
            message.message_id = str(uuid.uuid4())
        await self._queue.put(message)

    async def _handle_response(self, message, response):
        if isinstance(message, InvokeQueueMessage):
            if not response:
                raise ValueError("response is empty")
            if isinstance(response, AsyncIterator):
                raise ValueError("InvokeQueueMessage need not AsyncIterator response")
            message.response.set_result(response)

        if isinstance(message, StreamQueueMessage):
            if not response:
                raise response("response is empty")
            if not isinstance(response, AsyncIterator):
                raise response("StreamQueueMessage need AsyncIterator response")
            message.response.set_result(response)

    async def _consume_message(self):
        while self._is_active and self._handler:
            message = await self._queue.get()
            try:
                response = self._handler(message.payload)
                if isinstance(response, Awaitable):
                    response = await asyncio.wait_for(response, timeout=self._timeout)
                await self._handle_response(message, response)
            except BaseError as e:
                message.error_code = e.code
                message.error_msg = e.message
                # Set Future exception so caller knows about failure immediately
                if isinstance(message, (InvokeQueueMessage, StreamQueueMessage)):
                    if not message.response.done():
                        message.response.set_exception(e)
            except Exception as e:
                message.error_code = StatusCode.MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR.code
                message.error_msg = build_error(StatusCode.MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR, reason=str(e))
                # Set Future exception so caller knows about failure immediately
                if isinstance(message, (InvokeQueueMessage, StreamQueueMessage)):
                    if not message.response.done():
                        message.response.set_exception(e)
            finally:
                self._queue.task_done()


class MessageQueueInMemory(MessageQueueBase):
    def __init__(self, queue_max_size=10000, timeout=120000.0):
        self._is_running = False
        self._subscribers: ThreadSafeDict[str, SubscriptionInMemory] = ThreadSafeDict()
        self._queue_max_size = queue_max_size
        self._queue = asyncio.Queue(maxsize=self._queue_max_size)
        self._consume_task = None
        self._timeout = timeout

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._consume_task = asyncio.create_task(self._consume_message())

    async def stop(self):
        if self._is_running:
            self._is_running = False
            if self._consume_task:
                self._consume_task.cancel()
                try:
                    await self._consume_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._consume_task = None
            self._queue = asyncio.Queue(maxsize=self._queue_max_size)

    def subscribe(self, topic: str) -> SubscriptionInMemory:
        if topic in self._subscribers:
            raise ValueError(f"Topic '{topic}' is already subscribed.")
        subscription = SubscriptionInMemory(max_size=self._queue_max_size, timeout=self._timeout)
        self._subscribers[topic] = subscription
        return subscription

    async def unsubscribe(self, topic):
        if topic in self._subscribers:
            await self._subscribers[topic].deactivate()
            del self._subscribers[topic]

    async def produce_message(self, topic: str, message: QueueMessage):
        await self._queue.put((topic, message))

    async def _consume_message(self):
        while self._is_running:
            topic, message = await self._queue.get()
            if topic in self._subscribers and self._subscribers[topic].is_active():
                await self._subscribers[topic].push_message(message)
            self._queue.task_done()
