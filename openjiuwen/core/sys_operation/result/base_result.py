# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC
from typing import Generic, TypeVar, Optional, Type, Dict, Any

from pydantic import BaseModel, Field, ConfigDict

from openjiuwen.core.common.exception.codes import StatusCode

T = TypeVar('T')
ResultType = TypeVar('ResultType')


class BaseResult(BaseModel, Generic[T], ABC):
    """BaseResult"""
    code: int = Field(..., description="Status code: 0 = success, non-0 = failure")
    message: str = Field(..., description="Message details")
    data: Optional[T] = Field(None, description="Business data (returned only on success)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


def build_operation_error_result(
        error_type: StatusCode,
        msg_format_kwargs: Dict[str, Any],
        result_cls: Type[ResultType],
        data: Optional[Any] = None,
        **kwargs
) -> ResultType:
    """
    Create a standardized error result object with specified error type and formatted message.
    Generate a concrete ResultType subclass instance that contains error code,
    formatted error message, optional data and other custom key-value pairs.

    Args:
        error_type: StatusCode enum type, contains error code and original error message template
        msg_format_kwargs: Dictionary of key-value pairs for formatting the error message template
        result_cls: Concrete subclass of ResultType, used to instantiate the final result object
        data: Optional additional data to carry in the error result, default is None
        **kwargs: Custom key-value pairs to extend the result object, will override default fields if key conflicts

    Returns:
        ResultType: Instantiated error result object of the specified result_cls type
    """
    error_message = error_type.errmsg.format(**msg_format_kwargs)
    base_kwargs = {"code": error_type.code, "message": error_message, "data": data}
    final_kwargs = {**base_kwargs, **kwargs}
    return result_cls(**final_kwargs)
