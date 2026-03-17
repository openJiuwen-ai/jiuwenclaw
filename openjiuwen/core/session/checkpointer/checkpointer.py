# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Dict,
    Optional,
    Type,
)

from pydantic import (
    BaseModel,
    Field,
)

from openjiuwen.core.session.checkpointer.base import Checkpointer
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer


class CheckpointerConfig(BaseModel):
    type: str = Field(default="in_memory")
    conf: dict = Field(default_factory=dict)


class CheckpointerProvider(ABC):
    @abstractmethod
    async def create(self, conf: dict) -> Checkpointer:
        ...


class CheckpointerFactory:
    _registry: Dict[str, CheckpointerProvider] = dict()
    _default_checkpointer: Checkpointer = None
    _type_checkpointers: Dict[str, Checkpointer] = dict()

    @classmethod
    def register(cls, name: str):
        def wrapper(provider_class: Type[CheckpointerProvider]):
            cls._registry[name] = provider_class()
            return provider_class

        return wrapper

    @classmethod
    async def create(cls, checkpointer_conf: CheckpointerConfig) -> Checkpointer:
        import openjiuwen.core.session.checkpointer.persistence as _  # noqa: F401
        provider = cls._registry.get(checkpointer_conf.type)
        if provider is None:
            raise Exception()
        return await cls._registry[checkpointer_conf.type].create(checkpointer_conf.conf)

    @classmethod
    def set_default_checkpointer(cls, checkpointer: Checkpointer):
        """Set the default checkpointer instance."""
        cls._default_checkpointer = checkpointer

    @classmethod
    def set_checkpointer(cls, store_type: str, checkpointer: Checkpointer):
        """
        Set a checkpointer instance for a specific type.
        
        Args:
            store_type: Checkpointer type (e.g., "in_memory", "redis").
            checkpointer: Checkpointer instance to set for the type.
        """
        cls._type_checkpointers[store_type] = checkpointer

    @classmethod
    def get_checkpointer(cls, store_type: Optional[str] = None) -> Checkpointer:
        """
        Get checkpointer instance.
        
        Args:
            store_type: Optional checkpointer type. If provided:
                  - First checks if a checkpointer instance was set for this type via set_checkpointer.
                  - If type is "in_memory" and no instance was set, returns the default in-memory checkpointer.
                  - Otherwise, returns the default checkpointer set via set_default_checkpointer.
                  If not provided, returns the default checkpointer.
        
        Returns:
            Checkpointer instance.
        """
        # If type is specified, try to get the checkpointer for that type
        if store_type is not None:
            # First check if a checkpointer instance was set for this type
            if store_type in cls._type_checkpointers:
                return cls._type_checkpointers[store_type]

            # If type is "in_memory" and no instance was set, return default in-memory checkpointer
            if store_type == "in_memory":
                return default_inmemory_checkpointer

        # Otherwise, return the default checkpointer (if set) or default in-memory checkpointer
        if cls._default_checkpointer is None:
            return default_inmemory_checkpointer
        return cls._default_checkpointer


@CheckpointerFactory.register("in_memory")
class InMemoryCheckpointerProvider(CheckpointerProvider):
    async def create(self, conf: dict) -> Checkpointer:
        return default_inmemory_checkpointer


default_inmemory_checkpointer = InMemoryCheckpointer()
