# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field, field_validator
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.memory.common.crypto import AES_KEY_LENGTH
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class MemoryEngineConfig(BaseModel):
    default_model_cfg: ModelRequestConfig = Field(default=None)
    default_model_client_cfg: ModelClientConfig = Field(default=None)
    forbidden_variables: str = Field(default="")  # forbidden variables config, split by comma
    input_msg_max_len: int = Field(default=8192)  # max length of input message
    crypto_key: bytes = Field(default=b'')  # aes key, length must be 32, not enable encrypt memory if empty
    single_turn_history_summary_max_token: int = Field(default=128, gt=0)

    @field_validator('crypto_key')
    @classmethod
    def check_crypto_key(cls, v: bytes) -> bytes:
        if len(v) == 0:
            return b''

        if len(v) == AES_KEY_LENGTH:
            return v

        raise build_error(
            StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
            config_type="crypto_key",
            error_msg=f"crypto_key must be empty or {AES_KEY_LENGTH} bytes length",
        )


class MemoryScopeConfig(BaseModel):
    model_cfg: ModelRequestConfig = Field(default=None)
    model_client_cfg: ModelClientConfig = Field(default=None)
    embedding_cfg: EmbeddingConfig = Field(default=None)


class AgentMemoryConfig(BaseModel):
    mem_variables: list[Param] = Field(default_factory=list)  # memory variables config
    enable_long_term_mem: bool = Field(default=True)  # enable long term memory or not
    enable_user_profile: bool = Field(default=True)  # enable user profile memory or not
    enable_semantic_memory: bool = Field(default=True)  # enable semantic memory or not
    enable_episodic_memory: bool = Field(default=True)  # enable episodic memory or not
    enable_summary_memory: bool = Field(default=True)  # enable summary memory or not
