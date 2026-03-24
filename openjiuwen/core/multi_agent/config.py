# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team Configuration Module

Defines the runtime configuration for agent teams.
"""

from pydantic import BaseModel, Field


class TeamConfig(BaseModel):
    """Mutable runtime parameters for an agent team.

    Attributes:
        max_agents: Maximum number of agents allowed in the team
        max_concurrent_messages: Maximum concurrent message processing
        message_timeout: Message processing timeout in seconds
    """
    max_agents: int = Field(
        default=10,
        description="Maximum number of agents in team"
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

    def configure_max_agents(self, max_agents: int) -> 'TeamConfig':
        """Configure maximum agents

        Args:
            max_agents: Maximum number of agents

        Returns:
            self (supports chaining)
        """
        self.max_agents = max_agents
        return self

    def configure_timeout(self, timeout: float) -> 'TeamConfig':
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
    ) -> 'TeamConfig':
        """Configure concurrency limit

        Args:
            max_concurrent: Maximum concurrent messages

        Returns:
            self (supports chaining)
        """
        self.max_concurrent_messages = max_concurrent
        return self
