# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic provenance checks and immutable DeepResearch revisions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SAFE_ID_RE = re.compile(r"^(?:doc|rev)_[A-Za-z0-9_-]{1,128}$")
CITATION_RE = re.compile(r"\[\[(?P<index>\d+)\]\]\((?P<url>https?://[^\s)]+)\)")
FORBIDDEN_OUTPUT_RE = re.compile(r"https?://|\]\s*:\s*\S+|#inference:", re.IGNORECASE)
LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
TOKEN_TTL_SECONDS = 10 * 60


class RewriteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RewriteBlock:
    block_id: str
    start: int
    end: int
    text: str


@dataclass(slots=True)
class _RewriteContext:
    expires_at: float
    session_id: str
    workspace_root: Path
    report_path: Path
    provenance: dict
    block: RewriteBlock
    selection_start: int
    selection_end: int
    action: str
    instruction: str
    allowed_citations: dict[str, dict]


_CONTEXTS: dict[str, _RewriteContext] = {}
_CONTEXT_LOCK = threading.Lock()
_DOCUMENT_LOCKS: dict[str, threading.Lock] = {}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _block_id(start: int, text: str) -> str:
    digest = 0x811C9DC5
    for byte in text.encode("utf-8"):
        digest ^= byte
        digest = (digest * 0x01000193) & 0xFFFFFFFF
    return f"block_{start}_{digest:08x}"


def _is_unsupported_line(line: str) -> bool:
    stripped = line.lstrip()
    return (
        stripped.startswith("#")
        or stripped.startswith(">")
        or stripped.startswith("<")
        or stripped.startswith("```")
        or stripped.startswith("~~~")
        or "|" in stripped
        or stripped.startswith("![")
    )


def iter_rewrite_blocks(markdown: str) -> Iterator[RewriteBlock]:
    """Yield source-addressable plain paragraphs and single list items."""
    lines = markdown.splitlines(keepends=True)
    offset = 0
    index = 0
    in_fence = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            offset += len(line)
            index += 1
            continue
        if in_fence or not stripped or _is_unsupported_line(line):
            offset += len(line)
            index += 1
            continue

        start = offset
        if LIST_RE.match(line):
            text = line.rstrip("\r\n")
            offset += len(line)
            index += 1
        else:
            parts: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip() or _is_unsupported_line(candidate) or LIST_RE.match(candidate):
                    break
                parts.append(candidate)
                offset += len(candidate)
                index += 1
            text = "".join(parts).rstrip("\r\n")
        if text:
            yield RewriteBlock(_block_id(start, text), start, start + len(text), text)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_provenance(report_path: Path) -> dict:
    sidecar = report_path.with_suffix(".provenance.json")
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RewriteError("DOCUMENT_NOT_FOUND", "report provenance is unavailable") from exc
    if not isinstance(value, dict):
        raise RewriteError("DOCUMENT_NOT_FOUND", "report provenance is invalid")
    return value


def _citation_index(provenance: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for item in provenance.get("citations") or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("id", ""))
        url = str(item.get("url", ""))
        reference_index = str(item.get("reference_index", ""))
        if source_id:
            by_id[source_id] = item
        if url and reference_index:
            by_key[f"{reference_index}\0{url}"] = item
    return by_id, by_key


def _block_contains_inference(block_text: str, provenance: dict) -> bool:
    if "#inference:" in block_text:
        return True
    for item in provenance.get("inference_manifest") or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "")
            if path and path in block_text:
                return True
    return False


def prepare_rewrite(
    *,
    workspace_root: str | Path,
    report_path: str | Path,
    document_id: str,
    revision_id: str,
    content_sha256: str,
    action: str,
    block_id: str,
    start: int,
    end: int,
    selected_text: str,
    prefix: str = "",
    suffix: str = "",
    instruction: str = "",
    session_id: str,
) -> dict:
    root = Path(workspace_root).expanduser().resolve()
    report = Path(report_path).expanduser().resolve()
    if not _inside(report, root) or report.suffix.lower() != ".md":
        raise RewriteError("BAD_REQUEST", "report path is outside the current workspace")
    if not SAFE_ID_RE.fullmatch(document_id) or not SAFE_ID_RE.fullmatch(revision_id):
        raise RewriteError("BAD_REQUEST", "invalid document or revision id")
    if action not in {"rewrite", "expand", "polish"}:
        raise RewriteError("BAD_REQUEST", "unsupported rewrite action")
    if not selected_text or len(selected_text) > 12_000 or len(instruction) > 2_000:
        raise RewriteError("BAD_REQUEST", "selection or instruction size is invalid")

    try:
        markdown = report.read_text(encoding="utf-8")
    except OSError as exc:
        raise RewriteError("DOCUMENT_NOT_FOUND", "report is unavailable") from exc
    provenance = _load_provenance(report)
    actual_hash = _sha256(markdown.encode("utf-8"))
    if (
        provenance.get("document_id") != document_id
        or provenance.get("revision_id") != revision_id
        or provenance.get("content_sha256") != content_sha256
        or actual_hash != content_sha256
    ):
        raise RewriteError("REVISION_CONFLICT", "the report revision changed")

    block = next((item for item in iter_rewrite_blocks(markdown) if item.block_id == block_id), None)
    if block is None:
        raise RewriteError("UNSUPPORTED_SELECTION", "the selected Markdown block is unsupported")
    if _block_contains_inference(block.text, provenance):
        raise RewriteError("INFERENCE_REWRITE_UNSUPPORTED", "inference-linked text cannot be rewritten")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(block.text):
        raise RewriteError("REVISION_CONFLICT", "selection offsets are stale")
    if block.text[start:end] != selected_text:
        raise RewriteError("REVISION_CONFLICT", "selected text no longer matches the report")
    if prefix and block.text[max(0, start - len(prefix)):start] != prefix:
        raise RewriteError("REVISION_CONFLICT", "selection prefix no longer matches")
    if suffix and block.text[end:end + len(suffix)] != suffix:
        raise RewriteError("REVISION_CONFLICT", "selection suffix no longer matches")

    _, by_key = _citation_index(provenance)
    allowed: dict[str, dict] = {}
    for match in CITATION_RE.finditer(block.text):
        citation = by_key.get(f"{match.group('index')}\0{match.group('url')}")
        if citation is not None:
            allowed[str(citation.get("id"))] = citation

    token = uuid.uuid4().hex
    context = _RewriteContext(
        expires_at=time.monotonic() + TOKEN_TTL_SECONDS,
        session_id=session_id,
        workspace_root=root,
        report_path=report,
        provenance=provenance,
        block=block,
        selection_start=start,
        selection_end=end,
        action=action,
        instruction=instruction,
        allowed_citations=allowed,
    )
    with _CONTEXT_LOCK:
        _CONTEXTS[token] = context
    return {
        "context_token": token,
        "action": action,
        "selected_text": selected_text,
        "block_context": block.text,
        "instruction": instruction,
        "allowed_source_ids": sorted(allowed),
        "citation_evidence": [allowed[key] for key in sorted(allowed)],
    }


def _take_context(token: str, session_id: str) -> _RewriteContext:
    with _CONTEXT_LOCK:
        context = _CONTEXTS.get(token)
        if context is None or context.expires_at < time.monotonic() or context.session_id != session_id:
            if context is not None and context.expires_at < time.monotonic():
                _CONTEXTS.pop(token, None)
            raise RewriteError("CONTEXT_EXPIRED", "rewrite context is missing or expired")
        _CONTEXTS.pop(token, None)
        return context


def _validate_segments(structured_result: object, allowed: dict[str, dict]) -> list[dict]:
    if not isinstance(structured_result, dict) or structured_result.get("facts_added") is not False:
        raise RewriteError("MODEL_OUTPUT_INVALID", "facts_added must be false")
    segments = structured_result.get("segments")
    if not isinstance(segments, list) or not segments or len(segments) > 50:
        raise RewriteError("MODEL_OUTPUT_INVALID", "segments must be a non-empty list")
    total = 0
    normalized: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise RewriteError("MODEL_OUTPUT_INVALID", "each segment must be an object")
        text = segment.get("text")
        source_ids = segment.get("source_ids", [])
        if not isinstance(text, str) or not text.strip() or FORBIDDEN_OUTPUT_RE.search(text):
            raise RewriteError("MODEL_OUTPUT_INVALID", "segment text is invalid")
        if not isinstance(source_ids, list) or any(str(item) not in allowed for item in source_ids):
            raise RewriteError("MODEL_OUTPUT_INVALID", "segment cites a source outside the whitelist")
        total += len(text)
        if total > 24_000:
            raise RewriteError("MODEL_OUTPUT_INVALID", "rewrite output is too large")
        normalized.append({"text": text, "source_ids": [str(item) for item in source_ids]})
    return normalized


def _render_segments(segments: list[dict], allowed: dict[str, dict]) -> str:
    rendered: list[str] = []
    for segment in segments:
        citations: list[str] = []
        seen: set[str] = set()
        for source_id in segment["source_ids"]:
            if source_id in seen:
                continue
            seen.add(source_id)
            item = allowed[source_id]
            citations.append(f"[[{item['reference_index']}]]({item['url']})")
        rendered.append(segment["text"] + "".join(citations))
    return "\n\n".join(rendered)


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def commit_rewrite(*, context_token: str, session_id: str, structured_result: object) -> dict:
    context = _take_context(context_token, session_id)
    segments = _validate_segments(structured_result, context.allowed_citations)
    replacement = _render_segments(segments, context.allowed_citations)
    document_id = str(context.provenance["document_id"])
    with _CONTEXT_LOCK:
        document_lock = _DOCUMENT_LOCKS.setdefault(document_id, threading.Lock())
    with document_lock:
        current = context.report_path.read_text(encoding="utf-8")
        parent_hash = _sha256(current.encode("utf-8"))
        if parent_hash != context.provenance.get("content_sha256"):
            raise RewriteError("REVISION_CONFLICT", "the parent report changed")
        absolute_start = context.block.start + context.selection_start
        absolute_end = context.block.start + context.selection_end
        child_markdown = current[:absolute_start] + replacement + current[absolute_end:]
        revision_id = f"rev_{uuid.uuid4().hex}"
        child_path = context.report_path.with_name(
            f"{context.report_path.stem}-rev-{revision_id[4:12]}.md"
        )
        child_hash = _sha256(child_markdown.encode("utf-8"))
        child_provenance = dict(context.provenance)
        history = list(context.provenance.get("rewrite_history") or [])
        history.append({
            "action": context.action,
            "parent_revision_id": context.provenance["revision_id"],
            "selection_sha256": _sha256(
                context.block.text[context.selection_start:context.selection_end].encode("utf-8")
            ),
            "result_sha256": _sha256(replacement.encode("utf-8")),
            "source_ids": sorted(context.allowed_citations),
        })
        child_provenance.update({
            "revision_id": revision_id,
            "parent_revision_id": context.provenance["revision_id"],
            "markdown_path": str(child_path),
            "content_sha256": child_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": {"action": context.action},
            "rewrite_history": history,
        })
        _atomic_write(child_path, child_markdown.encode("utf-8"))
        provenance_path = child_path.with_suffix(".provenance.json")
        _atomic_write(
            provenance_path,
            json.dumps(child_provenance, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return {
        "document_id": document_id,
        "revision_id": revision_id,
        "parent_revision_id": context.provenance["revision_id"],
        "report_path": str(child_path),
        "provenance_path": str(provenance_path),
        "citation_status": "verified",
    }
