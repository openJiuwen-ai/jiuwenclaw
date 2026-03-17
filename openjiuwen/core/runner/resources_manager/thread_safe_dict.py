# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import threading
from typing import (
    Dict, Generic, Iterator, KeysView, MutableMapping,
    Optional, TypeVar, ValuesView, ItemsView, Iterable, Mapping, Callable
)

K = TypeVar("K")
V = TypeVar("V")


class ThreadSafeDict(MutableMapping[K, V], Generic[K, V]):
    """
    Thread-Safe Dict
    """

    __slots__ = ("_lock", "_data")

    def __init__(self, initial_data: Optional[Dict[K, V]] = None) -> None:
        self._lock = threading.RLock()
        self._data: Dict[K, V] = {} if initial_data is None else initial_data

    def __getitem__(self, key: K) -> V:
        with self._lock:
            return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = value

    def __delitem__(self, key: K) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[K]:
        with self._lock:
            return iter(self._data.copy())

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            return self._data.get(key, default)

    def get_or_set(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            v = self._data.get(key)
            if v is None:
                self._data.setdefault(key, default)
                return self._data.get(key)
            return v

    def pop(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            return self._data.pop(key, default)

    def setdefault(self, key: K, default: Optional[V] = None) -> V:
        with self._lock:
            return self._data.setdefault(key, default)

    def get_or_create(self, key: K, creator: Callable[..., V], *args, **kwargs) -> V:
        with self._lock:
            if key not in self._data:
                self._data[key] = creator(*args, **kwargs)
            return self._data[key]

    def update(
            self,
            m: Optional[Iterable[tuple[K, V]] | Mapping[K, V]] = None,
            /,
            **kwargs: V,
    ) -> None:
        with self._lock:
            if m is not None:
                self._data.update(m)
            if kwargs:
                self._data.update(kwargs)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def keys(self) -> KeysView[K]:
        with self._lock:
            return self._data.keys()

    def values(self) -> ValuesView[V]:
        with self._lock:
            return self._data.values()

    def items(self) -> ItemsView[K, V]:
        with self._lock:
            return self._data.items()

    def __str__(self) -> str:
        with self._lock:
            return str(self._data)

    def __repr__(self) -> str:
        with self._lock:
            return repr(self._data)
