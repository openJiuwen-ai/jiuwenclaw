from __future__ import annotations

import json
from typing import Any, Dict, Iterable, AsyncIterable, List, Optional


def to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    raise TypeError(f"Unsupported chunk type: {type(obj)}")


class ChatStreamAggregator:
    def __init__(self):
        self.id: Optional[str] = None
        self.created: Optional[int] = None
        self.model: Optional[str] = None
        self.usage: Optional[Dict[str, Any]] = None

        self._choices: Dict[int, Dict[str, Any]] = {}

    def _get_choice_state(self, index: int) -> Dict[str, Any]:
        if index not in self._choices:
            self._choices[index] = {
                "index": index,
                "role": None,
                "content_parts": [],
                "reasoning_parts": [],
                "finish_reason": None,
                "logprobs": None,
                "function_call": None,
                "tool_calls": {},
            }
        return self._choices[index]

    def add_chunk_and_get_events(self, chunk: Any) -> List[Dict[str, Any]]:
        """
        关键函数：
        1. 把当前 chunk 合并进完整结果
        2. 同时返回当前 chunk 对应的增量事件，方便前端实时渲染
        """
        chunk_dict = to_dict(chunk)

        self.id = self.id or chunk_dict.get("id")
        self.created = self.created or chunk_dict.get("created")
        self.model = self.model or chunk_dict.get("model")

        events: List[Dict[str, Any]] = []

        if chunk_dict.get("usage") is not None:
            self.usage = chunk_dict["usage"]

        choices = chunk_dict.get("choices") or []

        # 注意：include_usage=True 时，最后一个 chunk 可能是 choices=[]，只有 usage。
        if not choices:
            if chunk_dict.get("usage") is not None:
                events.append({
                    "type": "usage",
                    "usage": chunk_dict["usage"],
                })
            return events

        for choice in choices:
            index = choice.get("index", 0)
            state = self._get_choice_state(index)

            if choice.get("finish_reason") is not None:
                state["finish_reason"] = choice.get("finish_reason")

            if choice.get("logprobs") is not None:
                state["logprobs"] = choice.get("logprobs")

            delta = choice.get("delta") or {}

            role = delta.get("role")
            if role:
                state["role"] = role

            content = delta.get("content")
            if content:
                state["content_parts"].append(content)

                events.append({
                    "type": "answer_delta",
                    "index": index,
                    "content": content,
                })

            reasoning_content = delta.get("reasoning_content")
            if reasoning_content:
                state["reasoning_parts"].append(reasoning_content)

                events.append({
                    "type": "reasoning_delta",
                    "index": index,
                    "content": reasoning_content,
                })

            function_call = delta.get("function_call")
            if function_call:
                self._merge_function_call(state, function_call)

                events.append({
                    "type": "function_call_delta",
                    "index": index,
                    "delta": function_call,
                })

            tool_calls = delta.get("tool_calls") or []
            for tool_call_delta in tool_calls:
                self._merge_tool_call(state, tool_call_delta)

                events.append({
                    "type": "tool_call_delta",
                    "index": index,
                    "delta": tool_call_delta,
                })

        return events

    def _merge_function_call(self, state: Dict[str, Any], function_call: Dict[str, Any]) -> None:
        if state["function_call"] is None:
            state["function_call"] = {
                "name": None,
                "arguments": "",
            }

        if function_call.get("name"):
            state["function_call"]["name"] = function_call["name"]

        if function_call.get("arguments"):
            state["function_call"]["arguments"] += function_call["arguments"]

    def _merge_tool_call(self, state: Dict[str, Any], tool_call_delta: Dict[str, Any]) -> None:
        tool_index = tool_call_delta.get("index", 0)

        if tool_index not in state["tool_calls"]:
            state["tool_calls"][tool_index] = {
                "index": tool_index,
                "id": None,
                "type": None,
                "function": {
                    "name": None,
                    "arguments": "",
                },
            }

        current = state["tool_calls"][tool_index]

        if tool_call_delta.get("id"):
            current["id"] = tool_call_delta["id"]

        if tool_call_delta.get("type"):
            current["type"] = tool_call_delta["type"]

        function_delta = tool_call_delta.get("function") or {}

        if function_delta.get("name"):
            current["function"]["name"] = function_delta["name"]

        if function_delta.get("arguments"):
            current["function"]["arguments"] += function_delta["arguments"]

    def build(self) -> Dict[str, Any]:
        final_choices = []

        for index in sorted(self._choices.keys()):
            state = self._choices[index]

            message = {
                "role": state["role"] or "assistant",
                "content": "".join(state["content_parts"]),
            }

            reasoning_content = "".join(state["reasoning_parts"])
            if reasoning_content:
                message["reasoning_content"] = reasoning_content

            if state["function_call"] is not None:
                message["function_call"] = state["function_call"]

            if state["tool_calls"]:
                message["tool_calls"] = [
                    state["tool_calls"][i]
                    for i in sorted(state["tool_calls"].keys())
                ]

            final_choices.append({
                "index": index,
                "message": message,
                "finish_reason": state["finish_reason"],
                "logprobs": state["logprobs"],
            })

        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": final_choices,
            "usage": self.usage,
        }

async def async_stream_with_aggregation(
    stream: AsyncIterable[Any],
):
    """
    一边读取 LLM stream，一边 yield 增量事件。
    最后 yield done，并携带完整聚合后的非流式结构。
    """
    aggregator = ChatStreamAggregator()

    try:
        async for chunk in stream:
            events = aggregator.add_chunk_and_get_events(chunk)

            for event in events:
                yield event

        final_result = aggregator.build()

        yield {
            "type": "done",
            "result": final_result,
        }

    except Exception as e:
        yield {
            "type": "error",
            "message": str(e),
        }