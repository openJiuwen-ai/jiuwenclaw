# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenAI Model Wrapper for AHE evaluator.

Provides a simple wrapper around AsyncOpenAI client to match
the expected interface for TraceOutcomeEvaluator.
"""

from __future__ import annotations

from typing import Any


class OpenAIModelWrapper:
    """Wrapper for AsyncOpenAI client to match expected interface.

    The evaluator expects a model with an `invoke(messages)` method
    that returns a response with a `.content` attribute. This wrapper
    adapts AsyncOpenAI to that interface.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> None:
        """Initialize the wrapper.

        Args:
            client: AsyncOpenAI client instance
            model: Model name (e.g., "gpt-4")
            temperature: Generation temperature
            max_tokens: Maximum tokens in response
        """
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def invoke(self, messages: list[Any], tools: list[dict] | None = None) -> Any:
        """Invoke the model with messages and optional tools.

        Args:
            messages: List of message objects (can be dict or object with .role/.content)
            tools: Optional list of OpenAI function schemas to enable tool calling

        Returns:
            Response object with .content attribute and .tool_calls if tools provided
        """
        # Convert messages to OpenAI format
        formatted_messages = []
        for msg in messages:
            # Handle dict messages (from ReAct loop)
            if isinstance(msg, dict):
                formatted_messages.append(msg)
                continue

            # Handle object messages (SimpleMessage from _call_llm)
            msg_type = str(type(msg))
            if "SystemMessage" in msg_type or "system" in msg_type.lower():
                role = "system"
            elif "UserMessage" in msg_type or "user" in msg_type.lower():
                role = "user"
            else:
                # Fallback: try to get role attribute or default to user
                role = getattr(msg, "role", "user")

            # Get content
            content = msg.content if hasattr(msg, "content") else str(msg)
            formatted_messages.append({"role": role, "content": content})

        # Build API request parameters
        params = {
            "model": self._model,
            "messages": formatted_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout": 60.0,  # Add 60 second timeout to prevent hanging
        }

        # Add tools if provided
        if tools:
            params["tools"] = tools

        # Call OpenAI API with timeout protection
        try:
            response = await self._client.chat.completions.create(**params)
        except Exception as exc:
            # Log timeout or other API errors with full stack trace
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "OpenAIModelWrapper.invoke failed: %s",
                exc,
                exc_info=True,  # Print full stack trace
            )
            # Raise exception to let caller handle the error explicitly
            raise

        # Defensive check: verify response has choices
        if not response.choices:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "OpenAI API returned empty choices. Model: %s, Messages: %d",
                self._model,
                len(messages),
            )
            # Return empty content but with proper structure
            class EmptyResponse:
                content = ""
                tool_calls = None
                choices = []
            return EmptyResponse()

        # Extract message from response
        message = response.choices[0].message

        # Create wrapper that exposes both .content and .choices for compatibility
        # Some callers use response.content, others use response.choices[0].message.content
        class SimpleResponse:
            """Response wrapper compatible with both .content and .choices access patterns."""
            def __init__(self, content: str, tool_calls: list | None, original_message: Any):
                self.content = content
                self.tool_calls = tool_calls
                # Create a fake choices structure for compatibility
                class FakeChoice:
                    def __init__(self, msg):
                        self.message = msg
                self.choices = [FakeChoice(original_message)]

        return SimpleResponse(
            content=message.content or "",
            tool_calls=message.tool_calls if hasattr(message, "tool_calls") else None,
            original_message=message,
        )