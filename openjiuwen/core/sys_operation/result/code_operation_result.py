# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Literal, Dict, Any

from pydantic import BaseModel, Field, ConfigDict

from openjiuwen.core.sys_operation.result import BaseResult


class ExecuteCodeData(BaseModel):
    """Code Execution Result Data Model"""
    code_content: str = Field(..., description="Original code executed")
    language: str = Field(..., description="Programming language of the original code")
    exit_code: Optional[int] = Field(default=None, description="Execution exit code")
    stdout: str = Field(default="", description="The code's standard output (stdout) stream")
    stderr: str = Field(default="", description="The code's standard error (stderr) stream")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExecuteCodeChunkData(BaseModel):
    """Data structure for chunked execute code"""
    text: str = Field(default="", description="Raw content of the output chunk")
    type: Optional[Literal["stdout", "stderr"]] = Field(default=None, description="Type of the output chunk")
    chunk_index: int = Field(..., description="Index of current chunk (starting from 0)")
    exit_code: Optional[int] = Field(default=None, description="Execution exit code")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Data for execution")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExecuteCodeResult(BaseResult[ExecuteCodeData]):
    """ExecuteCmdResult"""
    pass


class ExecuteCodeStreamResult(BaseResult[ExecuteCodeChunkData]):
    """ExecuteCodeStreamResult"""
    pass
