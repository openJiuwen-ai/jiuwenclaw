# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human, JSON, and JSONL renderers for one Runtime event stream."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from jiuwenswarm.runtime.events import RuntimeEvent


def _event_text(payload: dict[str, Any]) -> str:
    for key in ("delta", "content", "text", "message", "answer"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class EventRenderer:
    """Render events without influencing Runtime execution."""

    def __init__(
        self,
        output_format: str,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        show_reasoning: bool = False,
        show_tools: bool = False,
    ) -> None:
        self.output_format = output_format
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.show_reasoning = show_reasoning
        self.show_tools = show_tools
        self.events: list[dict[str, Any]] = []
        self._wrote_delta = False
        self.failed = False

    def render(self, event: RuntimeEvent) -> None:
        data = event.to_dict()
        self.events.append(data)
        self.failed = self.failed or not event.ok or event.event_type in {
            "chat.error",
            "runtime.error",
            "team.error",
        }
        if self.output_format == "jsonl":
            self.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
            self.stdout.flush()
            return
        if self.output_format == "json":
            return
        self._render_human(event)

    def finish(self, *, session_id: str, request_id: str) -> None:
        if self.output_format == "json":
            document = {
                "ok": not self.failed,
                "session_id": session_id,
                "request_id": request_id,
                "events": self.events,
            }
            self.stdout.write(
                json.dumps(document, ensure_ascii=False, default=str) + "\n"
            )
        elif self.output_format == "human" and self._wrote_delta:
            self.stdout.write("\n")
        self.stdout.flush()

    def _render_human(self, event: RuntimeEvent) -> None:
        event_type = event.event_type
        payload = event.payload
        text = _event_text(payload)
        if event_type == "chat.delta":
            self.stdout.write(text)
            self.stdout.flush()
            self._wrote_delta = True
        elif event_type == "chat.final":
            if text and not self._wrote_delta:
                self.stdout.write(text)
        elif event_type == "chat.reasoning" and self.show_reasoning and text:
            self.stderr.write(f"[reasoning] {text}\n")
        elif event_type in {"chat.tool_call", "chat.tool_result"} and self.show_tools:
            self.stderr.write(f"[{event_type}] {text or payload}\n")
        elif not event.ok or event_type in {"chat.error", "runtime.error", "team.error"}:
            self.stderr.write(f"error: {text or payload.get('error') or payload}\n")
        elif event_type == "plan.mode_exited":
            self.stderr.write(f"[plan] exited to {payload.get('mode', 'normal')}\n")
        self.stderr.flush()


__all__ = ["EventRenderer"]
