# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.dsubscription.response_collector import ResponseCollector
from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage
from openjiuwen.core.runner.message_queue_base import MessageQueueBase, SubscriptionBase
from openjiuwen.core.runner.runner_config import get_runner_config


@dataclass(frozen=True)
class CollectorKey:
    remote_id: str
    message_id: str
    request_id: Optional[str] = None


class ReplyTopicSubscription():
    """Responsible for listening to reply_topic and distributing responses to corresponding ResponseCollectors"""

    def __init__(self, mq: Optional[MessageQueueBase] = None, topic: str = None):
        self._is_active = None
        self.mq = mq
        self.topic = topic or get_runner_config().reply_topic_template().format(
            instance_id=get_runner_config().instance_id)
        self.collectors: dict[CollectorKey, ResponseCollector] = {}
        self.subscription: Optional[SubscriptionBase] = None

    def activate(self):
        """Initialize"""
        self.subscription = self.mq.subscribe(self.topic)
        self.subscription.set_message_handler(self.on_message)
        self.subscription.activate()
        self._is_active = True

        logger.info(f"[ReplyTopicSubscription] activated topic={self.topic}")

    async def deactivate(self):
        """Clean up all collectors"""
        self._is_active = False
        if self.subscription:
            await self.mq.unsubscribe(self.topic)
        await self.unregister_collector()
        logger.info(f"[ReplyTopicSubscription] Stopped")

    @staticmethod
    def _make_key(sender_id: str, message_id: str, request_id: Optional[str] = None) -> CollectorKey:
        """Construct unique key for collector"""
        request_id = request_id or None
        return CollectorKey(sender_id, message_id, request_id)

    async def on_message(self, msg: DmqResponseMessage):
        """Distribute message to corresponding ResponseCollector"""
        key = ReplyTopicSubscription._make_key(msg.sender_id, msg.message_id, msg.request_id)
        logger.info(f"[ReplyTopicSubscription] receive message key={key}")

        collector = self.collectors.get(key)
        if collector:
            await collector.put_message(msg)
        else:
            logger.info(f"[ReplyTopicSubscription] No collector for {key}, discard message")

    async def register_collector(self, message_id: str, remote_id: str, request_id: Optional[str] = None,
                                 ttl: float = None) -> ResponseCollector:
        """注册 collector，用于等待对应返回"""
        if not self.is_active():
            raise asyncio.CancelledError(f"ReplyTopicSubscription was cancelled")
        if len(self.collectors) >= get_runner_config().distributed_config.max_request_concurrency:
            raise RuntimeError(
                f"[ReplyTopicSubscription] Too many collectors "
                f"({get_runner_config().distributed_config.max_request_concurrency})")

        key = ReplyTopicSubscription._make_key(remote_id, message_id, request_id)
        if key in self.collectors:
            raise RuntimeError(f"[ReplyTopicSubscription] Collector already exists for {key}")

        collector = ResponseCollector(message_id=message_id, receiver_id=remote_id, ttl=ttl)
        self.collectors[key] = collector
        logger.info(f"[ReplyTopicSubscription] register collector for {key}")
        return collector

    async def unregister_collector(
            self,
            message_id: Optional[str] = None,
            remote_id: Optional[str] = None,
            request_id: Optional[str] = None,
    ):
        """
        Clean up by message_id + remote_id + request_id
        - If all are None, it means cleaning up all collectors
        """
        logger.info(
            f"[ReplyTopicSubscription] unregister_collector message_id: {message_id}, remote_id: {remote_id}, "
            f"request_id:{request_id}")

        if not self.collectors:
            return

        # Filter targets
        keys_to_remove = []

        def match(key, message_id, remote_id, request_id):
            if message_id is None and remote_id is None and request_id is None:
                return True

            return (
                    (message_id is None or key.message_id == message_id)
                    and (remote_id is None or key.remote_id == remote_id)
                    and (request_id is None or key.request_id == request_id)
            )

        for key, collector in self.collectors.items():
            if match(key, message_id, remote_id, request_id):
                keys_to_remove.append(key)

        if not keys_to_remove:
            logger.info(
                f"[ReplyTopicSub] No matching collectors for message_id={message_id}, "
                f"remote_id={remote_id}, request_id={request_id}, collectors={self.collectors}"
            )
            return

        logger.info(
            f"[ReplyTopicSub] unregistering {len(keys_to_remove)} collectors "
            f"(msg_id={message_id}, recv_id={remote_id}, req_id={request_id})"
        )

        tasks = []
        for key in keys_to_remove:
            collector = self.collectors.pop(key, None)
            if collector:
                tasks.append(collector.close())

        # Close concurrently
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[ReplyTopicSub] unregistered {len(keys_to_remove)} collectors")

    def is_active(self):
        return self._is_active
