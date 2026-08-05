"""Exception types for the search agent loop.

``ContextExhaustedError`` is raised by the LLM client when the conversation
exceeds the model's context window (a non-retryable condition). The ReAct loop
catches it and treats it as a successful termination, preserving the trajectory
gathered so far instead of retrying the oversized call.
"""


class ContextExhaustedError(Exception):
    """Raised when the LLM reports the context window is exceeded.

    Non-retryable: the ReAct loop catches this and stops, keeping any partial
    results already collected rather than retrying the oversized call.
    """

    pass


__all__ = ["ContextExhaustedError"]
