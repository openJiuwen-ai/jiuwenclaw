# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, Type, Union
from pydantic import BaseModel, Field



class ToolInfo(BaseModel):
    type: str = Field(default="function")
    name: str = Field(default="")
    description: str = Field(default="")
    parameters: Union[Dict[str, Any], Type[BaseModel]] = Field(default_factory=dict)


class McpToolInfo(ToolInfo):
    server_name: str