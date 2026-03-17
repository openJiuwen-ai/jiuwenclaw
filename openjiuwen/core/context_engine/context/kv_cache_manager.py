# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.context_engine.base import ContextWindow


class KVCacheManager:
    def __init__(self, session_id: str):
        self._session_id = session_id
        self._last_context_window: Optional[ContextWindow] = None

    async def release(self, context_window: ContextWindow, **kwargs):
        model = kwargs.get("model")
        if model is None or not isinstance(model, InferenceAffinityModel):
            return

        if not self._last_context_window:
            self._last_context_window = context_window
            return

        should_release, messages_released_index, tools_released_index = self._check_release_needed(context_window)

        if should_release and (messages_released_index is not None or tools_released_index is not None):
            kwargs = {}

            if tools_released_index is not None:
                kwargs["tools"] = self._last_context_window.get_tools()
                kwargs["tools_released_index"] = tools_released_index

            result = await model.release(
                model=model.model_config.model_name,
                session_id=self._session_id,
                messages=self._last_context_window.get_messages(),
                messages_released_index=messages_released_index,
                **kwargs
            )

        self._last_context_window = context_window

    def _check_release_needed(self, context_window: ContextWindow) -> Tuple[bool, Optional[int], Optional[int]]:
        """Check if cache release is needed

        Returns:
            (should_release, messages_released_index, tools_released_index)
            - should_release: Whether cache needs to be released
            - messages_released_index: Index to release from, or len(previous) if prefix matches
            - tools_released_index: Index to release from, or len(previous) if prefix matches
        """
        should_release = False
        msg_idx = None
        tool_idx = None

        # Check messages
        prev_msgs = self._last_context_window.get_messages() or []
        curr_msgs = context_window.get_messages() or []

        if prev_msgs:
            msg_idx = len(prev_msgs)  # Default: no release needed
            for idx in range(min(len(prev_msgs), len(curr_msgs))):
                if prev_msgs[idx] != curr_msgs[idx]:
                    should_release = True
                    msg_idx = idx
                    logger.info(f"  [RELEASE REASON] Message modified at index {idx}")
                    break

        # Check tools
        prev_tools = self._last_context_window.get_tools() or []
        curr_tools = context_window.get_tools() or []

        if prev_tools:
            tool_idx = len(prev_tools)  # Default: no release needed
            for idx in range(min(len(prev_tools), len(curr_tools))):
                if prev_tools[idx] != curr_tools[idx]:
                    should_release = True
                    tool_idx = idx
                    logger.info(f"  [RELEASE REASON] Tool modified at index {idx}")
                    break

        return should_release, msg_idx, tool_idx