# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, OrderedDict

import pulsar

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import serialize_message, deserialize_message
from openjiuwen.core.runner.message_queue_base import (
    MessageQueueBase,
    SubscriptionBase,
    QueueMessage,
    AsyncMessageHandler,
)
from openjiuwen.core.runner.runner_config import PulsarConfig


class PulsarSubscription(SubscriptionBase):
    def __init__(self, topic: str, consumer: pulsar.Consumer, executor: ThreadPoolExecutor):
        self._topic = topic
        self._consumer = consumer
        self._executor = executor
        self._handler: Optional[AsyncMessageHandler] = None
        self._task: Optional[asyncio.Task] = None
        self._active = False

    def set_message_handler(self, handler: AsyncMessageHandler):
        self._handler = handler

    def activate(self):
        if not self._active:
            self._active = True
            self._task = asyncio.create_task(self._consume_loop())
            logger.info(f"[PulsarSubscription] activated topic={self._topic}")

    async def deactivate(self):
        if not self._active:
            return
        self._active = False

        if self._task:
            self._task.cancel()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._consumer.close)

        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info(f"[PulsarSubscription] deactivated topic={self._topic}")

    def is_active(self) -> bool:
        return self._active

    async def _consume_loop(self):
        loop = asyncio.get_running_loop()
        while self._active:
            try:
                msg = await loop.run_in_executor(
                    self._executor, lambda: self._consumer.receive(timeout_millis=1000)
                )

                data = msg.data()
                payload = deserialize_message(data)
                logger.info(
                    f"[PulsarSubscription] Received message, topic={self._topic}, message_id={payload.message_id}, "
                    f"type:{payload.type}")
                if self._handler:
                    await self._handler(payload)
                await loop.run_in_executor(self._executor, lambda: self._consumer.acknowledge(msg))
            except pulsar.Timeout:
                continue
            except Exception as e:
                logger.exception(f"[PulsarSubscription] receive error: {e}")


class MessageQueuePulsar(MessageQueueBase):
    """Pulsar MQ Wrapper"""
    MAX_PRODUCERS = 10000
    DEFAULT_SUBSCRIPTION_NAME = "default"

    def __init__(self, pulsar_config: PulsarConfig):
        self._url = pulsar_config.url
        self._max_workers = pulsar_config.max_workers or 8
        self._client: Optional[pulsar.Client] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._producers: OrderedDict[str, pulsar.Producer] = OrderedDict()
        self._subs: Dict[str, PulsarSubscription] = {}
        self._is_running = False
        self._lock = asyncio.Lock()

    def start(self):
        if self._is_running:
            return
        self._client = pulsar.Client(self._url)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._is_running = True
        logger.info(f"[MessageQueuePulsar] started with url={self._url}")

    async def stop(self):
        if not self._is_running:
            return
        self._is_running = False
        logger.info(f"[MessageQueuePulsar] closing {len(self._subs)} subscriptions")
        for topic in list(self._subs.keys()):
            await self.unsubscribe(topic)

        logger.info(f"[MessageQueuePulsar] closing {len(self._producers)} producers")
        for pd in self._producers.values():
            pd.close()
        self._producers.clear()

        self._executor.shutdown(wait=True)
        self._client.close()
        logger.info(f"[MessageQueuePulsar] stopped")

    def subscribe(self, topic: str) -> PulsarSubscription:
        try:
            self._validate_running()
            if topic in self._subs:
                return self._subs[topic]
            consumer = self._client.subscribe(topic, subscription_name=self.DEFAULT_SUBSCRIPTION_NAME,
                                              consumer_type=pulsar.ConsumerType.KeyShared)
            # All Pulsar operations reuse the same thread pool
            sub = PulsarSubscription(topic, consumer, self._executor)
            self._subs[topic] = sub
            logger.info(f"[MessageQueuePulsar] Create new subscription, topic={topic}")
            return sub
        except Exception as e:
            raise build_error(StatusCode.MESSAGE_QUEUE_TOPIC_SUBSCRIPTION_ERROR, cause=e, topic=topic, reason=e)

    async def unsubscribe(self, topic: str):
        sub = self._subs.pop(topic, None)
        if sub:
            await sub.deactivate()
            logger.info(f"[MessageQueuePulsar] unsubscribed {topic}")

    def _validate_running(self):
        if not self._is_running:
            raise RuntimeError("pulsar message queue is not running")

    async def produce_message(self, topic: str, message: QueueMessage):
        try:
            self._validate_running()
            # Get or create producer
            producer = await self._get_or_create_producer(topic)
            # Serialize and send
            content = serialize_message(message)

            loop = asyncio.get_running_loop()

            logger.info(
                f"[MessageQueuePulsar] Sending message to topic={topic}, message_id={message.message_id}")

            await loop.run_in_executor(
                self._executor,
                lambda: producer.send(
                    content,
                    partition_key=message.message_id
                )
            )

            logger.info(
                f"[MessageQueuePulsar] Message sent successfully: topic={topic}, message_id={message.message_id}"
            )
        except Exception as e:
            raise build_error(StatusCode.MESSAGE_QUEUE_TOPIC_MESSAGE_PRODUCTION_ERROR, cause=e, topic=topic,
                              message=message, reason=e)

    async def _get_or_create_producer(self, topic: str) -> pulsar.Producer:
        producer = self._producers.get(topic)
        if producer:
            # LRU move
            self._producers.move_to_end(topic)
            return producer

        async with self._lock:
            # Double-check inside lock
            producer = self._producers.get(topic)
            if producer:
                self._producers.move_to_end(topic)
                return producer

            # LRU eviction if too many
            if len(self._producers) >= self.MAX_PRODUCERS:
                old_topic, old_producer = self._producers.popitem(last=False)
                old_producer.close()
                logger.debug(f"[MessageQueuePulsar] LRU producer evicted: {old_topic}")

            logger.info(f"[MessageQueuePulsar] Creating new producer for topic={topic}")

            loop = asyncio.get_running_loop()
            producer = await loop.run_in_executor(
                self._executor,
                lambda: self._client.create_producer(topic)
            )

            self._producers[topic] = producer
            return producer
