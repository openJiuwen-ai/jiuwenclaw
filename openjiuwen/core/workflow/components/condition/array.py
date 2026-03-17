# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, Any

from openjiuwen.core.workflow.components.condition.condition import Condition
from openjiuwen.core.session import BaseSession
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.common.constants.constant import INDEX

DEFAULT_MAX_LOOP_NUMBER = 1000


class ArrayCondition(Condition):
    def __init__(self, arrays: dict[str, Union[str, list[Any]]]):
        super().__init__(arrays)
        self._arrays = arrays

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        current_idx = session.state().get(INDEX)
        min_length = DEFAULT_MAX_LOOP_NUMBER
        updates: dict[str, Any] = {}
        for key, array_info in self._arrays.items():
            arr = inputs.get(key, [])
            min_length = min(len(arr), min_length)
            if current_idx >= min_length:
                return False
            updates[key] = arr[current_idx]
        session.state().update(updates)
        io_updates = updates.copy()
        return True, io_updates


class ArrayConditionInSession(Condition):
    def __init__(self, arrays: dict[str, Union[list[Any], tuple[Any]]]):
        super().__init__()
        self._arrays = arrays
        self._min_length = self._check_arrays(arrays)

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        current_idx = session.state().get(INDEX)
        if current_idx >= self._min_length:
            return False

        updates: dict[str, Any] = {}
        for key, array_info in self._arrays.items():
            try:
                if not isinstance(array_info, (list, tuple)):
                    raise ValueError(
                        f"Expected list/tuple for '{key}' in loop_array, got {type(array_info).__name__}")

                updates[key] = array_info[current_idx]
            except (TypeError, IndexError, KeyError) as e:
                raise ValueError(f"Array loop error in '{key}': <error_details>") from e
        session.state().update(updates)
        io_updates = updates.copy()
        return True, io_updates

    def _check_arrays(self, arrays: dict[str, Union[list[Any], tuple[Any]]]) -> int:
        min_length = DEFAULT_MAX_LOOP_NUMBER
        for key, array_info in arrays.items():
            if array_info is None:
                raise ValueError(f"Value for key '{key}' in loop_array cannot be None")
            if not isinstance(array_info, (list, tuple)):
                raise ValueError(f"Expected list/tuple for '{key}' in loop_array, got {type(array_info).__name__}")
            min_length = min(len(array_info), min_length)
        return min_length
