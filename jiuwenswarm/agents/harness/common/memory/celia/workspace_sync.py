"""OpenClaw-compatible Celia marker files and update history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

OVERVIEW_MARKER = "CELIA_MEMORY_OVERVIEW"
SCENES_MARKER = "CELIA_MEMORY_SCENES"
_LOCK = threading.RLock()


def _utf8_trim(text: str, budget: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= budget:
        return text
    return data[: max(0, budget - 14)].decode("utf-8", errors="ignore").rstrip() + "\n- ..."


def _hash16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def marker_block(name: str, body: str, budget: int) -> str:
    body = _utf8_trim(body.strip(), budget - 160)
    return (
        f"<!-- {name}_BEGIN h={_hash16(body)} -->\n"
        f"{body}\n"
        f"<!-- {name}_END -->"
    )


def _marker_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^\s*<!--\s*{re.escape(name)}_BEGIN(?:\s+h=[0-9a-f]+)?\s*-->.*?"
        rf"<!--\s*{re.escape(name)}_END\s*-->\s*"
    )


def _semantic_body(block: str, name: str) -> str:
    body = re.sub(rf"(?m)^<!--\s*{re.escape(name)}_(?:BEGIN[^>]*|END)\s*-->\s*$", "", block)
    return "\n".join(line.rstrip() for line in body.strip().splitlines()).strip()


def read_marker_body(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    match = _marker_pattern(name).search(path.read_text(encoding="utf-8"))
    return _semantic_body(match.group(0), name) if match else ""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _append_history(history_path: Path, filename: str, detail: str) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().replace(tzinfo=None, microsecond=0).isoformat()
    with history_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{timestamp}|{filename.lower()}|{detail}\n")


def safe_write_marker(
    path: Path,
    name: str,
    body: str,
    budget: int,
    history_path: Path | None = None,
) -> bool:
    """Replace one managed block without changing user-authored content."""
    with _LOCK:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        pattern = _marker_pattern(name)
        matches = list(pattern.finditer(existing))
        old_semantic = _semantic_body(matches[0].group(0), name) if matches else ""
        new_semantic = body.strip()
        new_block = marker_block(name, body, budget) if new_semantic else ""
        broken = existing.count(f"{name}_BEGIN") != existing.count(f"{name}_END")
        if broken and existing:
            rescue = path.with_name(f"{path.name}.celia-rescue.{int(datetime.now().timestamp() * 1000)}")
            _atomic_write(rescue, existing)

        # Remove every complete block; append exactly one normalized block at file tail.
        cleaned = pattern.sub("", existing).rstrip()
        if broken:
            cleaned = re.sub(
                rf"(?ms)^\s*<!--\s*{re.escape(name)}_BEGIN[^>]*-->.*$",
                "",
                cleaned,
            ).rstrip()
            cleaned = re.sub(
                rf"(?m)^\s*<!--\s*{re.escape(name)}_END\s*-->\s*$",
                "",
                cleaned,
            ).rstrip()
        merged = cleaned
        if new_block:
            merged = f"{cleaned}\n\n{new_block}" if cleaned else new_block
        merged = merged.rstrip() + "\n"
        if merged == existing:
            return False
        _atomic_write(path, merged)
        if history_path is not None and old_semantic != new_semantic and new_semantic:
            detail = "更新了用户画像" if name == OVERVIEW_MARKER else "更新了高频场景信息"
            _append_history(history_path, path.name, detail)
        return True


def _unwrap(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return _unwrap(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return value
    if isinstance(value, dict):
        for key in ("result", "data", "content"):
            candidate = value.get(key)
            if candidate is not None and candidate is not value:
                unwrapped = _unwrap(candidate)
                if unwrapped not in (None, "", [], {}):
                    return unwrapped
    if isinstance(value, list) and len(value) == 1:
        item = value[0]
        if isinstance(item, dict) and "text" in item:
            return _unwrap(item["text"])
    return value


def extract_l0_body(value: Any) -> str:
    current = _unwrap(value)
    if isinstance(current, dict):
        for key in ("global_summary", "globalSummary", "summary"):
            if current.get(key) is not None:
                return str(current[key]).strip()
        return ""
    return str(current or "").strip()


def extract_l1_entries(value: Any) -> list[dict[str, Any]]:
    current = _unwrap(value)
    if isinstance(current, dict):
        for key in ("entries", "scenes", "items", "results"):
            if isinstance(current.get(key), list):
                current = current[key]
                break
    return [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []


def select_fixed_scenes(value: Any, dynamic_limit: int = 10) -> list[dict[str, Any]]:
    scenes = []
    for entry in extract_l1_entries(value):
        kind = entry.get("type")
        is_scene = kind == 1 or str(kind or "").strip().lower() == "scene"
        if is_scene:
            scenes.append(entry)
    presets = [item for item in scenes if bool(item.get("is_preset") or item.get("isPreset"))]
    dynamic = [item for item in scenes if item not in presets]

    def count(item: dict[str, Any]) -> float:
        try:
            return float(item.get("factCount") or item.get("fact_count") or 0)
        except (TypeError, ValueError):
            return 0

    dynamic.sort(key=count, reverse=True)
    return presets + dynamic[:dynamic_limit]


def render_scenes(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in entries:
        scene_id = item.get("id") or item.get("path") or item.get("scenePath") or "scene"
        summary = str(item.get("summary") or item.get("brief") or "").strip()
        lines.append(f"- [{scene_id}] {summary}".rstrip())
    return "\n".join(lines)


def sync_workspace_files(
    workspace_dir: Path,
    l0: Any,
    l1_index: Any,
    history_path: Path,
    *,
    sync_l0: bool = True,
    sync_l1: bool = True,
) -> tuple[str, str]:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("USER.md", "MEMORY.md"):
        target = workspace_dir / filename
        if not target.exists():
            _atomic_write(target, "")
    user_md = workspace_dir / "USER.md"
    memory_md = workspace_dir / "MEMORY.md"
    overview = extract_l0_body(l0) if sync_l0 else read_marker_body(user_md, OVERVIEW_MARKER)
    scenes = render_scenes(select_fixed_scenes(l1_index)) if sync_l1 else read_marker_body(memory_md, SCENES_MARKER)
    if sync_l0:
        safe_write_marker(user_md, OVERVIEW_MARKER, overview, 4 * 1024, history_path)
    if sync_l1:
        safe_write_marker(memory_md, SCENES_MARKER, scenes, 6 * 1024, history_path)
    return overview, scenes


def read_memory_history(history_path: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now().astimezone()
    local_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = local_midnight - timedelta(days=29)
    visible = local_midnight - timedelta(days=6)
    retained: list[str] = []
    result: list[dict[str, Any]] = []
    if history_path.is_file():
        for raw in history_path.read_text(encoding="utf-8").splitlines():
            parts = raw.split("|", 2)
            if len(parts) != 3:
                continue
            try:
                timestamp = datetime.fromisoformat(parts[0])
                if timestamp.tzinfo is None:
                    timestamp = timestamp.astimezone()
            except ValueError:
                continue
            if timestamp >= cutoff:
                retained.append(raw)
            if timestamp >= visible:
                result.append({"timestamp": parts[0], "fileName": parts[1], "detail": parts[2]})
    normalized = "\n".join(retained)
    if retained:
        normalized += "\n"
    if history_path.exists() and history_path.read_text(encoding="utf-8") != normalized:
        _atomic_write(history_path, normalized)
    result.sort(key=lambda item: item["timestamp"], reverse=True)
    return result
