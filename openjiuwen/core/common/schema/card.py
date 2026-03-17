# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid

from pydantic import BaseModel, Field


class BaseCard(BaseModel):
    """数字名片基类

    Attributes:
        id: 唯一标识符
        name: 名称，也是在某个 namespace 中的唯一标识符
        description: 功能、适用场景等描述信息
    """
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str = Field(default='')
    description: str = Field(default='')

    def tool_info(self):
        ...

    def str(self):
        return f'id={self.id},name={self.name}'
