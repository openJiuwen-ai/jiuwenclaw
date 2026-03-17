# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Any, Callable, AsyncIterator, Optional

from openjiuwen.core.common.exception.errors import BaseError, RunnerTermination, build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqRequestMessage, DMessageType
from openjiuwen.core.runner.drunner.server_adapter.mq_message_utils import (build_stream_response,
                                                                            build_final_response, \
                                                                            build_batch_response, build_error_response)
from openjiuwen.core.runner.message_queue_base import MessageQueueBase, SubscriptionBase


@dataclass
class MessageTask:
    message: DmqRequestMessage
    task: asyncio.Task


class MqServerAdapter:
    """Server Adapter responsible for handling MQ requests"""

    def __init__(
            self,
            adapter_id: str,
            topic: str,
            invoke_handler: Callable[[dict], Any],
            stream_handler: Callable[[dict], AsyncIterator[Any]],
    ):
        self.adapter_id = adapter_id
        self.topic = topic
        self.invoke_handler = invoke_handler
        self.stream_handler = stream_handler
        from openjiuwen.core.runner.runner import Runner
        self.mq: MessageQueueBase = Runner.dist_pubsub
        self.subscription: Optional[SubscriptionBase] = None
        self.active = False
        self._running_tasks: Dict[str, MessageTask] = {}
        self._loop = asyncio.get_event_loop()

    def start(self):
        if not self.active:
            self.subscription = self.mq.subscribe(self.topic)
            self.subscription.set_message_handler(self._handle_message)
            self.subscription.activate()
            self.active = True
            logger.info(f"[{self.adapter_id}] Adapter started on {self.topic}")

    async def _handle_message(self, message: DmqRequestMessage) -> None:
        msg_id = message.message_id
        logger.info(f"[{self.adapter_id}] Received message {msg_id}, message_type={message.type}")

        # Discard expired messages directly
        if message.expire_at and message.expire_at < time.time():
            logger.warning(f"[{self.adapter_id}] Ignoring expired message {msg_id}, "
                           f"expire_at: {message.expire_at}, current_time: {time.time()}")
            return

        # Passive cancellation
        if message.type == DMessageType.STOP:
            await self._cancel_task(msg_id, inner_cancel=False)
            return

        # Duplicate message
        if msg_id in self._running_tasks:
            logger.warning(f"[{self.adapter_id}] Duplicate msg_id {msg_id}, replacing old task")
            await self._cancel_task(msg_id, inner_cancel=True)

        # Start execution task
        task = asyncio.create_task(self._process_message(message))
        self._running_tasks[msg_id] = MessageTask(message, task)
        task.add_done_callback(lambda t: self._cleanup_task(msg_id, t))

        # Scheduled timeout cancellation
        if message.expire_at:
            when = message.expire_at
            delay = when - time.time()
            if delay > 0:
                self._loop.call_at(
                    self._loop.time() + delay,
                    lambda: self._timeout_cancel(msg_id)
                )
        logger.info(f"[{self.adapter_id}] Submitted task message_id={msg_id}")

    async def _process_message(self, message: DmqRequestMessage):
        try:
            if message.enable_stream:
                seq = 0
                async for chunk in self.stream_handler(message.payload):
                    resp = build_stream_response(message, self.adapter_id, chunk, seq, last=False)
                    await self.mq.produce_message(message.reply_topic, resp)
                    seq += 1

                final_resp = build_final_response(message, self.adapter_id, seq)
                await self.mq.produce_message(message.reply_topic, final_resp)
            else:
                result = await self.invoke_handler(message.payload)
                resp = build_batch_response(message, self.adapter_id, result)
                await self.mq.produce_message(message.reply_topic, resp)


        except asyncio.CancelledError:
            # Cancellation exception only needs to send error code to client when cancelled internally by adapter stop
            logger.info(f"[{self.adapter_id}] Task {message.message_id} cancelled")
            raise

        except RunnerTermination as e:
            # runner and mq both close before adapter
            logger.info(f"[{self.adapter_id}] Task {message.message_id} cancelled")
            raise e
        except BaseError as e:
            # Runner.run execution exception needs to be returned to client
            logger.warning(f"[{self.adapter_id}] adapter run error msg: {message.message_id}: {e}")
            resp = build_error_response(message, self.adapter_id, e)
            await self.mq.produce_message(message.reply_topic, resp)

        except Exception as e:
            # Runner.run returned unexpected exception, needs to be returned to client
            logger.exception(f"[{self.adapter_id}] Unexpected error: {e}")
            err = build_error(StatusCode.MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR, reason=str(e))
            resp = build_error_response(message, self.adapter_id, err)
            await self.mq.produce_message(message.reply_topic, resp)

    def _timeout_cancel(self, msg_id: str):
        msg_task = self._running_tasks.get(msg_id)
        if not msg_task:
            return
        task = msg_task.task
        if not task.done():
            logger.warning(f"[{self.adapter_id}] Task {msg_id} expired and will be cancelled")
            task.cancel()

    async def _cancel_task(self, msg_id: str, *, inner_cancel: bool):
        msg_task = self._running_tasks.get(msg_id)
        if not msg_task:
            logger.info(f"[{self.adapter_id}] No task found for msg_id {msg_id} during cancellation")
            return
        message, task = msg_task.message, msg_task.task

        logger.info(f"[{self.adapter_id}] Cancelling task {msg_id}")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"[{self.adapter_id}] Task {msg_id} successfully cancelled")
            pass
        finally:
            self._running_tasks.pop(msg_id, None)
            logger.info(f"[{self.adapter_id}] Removed task {msg_id} from running tasks")
            if inner_cancel:
                logger.info(f"[{self.adapter_id}] Sending cancellation error response for task {msg_id}")
                try:
                    err = RunnerTermination(reason=f"Task cancelled by adapter stop ({self.adapter_id})")
                    resp = build_error_response(message, self.adapter_id, err)
                    await self.mq.produce_message(message.reply_topic, resp)
                    logger.info(f"[{self.adapter_id}] Sent cancellation error response for task {msg_id}")
                except Exception as e:
                    logger.warning(f"[{self.adapter_id}] Failed to send cancel error for task {msg_id}: {e}")

    def _cleanup_task(self, msg_id: str, task: asyncio.Task):
        """任务结束清理"""
        self._running_tasks.pop(msg_id, None)
        if task.cancelled():
            logger.info(f"[{self.adapter_id}] Task {msg_id} cancelled (cleanup)")
        exc = task.exception()
        if exc:
            logger.error(f"[{self.adapter_id}] Task {msg_id} failed: {exc}")

    async def stop(self):
        """停止adapter"""
        logger.info(f"[{self.adapter_id}] Stopping adapter...")
        if not self.active:
            return
        self.active = False

        if self.subscription:
            await self.subscription.deactivate()
            self.subscription = None

        logger.info(f"[{self.adapter_id}] Cancelling all running tasks...")
        await asyncio.gather(
            *[self._cancel_task(mid, inner_cancel=True) for mid in list(self._running_tasks.keys())],
            return_exceptions=True,
        )
        logger.info(f"[{self.adapter_id}] Adapter stopped")
