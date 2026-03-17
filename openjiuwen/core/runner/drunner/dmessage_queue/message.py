# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum
from typing import Optional, Union, Any

from openjiuwen.core.runner.message_queue_base import QueueMessage


class DMessageType(str, Enum):
    """Distributed message type"""
    INPUT = "INPUT"
    STOP = "STOP"
    OUTPUT = "OUTPUT"


class ResultType(str, Enum):
    """ResultType"""
    MESSAGE = "MESSAGE"
    ERROR = "ERROR"


class DmqMessage(QueueMessage):
    pass


class DmqRequestMessage(DmqMessage):
    """Distributed request message"""
    type: str = DMessageType.INPUT
    reply_topic: str = ""
    request_id: str = ""
    sender_id: str = ""
    receiver_id: str = ""
    enable_stream: bool = False
    expire_at: Optional[float] = None


class DmqResponseMessage(DmqMessage):
    """Distributed response message"""
    type: str = DMessageType.OUTPUT
    payload: Union[Any, list]
    result_type: ResultType = ResultType.MESSAGE
    request_id: str = ""
    sender_id: str = ""
    receiver_id: str = ""
    seq: int = 0
    last_chunk: bool = False
    expire_at: Optional[float] = None
