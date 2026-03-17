# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional

from openjiuwen.core.foundation.llm import Model, ModelRequestConfig, ModelClientConfig


class BasePromptBuilder(ABC):
    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        self._model: Model = Model(model_client_config, model_config)

    @abstractmethod
    async def build(self,
                    *args,
                    **kwargs
                    ) -> Optional[str]:
        raise NotImplementedError()

    @abstractmethod
    async def stream_build(self,
                           *args,
                           **kwargs
                           ) -> AsyncGenerator:
        raise NotImplementedError()