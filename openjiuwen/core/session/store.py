# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Any, Union, Optional

from openjiuwen.core.session.utils import get_by_schema, update_dict


class Store(ABC):
    """
    Store is the abstract base class for
    """

    @abstractmethod
    def read(self, key: Union[str, dict]) -> Optional[Any]:
        pass

    @abstractmethod
    def write(self, value: dict) -> None:
        pass


class FileStore(Store):
    def read(self, key: Union[str, dict]) -> Optional[Any]:
        pass

    def write(self, value: dict) -> None:
        pass


class MemoryStore(Store):
    def __init__(self):
        self._data: dict = {}

    def read(self, key: Union[str, dict]) -> Optional[Any]:
        return get_by_schema(key, self._data)

    def write(self, value: dict) -> None:
        update_dict(value, self._data)
