# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.token.base import TokenCounter


class ContextWindowChange(BaseModel):
    """
    Difference between the last LLM-bound ContextWindow and the current one.

    The old messages/tools are kept so AscendAffinity can evict KV cache by
    locating half-open ranges ``[start, end)`` in the window that was already
    sent to inference.
    """
    old_messages: List[BaseMessage] = Field(default_factory=list)
    old_tools: List[ToolInfo] = Field(default_factory=list)
    msg_start: Optional[int] = None
    msg_end: Optional[int] = None
    tools_start: Optional[int] = None
    tools_end: Optional[int] = None

    @property
    def has_change(self) -> bool:
        return self.msg_start is not None or self.tools_start is not None


class ModelContext(ABC):
    """
    Abstract base class for managing conversational context in a model-agnostic way.

    Provides a standard interface for adding, retrieving, filtering, and deriving
    conversation messages, as well as constructing context windows for model inference.
    Supports both string and BaseMessage content, with configurable placeholder
    formats for template interpolation.

    Key Methods
    -----------
    add_messages() : Add messages to context (head or tail)
    pop_messages() : Remove messages from context (head or tail)
    get_messages() : Retrieve messages from context
    get_context_window() : Construct context window for model inference
    """

    @abstractmethod
    def __len__(self):
        """
        Return the length of the context.

        The exact unit (number of messages) is
        implementation-defined and should be documented by each subclass.
        """

    @abstractmethod
    def get_messages(self, size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]:
        """
        Retrieve messages from the conversation context without removing them.

        Parameters
        ----------
        size : int, optional
            Number of messages to retrieve; defaults to all if omitted.
        with_history : bool, optional
            If True, return messages with history; defaults to True.

        Returns
        -------
        List[BaseMessage]
            The retrieved messages in their original order.
        """

    @abstractmethod
    def set_messages(self, messages: List[BaseMessage], with_history: bool = True):
        """
        Replace the current message list with the provided one.

        Parameters
        ----------
        messages : List[BaseMessage]
            New sequence of messages to insert into the window.
        with_history : bool, default True
            - `True`  – replace the concatenated [`context_messages` + `history_messages`].
            - `False` – replace `history_messages` only, leaving `context_messages` intact.
            In both cases the original order of the preserved segments is maintained.

        Returns
        -------
        None
            The window is updated in-place.
        """

    @abstractmethod
    def pop_messages(self, size: int = 1, with_history: bool = True) -> List[BaseMessage]:
        """
        Remove and return the oldest `size` messages from the **current request's message list**
        (i.e., messages added since the context was created/loaded).

        Args:
            size: Number of messages to pop (default 1).
            with_history: If True, also removes the corresponding messages from
                          the underlying persistent history; otherwise only the
                          current-request list is affected.

        Returns:
            List[BaseMessage]: The messages that were removed.
        """

    @abstractmethod
    async def clear_messages(self, with_history: bool = True):
        """
        Remove all messages that have been added in the current turn.

        Args:
            with_history: If True, also wipes the underlying persistent history
                          (i.e., the initial messages loaded from memory or
                          provided at context creation).
                          If False, only the messages accumulated during this
                          request are discarded; the persistent history remains
                          unchanged.
        """

    @abstractmethod
    async def add_messages(self, message: BaseMessage | List[BaseMessage]) -> List[BaseMessage]:
        """
        Add one or more messages to the conversation context.

        Parameters
        ----------
        message : BaseMessage | List[BaseMessage]
            A single message or a list of messages to add.

        Returns
        -------
        List[BaseMessage]
            The updated message list after insertion.
        """

    @abstractmethod
    async def get_context_window(self,
                                 system_messages: List[BaseMessage] = None,
                                 tools: List[ToolInfo] = None,
                                 window_size: Optional[int] = None,
                                 dialogue_round: Optional[int] = None,
                                 **kwargs
                                 ) -> "ContextWindow":
        """
        Build and return a window of messages suitable for model inference.

        Parameters
        ----------
        system_messages : List[BaseMessage], optional
            System-level messages to prepend to the window.
        tools : List[ToolInfo], optional
            Tool definitions to include in the window.
        window_size : int, optional
            Maximum number of historical messages to include; defaults to all if omitted.
        dialogue_round : int, optional
            Number of most-recent conversation rounds to retain. A round is defined
            as starting from a user message and ending at the next assistant message
            that contains no tool calls (i.e., the final response in a tool-using
            turn). Incomplete rounds (user without following assistant) still count
            as one round. Takes precedence over `window_size` when both are specified.
            Must be > 0 if set; None (default) disables round-based truncation.
        **kwargs : dict, optional
            Additional context-specific parameters.

        Returns
        -------
        ContextWindow
            A window object containing the constructed message list and metadata.
        """
        pass

    def detect_context_window_change(
            self,
            new_window: "ContextWindow",
    ) -> Optional[ContextWindowChange]:
        """
        Return the diff from the previous tracked LLM-bound window.

        Implementations may keep a snapshot for KV cache management. The
        default no-op keeps existing ModelContext subclasses compatible.
        """
        return None

    @abstractmethod
    def statistic(self) -> "ContextStats":
        """
        Compute context-wide statistics.

        Returns
        -------
        ContextStats
            Aggregated message and token counts for the context.
        """

    @abstractmethod
    def session_id(self) -> str:
        """
        Return the globally unique identifier of the current user session.
        """

    @abstractmethod
    def context_id(self) -> str:
        """
        Return the globally unique identifier of the current context
        (conversation, request, or task) within the session.
        """

    @abstractmethod
    def token_counter(self) -> TokenCounter:
        """
        Return a TokenCounter instance that can accurately count tokens
        for the model family used by this context.
        """

    def last_context_window_access_at(self) -> float | None:
        """Return the timestamp of the latest context-window access, if tracked."""
        return None

    def set_last_context_window_access_at(self, timestamp: float) -> None:
        """Record the timestamp of the latest context-window access."""
        return

    def context_window_tokens(self) -> int | None:
        """Return the resolved model context-window size in tokens, if available."""
        return None


class ContextStats(BaseModel):
    """
    Token-usage snapshot for any context container
    (ModelContext or ContextWindow).

    Fields
    ------
    total_messages : int
        Sum of system|user|assistant|tool messages.
    total_tokens : int
        Sum of all token fields below.

    Message counts
    --------------
    system_messages / user_messages / assistant_messages / tool_messages : int
        Number of messages for each role.

    total_dialogues : int
        Number of dialogue rounds in the message list. A round is defined as
        user message(s) followed by an assistant message without tool_calls;
        assistant messages with tool_calls plus subsequent tool messages do
        not start a new round until a final assistant reply without tool_calls.

    Token counts
    ------------
    system_message_tokens / user_message_tokens /
    assistant_message_tokens / tool_message_tokens : int
        Tokens consumed by the corresponding message role.

    tools : int
        Number of ToolInfo objects injected into the prompt.
    tool_tokens : int
        Tokens consumed by the injected tools (functions, plugins, etc.).
    """
    total_messages: int = 0
    total_tokens: int = 0
    total_dialogues: int = 0

    system_messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_messages: int = 0
    tools: int = 0

    system_message_tokens: int = 0
    user_message_tokens: int = 0
    assistant_message_tokens: int = 0
    tool_message_tokens: int = 0
    tool_tokens: int = 0


class ContextWindow(BaseModel):
    """
    A lightweight, serializable snapshot of the messages and tools that will
    actually be sent to the LLM endpoint.

    Attributes
    ----------
    system_messages : List[BaseMessage]
        System-level directives (e.g., instructions, personas) that should
        remain at the beginning of the final message list.
    context_messages : List[BaseMessage]
        Conversation history or user inputs that may be truncated, compressed,
        or re-ordered by ContextEngine processors.
    tools : List[ToolInfo]
        Tool definitions (functions, plugins) that the model is allowed to
        invoke during the turn.
    """
    system_messages: List[BaseMessage] = Field(default_factory=list)
    context_messages: List[BaseMessage] = Field(default_factory=list)
    tools: List[ToolInfo] = Field(default_factory=list)
    statistic: "ContextStats" = Field(default_factory=ContextStats)

    def get_messages(self) -> List[BaseMessage]:
        return self.system_messages + self.context_messages

    def get_tools(self) -> List[ToolInfo]:
        return self.tools
