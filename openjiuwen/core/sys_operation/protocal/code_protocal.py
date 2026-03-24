# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Literal, Optional, Dict, Any, AsyncIterator

from openjiuwen.core.sys_operation.result import (
    ExecuteCodeResult,
    ExecuteCodeStreamResult,
)


class BaseCodeProtocal(ABC):
    """Unified Code execution method signatures shared by Operation and Provider layers."""

    @abstractmethod
    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        pass

    @abstractmethod
    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        pass
