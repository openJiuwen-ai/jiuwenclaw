# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt attachment directory loader for jiuwenswarm."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachment,
    PromptAttachmentUpdate,
)
from jiuwenswarm.agents.harness.common.prompt_attachment_compat import (
    PromptAttachmentKind,
    PromptAttachmentScope,
)


logger = logging.getLogger(__name__)

SESSION_SOURCE = "jiuwenswarm.prompt_attachment.session"
TURN_SOURCE = "jiuwenswarm.prompt_attachment.turn"
DEFAULT_MAX_FILE_CHARS = 12000
_SAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_TEXT_SUFFIXES = frozenset({".md", ".txt"})
_JSON_SUFFIX = ".json"
_README_TEXT = """# Prompt Attachment

Files in this directory are injected as dynamic prompt attachments for model calls.
They are not user-uploaded attachments and are not written to long-term
conversation history.

Layout:
- <session_id>/session/: session-scope prompt attachment files.
- <session_id>/turn/<invoke_turn_id>/: request-scope prompt attachment
  files, hot-loaded before each request.

Markdown frontmatter is intentionally small: simple key-value fields and one
level of metadata map are supported. Arrays, multiline strings, and full YAML
features are not parsed by this loader.
"""
_KIND_BY_STEM = {
    "runtime": PromptAttachmentKind.RUNTIME,
    "request_context": PromptAttachmentKind.RUNTIME,
    "diagnostics": PromptAttachmentKind.DIAGNOSTIC,
    "memory_summary": PromptAttachmentKind.MEMORY,
    "open_files": PromptAttachmentKind.FILE,
}
_USER_SOURCE = "jiuwenswarm.prompt_attachment.user"
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse_path(path: Path) -> bool:
    """Return True for symlink, junction, or other Windows reparse-point paths."""

    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _iter_safe_files(scope_dir: Path, suffixes: frozenset[str]) -> Iterable[Path]:
    if not scope_dir.exists() or not scope_dir.is_dir():
        return
    scope_root = scope_dir.resolve()
    pending = [scope_dir]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.as_posix())
        except OSError as exc:
            logger.warning("[PromptAttachmentLoader] failed to list prompt attachment directory %s: %s", current, exc)
            continue
        for path in entries:
            try:
                relative = path.relative_to(scope_dir)
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative.parts):
                continue
            if _is_reparse_path(path):
                logger.warning("[PromptAttachmentLoader] skip linked prompt attachment path: %s", path)
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                logger.warning("[PromptAttachmentLoader] failed to resolve prompt attachment path %s: %s", path, exc)
                continue
            if not _is_relative_to(resolved, scope_root):
                logger.warning("[PromptAttachmentLoader] skip prompt attachment path outside scope: %s", path)
                continue
            if path.is_dir():
                pending.append(path)
            elif path.is_file() and path.suffix.lower() in suffixes:
                yield path


def _managed_source_for_scope(scope: PromptAttachmentScope) -> str:
    return TURN_SOURCE if scope == PromptAttachmentScope.TURN else SESSION_SOURCE


def _metadata_with_origin_source(metadata: dict[str, Any], origin_source: str | None) -> dict[str, Any]:
    result = dict(metadata)
    if origin_source and origin_source not in {SESSION_SOURCE, TURN_SOURCE}:
        result.setdefault("origin_source", origin_source)
    return result


def sanitize_session_id(session_id: str | None) -> str:
    """Return a deterministic path-safe session id."""

    raw = str(session_id or "").strip()
    if not raw:
        return "default"
    safe = _SAFE_SESSION_CHARS.sub("_", raw.replace("/", "_").replace("\\", "_")).strip("._-")
    if not safe:
        safe = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    if safe in {".", ".."}:
        safe = f"session_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
    if len(safe) > 80:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:67]}_{digest}"
    return safe


def _safe_id_part(value: str) -> str:
    raw = str(value or "").strip()
    safe = _SAFE_SESSION_CHARS.sub("_", raw).strip("._-")
    if safe:
        return safe
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _kind_value(kind: PromptAttachmentKind | str) -> str:
    return kind.value if isinstance(kind, PromptAttachmentKind) else str(kind)


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if stripped in {"true", "True"}:
        return True
    if stripped in {"false", "False"}:
        return False
    if stripped in {"null", "None", "~"}:
        return None
    try:
        return int(stripped)
    except ValueError:
        return stripped.strip('"').strip("'")


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n") and not raw.startswith("---\r\n"):
        return {}, raw
    normalized = raw.replace("\r\n", "\n")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, raw
    header = normalized[4:end]
    body = normalized[end + len("\n---\n"):]
    if body.startswith("\n"):
        body = body[1:]
    data: dict[str, Any] = {}
    current_map: dict[str, Any] | None = None
    for line in header.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_map is not None and ":" in line:
            key, value = line.strip().split(":", 1)
            current_map[key.strip()] = _parse_scalar(value)
            continue
        current_map = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if value.strip():
            data[key] = _parse_scalar(value)
        else:
            nested: dict[str, Any] = {}
            data[key] = nested
            current_map = nested
    return data, body


def _dump_frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key in sorted(data):
        value = data[key]
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for nested_key in sorted(value):
                lines.append(f"  {nested_key}: {value[nested_key]}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _resolve_from_context(ctx: Any, *names: str) -> str | None:
    for name in names:
        value = getattr(ctx, name, None)
        if value:
            return str(value)
    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict):
        for name in names:
            value = extra.get(name)
            if value:
                return str(value)
    request = getattr(ctx, "request", None)
    if request is not None:
        for name in names:
            value = getattr(request, name, None)
            if value:
                return str(value)
    session = getattr(ctx, "session", None)
    if session is not None:
        for name in names:
            value = getattr(session, name, None)
            if value:
                return str(value)
    return None


class PromptAttachmentFileStore:
    """User-friendly file CRUD for prompt attachment directories."""

    def __init__(self, root: Path | str, *, max_file_chars: int = DEFAULT_MAX_FILE_CHARS) -> None:
        self.root = Path(root)
        self.max_file_chars = max_file_chars

    def for_context(self, ctx: Any) -> "PromptAttachmentContextStore":
        return PromptAttachmentContextStore(self, ctx)

    def for_session(self, session_id: str, *, invoke_turn_id: str | None = None) -> "PromptAttachmentSessionStore":
        return PromptAttachmentSessionStore(self, session_id=session_id, invoke_turn_id=invoke_turn_id)

    def add_markdown(
        self,
        *,
        session_id: str,
        content: str,
        scope: PromptAttachmentScope | str = PromptAttachmentScope.SESSION,
        invoke_turn_id: str | None = None,
        name: str | None = None,
        priority: int = 0,
        kind: PromptAttachmentKind | str = PromptAttachmentKind.TEXT,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PromptAttachment:
        path = self._file_path(
            session_id=session_id,
            scope=scope,
            invoke_turn_id=invoke_turn_id,
            name=name,
            suffix=".md",
        )
        if path.exists():
            raise FileExistsError(f"prompt attachment already exists: {path}")
        frontmatter = self._frontmatter(
            priority=priority,
            kind=kind,
            source=source or _USER_SOURCE,
            metadata=metadata,
        )
        with _path_lock(path):
            _atomic_write_text(path, _dump_frontmatter(frontmatter) + content)
        return self._item_from_file(path, session_id=session_id, scope=scope, invoke_turn_id=invoke_turn_id)

    def update_markdown(
        self,
        id_or_name: str,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str = PromptAttachmentScope.SESSION,
        invoke_turn_id: str | None = None,
        content: str | None = None,
        priority: int | None = None,
        source: str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        metadata: dict[str, Any] | None = None,
        metadata_replace: bool = False,
        replace: bool = False,
    ) -> PromptAttachment:
        path = self._resolve_id_or_name(
            id_or_name,
            session_id=session_id,
            scope=scope,
            invoke_turn_id=invoke_turn_id,
            include_json=False,
        )
        if path is None:
            raise FileNotFoundError(f"prompt attachment does not exist: {id_or_name}")
        with _path_lock(path):
            old_meta, old_content = _parse_frontmatter(path.read_text(encoding="utf-8"))
            next_meta = {} if replace else dict(old_meta)
            if priority is not None:
                next_meta["priority"] = priority
            if source is not None:
                next_meta["source"] = source
            if kind is not None:
                next_meta["kind"] = _kind_value(self._coerce_kind(kind))
            if metadata is not None:
                if metadata_replace:
                    next_meta["metadata"] = dict(metadata)
                else:
                    next_meta["metadata"] = {**dict(next_meta.get("metadata") or {}), **metadata}
            next_content = old_content if content is None else content
            _atomic_write_text(path, _dump_frontmatter(next_meta) + next_content)
        return self._item_from_file(path, session_id=session_id, scope=scope, invoke_turn_id=invoke_turn_id)

    def add(self, prompt_attachment: PromptAttachment) -> PromptAttachment:
        path = self._json_path(prompt_attachment)
        if path.exists():
            raise FileExistsError(f"prompt attachment already exists: {prompt_attachment.id}")
        with _path_lock(path):
            _atomic_write_text(
                path,
                json.dumps(
                    prompt_attachment.model_dump(exclude_none=True),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        return prompt_attachment.model_copy(deep=True)

    def update(
        self,
        prompt_attachment_id: str,
        update: PromptAttachmentUpdate,
        *,
        metadata_replace: bool = False,
    ) -> PromptAttachment:
        path, current = self._find_json(prompt_attachment_id)
        if path is None or current is None:
            raise FileNotFoundError(f"prompt attachment does not exist: {prompt_attachment_id}")
        with _path_lock(path):
            data = current.model_dump()
            patch = update.model_dump(exclude_unset=True)
            if "metadata" in patch and not metadata_replace:
                patch["metadata"] = {
                    **dict(data.get("metadata") or {}),
                    **dict(patch.get("metadata") or {}),
                }
            data.update(patch)
            updated = PromptAttachment(**data)
            next_path = self._json_path(updated)
            _atomic_write_text(
                next_path,
                json.dumps(updated.model_dump(exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            if next_path != path:
                path.unlink()
        return updated

    def get(
        self,
        id_or_name: str,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str = PromptAttachmentScope.SESSION,
        invoke_turn_id: str | None = None,
    ) -> PromptAttachment | None:
        path = self._resolve_id_or_name(
            id_or_name,
            session_id=session_id,
            scope=scope,
            invoke_turn_id=invoke_turn_id,
        )
        if path is None:
            return None
        return self._item_from_file(path, session_id=session_id, scope=scope, invoke_turn_id=invoke_turn_id)

    def delete(
        self,
        id_or_name: str,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str = PromptAttachmentScope.SESSION,
        invoke_turn_id: str | None = None,
    ) -> bool:
        path = self._resolve_id_or_name(
            id_or_name,
            session_id=session_id,
            scope=scope,
            invoke_turn_id=invoke_turn_id,
        )
        if path is None:
            return False
        with _path_lock(path):
            if not path.exists():
                return False
            path.unlink()
        return True

    def list(
        self,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str | None = None,
        invoke_turn_id: str | None = None,
    ) -> list[PromptAttachment]:
        scopes = [self._coerce_scope(scope)] if scope is not None else [
            PromptAttachmentScope.SESSION,
            PromptAttachmentScope.TURN,
        ]
        items: list[PromptAttachment] = []
        for scope_value in scopes:
            for scope_dir, turn_id in self._list_scope_dirs(
                session_id=session_id,
                scope=scope_value,
                invoke_turn_id=invoke_turn_id,
            ):
                if not scope_dir.exists():
                    continue
                for path in _iter_safe_files(scope_dir, _TEXT_SUFFIXES | {_JSON_SUFFIX}):
                    item = self._item_from_file(
                        path,
                        session_id=session_id,
                        scope=scope_value,
                        invoke_turn_id=turn_id,
                    )
                    items.append(item)
        return items

    def _list_scope_dirs(
        self,
        *,
        session_id: str,
        scope: PromptAttachmentScope,
        invoke_turn_id: str | None,
    ) -> list[tuple[Path, str | None]]:
        if scope == PromptAttachmentScope.SESSION:
            return [(self._scope_dir(session_id, scope=scope, invoke_turn_id=None), None)]
        if invoke_turn_id:
            return [(self._scope_dir(session_id, scope=scope, invoke_turn_id=invoke_turn_id), invoke_turn_id)]
        turn_root = self.root / sanitize_session_id(session_id) / "turn"
        if not turn_root.exists():
            return []
        return [
            (path, path.name)
            for path in sorted(turn_root.iterdir(), key=lambda item: item.as_posix())
            if path.is_dir() and not path.name.startswith(".")
        ]

    def _item_from_file(
        self,
        path: Path,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str,
        invoke_turn_id: str | None,
    ) -> PromptAttachment:
        scope_value = self._coerce_scope(scope)
        if path.suffix.lower() == _JSON_SUFFIX:
            item = PromptAttachment(**json.loads(path.read_text(encoding="utf-8")))
            metadata = _metadata_with_origin_source(dict(item.metadata or {}), item.source)
            return item.model_copy(update={
                "source": _managed_source_for_scope(scope_value),
                "metadata": metadata,
            })
        scope_dir = self._scope_dir(session_id, scope=scope_value, invoke_turn_id=invoke_turn_id)
        meta, content = _parse_frontmatter(path.read_text(encoding="utf-8"))
        item_id = self._id_from_path(
            path,
            scope_dir,
            session_id=session_id,
            scope=scope_value,
            invoke_turn_id=invoke_turn_id,
        )
        metadata = _metadata_with_origin_source(dict(meta.get("metadata") or {}), meta.get("source"))
        metadata.update({"path": str(path), "relative_path": path.relative_to(scope_dir).as_posix()})
        return PromptAttachment(
            id=item_id,
            scope=scope_value,
            kind=meta.get("kind") or PromptAttachmentLoader.kind_for_file(path),
            content=content,
            priority=int(meta.get("priority") or 0),
            source=_managed_source_for_scope(scope_value),
            session_id=session_id,
            invoke_turn_id=invoke_turn_id if scope_value == PromptAttachmentScope.TURN else None,
            metadata=metadata,
            content_kind="text/markdown" if path.suffix.lower() == ".md" else "text/plain",
        )

    def _resolve_id_or_name(
        self,
        id_or_name: str,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str,
        invoke_turn_id: str | None,
        include_json: bool = True,
    ) -> Path | None:
        scope_value = self._coerce_scope(scope)
        scope_dir = self._scope_dir(session_id, scope=scope_value, invoke_turn_id=invoke_turn_id)
        suffixes = _TEXT_SUFFIXES | ({_JSON_SUFFIX} if include_json else frozenset())
        try:
            name_path = self._safe_relative_file_name(name=id_or_name, suffix=".md")
            candidate = scope_dir / name_path
            if candidate.suffix.lower() in suffixes and candidate.exists():
                return candidate
        except ValueError:
            pass
        for path in _iter_safe_files(scope_dir, suffixes):
            item_id = self._id_from_path(
                path,
                scope_dir,
                session_id=session_id,
                scope=scope_value,
                invoke_turn_id=invoke_turn_id,
            )
            if item_id == id_or_name:
                return path
        if not include_json:
            return None
        json_path, _ = self._find_json(
            id_or_name,
            session_id=session_id,
            scope=scope_value,
            invoke_turn_id=invoke_turn_id,
        )
        return json_path

    def _file_path(
        self,
        *,
        session_id: str,
        scope: PromptAttachmentScope | str,
        invoke_turn_id: str | None,
        name: str | None,
        suffix: str,
    ) -> Path:
        scope_value = self._coerce_scope(scope)
        if name is None:
            name = f"auto_{time.time_ns()}_{threading.get_ident()}{suffix}"
        return self._scope_dir(
            session_id,
            scope=scope_value,
            invoke_turn_id=invoke_turn_id,
        ) / self._safe_relative_file_name(
            name=name, suffix=suffix,
        )

    def _scope_dir(
        self,
        session_id: str,
        *,
        scope: PromptAttachmentScope,
        invoke_turn_id: str | None,
    ) -> Path:
        safe_session_id = sanitize_session_id(session_id)
        if scope == PromptAttachmentScope.TURN:
            if not invoke_turn_id:
                raise ValueError("invoke_turn_id is required for turn-scope prompt attachments")
            return self.root / safe_session_id / "turn" / _safe_id_part(invoke_turn_id)
        return self.root / safe_session_id / "session"

    @staticmethod
    def _frontmatter(
        *,
        priority: int,
        kind: PromptAttachmentKind | str,
        source: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": _kind_value(kind),
            "priority": priority,
            "source": source,
        }
        if metadata:
            data["metadata"] = dict(metadata)
        return data

    @staticmethod
    def _id_from_path(
        path: Path,
        scope_dir: Path,
        *,
        session_id: str,
        scope: PromptAttachmentScope,
        invoke_turn_id: str | None,
    ) -> str:
        relative_key = PromptAttachmentLoader.relative_key(path, scope_dir)
        safe_session_id = sanitize_session_id(session_id)
        if scope == PromptAttachmentScope.TURN:
            return f"{scope.value}.{safe_session_id}.{_safe_id_part(invoke_turn_id or 'turn')}.{relative_key}"
        return f"{scope.value}.{safe_session_id}.{relative_key}"

    def _json_path(self, prompt_attachment: PromptAttachment) -> Path:
        scope = self._coerce_scope(prompt_attachment.scope)
        return self._scope_dir(
            prompt_attachment.session_id or "default",
            scope=scope,
            invoke_turn_id=prompt_attachment.invoke_turn_id,
        ) / f"{_safe_id_part(prompt_attachment.id)}{_JSON_SUFFIX}"

    def _find_json(
        self,
        prompt_attachment_id: str,
        *,
        session_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
        invoke_turn_id: str | None = None,
    ) -> tuple[Path | None, PromptAttachment | None]:
        file_name = f"{_safe_id_part(prompt_attachment_id)}{_JSON_SUFFIX}"
        if session_id is not None and scope is not None:
            scope_value = self._coerce_scope(scope)
            search_dirs = []
            for scope_dir, _ in self._list_scope_dirs(
                session_id=session_id,
                scope=scope_value,
                invoke_turn_id=invoke_turn_id,
            ):
                search_dirs.append(scope_dir)
        elif session_id is not None:
            search_dirs = []
            for scope_value in (PromptAttachmentScope.SESSION, PromptAttachmentScope.TURN):
                turn_id = invoke_turn_id if scope_value == PromptAttachmentScope.TURN else None
                for scope_dir, _ in self._list_scope_dirs(
                    session_id=session_id,
                    scope=scope_value,
                    invoke_turn_id=turn_id,
                ):
                    search_dirs.append(scope_dir)
        else:
            search_dirs = [self.root]
        for search_dir in search_dirs:
            for path in _iter_safe_files(search_dir, frozenset({_JSON_SUFFIX})):
                if path.name != file_name:
                    continue
                item = PromptAttachment(**json.loads(path.read_text(encoding="utf-8")))
                if item.id == prompt_attachment_id:
                    return path, item
        return None, None

    @staticmethod
    def coerce_scope(scope: PromptAttachmentScope | str) -> PromptAttachmentScope:
        return scope if isinstance(scope, PromptAttachmentScope) else PromptAttachmentScope(str(scope))

    @staticmethod
    def _coerce_scope(scope: PromptAttachmentScope | str) -> PromptAttachmentScope:
        return PromptAttachmentFileStore.coerce_scope(scope)

    @staticmethod
    def _coerce_kind(kind: PromptAttachmentKind | str) -> PromptAttachmentKind:
        return kind if isinstance(kind, PromptAttachmentKind) else PromptAttachmentKind(str(kind))

    @staticmethod
    def _safe_relative_file_name(*, name: str, suffix: str) -> Path:
        raw_name = str(name or "").strip()
        raw_path = Path(raw_name if Path(raw_name).suffix else f"{raw_name}{suffix}")
        if raw_path.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.parts):
            raise ValueError(f"unsafe prompt attachment file name: {name}")
        if any(part.startswith(".") for part in raw_path.parts):
            raise ValueError(f"hidden prompt attachment file names are not supported: {name}")
        if raw_path.suffix.lower() not in (_TEXT_SUFFIXES | {_JSON_SUFFIX}):
            raise ValueError(f"unsupported prompt attachment file suffix: {raw_path.suffix}")
        return Path(*[
            (
                f"{_safe_id_part(Path(part).stem)}{Path(part).suffix.lower()}"
                if index == len(raw_path.parts) - 1
                else _safe_id_part(part)
            )
            for index, part in enumerate(raw_path.parts)
        ])


class PromptAttachmentContextStore:
    """Context-bound writer that hides session and turn ids from callers."""

    def __init__(self, store: PromptAttachmentFileStore, ctx: Any) -> None:
        self._store = store
        self.session_id = _resolve_from_context(ctx, "session_id", "_session_id", "id") or "default"
        self.invoke_turn_id = _resolve_from_context(
            ctx,
            "invoke_turn_id",
            "_invoke_turn_id",
            "request_id",
            "rid",
        )

    def add_markdown(self, **kwargs: Any) -> PromptAttachment:
        scope = kwargs.get("scope", PromptAttachmentScope.TURN)
        if PromptAttachmentFileStore.coerce_scope(scope) == PromptAttachmentScope.TURN and not self.invoke_turn_id:
            raise ValueError(
                "No active invoke_turn_id is bound. Use for_session(...).add_turn_markdown or pass context."
            )
        kwargs.setdefault("scope", scope)
        return self._store.add_markdown(
            session_id=self.session_id,
            invoke_turn_id=self.invoke_turn_id,
            **kwargs,
        )

    def update_markdown(self, id_or_name: str, **kwargs: Any) -> PromptAttachment:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.update_markdown(id_or_name, session_id=self.session_id, **kwargs)

    def get(self, id_or_name: str, **kwargs: Any) -> PromptAttachment | None:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.get(id_or_name, session_id=self.session_id, **kwargs)

    def delete(self, id_or_name: str, **kwargs: Any) -> bool:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.delete(id_or_name, session_id=self.session_id, **kwargs)

    def list(self, **kwargs: Any) -> list[PromptAttachment]:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.list(session_id=self.session_id, **kwargs)

    def _bind_turn_id_if_needed(self, kwargs: dict[str, Any]) -> None:
        scope = kwargs.get("scope")
        if scope is None or PromptAttachmentFileStore.coerce_scope(scope) != PromptAttachmentScope.TURN:
            return
        if kwargs.get("invoke_turn_id"):
            return
        if not self.invoke_turn_id:
            raise ValueError("No active invoke_turn_id is bound. Pass invoke_turn_id explicitly.")
        kwargs["invoke_turn_id"] = self.invoke_turn_id


class PromptAttachmentSessionStore:
    """Session-bound writer for services that know session_id but not full ctx."""

    def __init__(self, store: PromptAttachmentFileStore, *, session_id: str, invoke_turn_id: str | None = None) -> None:
        self._store = store
        self.session_id = session_id
        self.invoke_turn_id = invoke_turn_id

    def add_session_markdown(self, **kwargs: Any) -> PromptAttachment:
        return self._store.add_markdown(
            session_id=self.session_id,
            scope=PromptAttachmentScope.SESSION,
            **kwargs,
        )

    def add_turn_markdown(self, *, invoke_turn_id: str | None = None, **kwargs: Any) -> PromptAttachment:
        turn_id = invoke_turn_id or self.invoke_turn_id
        if not turn_id:
            raise ValueError("No active invoke_turn_id is bound. Pass invoke_turn_id explicitly.")
        return self._store.add_markdown(
            session_id=self.session_id,
            invoke_turn_id=turn_id,
            scope=PromptAttachmentScope.TURN,
            **kwargs,
        )

    def add_current_turn_markdown(self, **kwargs: Any) -> PromptAttachment:
        return self.add_turn_markdown(**kwargs)

    def update_markdown(self, id_or_name: str, **kwargs: Any) -> PromptAttachment:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.update_markdown(id_or_name, session_id=self.session_id, **kwargs)

    def get(self, id_or_name: str, **kwargs: Any) -> PromptAttachment | None:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.get(id_or_name, session_id=self.session_id, **kwargs)

    def delete(self, id_or_name: str, **kwargs: Any) -> bool:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.delete(id_or_name, session_id=self.session_id, **kwargs)

    def list(self, **kwargs: Any) -> list[PromptAttachment]:
        self._bind_turn_id_if_needed(kwargs)
        return self._store.list(session_id=self.session_id, **kwargs)

    def _bind_turn_id_if_needed(self, kwargs: dict[str, Any]) -> None:
        scope = kwargs.get("scope")
        if scope is None or PromptAttachmentFileStore.coerce_scope(scope) != PromptAttachmentScope.TURN:
            return
        if kwargs.get("invoke_turn_id"):
            return
        if not self.invoke_turn_id:
            raise ValueError("No active invoke_turn_id is bound. Pass invoke_turn_id explicitly.")
        kwargs["invoke_turn_id"] = self.invoke_turn_id


class PromptAttachmentLoader:
    """Load jiuwenswarm prompt attachment files into a DeepAgent manager."""

    def __init__(self, root: Path | str, *, max_file_chars: int = DEFAULT_MAX_FILE_CHARS) -> None:
        self.root = Path(root)
        self.max_file_chars = max_file_chars
        self.file_store = PromptAttachmentFileStore(self.root, max_file_chars=max_file_chars)

    def for_context(self, ctx: Any) -> PromptAttachmentContextStore:
        """Return a context-bound file writer facade."""

        return self.file_store.for_context(ctx)

    def for_session(self, session_id: str, *, invoke_turn_id: str | None = None) -> PromptAttachmentSessionStore:
        """Return a session-bound file writer facade."""

        return self.file_store.for_session(session_id, invoke_turn_id=invoke_turn_id)

    def ensure_layout(self) -> None:
        """Create the root prompt attachment layout."""

        self.root.mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.md"
        should_write_readme = True
        if readme.exists():
            try:
                current = readme.read_text(encoding="utf-8")
                should_write_readme = current != _README_TEXT or any(ord(char) >= 128 for char in current)
            except UnicodeDecodeError:
                should_write_readme = True
        if should_write_readme:
            readme.write_text(_README_TEXT, encoding="utf-8")

    def load_session_attachments(self, session_id: str) -> list[PromptAttachment]:
        """Load session-scope prompt attachments for one jiuwenswarm session."""

        safe_session_id = sanitize_session_id(session_id)
        scope_dir = self.root / safe_session_id / "session"
        return self._load_scope_dir(
            scope_dir,
            scope=PromptAttachmentScope.SESSION,
            source=SESSION_SOURCE,
            session_id=session_id,
            safe_session_id=safe_session_id,
        )

    def load_turn_attachments(self, session_id: str, invoke_turn_id: str) -> list[PromptAttachment]:
        """Load turn-scope prompt attachments for one user request."""

        safe_session_id = sanitize_session_id(session_id)
        turn_root = self.root / safe_session_id / "turn"
        safe_turn_id = _safe_id_part(invoke_turn_id or "turn")
        return self._load_scope_dir(
            turn_root / safe_turn_id,
            scope=PromptAttachmentScope.TURN,
            source=TURN_SOURCE,
            session_id=session_id,
            safe_session_id=safe_session_id,
            invoke_turn_id=invoke_turn_id,
        )

    async def sync_to_agent(
        self,
        agent: Any,
        *,
        session_id: str,
        invoke_turn_id: str,
    ) -> None:
        """Synchronize current prompt attachment files to a DeepAgent instance.

        Loader failures are intentionally non-fatal. User requests must continue
        even if one prompt attachment file is unreadable.
        """

        try:
            manager = getattr(agent, "prompt_attachment_manager", None)
            if manager is None:
                raise AttributeError("agent.prompt_attachment_manager is unavailable")
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to get PromptAttachmentManager: %s", exc)
            return

        try:
            # Clear only this request's old turn attachments. Clearing the whole
            # session would make concurrent requests remove each other's
            # request-local attachments.
            await manager.remove_by_filter(
                source=TURN_SOURCE,
                session_id=session_id,
                invoke_turn_id=invoke_turn_id,
                scope=PromptAttachmentScope.TURN,
            )
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to clear old turn prompt attachments: %s", exc)

        try:
            session_attachments = self.load_session_attachments(session_id)
            turn_attachments = self.load_turn_attachments(session_id, invoke_turn_id)
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to load prompt attachment directory: %s", exc)
            return
        logger.info(
            "[PromptAttachmentLoader] sync prompt attachments: session_id=%s invoke_turn_id=%s session=%d turn=%d",
            session_id,
            invoke_turn_id,
            len(session_attachments),
            len(turn_attachments),
        )

        try:
            await manager.replace_source(
                source=SESSION_SOURCE,
                prompt_attachments=session_attachments,
                session_id=session_id,
                scope=PromptAttachmentScope.SESSION,
            )
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to sync session prompt attachments: %s", exc)

        try:
            await manager.replace_source(
                source=TURN_SOURCE,
                prompt_attachments=turn_attachments,
                session_id=session_id,
                invoke_turn_id=invoke_turn_id,
                scope=PromptAttachmentScope.TURN,
            )
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to sync turn prompt attachments: %s", exc)

    @staticmethod
    async def clear_turn_from_agent(
        agent: Any,
        *,
        session_id: str,
        invoke_turn_id: str,
    ) -> None:
        """Remove prompt attachments loaded for one completed request."""

        try:
            manager = getattr(agent, "prompt_attachment_manager", None)
            if manager is None:
                raise AttributeError("agent.prompt_attachment_manager is unavailable")
            # Request cleanup must stay scoped by invoke_turn_id for concurrent
            # requests in the same session.
            await manager.remove_by_filter(
                source=TURN_SOURCE,
                session_id=session_id,
                invoke_turn_id=invoke_turn_id,
                scope=PromptAttachmentScope.TURN,
            )
        except Exception as exc:
            logger.warning("[PromptAttachmentLoader] failed to clear completed turn prompt attachments: %s", exc)

    def _load_scope_dir(
        self,
        scope_dir: Path,
        *,
        scope: PromptAttachmentScope,
        source: str,
        session_id: str,
        safe_session_id: str,
        invoke_turn_id: str | None = None,
    ) -> list[PromptAttachment]:
        if not scope_dir.exists():
            return []
        if not scope_dir.is_dir():
            logger.warning("[PromptAttachmentLoader] prompt attachment scope path is not a directory: %s", scope_dir)
            return []

        items: list[PromptAttachment] = []
        for path in self._iter_attachment_files(scope_dir):
            try:
                if path.suffix.lower() == _JSON_SUFFIX:
                    item = self._read_json_file(
                        path,
                        scope_dir,
                        scope=scope,
                        source=source,
                        session_id=session_id,
                        invoke_turn_id=invoke_turn_id,
                    )
                    if item is not None:
                        items.append(item)
                    continue
                content = self._read_text_file(path, scope_dir)
            except Exception as exc:
                logger.warning("[PromptAttachmentLoader] failed to read prompt attachment file %s: %s", path, exc)
                continue
            if content is None:
                continue
            meta, content = _parse_frontmatter(content)
            metadata = _metadata_with_origin_source(dict(meta.get("metadata") or {}), meta.get("source"))
            metadata.update({"path": str(path), "relative_path": path.relative_to(scope_dir).as_posix()})
            relative_key = self._relative_key(path, scope_dir)
            if scope == PromptAttachmentScope.TURN:
                safe_turn_id = _safe_id_part(invoke_turn_id or "turn")
                item_id = f"{scope.value}.{safe_session_id}.{safe_turn_id}.{relative_key}"
            else:
                item_id = f"{scope.value}.{safe_session_id}.{relative_key}"
            items.append(PromptAttachment(
                id=item_id,
                scope=scope,
                kind=meta.get("kind") or self._kind_for_file(path),
                content=content,
                priority=int(meta.get("priority") or 0),
                source=source,
                session_id=session_id,
                invoke_turn_id=invoke_turn_id if scope == PromptAttachmentScope.TURN else None,
                metadata=metadata,
                content_kind="text/markdown" if path.suffix.lower() == ".md" else "text/plain",
            ))
        return items

    @staticmethod
    def _iter_attachment_files(scope_dir: Path) -> Iterable[Path]:
        files = list(_iter_safe_files(scope_dir, _TEXT_SUFFIXES | {_JSON_SUFFIX}))
        return sorted(files, key=lambda path: path.relative_to(scope_dir).as_posix())

    @staticmethod
    def _read_json_file(
        path: Path,
        scope_dir: Path,
        *,
        scope: PromptAttachmentScope,
        source: str,
        session_id: str,
        invoke_turn_id: str | None,
    ) -> PromptAttachment | None:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            logger.debug(
                "[PromptAttachmentLoader] skip empty prompt attachment file: %s",
                path.relative_to(scope_dir).as_posix(),
            )
            return None
        item = PromptAttachment(**json.loads(raw))
        if item.scope != scope:
            logger.warning("[PromptAttachmentLoader] skip prompt attachment with mismatched scope: %s", path)
            return None
        metadata = _metadata_with_origin_source(dict(item.metadata or {}), item.source)
        metadata.update({"path": str(path), "relative_path": path.relative_to(scope_dir).as_posix()})
        return item.model_copy(update={
            "source": source,
            "session_id": item.session_id or session_id,
            "invoke_turn_id": invoke_turn_id if scope == PromptAttachmentScope.TURN else None,
            "metadata": metadata,
        })

    def _read_text_file(self, path: Path, scope_dir: Path) -> str | None:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            logger.debug(
                "[PromptAttachmentLoader] skip empty prompt attachment file: %s",
                path.relative_to(scope_dir).as_posix(),
            )
            return None
        if self.max_file_chars > 0 and len(text) > self.max_file_chars:
            original_chars = len(text)
            text = text[:self.max_file_chars] + "\n\n[Prompt attachment file truncated by jiuwenswarm loader.]"
            logger.warning(
                "[PromptAttachmentLoader] truncated prompt attachment file: "
                "path=%s original_chars=%s truncated_chars=%s",
                path.relative_to(scope_dir).as_posix(),
                original_chars,
                len(text),
            )
        return text

    @staticmethod
    def kind_for_file(path: Path) -> PromptAttachmentKind:
        return _KIND_BY_STEM.get(path.stem, PromptAttachmentKind.TEXT)

    @staticmethod
    def _kind_for_file(path: Path) -> PromptAttachmentKind:
        return PromptAttachmentLoader.kind_for_file(path)

    @staticmethod
    def relative_key(path: Path, scope_dir: Path) -> str:
        rel = path.relative_to(scope_dir).with_suffix("")
        return ".".join(_safe_id_part(part) for part in rel.parts)

    @staticmethod
    def _relative_key(path: Path, scope_dir: Path) -> str:
        return PromptAttachmentLoader.relative_key(path, scope_dir)


__all__ = [
    "DEFAULT_MAX_FILE_CHARS",
    "PromptAttachmentContextStore",
    "PromptAttachmentFileStore",
    "PromptAttachmentLoader",
    "PromptAttachmentSessionStore",
    "SESSION_SOURCE",
    "TURN_SOURCE",
    "sanitize_session_id",
]
