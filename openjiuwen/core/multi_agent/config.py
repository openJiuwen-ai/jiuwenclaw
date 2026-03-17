# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Group Configuration Module

This module defines the runtime configuration for agent groups.
"""

from pydantic import BaseModel, Field


class GroupConfig(BaseModel):
    """Group Runtime Configuration

    Mutable runtime parameters for agent group execution.
    Follows the same pattern as ReActAgentConfig.

    Attributes:
        max_agents: Maximum number of agents allowed in group
        max_concurrent_messages: Maximum concurrent message processing
        message_timeout: Timeout for message processing (seconds)
    """
    max_agents: int = Field(
        default=10,
        description="Maximum number of agents in group"
    )
    max_concurrent_messages: int = Field(
        default=100,
        description="Maximum concurrent messages"
    )
    message_timeout: float = Field(
        default=30.0,
        description="Message timeout in seconds"
    )

    model_config = {"extra": "allow"}

    def configure_max_agents(self, max_agents: int) -> 'GroupConfig':
        """Configure maximum agents

        Args:
            max_agents: Maximum number of agents

        Returns:
            self (supports chaining)
        """
        self.max_agents = max_agents
        return self

    def configure_timeout(self, timeout: float) -> 'GroupConfig':
        """Configure message timeout

        Args:
            timeout: Timeout in seconds

        Returns:
            self (supports chaining)
        """
        self.message_timeout = timeout
        return self

    def configure_concurrency(
        self,
        max_concurrent: int
    ) -> 'GroupConfig':
        """Configure concurrency limit

        Args:
            max_concurrent: Maximum concurrent messages

        Returns:
            self (supports chaining)
        """
        self.max_concurrent_messages = max_concurrent
        return self
