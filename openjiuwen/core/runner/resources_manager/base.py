# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import Awaitable, Callable, Generic, TypeAlias, TypeVar

from pydantic import BaseModel

from openjiuwen.core.multi_agent import BaseTeam, TeamCard
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard

# ============================================================================
# Type Aliases
# ============================================================================

AgentProvider = Callable[['AgentCard'], Awaitable['BaseAgent']] | Callable[['AgentCard'], 'BaseAgent']
"""
Agent provider type definition.

A callable that takes an AgentCard and returns an asynchronous BaseAgent instance.
Used for lazy loading of Agent resources to avoid immediate creation upon registration.
Enables deferred initialization until the agent is actually needed.
"""

AgentTeamProvider = Callable[['TeamCard'], Awaitable['BaseTeam']] | Callable[['TeamCard'], 'BaseTeam']
"""
Agent team provider type definition.

A callable that takes a TeamCard and returns an asynchronous BaseTeam instance.
Used for lazy loading of agent team resources, suitable for complex agent teams 
that require on-demand initialization with specific configurations.
"""

WorkflowProvider = Callable[[WorkflowCard], Awaitable[Workflow]] | Callable[[WorkflowCard], Workflow]
"""
Workflow provider type definition.

A callable that takes a WorkflowCard and returns an asynchronous Workflow instance.
Used for lazy loading of workflow resources, supporting on-demand initialization 
of complex workflow configurations and dependencies.
"""

ModelProvider = Callable[[...], Awaitable[BaseModel]] | Callable[[...], BaseModel]
"""
Model provider type definition.

A callable that accepts variable arguments and returns an asynchronous Model instance.
Used for lazy loading of model resources with flexible configuration parameters.
Note: The ellipsis `...` indicates support for varying numbers and types of configuration arguments,
allowing different model initialization patterns.
"""

Tag = str
"""
Tag type definition for categorizing and filtering resources.

Tags are string identifiers that can be attached to resources for organization, 
categorization, and query operations. Tags should be descriptive to clearly 
indicate resource functionality, status, or ownership.
"""

# ============================================================================
# Special Tag Constants
# ============================================================================

ALL = "*"
"""
Special tag constant representing all resources.

When used in queries or operations, it matches all resources regardless of their actual tags.
Commonly used for batch operations or global filtering scenarios where 
comprehensive resource selection is required.
"""

GLOBAL: Tag = "__global__"
"""
Default tag constant for resources without explicit tagging.

Resources with this tag are considered publicly accessible or uncategorized common resources.
Typically used for system-level or public resources that don't require 
specific categorization.
"""

ACTIVE: Tag = "__active__"
"""
Active state tag constant.

Used to mark resources that are currently active and available for use.
Commonly used in resource management systems to distinguish between 
available and unavailable resources based on their operational status.
"""

INACTIVE: Tag = "__inactive__"
"""
Inactive state tag constant.

Used to mark resources that are currently inactive or temporarily unavailable.
Helps with state management and lifecycle control of resources, 
allowing systematic handling of dormant or disabled resources.
"""


# ============================================================================
# Tag Strategy Enums
# ============================================================================

class TagMatchStrategy(str, Enum):
    """
    Enumeration defining strategies for matching multiple tags when querying or filtering resources.

    These strategies control how tag-based filters apply logical operations
    between multiple tag conditions.
    """
    ALL = "all"
    """
    Full match strategy: Resource must contain ALL specified tags.

    Example: When querying resources with tags ["A", "B"], only resources that 
             contain both tag A AND tag B will be matched.

    Use case: Suitable for precise filtering scenarios where multiple 
              conditions must be simultaneously satisfied.
    """

    ANY = "any"
    """
    Partial match strategy: Resource must contain ANY of the specified tags.

    Example: When querying resources with tags ["A", "B"], resources containing 
             either tag A OR tag B will be matched.

    Use case: Suitable for broad filtering scenarios where meeting any 
              condition is sufficient.
    """


class TagUpdateStrategy(str, Enum):
    """
    Enumeration defining strategies for updating resource tags.

    These strategies control how new tags interact with existing tags
    during update operations, providing different merge/replace semantics.
    """
    MERGE = "merge"
    """
    Merge strategy: Combine new tags with existing tags, removing duplicates.

    When this strategy is applied:
    - New tags are unioned with existing tags
    - Duplicate tags are automatically deduplicated
    - Tag order is not guaranteed to be preserved

    Example: If a resource has existing tags ["A", "B"] and new tags ["B", "C"] 
             are merged, the result will be ["A", "B", "C"] (duplicate "B" is removed).

    Use case: When you want to add new tags to a resource without removing 
              existing tags, maintaining accumulated tag history.
    """

    REPLACE = "replace"
    """
    Replace strategy: Completely replace all existing tags with new tags.

    When this strategy is applied:
    - All existing tags are removed
    - New tags become the complete set of tags for the resource
    - This effectively resets the resource's tagging state

    Example: If a resource has existing tags ["A", "B", "C"] and new tags ["X", "Y"] 
             replace them, the result will be ["X", "Y"] (previous tags are completely removed).

    Use case: When you need to completely redefine a resource's tags, 
              such as during reclassification or ownership changes.
    """


# ============================================================================
# Result Type for Error Handling
# ============================================================================

T = TypeVar("T")
"""Type variable representing the success value type in Result types."""

E = TypeVar("E")
"""Type variable representing the error value type in Result types."""


class Ok(Generic[T]):
    """
    Represents a successful operation result.

    This class encapsulates a successful return value in a type-safe manner,
    following the Result pattern for explicit error handling.
    """

    def __init__(self, value: T) -> None:
        """
        Initialize an Ok result with a success value.

        Args:
            value: The successful result value to encapsulate.
        """
        self._value = value

    def is_ok(self) -> bool:
        """
        Check if the result represents success.

        Returns:
            True always, since this is an Ok instance.
        """
        return True

    def is_err(self) -> bool:
        """
        Check if the result represents an error.

        Returns:
            False always, since this is an Ok instance.
        """
        return False

    def msg(self) -> T:
        """
        Get the success message/value.

        Returns:
            The encapsulated success value.

        Note: This naming might be confusing - consider renaming to `value()`
              for clarity, unless "msg" is a established convention in your codebase.
        """
        return self._value


class Error(Generic[E]):
    """
    Represents a failed operation result.

    This class encapsulates an error value in a type-safe manner,
    following the Result pattern for explicit error handling.
    """

    def __init__(self, error: E = None) -> None:
        """
        Initialize an Error result with an error value.

        Args:
            error: The error value to encapsulate.
        """
        self._error = error

    def is_ok(self) -> bool:
        """
        Check if the result represents success.

        Returns:
            False always, since this is an Error instance.
        """
        return False

    def is_err(self) -> bool:
        """
        Check if the result represents an error.

        Returns:
            True always, since this is an Error instance.
        """
        return True

    def msg(self) -> E:
        """
        Get the error message/value.

        Returns:
            The encapsulated error value.

        Note: This naming might be confusing - consider renaming to `error()`
              for consistency with the actual error nature.
        """
        return self._error

    def error(self) -> E:
        """
        Get the error value (alternative to msg()).

        Returns:
            The encapsulated error value.

        Note: Having both msg() and error() methods that return the same thing
              might be redundant. Consider consolidating to one method.
        """
        return self._error


Result: TypeAlias = Ok[T] | Error[E]
"""
Result type alias for operation outcomes using the Result pattern.

Represents either a successful result (Ok[T]) or an error result (Error[E]).
This pattern provides explicit error handling without exceptions,
making error states part of the type system and API contracts.

Usage examples:
    - Function returns Result[str, int] means it returns either Ok[str] or Error[int]
    - Enables compile-time checking of error handling
    - Forces explicit handling of both success and error cases
"""
