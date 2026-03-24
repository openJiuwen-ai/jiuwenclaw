# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncIterator

from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult,
    ExecuteCmdStreamResult,
)


class BaseShellProtocal(ABC):
    """Unified Shell method signatures shared by Operation and Provider layers."""

    @abstractmethod
    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        pass

    @abstractmethod
    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        pass
