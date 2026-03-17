# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, List, Dict, Any
import json

from openjiuwen.core.foundation.llm import BaseMessage


class ContextUtils:
    """
    Utility helper functions for manipulating and parsing conversation contexts.
    All methods are static and stateless.
    """

    @staticmethod
    def find_last_ai_message_without_tool_call(
            messages: List[BaseMessage],
    ) -> Optional[int]:
        """
        Return the index of the most-recent **assistant** message that
        **does not** contain any tool-call field.

        Search is performed backwards (newest → oldest).
        If no qualifying message exists, return None.
        """
        if not messages:
            return None

        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.role == "assistant":
                if not getattr(msg, "tool_call", None):
                    return idx
        return None

    @staticmethod
    def replace_messages(
            messages: List[BaseMessage],
            target_messages: List[BaseMessage],
            start_index: int,
            end_index: int,
    ) -> List[BaseMessage]:
        """
        Return a **new** list where the slice
        `messages[start_index : end_index + 1]`
        is replaced by `target_messages`.

        Works for single or multiple message replacement.
        Raises IndexError if indices are out of range or inverted.
        """
        if start_index < 0 or end_index >= len(messages) or start_index > end_index:
            raise IndexError("Invalid start/end index")

        return messages[:start_index] + target_messages + messages[end_index + 1:]

    @staticmethod
    def format_reloaded_messages(
            offload_handle: str,
            messages: List[BaseMessage]
    ):
        """
        Format a list of reloaded messages into a human-readable string for LLM consumption.

        This method creates a structured text representation of messages that were
        previously offloaded and have now been retrieved. The formatted output
        includes the offload handle for traceability and each message serialized
        as JSON for structured parsing by the model.

        Args:
            offload_handle: The unique identifier of the offloaded content being
                restored. Used to correlate the reloaded content with its original
                offload marker (e.g., [[OFFLOAD: handle=xxx, type=...]]).
            messages: List of BaseMessage objects that have been retrieved from
                external storage and need to be presented back to the LLM.

        Returns:
            A formatted string containing the handle reference and serialized
            messages, suitable for injection back into the conversation context.
        """
        formatted_content = f"reload messages with handle={offload_handle}:\n"
        for i, msg in enumerate(messages, 1):
            formatted_content += f"message {i}: "
            formatted_content += json.dumps(msg.model_dump(), ensure_ascii=False)
            if i != len(messages):
                formatted_content += "\n"
        return formatted_content

    @staticmethod
    def find_all_dialogue_round(messages: List[BaseMessage]) -> List[List[Optional[int]]]:
        """
        Build all dialogue round boundaries by scanning messages from end to start.

        A round is defined as: user message → next assistant message without tool_calls.
        Incomplete rounds (no final assistant) still count as one round.

        Args:
            messages: List of BaseMessage objects

        Returns:
            List of rounds, each round is [user_idx, assistant_idx].
            assistant_idx may be None for incomplete rounds.
            Order is from newest round to oldest (index 0 = last round).
        """
        rounds: List[List[Optional[int]]] = []
        i = len(messages) - 1

        while i >= 0:
            # Find the closing assistant of this round (may not exist)
            assistant_idx = None

            # Skip any trailing non-assistant messages (shouldn't happen in valid data)
            while i >= 0 and messages[i].role != "assistant":
                i -= 1

            if i >= 0:
                # Found assistant, check if it has tool_calls
                msg = messages[i]
                # tool_calls indicated by content type or metadata (adapt as needed)
                has_tool_calls = (
                    msg.role == "assistant"
                    and hasattr(msg, "tool_calls")
                    and msg.tool_calls
                )

                if not has_tool_calls:
                    # This assistant closes a round
                    assistant_idx = i

                # Move to find the user that started this round
                i -= 1

            # Now find the user message that starts this round
            while i >= 0 and messages[i].role != "user":
                i -= 1

            if i < 0:
                # No user found, incomplete round at start
                break

            user_idx = i
            if not rounds:
                # Find incomplete round
                for last_round_index in range(len(messages) - 1, user_idx, -1):
                    if messages[last_round_index].role == "user":
                        rounds.append([last_round_index, None])
                        break

            rounds.append([user_idx, assistant_idx])
            i -= 1  # Continue to previous round

        return rounds

    @staticmethod
    def find_last_n_dialogue_round(
            messages: List[BaseMessage],
            n: int
    ) -> int:
        """
        Find the starting index of the n-th conversation round from the end.

        A round is defined as: user message → next assistant message without tool_calls.
        Incomplete rounds (no final assistant) still count as one round.

        Args:
            messages: List of BaseMessage objects
            n: Which round from the end (1 = last round, 2 = second-to-last, etc.)

        Returns:
            Starting index of the target round in the messages list

        Raises:
            ValueError: If n <= 0 or fewer than n rounds exist
        """
        rounds = ContextUtils.find_all_dialogue_round(messages)
        if not rounds:
            return -1
        target_round = rounds[min(n, len(rounds)) - 1]
        return target_round[0]