# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable, TypeVar, Optional
from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode

Output = TypeVar("Output", covariant=True)


class QueueMessage(BaseModel):
    message_id: str = ""
    payload: Any = None
    error_code: int = StatusCode.SUCCESS.code
    error_msg: str = ""

    model_config = {
        "arbitrary_types_allowed": True
    }


class LocalMessageQueue:
    async def start(self):
        pass

    async def stop(self):
        pass


class InvokeQueueMessage(QueueMessage):
    response: Optional[asyncio.Future[Output]] = Field(default=None, exclude=True)

    def __init__(self, **data):
        super().__init__(**data)
        if self.response is None:
            self.response = asyncio.Future()


class StreamQueueMessage(QueueMessage):
    response: Optional[asyncio.Future[Output]] = Field(default=None, exclude=True)

    def __init__(self, **data):
        super().__init__(**data)
        if self.response is None:
            self.response = asyncio.Future()


AsyncMessageHandler = Callable[[Any], Awaitable[Any]]


class SubscriptionBase(ABC):

    def set_message_handler(self, handler: AsyncMessageHandler):
        pass

    def activate(self):
        pass

    async def deactivate(self):
        pass

    def is_active(self):
        pass


class MessageQueueBase(ABC):
    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass

    @abstractmethod
    def subscribe(self, topic: str) -> SubscriptionBase:
        pass

    @abstractmethod
    async def unsubscribe(self, topic: str):
        pass

    @abstractmethod
    async def produce_message(self, topic: str, message: QueueMessage):
        pass
