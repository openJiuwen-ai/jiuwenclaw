# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ConfigDict, field_validator




class BaseModelInfo(BaseModel):
    api_key: str = Field(default="")
    api_base: str = Field(min_length=1)
    model_name: str = Field(default="", alias="model")
    temperature: float = Field(default=0.95)
    top_p: float = Field(default=0.1)
    streaming: bool = Field(default=False, alias="stream")
    timeout: int = Field(default=60, gt=0)
    model_config = ConfigDict(extra='allow')

    @field_validator('model_name', mode='before')
    @classmethod
    def handle_model_name(cls, v, values):
        if not v and 'model' in values.data:
            return values.data['model']
        return v


@dataclass
class ModelConfig:
    model_provider: str
    model_info: BaseModelInfo = field(default_factory=BaseModelInfo)
