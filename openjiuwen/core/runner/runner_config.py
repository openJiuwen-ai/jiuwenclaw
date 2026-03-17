# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from dataclasses import (
    dataclass,
    field,
)
from enum import Enum
from typing import Optional

from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig


class MessageQueueType(str, Enum):
    PULSAR = "pulsar"
    FAKE = "fake"


@dataclass
class PulsarConfig:
    url: Optional[str] = None
    max_workers: int = 8


@dataclass
class MessageQueueConfig:
    """Message Queue Configuration"""
    type: str = MessageQueueType.PULSAR
    pulsar_config: Optional[PulsarConfig] = None


@dataclass
class DistributedConfig:
    """Distributed Configuration"""
    request_timeout: float = 30.0
    max_request_concurrency: int = 10000
    message_queue_config: MessageQueueConfig = field(default_factory=MessageQueueConfig)
    agent_topic_template = "openjiuwen.single_agent.{agent_id}.{version}"
    reply_topic_template = "openjiuwen.reply.runner.{instance_id}"

    def get_agent_topic_template(self, env_prefix: str = "") -> str:
        """Get single_agent topic template with environment prefix"""
        if env_prefix:
            return f"{env_prefix}.{self.agent_topic_template}"
        return self.agent_topic_template

    def get_reply_topic_template(self, env_prefix: str = "") -> str:
        """Get reply topic template with environment prefix"""
        if env_prefix:
            return f"{env_prefix}.{self.reply_topic_template}"
        return self.reply_topic_template


@dataclass
class RunnerConfig:
    """Runner Global Configuration"""
    distributed_mode: bool = True
    distributed_config: Optional[DistributedConfig] = field(default_factory=DistributedConfig)
    env_prefix: str = ""
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    checkpointer_config: Optional[CheckpointerConfig] = None

    def agent_topic_template(self) -> str:
        """Get single_agent topic template with environment prefix"""
        return self.distributed_config.get_agent_topic_template(self.env_prefix)

    def reply_topic_template(self) -> str:
        """Get reply topic template with environment prefix"""
        return self.distributed_config.get_reply_topic_template(self.env_prefix)


DEFAULT_RUNNER_CONFIG = RunnerConfig(
    distributed_mode=False,
    distributed_config=DistributedConfig(
        request_timeout=30.0,
        message_queue_config=MessageQueueConfig(
            type=MessageQueueType.FAKE,
        )
    ),
)

_global_config: Optional[RunnerConfig] = None


def set_runner_config(cfg: RunnerConfig):
    global _global_config
    _global_config = cfg


def get_runner_config() -> RunnerConfig:
    global _global_config
    if _global_config is None:
        _global_config = DEFAULT_RUNNER_CONFIG
    return _global_config
