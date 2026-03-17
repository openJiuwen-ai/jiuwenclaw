# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from openjiuwen.core.common.exception.errors import RunnerTermination, build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage, ResultType

# Max queue size per collector
MAX_QUEUE_SIZE = 10000


class CancelReason(str, Enum):
    """Cancellation reason: used to distinguish exception types after awakening"""
    RUNNER_STOPPED = "runner_stopped"  # Runner/Adapter actively stopped (should throw RUNNER_STOPPED)
    TTL_EXPIRE = "ttl_expire"  # TTL expired (should throw TimeoutError)
    QUEUE_FULL = "queue_full"  # Queue full (should throw CancelledError)
    FINISH = "finish"  # Normal completion, no need to wake up


@dataclass(frozen=True)
class CancelEvent:
    reason: CancelReason
    info: Optional[str] = None


class ResponseCollector:
    """Responsible for collecting responses for specified requests, and supports cancellation and timeout"""

    def __init__(self, message_id: str, receiver_id: str, request_id: str = None, ttl: float = None):
        self.message_id = message_id
        self.receiver_id = receiver_id
        self.request_id = request_id
        self.ttl = ttl or 30.0
        self.queue: asyncio.Queue[DmqResponseMessage | CancelEvent] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

        self._cancelled = False
        self._expired = False

        # Start TTL expiration task
        self._expire_task = asyncio.create_task(self._expire_after_ttl())

    def is_cancelled(self) -> bool:
        return self._cancelled

    def is_expired(self) -> bool:
        return self._expired

    def is_active(self) -> bool:
        return not (self._cancelled or self._expired)

    async def _expire_after_ttl(self):
        """Automatically mark as expired when TTL expires"""
        try:
            await asyncio.sleep(self.ttl)
            if not self._cancelled:
                self._expired = True
                await self._cleanup_queue()
                logger.warning(f"[Collector:{self.message_id}] expired after {self.ttl:.1f}s")
                # Wake up blocked waiting requests
                self._wake_waiters(CancelEvent(CancelReason.TTL_EXPIRE))
        except asyncio.CancelledError:
            # Actively closed, not recorded as expired
            return

    async def put_message(self, msg: DmqResponseMessage):
        """Receive message from replyTopic"""
        if not self.is_active():
            logger.warning(f"[Collector:{self.message_id}] inactive, discard message")
            return

        if self.queue.full():
            logger.warning(f"[Collector:{self.message_id}] queue full({MAX_QUEUE_SIZE}), auto-cancelled")
            await self.close(reason=CancelReason.QUEUE_FULL)
            return

        await self.queue.put(msg)

    async def result(self, timeout: Optional[float] = None) -> Any:
        timeout = timeout or self.ttl

        if self._cancelled:
            raise asyncio.CancelledError(f"Collector({self.message_id}) was cancelled before request send")
        if self._expired:
            raise TimeoutError(f"Collector({self.message_id}) expired")
        try:
            msg = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            await self.check_message(msg)

            return msg.payload
        except asyncio.TimeoutError:
            self._expired = True
            await self._cleanup_queue()
            logger.warning(f"[Collector:{self.message_id}] result timeout ({timeout:.1f}s)")
            raise TimeoutError(f"Collector({self.message_id}) timeout waiting for result")
        except Exception as e:
            raise e
        finally:
            await self.close(reason=CancelReason.FINISH)

    async def stream(self, timeout: Optional[float] = None):
        """Stream results"""
        timeout = timeout or self.ttl
        try:
            while True:
                msg = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                logger.debug("[Collector:%s] stream get message %s", self.message_id, msg)
                await self.check_message(msg)
                if msg.last_chunk:
                    # Last message is MQ empty marker, do not return
                    break
                yield msg.payload
        except asyncio.TimeoutError:
            self._expired = True
            logger.warning(f"[Collector:{self.message_id}] stream timeout ({timeout:.1f}s)")
            raise TimeoutError(f"Collector({self.message_id}) stream timeout")
        except Exception as e:
            raise e
        finally:
            await self.close(reason=CancelReason.FINISH)

    async def check_message(self, msg: DmqResponseMessage | CancelEvent):
        if isinstance(msg, CancelEvent):
            logging.info(f"[Collector:{self.message_id}] rev CancelEvent stream cancelled by {msg.reason}")
            if msg.reason == CancelReason.TTL_EXPIRE:
                # TTL expired → throw TimeoutError
                raise TimeoutError(f"Collector({self.message_id}) timeout")
            elif msg.reason == CancelReason.QUEUE_FULL:
                # Message queue full but not retrieved, client probably doesn't need it
                raise asyncio.CancelledError(f"Collector({self.message_id}) queue full")
            else:
                raise RunnerTermination(reason=f"Collector({self.message_id}) was cancelled",
                                        status=StatusCode.RUNNER_TERMINATION_ERROR)
        if msg.result_type == ResultType.ERROR:
            # Remote error codes encapsulated in error message
            raise build_error(StatusCode.REMOTE_AGENT_RESPONSE_PROCESS_ERROR, message_id=self.message_id,
                              process_id=self.receiver_id,
                              error_code=msg.error_code, error_msg=msg.error_msg)

    async def close(self, reason: CancelReason = CancelReason.RUNNER_STOPPED):
        """Active cancellation (including queue full, system shutdown)"""
        if self._cancelled:
            return

        self._cancelled = True
        if self._expire_task and not self._expire_task.done():
            self._expire_task.cancel()

        await self._cleanup_queue()
        if reason != CancelReason.FINISH:
            self._wake_waiters(CancelEvent(reason))
            logger.info(f"[Collector:{self.message_id}] cancelled by close({reason})")
        logger.info(f"[Collector:{self.message_id}] cancelled by finished")

    async def _cleanup_queue(self):
        """清空队列"""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                break

    def _wake_waiters(self, cancel_event: CancelEvent):
        """往队列放入取消信号以唤醒result/stream接口"""
        try:
            self.queue.put_nowait(cancel_event)
        except asyncio.QueueFull:
            pass
