# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Thin Host facts for one already-normalized root tool call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from openjiuwen.harness.security.files import extract_accesses_native

from jiuwenswarm.agents.harness.common.rails.permissions.tool_capabilities import (
    ToolCapability,
    classify_tool,
)


@dataclass(frozen=True, slots=True)
class ToolDecisionFacts:
    """Host facts; ``accesses_known`` means path accesses are complete."""

    capability: ToolCapability
    untrusted_args: Mapping[str, Any]
    arguments_valid_object: bool
    accesses_known: bool
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    artifact_write_paths: tuple[str, ...]
    external_paths: tuple[str, ...]
    command: str
    raw_command: str
    effective_workdir: str
    workspace_root: str
    platform_trusted_root: str

    @property
    def tool_name(self) -> str:
        return self.capability.tool_name

    @property
    def tool_category(self) -> str:
        return self.capability.category

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.read_paths, *self.write_paths)))


@dataclass(frozen=True, slots=True)
class DecisionRoute:
    """One compact Host routing result."""

    level: str
    reason: str
    source: str

    @property
    def is_hard_block(self) -> bool:
        return self.source == "hard_guard"

    @property
    def requires_manual(self) -> bool:
        return self.source == "manual_only"

    @property
    def requires_reviewer(self) -> bool:
        return self.source == "semantic_reviewer"

    @property
    def is_deterministic_allow(self) -> bool:
        return self.source == "recent_search_result"

    @property
    def accepted(self) -> bool:
        return not self.is_hard_block

    @property
    def allowed_outcomes(self) -> tuple[str, ...]:
        if self.requires_reviewer or self.is_deterministic_allow:
            return ("allow_once", "manual", "deny")
        if self.requires_manual:
            return ("manual", "deny")
        return ()

    @property
    def no_auto_allow_reason(self) -> str:
        return self.reason if self.requires_manual else ""


def build_tool_decision_facts(
    tool_name: str,
    tool_args: Mapping[str, Any],
    *,
    workspace_root: Path | str | None,
    platform_trusted_root: Path | str | None = None,
    original_args_were_valid_object: bool,
    external_paths: Sequence[str] = (),
    send_paths: Sequence[str] = (),
) -> ToolDecisionFacts:
    """Project adapter, Core-access and Engine facts without reparsing them."""

    capability = classify_tool(tool_name)
    args = {str(key): value for key, value in tool_args.items()}
    root = _workspace_root(workspace_root)
    platform_root = _workspace_root(platform_trusted_root)
    read_paths, write_paths, artifact_write_paths, accesses_known = _core_accesses(
        capability,
        args,
        root,
        send_paths=send_paths,
    )
    command = ""
    raw_command = ""
    if capability.category == "shell":
        value = args.get("command") or args.get("cmd")
        raw_command = value if isinstance(value, str) else ""
        command = raw_command.strip()
    return ToolDecisionFacts(
        capability=capability,
        untrusted_args=MappingProxyType(args),
        arguments_valid_object=bool(original_args_were_valid_object),
        accesses_known=accesses_known,
        read_paths=read_paths,
        write_paths=write_paths,
        artifact_write_paths=artifact_write_paths,
        external_paths=tuple(dict.fromkeys(str(path) for path in external_paths)),
        command=command,
        raw_command=raw_command,
        effective_workdir=_effective_workdir(capability, args, root),
        workspace_root=root.as_posix() if root is not None else "",
        platform_trusted_root=(
            platform_root.as_posix() if platform_root is not None else ""
        ),
    )


def _workspace_root(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _effective_workdir(
    capability: ToolCapability,
    args: Mapping[str, Any],
    root: Path | None,
) -> str:
    """Return the Host-normalized command workdir relative to the workspace."""

    if root is None or capability.tool_name not in {"bash", "mcp_exec_command"}:
        return ""
    if capability.tool_name == "bash" and "cwd" in args:
        return ""
    value = args.get("workdir")
    if not isinstance(value, str) or not value:
        return ""
    try:
        raw_path = Path(value).expanduser()
        resolved = (
            raw_path.resolve(strict=False)
            if raw_path.is_absolute()
            else (root / raw_path).resolve(strict=False)
        )
        relative = resolved.relative_to(root).as_posix() or "."
        if len(relative.encode("utf-8")) > 1024:
            return ""
        return relative
    except (OSError, RuntimeError, TypeError, UnicodeEncodeError, ValueError):
        return ""


def _core_accesses(
    capability: ToolCapability,
    args: Mapping[str, Any],
    root: Path | None,
    *,
    send_paths: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
    if capability.tool_name == "send_file_to_user":
        paths = tuple(dict.fromkeys(str(path) for path in send_paths if str(path)))
        return paths, (), (), bool(paths)
    if capability.category not in {"path", "shell"}:
        return (), (), (), True
    if root is None:
        return (), (), (), False
    try:
        accesses = extract_accesses_native(capability.tool_name, args, root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return (), (), (), False
    reads: list[str] = []
    writes: list[str] = []
    artifact_writes: list[str] = []
    for path, action, _source in accesses:
        target = writes if action in {"write", "exec"} else reads
        normalized = path.as_posix()
        if normalized not in target:
            target.append(normalized)
        if action == "write" and normalized not in artifact_writes:
            artifact_writes.append(normalized)
    # Core's L1 shell extraction reports only positively observed accesses. It
    # does not claim to enumerate effects performed by the invoked programs.
    known = capability.category == "path" and bool(reads or writes)
    return tuple(reads), tuple(writes), tuple(artifact_writes), known


__all__ = ["DecisionRoute", "ToolDecisionFacts", "build_tool_decision_facts"]
