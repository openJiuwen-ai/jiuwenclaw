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

from markdown_it import MarkdownIt

from jiuwenclaw.agentserver.tools.deepresearch_plugin.markdown_rewrite_map import (
    MarkdownRewriteMap,
    ProtectedAnchor,
    RewriteSlot,
    RewriteUnit,
    Utf8BoundaryTable,
    build_rewrite_map,
    structure_signature,
)

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
    parent_hash: str
    selection_start_byte: int
    selection_end_byte: int
    selected_units: tuple["_SelectedUnitRange", ...]
    structure_signature: tuple[object, ...]
    protected_anchors: tuple[ProtectedAnchor, ...]
    action: str
    instruction: str
    allowed_citations: dict[str, dict]


@dataclass(frozen=True, slots=True)
class _SelectedSlotRange:
    slot_id: str
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class _SelectedUnitRange:
    unit_id: str
    start_byte: int
    end_byte: int
    slots: tuple[_SelectedSlotRange, ...]


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


def _load_final_result_citations(report_path: Path, provenance: dict, root: Path) -> list[dict]:
    raw_path = provenance.get("final_result_path")
    expected_hash = provenance.get("final_result_sha256")
    if not isinstance(raw_path, str) or not raw_path or not isinstance(expected_hash, str):
        raise RewriteError("DOCUMENT_NOT_FOUND", "report final result is unavailable")
    snapshot_path = Path(raw_path).expanduser()
    if not snapshot_path.is_absolute():
        snapshot_path = report_path.parent / snapshot_path
    snapshot_path = snapshot_path.resolve()
    if not _inside(snapshot_path, root):
        raise RewriteError("BAD_REQUEST", "report final result is outside the current workspace")
    try:
        snapshot_bytes = snapshot_path.read_bytes()
    except OSError as exc:
        raise RewriteError("DOCUMENT_NOT_FOUND", "report final result is unavailable") from exc
    if _sha256(snapshot_bytes) != expected_hash:
        raise RewriteError("REVISION_CONFLICT", "the report final result changed")
    try:
        snapshot = json.loads(snapshot_bytes)
    except json.JSONDecodeError as exc:
        raise RewriteError("DOCUMENT_NOT_FOUND", "report final result is invalid") from exc
    citation_messages = snapshot.get("citation_messages") if isinstance(snapshot, dict) else None
    citations = citation_messages.get("data") if isinstance(citation_messages, dict) else None
    if not isinstance(citations, list) or any(not isinstance(item, dict) for item in citations):
        raise RewriteError("DOCUMENT_NOT_FOUND", "report citation data is invalid")
    return citations


def _citation_index(citations: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for item in citations:
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


def _mapping_conflict(message: str) -> None:
    raise RewriteError("SELECTION_MAPPING_CONFLICT", message)


def _intersects(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def _require_selection_protocol(selection: object) -> dict:
    if not isinstance(selection, dict):
        raise RewriteError(
            "SELECTION_PROTOCOL_UNSUPPORTED", "selection protocol version 2 is required"
        )
    version = selection.get("protocol_version")
    if type(version) is not int or version != 2:
        raise RewriteError(
            "SELECTION_PROTOCOL_UNSUPPORTED", "selection protocol version 2 is required"
        )
    return selection


def _validate_selection(selection: object, markdown: str) -> tuple[int, int, str]:
    selection = _require_selection_protocol(selection)
    start = selection.get("start_byte")
    end = selection.get("end_byte")
    selected_text = selection.get("selected_text")
    source_hash = selection.get("source_sha256")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        _mapping_conflict("selection byte range is invalid")
    if not isinstance(selected_text, str):
        _mapping_conflict("selected text must be a string")
    if not isinstance(source_hash, str) or re.fullmatch(r"[a-fA-F0-9]{64}", source_hash) is None:
        _mapping_conflict("selection source hash is invalid")
    boundary_table = Utf8BoundaryTable(markdown)
    try:
        boundary_table.require_byte_boundary(start)
        boundary_table.require_byte_boundary(end)
    except ValueError as exc:
        _mapping_conflict(str(exc))
    source_bytes = markdown.encode("utf-8")
    if end > len(source_bytes) or _sha256(source_bytes[start:end]) != source_hash.lower():
        _mapping_conflict("selection source hash does not match the document")
    return start, end, selected_text


def _slot_slice(slot: RewriteSlot, start: int, end: int) -> tuple[str, int, int] | None:
    boundaries = slot.visible_boundary_to_byte
    selected_start = max(start, slot.start_byte)
    selected_end = min(end, slot.end_byte)
    if selected_start >= selected_end:
        return None
    try:
        visible_start = boundaries.index(selected_start)
        visible_end = boundaries.index(selected_end)
    except ValueError:
        _mapping_conflict("selection endpoint is not a visible text boundary")
    return slot.text[visible_start:visible_end], selected_start, selected_end


def _anchor_visible_text(anchor: ProtectedAnchor) -> str:
    if anchor.kind == "citation":
        match = CITATION_RE.fullmatch(anchor.source)
        return f"[{match.group('index')}]" if match else ""
    if anchor.kind == "hard_break":
        return "\n"
    if anchor.kind == "inline_code":
        parsed = MarkdownIt("commonmark").parseInline(anchor.source)
        children = parsed[0].children if parsed else None
        if children and len(children) == 1 and children[0].type == "code_inline":
            return children[0].content
    return ""


def _unit_visible_text(unit: RewriteUnit, start: int, end: int) -> str:
    events: list[tuple[int, int, str]] = []
    for slot in unit.slots:
        sliced = _slot_slice(slot, start, end)
        if sliced is not None:
            text, selected_start, selected_end = sliced
            events.append((selected_start, selected_end, text))
    for anchor in unit.protected:
        if _intersects(start, end, anchor.start_byte, anchor.end_byte):
            events.append((anchor.start_byte, anchor.end_byte, _anchor_visible_text(anchor)))
    return "".join(item[2] for item in sorted(events))


def _full_unit_visible_text(unit: RewriteUnit) -> str:
    return _unit_visible_text(unit, unit.start_byte, unit.end_byte)


def _endpoint_is_visible(units: tuple[RewriteUnit, ...], endpoint: int) -> bool:
    return any(
        endpoint in slot.visible_boundary_to_byte
        for unit in units
        for slot in unit.slots
    )


def _selected_unit_payload(unit: RewriteUnit, start: int, end: int) -> dict:
    slots = []
    for slot in unit.slots:
        sliced = _slot_slice(slot, start, end)
        if sliced is None:
            continue
        text, _, _ = sliced
        value = {
            "slot_id": slot.slot_id,
            "text": text,
            "format": list(slot.formats),
        }
        if slot.link_id is not None:
            value["link_id"] = slot.link_id
        slots.append(value)
    return {
        "unit_id": unit.unit_id,
        "type": unit.unit_type,
        "level": unit.level,
        "list_depth": unit.list_depth,
        "list_marker": unit.list_marker,
        "slots": slots,
    }


def _selected_unit_range(
    unit: RewriteUnit, start: int, end: int
) -> _SelectedUnitRange:
    slots = []
    for slot in unit.slots:
        sliced = _slot_slice(slot, start, end)
        if sliced is not None:
            _, selected_start, selected_end = sliced
            slots.append(
                _SelectedSlotRange(slot.slot_id, selected_start, selected_end)
            )
    return _SelectedUnitRange(
        unit.unit_id,
        max(start, unit.start_byte),
        min(end, unit.end_byte),
        tuple(slots),
    )


def _selected_units(
    rewrite_map: MarkdownRewriteMap, start: int, end: int, provenance: dict
) -> tuple[RewriteUnit, ...]:
    for region in rewrite_map.unsupported_regions:
        if _intersects(start, end, region.start_byte, region.end_byte):
            raise RewriteError("UNSUPPORTED_SELECTION", "selection crosses unsupported Markdown")
    covered = tuple(
        unit
        for unit in rewrite_map.units
        if _intersects(start, end, unit.start_byte, unit.end_byte)
    )
    if not covered:
        raise RewriteError("UNSUPPORTED_SELECTION", "selection does not cover editable Markdown")

    protected = tuple(anchor for unit in covered for anchor in unit.protected)
    if any(
        anchor.kind == "inference"
        and _intersects(start, end, anchor.start_byte, anchor.end_byte)
        for anchor in protected
    ):
        raise RewriteError(
            "INFERENCE_REWRITE_UNSUPPORTED", "inference-linked text cannot be rewritten"
        )
    inference_paths = {
        item.get("path")
        for item in provenance.get("inference_manifest") or []
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("path")
    }
    if any(
        anchor.kind == "link_destination"
        and _intersects(start, end, anchor.start_byte, anchor.end_byte)
        and any(path in anchor.source for path in inference_paths)
        for anchor in protected
    ):
        raise RewriteError(
            "INFERENCE_REWRITE_UNSUPPORTED", "inference-linked text cannot be rewritten"
        )
    if any(
        anchor.kind == "image"
        and _intersects(start, end, anchor.start_byte, anchor.end_byte)
        for anchor in protected
    ):
        raise RewriteError("UNSUPPORTED_SELECTION", "images cannot be rewritten")
    if not _endpoint_is_visible(covered, start) or not _endpoint_is_visible(covered, end):
        raise RewriteError(
            "UNSUPPORTED_SELECTION", "selection endpoint is not an editable visible boundary"
        )

    indexes = [rewrite_map.units.index(unit) for unit in covered]
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise RewriteError("UNSUPPORTED_SELECTION", "selected units are not continuous")
    for unit in covered[1:-1]:
        if not unit.slots:
            raise RewriteError("UNSUPPORTED_SELECTION", "middle unit is not editable")
        editable_start = min(slot.visible_boundary_to_byte[0] for slot in unit.slots)
        editable_end = max(slot.visible_boundary_to_byte[-1] for slot in unit.slots)
        if start > editable_start or end < editable_end:
            raise RewriteError("UNSUPPORTED_SELECTION", "middle unit is only partially selected")
    list_depths = {unit.list_depth for unit in covered if unit.unit_type == "list_item"}
    if len(list_depths) > 1:
        raise RewriteError("UNSUPPORTED_SELECTION", "selected list items have different depths")
    return covered


def prepare_rewrite(
    *,
    workspace_root: str | Path,
    report_path: str | Path,
    action: str,
    selection: object,
    instruction: str = "",
    session_id: str,
) -> dict:
    root = Path(workspace_root).expanduser().resolve()
    report = Path(report_path).expanduser().resolve()
    _require_selection_protocol(selection)
    if not _inside(report, root) or report.suffix.lower() != ".md":
        raise RewriteError("BAD_REQUEST", "report path is outside the current workspace")
    if action not in {"shorten", "expand", "polish"}:
        raise RewriteError("BAD_REQUEST", "unsupported rewrite action")
    if not isinstance(instruction, str) or len(instruction) > 2_000:
        raise RewriteError("BAD_REQUEST", "instruction size is invalid")

    try:
        markdown = report.read_text(encoding="utf-8")
    except OSError as exc:
        raise RewriteError("DOCUMENT_NOT_FOUND", "report is unavailable") from exc
    provenance = _load_provenance(report)
    document_id = provenance.get("document_id")
    revision_id = provenance.get("revision_id")
    content_sha256 = provenance.get("content_sha256")
    if (
        not isinstance(document_id, str)
        or not SAFE_ID_RE.fullmatch(document_id)
        or not isinstance(revision_id, str)
        or not SAFE_ID_RE.fullmatch(revision_id)
        or not isinstance(content_sha256, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", content_sha256)
    ):
        raise RewriteError("DOCUMENT_NOT_FOUND", "report provenance is invalid")
    actual_hash = _sha256(markdown.encode("utf-8"))
    if actual_hash != content_sha256:
        raise RewriteError("REVISION_CONFLICT", "the report revision changed")
    final_result_citations = _load_final_result_citations(report, provenance, root)

    start, end, selected_text = _validate_selection(selection, markdown)
    if not selected_text or len(selected_text) > 12_000:
        raise RewriteError("BAD_REQUEST", "selection size is invalid")
    rewrite_map = build_rewrite_map(markdown)
    covered = _selected_units(rewrite_map, start, end, provenance)
    normalized_visible = "\n".join(
        _unit_visible_text(unit, start, end) for unit in covered
    ).replace("\r\n", "\n").replace("\r", "\n")
    if normalized_visible != selected_text.replace("\r\n", "\n").replace("\r", "\n"):
        _mapping_conflict("selected text does not match normalized Markdown visibility")

    _, by_key = _citation_index(final_result_citations)
    allowed: dict[str, dict] = {}
    selected_anchors = tuple(
        anchor
        for unit in covered
        for anchor in unit.protected
        if _intersects(start, end, anchor.start_byte, anchor.end_byte)
    )
    for anchor in selected_anchors:
        if anchor.kind != "citation":
            continue
        match = CITATION_RE.fullmatch(anchor.source)
        if match is not None:
            citation = by_key.get(f"{match.group('index')}\0{match.group('url')}")
            if citation is not None:
                allowed[str(citation.get("id"))] = citation

    first_index = rewrite_map.units.index(covered[0])
    last_index = rewrite_map.units.index(covered[-1])
    previous = rewrite_map.units[first_index - 1] if first_index else None
    next_unit = (
        rewrite_map.units[last_index + 1]
        if last_index + 1 < len(rewrite_map.units)
        else None
    )
    unit_payloads = tuple(_selected_unit_payload(unit, start, end) for unit in covered)
    selected_unit_ranges = tuple(
        _selected_unit_range(unit, start, end) for unit in covered
    )

    token = uuid.uuid4().hex
    context = _RewriteContext(
        expires_at=time.monotonic() + TOKEN_TTL_SECONDS,
        session_id=session_id,
        workspace_root=root,
        report_path=report,
        provenance=provenance,
        parent_hash=actual_hash,
        selection_start_byte=start,
        selection_end_byte=end,
        selected_units=selected_unit_ranges,
        structure_signature=structure_signature(rewrite_map),
        protected_anchors=selected_anchors,
        action=action,
        instruction=instruction,
        allowed_citations=allowed,
    )
    with _CONTEXT_LOCK:
        _CONTEXTS[token] = context
    return {
        "context_token": token,
        "action_category": "synonym_rewrite",
        "action": action,
        "units": list(unit_payloads),
        "readonly_context": {
            "previous_unit": _full_unit_visible_text(previous) if previous else None,
            "next_unit": _full_unit_visible_text(next_unit) if next_unit else None,
        },
        "instruction": instruction,
        "allowed_source_ids": sorted(allowed),
        "citation_evidence": [
            {name: allowed[key].get(name) for name in ("id", "title", "content", "chunk", "source")}
            for key in sorted(allowed)
        ],
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
        current_bytes = context.report_path.read_bytes()
        parent_hash = _sha256(current_bytes)
        if parent_hash != context.parent_hash:
            raise RewriteError("REVISION_CONFLICT", "the parent report changed")
        child_bytes = (
            current_bytes[: context.selection_start_byte]
            + replacement.encode("utf-8")
            + current_bytes[context.selection_end_byte :]
        )
        child_markdown = child_bytes.decode("utf-8")
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
                current_bytes[context.selection_start_byte : context.selection_end_byte]
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
        "citation_integrity_status": "verified",
        "citation_semantic_status": "not_verified",
    }
