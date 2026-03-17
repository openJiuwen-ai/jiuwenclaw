# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
import uuid
from typing import Optional, Dict, AsyncGenerator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.dsubscription.reply_topic_subscription import ReplyTopicSubscription
from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqRequestMessage, DMessageType
from openjiuwen.core.runner.drunner.remote_client.remote_client import RemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import RemoteClientConfig
from openjiuwen.core.runner.runner_config import get_runner_config


class MqRemoteClient(RemoteClient):
    def __init__(self, config: RemoteClientConfig):
        from openjiuwen.core.runner.message_queue_base import SubscriptionBase, MessageQueueBase
        self.mq: MessageQueueBase | None = None
        self.topic = config.topic
        self.remote_id = config.id
        self.config = config
        self._started = False
        self.system_reply_sub: Optional[ReplyTopicSubscription] = None
        self.request_subscription: Optional[SubscriptionBase] = None
        self._lock = asyncio.Lock()

    async def start(self):
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
        from openjiuwen.core.runner import Runner
        self.mq = Runner.dist_pubsub
        self.system_reply_sub = Runner.system_reply_sub
        if self.system_reply_sub is None:
            raise build_error(StatusCode.DIST_MESSAGE_QUEUE_CLIENT_START_ERROR, reason="reply topic not initialized")
        self.reply_topic = self.system_reply_sub.topic
        self._started = True
        logger.debug(f"[MqRemoteClient] init success topic: {self.topic}, reply_topic: {self.reply_topic}")

    async def stop(self):
        # ReplyTopic handles all collector unregistration
        self._started = False
        logger.info(f"[MqRemoteClient] Stopped client for {self.remote_id}")

    async def invoke(self, inputs: Dict, timeout: float = None) -> Dict:
        session_id = inputs.get("conversation_id", "default_session")
        message_id = "_".join((filter(None, [session_id, str(uuid.uuid4())])))
        if timeout is None:
            timeout = get_runner_config().distributed_config.request_timeout
        if timeout == 0:
            timeout = None
        logger.info(f"[MqRemoteClient] Invoke {self.remote_id} with message_id: {message_id}")

        # Register response collector
        collector = await self.system_reply_sub.register_collector(message_id=message_id, remote_id=self.remote_id,
                                                                   ttl=timeout)
        logger.info(f"[MqRemoteClient] Register collector with message_id: {message_id}, remote_id: {self.remote_id}")

        # Build request message
        request_msg = DmqRequestMessage(
            type=DMessageType.INPUT,
            reply_topic=self.reply_topic,
            message_id=message_id,
            sender_id=self.reply_topic,
            receiver_id=self.remote_id,
            enable_stream=False,
            payload=inputs,
            expire_at=time.time() + timeout if timeout else None,
        )
        # Send message
        logger.info(f"[MqRemoteClient] Publishing to topic: {self.topic}, reply_topic: {self.reply_topic}")
        await self.mq.produce_message(self.topic, request_msg)

        try:
            # Wait for response
            response = await collector.result()
            return response
        except asyncio.CancelledError:
            # Send STOP message when stream is cancelled
            logger.info(f"[MQRemoteClient] Stream {message_id} cancelled, sending STOP")
            await self._send_stop_message(message_id)
            raise
        except TimeoutError:
            raise
        except BaseError:
            raise
        except Exception as e:
            raise
        finally:
            await self.system_reply_sub.unregister_collector(message_id, self.remote_id)

    async def stream(self, inputs: Dict, timeout: float = None) -> AsyncGenerator:
        message_id = str(uuid.uuid4())
        if timeout is None:
            timeout = get_runner_config().distributed_config.request_timeout
        if timeout == 0:
            timeout = None
        logger.info(f"[MQRemoteClient] Stream with message_id: {message_id}")

        collector = await self.system_reply_sub.register_collector(message_id, self.remote_id, ttl=timeout)

        request_msg = DmqRequestMessage(
            type=DMessageType.INPUT,
            reply_topic=self.reply_topic,
            message_id=message_id,
            sender_id=self.reply_topic,
            receiver_id=self.remote_id,
            enable_stream=True,
            payload=inputs,
            expire_at=time.time() + timeout if timeout else None,
        )

        logger.info(f"[MQRemoteClient] Publishing to topic: {self.topic}")
        await self.mq.produce_message(self.topic, request_msg)

        try:
            async for response in collector.stream():
                yield response
        except asyncio.CancelledError:
            logger.info(f"[MQRemoteClient] Stream {message_id} cancelled, sending STOP")
            await self._send_stop_message(message_id)
            raise
        except TimeoutError:
            raise
        except BaseError:
            raise
        except Exception as e:
            raise
        finally:
            await self.system_reply_sub.unregister_collector(message_id, self.remote_id)

    async def _send_stop_message(self, message_id: str):
        """Send STOP message, message contains expiration time, no need to
        send STOP on timeout, only when closed early"""
        try:
            stop_msg = DmqRequestMessage(
                type=DMessageType.STOP,
                payload={},
                message_id=message_id,
                sender_id=self.reply_topic,
                receiver_id=self.remote_id,
                expire_at=time.time() + get_runner_config().distributed_config.request_timeout,
            )
            await self.mq.produce_message(self.topic, stop_msg)
            logger.info(f"[MqRemoteClient] Sent STOP message for {message_id}")
        except Exception as e:
            logger.error(f"[MqRemoteClient] Failed to send STOP message: {e}")
