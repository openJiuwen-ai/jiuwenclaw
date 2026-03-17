# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Utility Functions

Provides simplified access to callback framework singleton.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework


def get_callback_framework() -> "AsyncCallbackFramework":
    """Get the callback framework singleton.

    Returns:
        The AsyncCallbackFramework instance from Runner.

    Raises:
        AttributeError: If Runner or callback_framework is not initialized.
    """
    from openjiuwen.core.runner import Runner

    return Runner.callback_framework


class _LazyFrameworkProxy:
    """Lazy proxy that defers ``get_callback_framework()`` until runtime.

    Safe to instantiate at module level without triggering circular imports.

    ``emit_before`` / ``emit_after`` are provided explicitly so that the
    decorator *creation* (which happens at class-definition / import time)
    does not resolve the real framework.  The decorators produced by
    ``create_emit_before_decorator`` / ``create_emit_after_decorator`` only
    call ``framework.trigger(...)`` inside the wrapper — at that point all
    modules are fully loaded and ``__getattr__`` can safely resolve.
    """

    def emit_before(self, event, *, pass_args=True, extra_kwargs=None):
        from openjiuwen.core.runner.callback.decorator import create_emit_before_decorator
        return create_emit_before_decorator(
            self, event, pass_args=pass_args, extra_kwargs=extra_kwargs,
        )

    def emit_after(
        self, event, *, result_key="result", item_key="item",
        pass_args=False, stream_mode="per_item", extra_kwargs=None,
    ):
        from openjiuwen.core.runner.callback.decorator import create_emit_after_decorator
        return create_emit_after_decorator(
            self, event,
            result_key=result_key, item_key=item_key,
            pass_args=pass_args, stream_mode=stream_mode,
            extra_kwargs=extra_kwargs,
        )

    @staticmethod
    def __getattr__(name):
        return getattr(get_callback_framework(), name)


lazy_callback_framework = _LazyFrameworkProxy()


async def trigger(event, **kwargs):
    """Trigger a callback event.

    Args:
        event: The event from the Events class (e.g., LLMCallEvents.LLM_CALL_STARTED)
        **kwargs: Additional arguments passed to the trigger call.
    """
    fw = get_callback_framework()
    await fw.trigger(event, **kwargs)
