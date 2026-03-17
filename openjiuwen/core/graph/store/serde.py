# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


import json
import pickle
from abc import ABC, abstractmethod
from typing import Any


class Serializer(ABC):
    @abstractmethod
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        pass

    @abstractmethod
    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        pass


class JsonSerializer(Serializer):
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "json", json.dumps(obj, ensure_ascii=False).encode()

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data is None:
            return None
        if data[0] != "json":
            return None
        return json.loads(data[1].decode())


class PickleSerializer(Serializer):
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "pickle", pickle.dumps(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data is None:
            return None
        if data[0] != "pickle":
            return None
        return pickle.loads(data[1])


def create_serializer(type_name: str) -> Serializer:
    if type_name == "json":
        raise ValueError("json is not yet supported")
    elif type_name == "pickle":
        return PickleSerializer()
    else:
        raise ValueError(f"Unknown serializer type: {type_name}")
