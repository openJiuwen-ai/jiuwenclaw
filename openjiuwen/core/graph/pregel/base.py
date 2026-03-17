# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Callable


class Interrupt:
    value: Any

    def __init__(self, value: Any):
        self.value = value


class GraphInterrupt(Exception):
    def __init__(self, value: Interrupt | tuple[Interrupt, ...] = None):
        self.value = value
        super().__init__(str(value))


class IRouter(ABC):
    @abstractmethod
    async def dispatch(self, source_node: str) -> List[Message]:
        ...


class Message:
    def __init__(self, sender: str, target: str, payload: Any = None):
        self.sender = sender
        self.target = target
        self.payload = payload


class TriggerMessage(Message):
    """Activate a target node next superstep"""
    pass


class BarrierMessage(Message):
    """N→1 fan-in"""
    pass


class PregelNode:
    def __init__(self, name: str, func: Callable[[Any], Any], routers: list[IRouter]):
        self.name = name
        self.func = func
        self.routers = routers


class Channel(ABC):
    def __init__(self, name: str):
        self.name = name

    @property
    def key(self) -> str:
        return self.name

    @property
    def node_name(self) -> str:
        return self.name

    @abstractmethod
    def is_ready(self) -> bool:
        ...

    @abstractmethod
    def accept(self, msg: Message) -> None:
        ...

    @abstractmethod
    def consume(self) -> Any:
        """Return consumable input for node func and reset internal snapshot."""
        ...

    @abstractmethod
    def snapshot(self) -> Any:
        return None

    @abstractmethod
    def restore(self, snapshot: Any) -> None:
        pass
