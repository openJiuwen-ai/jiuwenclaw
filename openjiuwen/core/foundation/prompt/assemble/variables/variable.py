# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod
from typing import List, Optional


class Variable:
    """Base class for variable."""
    def __init__(self, name: str, input_keys: Optional[List] = None):
        self.name = name
        self.input_keys = input_keys
        self.value = ""

    @abstractmethod
    def update(self, **kwargs):
        """update variable."""

    def eval(self, **kwargs):
        """Validate the input key-values, update `self.value`, perform selection (if there is), and return value.
        Args:
            **kwargs: input key-value pairs for validate the variable.
        Returns:
            str: updated value of variable.
        """
        input_kwargs = self._prepare_inputs(**kwargs)
        self.update(**input_kwargs)
        return self.value

    def _prepare_inputs(self, **kwargs) -> dict:
        """prepare input key-value pairs."""
        input_kwargs = {k: v for k, v in kwargs.items() if k in self.input_keys}
        return input_kwargs

    def prepare_inputs(self, **kwargs) -> dict:
        """prepare input key-value pairs."""
        return self._prepare_inputs(**kwargs)