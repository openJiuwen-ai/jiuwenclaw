# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lightweight post-execution validation for semantic artifact paths."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.harness.common.rails.execution_guard.circuit_breaker_rail import (
    ToolResultErrorDetector,
)
from jiuwenswarm.agents.harness.common.workspace_paths import (
    normalize_workspace_root,
    resolve_workspace_path,
)

MAX_ARTIFACT_CANDIDATES = 32
MAX_GROUNDING_TEXTS = 64
MAX_GROUNDING_TEXT_BYTES = 64 * 1024
MAX_RESULT_NESTING_DEPTH = 4

_SEMANTIC_PATHS_EXTRA_KEY = "_auto_permission_semantic_artifact_paths"
_CANDIDATE_STATE_EXTRA_KEY = "_auto_permission_artifact_candidate_state"
_PATH_QUOTE_DELIMITERS = frozenset("'\"`")
_UNQUOTED_PATH_BOUNDARIES = frozenset(" \t\r\n|&;<>")


@dataclass(frozen=True, slots=True)
class ArtifactPathCandidate:
    path: str
    requires_grounding: bool


@dataclass(frozen=True, slots=True)
class ArtifactCandidateState:
    session_id: str
    tool_name: str
    tool_call_id: str
    workspace_root: str
    effective_workdir: str
    candidates: tuple[ArtifactPathCandidate, ...]
    grounding_texts: tuple[str, ...]
    facts: Any


@dataclass(frozen=True, slots=True)
class ArtifactPostGateResult:
    accepted: int = 0
    rejected: int = 0
    reason_codes: tuple[str, ...] = ()


def stage_semantic_artifact_paths(ctx: Any, paths: Iterable[str]) -> None:
    """Stage reviewer paths until the current final allow boundary."""

    extra = _mutable_context_extra(ctx)
    if extra is None:
        return
    normalized = tuple(dict.fromkeys(str(path) for path in paths if str(path)))
    if normalized:
        extra[_SEMANTIC_PATHS_EXTRA_KEY] = normalized
    else:
        extra.pop(_SEMANTIC_PATHS_EXTRA_KEY, None)


def clear_artifact_candidate_state(ctx: Any) -> None:
    extra = _mutable_context_extra(ctx)
    if extra is None:
        return
    extra.pop(_SEMANTIC_PATHS_EXTRA_KEY, None)
    extra.pop(_CANDIDATE_STATE_EXTRA_KEY, None)


def publish_artifact_candidate_state(
    ctx: Any,
    *,
    session_id: str | None,
    tool_name: str,
    tool_call_id: str | None,
    workspace_root: str | Path | None,
    host_write_paths: Iterable[str],
    untrusted_args: Mapping[str, Any],
    command: str = "",
    include_semantic: bool,
    effective_workdir: str | Path | None = None,
    facts: Any = None,
) -> int:
    """Publish one after-tool candidate state for a final allowed call."""

    extra = _mutable_context_extra(ctx)
    root = _safe_workspace_root(workspace_root)
    if extra is None or root is None or not session_id:
        clear_artifact_candidate_state(ctx)
        return 0
    raw_semantic = extra.pop(_SEMANTIC_PATHS_EXTRA_KEY, ())
    semantic_paths = (
        tuple(raw_semantic)
        if include_semantic and isinstance(raw_semantic, tuple)
        else ()
    )
    candidates: list[ArtifactPathCandidate] = []
    seen: set[tuple[str, bool]] = set()
    for raw_path, requires_grounding in (
        *((str(path), False) for path in host_write_paths),
        *((str(path), True) for path in semantic_paths),
    ):
        key = (raw_path, requires_grounding)
        if not raw_path or key in seen:
            continue
        seen.add(key)
        candidates.append(
            ArtifactPathCandidate(
                path=raw_path,
                requires_grounding=requires_grounding,
            )
        )
        if len(candidates) >= MAX_ARTIFACT_CANDIDATES:
            break
    if not candidates:
        extra.pop(_CANDIDATE_STATE_EXTRA_KEY, None)
        return 0
    extra[_CANDIDATE_STATE_EXTRA_KEY] = ArtifactCandidateState(
        session_id=str(session_id),
        tool_name=str(tool_name or ""),
        tool_call_id=str(tool_call_id or ""),
        workspace_root=root.as_posix(),
        effective_workdir=(
            _safe_effective_workdir(root, effective_workdir) or root
        ).as_posix(),
        candidates=tuple(candidates),
        grounding_texts=collect_grounding_texts(untrusted_args, command=command),
        facts=facts,
    )
    return len(candidates)


def consume_artifact_candidate_state(
    ctx: Any,
    *,
    tool_name: str,
) -> ArtifactCandidateState | None:
    """Consume the current one-shot state only for the matching tool callback."""

    extra = _mutable_context_extra(ctx)
    if extra is None:
        return None
    value = extra.pop(_CANDIDATE_STATE_EXTRA_KEY, None)
    extra.pop(_SEMANTIC_PATHS_EXTRA_KEY, None)
    if not isinstance(value, ArtifactCandidateState):
        return None
    if value.tool_name != str(tool_name or ""):
        return None
    return value


def collect_grounding_texts(
    value: Any,
    *,
    command: str = "",
) -> tuple[str, ...]:
    """Collect bounded raw string leaves for exact lexical path matching."""

    texts: list[str] = []
    size = 0

    def add(raw: Any) -> None:
        nonlocal size
        if not isinstance(raw, str) or not raw or len(texts) >= MAX_GROUNDING_TEXTS:
            return
        try:
            encoded_size = len(raw.encode("utf-8"))
        except UnicodeEncodeError:
            return
        if size + encoded_size > MAX_GROUNDING_TEXT_BYTES:
            return
        texts.append(raw)
        size += encoded_size

    def visit(item: Any, depth: int) -> None:
        if depth > 6 or len(texts) >= MAX_GROUNDING_TEXTS:
            return
        if isinstance(item, str):
            add(item)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested, depth + 1)
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            for nested in item:
                visit(nested, depth + 1)

    add(command)
    visit(value, 0)
    return tuple(dict.fromkeys(texts))


def tool_result_succeeded(ctx: Any) -> bool:
    """Conservatively classify existing structured/synchronous tool results."""

    if getattr(ctx, "exception", None) is not None:
        return False
    inputs = getattr(ctx, "inputs", None)
    result = inputs.get("tool_result") if isinstance(inputs, Mapping) else getattr(
        inputs, "tool_result", None
    )
    if result is None:
        return False
    return _result_succeeded(result)


def _result_succeeded(result: Any) -> bool:
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            return ToolResultErrorDetector.has_explicit_success(result)
        return _result_succeeded(decoded)
    if isinstance(result, Mapping):
        values = result
    else:
        model_dump = getattr(result, "model_dump", None)
        if callable(model_dump):
            try:
                values = model_dump()
            except (TypeError, ValueError):
                values = {}
        elif hasattr(result, "__dict__"):
            values = vars(result)
        else:
            values = {}
    if isinstance(values, Mapping):
        if _result_is_incomplete_async(values):
            return False
        if values.get("cancelled") is True:
            return False
        if values.get("success") is False or values.get("is_error") is True:
            return False
        exit_code = values.get("exit_code")
        if exit_code is not None:
            try:
                return int(exit_code) == 0
            except (TypeError, ValueError):
                return False
        status = str(values.get("status") or "").strip().lower()
        if status in {
            "error",
            "failed",
            "failure",
            "cancelled",
            "canceled",
            "timeout",
        }:
            return False
        if values.get("success") is True or status in {"ok", "success", "completed"}:
            return True
        data = values.get("data")
        if data is not None and data is not result:
            return _result_succeeded(data)
        return False
    return False


def _result_is_incomplete_async(
    values: Mapping[str, Any],
    *,
    depth: int = 0,
) -> bool:
    """Recognize bounded Host result shapes that have not completed yet."""

    if depth > MAX_RESULT_NESTING_DEPTH or values.get("background") is True:
        return values.get("background") is True
    status = str(values.get("status") or "").strip().lower()
    if status in {
        "background",
        "in_progress",
        "pending",
        "queued",
        "running",
        "started",
    }:
        return True
    data = values.get("data")
    if data is values:
        return False
    if isinstance(data, Mapping):
        return _result_is_incomplete_async(data, depth=depth + 1)
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        try:
            nested = model_dump()
        except (TypeError, ValueError):
            return False
        if isinstance(nested, Mapping):
            return _result_is_incomplete_async(nested, depth=depth + 1)
    return False


def _validate_candidate(
    candidate: ArtifactPathCandidate,
    *,
    root: Path,
    protected_roots: tuple[Path, ...],
    grounding_texts: tuple[str, ...],
    effective_workdir: Path,
) -> tuple[Path | None, str]:
    raw_path = str(candidate.path or "")
    if not raw_path or _has_dynamic_path_syntax(raw_path):
        return None, "path_dynamic_or_invalid"
    if candidate.requires_grounding and not _path_is_grounded(
        raw_path,
        root=root,
        texts=grounding_texts,
        effective_workdir=effective_workdir,
    ):
        return None, "path_not_grounded"
    resolved = _canonical_regular_file(raw_path, root=root)
    if resolved is None:
        return None, "path_missing_or_unsafe"
    if _is_protected(resolved, protected_roots):
        return None, "path_protected"
    return resolved, ""


def _canonical_regular_file(raw_path: str | Path, *, root: Path) -> Path | None:
    try:
        candidate = resolve_workspace_path(raw_path, root)
        if candidate is None:
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _path_is_grounded(
    raw_path: str,
    *,
    root: Path,
    texts: Iterable[str],
    effective_workdir: Path | None = None,
) -> bool:
    path_text = str(raw_path or "").replace("\\", "/")
    try:
        candidate = resolve_workspace_path(path_text, root)
        if candidate is None:
            return False
        relative = candidate.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    workdir = effective_workdir or root
    forms: list[str] = [
        f"$WORKSPACE/{relative}",
        (root / relative).as_posix(),
    ]
    try:
        invocation_relative = Path(
            os.path.relpath(candidate.resolve(strict=False), workdir)
        ).as_posix()
    except (OSError, RuntimeError, TypeError, ValueError):
        invocation_relative = ""
    if invocation_relative:
        forms[:0] = [invocation_relative, f"./{invocation_relative}"]
    for text in texts:
        normalized = str(text).replace("\\", "/")
        for form in dict.fromkeys(forms):
            if _contains_path_token(normalized, form):
                return True
    return False


def _contains_path_token(text: str, token: str) -> bool:
    if text == token:
        return True
    has_quote = any(quote in text for quote in _PATH_QUOTE_DELIMITERS)
    start = text.find(token)
    while start >= 0:
        end = start + len(token)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before == after and before in _PATH_QUOTE_DELIMITERS:
            return True
        if (
            not has_quote
            and _is_unquoted_path_boundary(before)
            and _is_unquoted_path_boundary(after)
        ):
            return True
        start = text.find(token, start + 1)
    return False


def _is_unquoted_path_boundary(character: str) -> bool:
    return not character or character in _UNQUOTED_PATH_BOUNDARIES


def _safe_effective_workdir(
    root: Path,
    value: str | Path | None,
) -> Path:
    """Resolve one execution directory inside the permission workspace."""

    if value is None or str(value) in {"", "."}:
        return root
    try:
        raw_path = Path(value).expanduser()
        resolved = (
            raw_path.resolve(strict=False)
            if raw_path.is_absolute()
            else (root / raw_path).resolve(strict=False)
        )
        resolved.relative_to(root)
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError):
        return root


def _protected_roots(root: Path, excluded_paths: Sequence[str]) -> tuple[Path, ...]:
    protected: list[Path] = []
    for raw_path in excluded_paths:
        try:
            candidate = resolve_workspace_path(raw_path, root)
            if candidate is not None:
                protected.append(candidate.resolve(strict=False))
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(protected))


def _is_protected(path: Path, protected_roots: Iterable[Path]) -> bool:
    for protected in protected_roots:
        try:
            path.relative_to(protected)
            return True
        except ValueError:
            continue
    return False


def _has_dynamic_path_syntax(path: str) -> bool:
    return bool(
        not path.strip()
        or path.startswith(("~", "$"))
        or any(character in path for character in "*?[]{}\x00\r\n")
    )


def _safe_workspace_root(value: str | Path | None) -> Path | None:
    try:
        return normalize_workspace_root(value)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _mutable_context_extra(ctx: Any) -> dict[str, Any] | None:
    if ctx is None:
        return None
    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, Mapping):
        extra = dict(extra)
    elif extra is None:
        extra = {}
    else:
        return None
    try:
        ctx.extra = extra
    except (AttributeError, TypeError):
        return None
    return extra


__all__ = [
    "ArtifactPathCandidate",
    "ArtifactCandidateState",
    "ArtifactPostGateResult",
    "MAX_ARTIFACT_CANDIDATES",
    "_canonical_regular_file",
    "_is_protected",
    "_path_is_grounded",
    "_protected_roots",
    "_safe_effective_workdir",
    "_safe_workspace_root",
    "clear_artifact_candidate_state",
    "collect_grounding_texts",
    "consume_artifact_candidate_state",
    "publish_artifact_candidate_state",
    "stage_semantic_artifact_paths",
    "tool_result_succeeded",
]
