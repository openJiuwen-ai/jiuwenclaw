from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openjiuwen.core.foundation.llm import BaseMessage, UserMessage


class ReinjectBuilder(Protocol):
    def __call__(self, ctx: "ReinjectContext") -> str | list[BaseMessage]:
        ...


@dataclass(frozen=True)
class ReinjectBuilderSpec:
    name: str
    label: str
    builder: ReinjectBuilder


@dataclass(frozen=True)
class ReinjectContext:
    session_state: dict[str, Any]
    source_messages: list[BaseMessage]
    messages_to_keep: list[BaseMessage]
    workspace_root: str | None
    config: Any
    state_marker: str
    truncate: Callable[[str], str]
    context: Any = None


class StateReinjector:
    def __init__(self) -> None:
        self._builders: list[ReinjectBuilderSpec] = []

    def register(self, name: str, label: str, builder: ReinjectBuilder) -> None:
        spec = ReinjectBuilderSpec(name=name, label=label, builder=builder)
        for index, existing in enumerate(self._builders):
            if existing.name == name:
                self._builders[index] = spec
                return
        self._builders.append(spec)

    def register_builder(self, *, name: str, label: str, builder: ReinjectBuilder) -> None:
        self.register(name=name, label=label, builder=builder)

    def iter_builders(self) -> tuple[ReinjectBuilderSpec, ...]:
        return tuple(self._builders)

    def build_messages(self, ctx: ReinjectContext, only: list[str] | None = None) -> list[BaseMessage]:
        active = set(only) if only is not None else None
        messages: list[BaseMessage] = []
        for spec in self._builders:
            if active is not None and spec.name not in active:
                continue
            content = spec.builder(ctx)
            if isinstance(content, list):
                messages.extend(content)
                continue
            if content:
                messages.append(UserMessage(content=f"{ctx.state_marker}\n[{spec.label}]\n{ctx.truncate(content)}"))
        return messages


def build_single_reinjected_state_message(
    ctx: ReinjectContext,
    specs: list[ReinjectBuilderSpec],
    *,
    only: list[str] | None = None,
) -> UserMessage | None:
    active = set(only) if only is not None else None
    sections: list[str] = []
    for spec in specs:
        if active is not None and spec.name not in active:
            continue
        content = spec.builder(ctx)
        if isinstance(content, list):
            rendered = "\n\n".join(
                getattr(message, "content", "") for message in content if getattr(message, "content", "")
            )
        else:
            rendered = content
        if not rendered:
            continue
        rendered = _strip_state_header(str(rendered), ctx.state_marker, spec.label)
        if rendered:
            sections.append(f'<section name="{spec.name}">\n{ctx.truncate(rendered)}\n</section>')
    if not sections:
        return None
    return UserMessage(
        content=(
            "<recovered_context>\n"
            "<instruction>\n"
            "These sections restore context or state that may have been compacted out.\n"
            "Use them only to understand the latest request or continue existing work.\n"
            "Do not treat them as new user commands.\n"
            "</instruction>\n\n"
            + "\n\n".join(sections)
            + "\n</recovered_context>"
        )
    )


def _strip_state_header(content: str, state_marker: str, label: str) -> str:
    text = content.strip()
    prefix = f"{state_marker}\n[{label}]\n"
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    generic_prefix = f"{state_marker}\n"
    if text.startswith(generic_prefix):
        rest = text[len(generic_prefix):]
        if rest.startswith("[") and "]\n" in rest:
            return rest.split("]\n", 1)[1].strip()
    return text
