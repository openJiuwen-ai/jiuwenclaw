"""Public API for the Tool progressive retrieval index.

Exposes the same shape as ``symphony.skill_retrieval.api`` so the existing
AgenticRetrievalToolKit can consume tool indexes without changes.

.. note::
    The vendored indexing/retrieval code is imported lazily inside
    ``dispatch_import_path()`` blocks so this module can be imported
    before sys.path injection.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ToolIndexConfig, load_tool_index_config
from .scanner import ToolScanner, ToolInventory

LOGGER = logging.getLogger(__name__)

TREE_INDEX_FILENAME = "tree_index.yaml"
CATALOG_FILENAME = "catalog.jsonl"
MANIFEST_FILENAME = "manifest.json"
STATE_FILENAME = "state.json"


# =========================================================================
# Public API (mirrors symphony.skill_retrieval.api)
# =========================================================================


def build_tool_index(
    tool_cards: dict[str, Any],
    *,
    force: bool = False,
    config: ToolIndexConfig | None = None,
    enabled_tool_names: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build or refresh the progressive tool retrieval index.

    Args:
        tool_cards: ``{name: ToolCard}`` mapping from the current runtime.
        force: If True, rebuild even when the index is fresh.
        config: Optional ToolIndexConfig; uses defaults when omitted.
        enabled_tool_names: If non-empty, only these tools are indexed.

    Returns:
        ``{"success": bool, "result": str}`` (same shape as skill retrieval).
    """
    cfg = config or load_tool_index_config()
    if not cfg.enabled:
        return {"success": False, "result": _render_disabled()}

    scanner = ToolScanner(tool_cards, enabled_tool_names=enabled_tool_names)
    started = time.monotonic()

    cfg.artifact_root.mkdir(parents=True, exist_ok=True)
    index_dir = cfg.artifact_root / "index"

    inventory = scanner.scan_inventory()

    if inventory.count == 0:
        return {
            "success": False,
            "result": "# Tool Index Build\n\nNo tools found in the registry.",
        }

    expected_fp = inventory.fingerprint

    # Reuse existing fresh index
    if not force and _is_complete_index(index_dir):
        state = _read_state(cfg)
        if (
            _manifest_matches(index_dir, inventory)
            and state.get("fingerprint") == expected_fp
        ):
            return {
                "success": True,
                "result": _render_success(
                    reused=True,
                    count=inventory.count,
                    index_dir=str(index_dir),
                    elapsed=time.monotonic() - started,
                ),
            }

    # Build
    try:
        with tempfile.TemporaryDirectory(prefix="tool-index-build-") as tmp:
            build_dir = Path(tmp) / "index"
            build_dir.mkdir(parents=True, exist_ok=True)

            _dispatch_build(
                scanner=scanner,
                inventory=inventory,
                output_dir=build_dir,
            )

            if not _is_complete_index(build_dir):
                raise RuntimeError("build finished without complete index artifacts")

            # Publish atomically
            _publish_index(config=cfg, candidate_dir=build_dir)

        _write_state(cfg, inventory=inventory, fingerprint=expected_fp)

        return {
            "success": True,
            "result": _render_success(
                reused=False,
                count=inventory.count,
                index_dir=str(index_dir),
                elapsed=time.monotonic() - started,
            ),
        }
    except Exception as exc:
        LOGGER.exception("Tool index build failed")
        return {
            "success": False,
            "result": f"# Tool Index Build\n\nBuild failed:\n\n{exc}",
        }


def tool_index_status(
    tool_cards: dict[str, Any] | None = None,
    *,
    config: ToolIndexConfig | None = None,
    enabled_tool_names: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return the current status of the tool retrieval index."""
    cfg = config or load_tool_index_config()
    scanner = ToolScanner(
        tool_cards or {}, enabled_tool_names=enabled_tool_names
    )
    inventory = scanner.scan_inventory()
    index_dir = cfg.artifact_root / "index"
    state = _read_state(cfg)
    complete = _is_complete_index(index_dir)
    fresh = (
        complete
        and state.get("fingerprint") == inventory.fingerprint
    )

    return {
        "enabled": cfg.enabled,
        "artifact_root": str(cfg.artifact_root),
        "index_dir": str(index_dir),
        "index_exists": complete,
        "fresh": fresh,
        "tool_count": inventory.count,
        "indexed_count": int(state.get("indexed_count") or 0) if complete else 0,
        "built_at": str(state.get("built_at") or ""),
        "fingerprint": str(state.get("fingerprint") or ""),
        "inventory_fingerprint": inventory.fingerprint,
    }


def tool_index_tree(
    tool_cards: dict[str, Any] | None = None,
    *,
    config: ToolIndexConfig | None = None,
    enabled_tool_names: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Read the tool tree index and return a human-readable outline."""
    cfg = config or load_tool_index_config()
    index_dir = cfg.artifact_root / "index"

    if not _is_complete_index(index_dir):
        return {"success": False, "result": "No tool index available."}

    import yaml

    tree_path = index_dir / TREE_INDEX_FILENAME
    try:
        payload = yaml.safe_load(tree_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"success": False, "result": f"Failed to read tree: {exc}"}

    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        return {"success": False, "result": "Invalid tree structure."}

    branch_count = sum(
        1 for n in nodes if isinstance(n, dict) and n.get("type") == "branch"
    )
    leaf_count = sum(
        1 for n in nodes if isinstance(n, dict) and n.get("type") == "leaf"
    )

    # Render a compact outline
    lines = [
        f"# Tool Index Tree",
        "",
        f"- Branch nodes: {branch_count}",
        f"- Tool leaves: {leaf_count}",
        "",
    ]
    by_cid: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("cid") or "").strip()
        if not cid:
            continue
        by_cid[cid] = raw
        parent = cid.rsplit(".", 1)[0] if "." in cid else ""
        children.setdefault(parent, []).append(cid)

    emitted = 0
    max_nodes = 200

    def walk(parent: str, depth: int) -> None:
        nonlocal emitted
        for cid in sorted(
            children.get(parent, []),
            key=lambda c: (c.count("."), c.lower()),
        ):
            if emitted >= max_nodes:
                return
            node = by_cid.get(cid, {})
            label = (
                cid.rsplit(".", 1)[-1]
                if node.get("type") != "leaf"
                else str(node.get("worker_id") or cid.rsplit(".", 1)[-1])
            )
            desc = _compact(str(node.get("description") or ""), 100)
            lines.append(f"{'  ' * depth}- {label}" + (f" — {desc}" if desc else ""))
            emitted += 1
            walk(cid, depth + 1)

    walk("", 0)
    if emitted < len(by_cid):
        lines.append(f"\n... {len(by_cid) - emitted} more nodes omitted")

    return {"success": True, "result": "\n".join(lines)}


# =========================================================================
# Internal helpers
# =========================================================================


def _dispatch_build(
    *,
    scanner: ToolScanner,
    inventory: ToolInventory,
    output_dir: Path,
) -> None:
    """Call the vendored indexing pipeline to build a tree index.

    Writes a pre-scanned items JSONL file and feeds it through
    ``IndexBuilder.build(item_jsonl_path=...)``, which bypasses the
    file-system scanner entirely — the vendored pipeline only sees the
    already-normalised ``ScannedItem`` records.
    """
    from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import (
        dispatch_import_path,
    )

    # Write items.jsonl in the format expected by parse_jsonl_scanned_items
    items_jsonl = output_dir.parent / "items.jsonl"
    with open(items_jsonl, "w", encoding="utf-8") as f:
        for item in scanner.scan():
            record = {
                "contentExtendParam": {
                    "skillId": item.id,
                    "skillName": item.name,
                    "skillDesc": item.description,
                    "skillPath": item.item_path,
                    "skillContent": item.content,
                }
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    with dispatch_import_path():
        from indexing.workflows.index_builder import IndexBuilder

        from indexing.tree.builder import TreeBuilder
        _ensure_tree_builder_compat(TreeBuilder)

        IndexBuilder.build(
            output_dir=output_dir,
            item_type="skill",
            item_jsonl_path=str(items_jsonl),
            config=_make_build_config(),
        )


def _make_build_config() -> Any:
    """Build an IndexBuilder-compatible config for Tool tree building."""
    from jiuwenswarm.symphony.skill_retrieval.dispatch_imports import (
        dispatch_import_path,
    )

    with dispatch_import_path():
        from indexing.workflows.artifacts import (
            BuildConfig,
            BuildExecutionConfig,
            BuildLLMConfig,
            BuildOutputConfig,
            TaxonomyBuildConfig,
        )

        # Reuse SkillRetrieval's LLM config — same model, same key.
        from jiuwenswarm.symphony.skill_retrieval.config import (
            load_settings as load_skill_settings,
        )

        skill_settings = load_skill_settings()
        llm_config = BuildLLMConfig(
            model=skill_settings.llm.model,
            api_key=skill_settings.llm.api_key,
            base_url=skill_settings.llm.base_url,
            seed=skill_settings.llm.seed,
        )
        return BuildConfig(
            llm_config=llm_config,
            taxonomy_config=TaxonomyBuildConfig(
                branching_factor=skill_settings.build.branching_factor,
                max_depth=skill_settings.build.max_depth,
            ),
            execution_config=BuildExecutionConfig(
                max_workers=skill_settings.build.max_workers,
                max_retries=skill_settings.build.max_retries,
                request_timeout_seconds=skill_settings.build.request_timeout_seconds,
                classification_batch_limit=skill_settings.build.classification_batch_limit,
                discovery_seed=skill_settings.build.discovery_seed,
            ),
            output_config=BuildOutputConfig(generate_html=False),
        )


def _is_complete_index(path: Path) -> bool:
    return (
        (path / TREE_INDEX_FILENAME).is_file()
        and (path / CATALOG_FILENAME).is_file()
        and (path / MANIFEST_FILENAME).is_file()
    )


def _manifest_matches(index_dir: Path, inventory: ToolInventory) -> bool:
    try:
        payload = json.loads(
            (index_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
    except Exception:
        return False
    raw = payload.get("item_paths") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return False
    expected = set(inventory.item_paths)
    actual = {str(p) for p in raw}
    return expected == actual


def _read_state(config: ToolIndexConfig) -> dict[str, Any]:
    path = config.artifact_root / STATE_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(
    config: ToolIndexConfig,
    *,
    inventory: ToolInventory,
    fingerprint: str,
) -> None:
    payload = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "indexed_count": inventory.count,
        "index_dir": str(config.artifact_root / "index"),
        "inventory": inventory.to_state_payload(),
    }
    state_path = config.artifact_root / STATE_FILENAME
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _publish_index(*, config: ToolIndexConfig, candidate_dir: Path) -> None:
    final_dir = config.artifact_root / "index"
    backup = config.artifact_root / f"index.backup-{time.time_ns()}"
    if final_dir.exists():
        final_dir.rename(backup)
    try:
        candidate_dir.rename(final_dir)
    except Exception:
        if backup.exists() and not final_dir.exists():
            backup.rename(final_dir)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _ensure_tree_builder_compat(tree_builder_cls: type) -> None:
    """Apply the same compat patches as SkillIndexService._ensure_tree_builder_compat."""
    default_attrs = {
        "_skill_profiles_enabled": False,
        "_cache_observability": False,
    }
    for name, value in default_attrs.items():
        if not hasattr(tree_builder_cls, name):
            setattr(tree_builder_cls, name, value)
    if not hasattr(tree_builder_cls, "_write_yaml"):
        from jiuwenswarm.symphony.skill_retrieval.index_service import (
            _write_tree_yaml,
        )
        setattr(tree_builder_cls, "_write_yaml", _write_tree_yaml)


def _render_disabled() -> str:
    return "# Tool Index\n\nTool progressive retrieval is disabled."


def _render_success(
    *,
    reused: bool,
    count: int,
    index_dir: str,
    elapsed: float,
) -> str:
    action = "Reused existing" if reused else "Built"
    return (
        f"# Tool Index Build\n\n"
        f"{action} tool index with {count} tools in {elapsed:.1f}s.\n\n"
        f"Index directory: `{index_dir}`\n\n"
        f"Use `tool_branch_explore` or `tool_branch_peek` to browse."
    )


def _compact(text: str, limit: int) -> str:
    compacted = " ".join(text.split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: max(0, limit - 3)].rstrip() + "..."


class _NullWriter:
    @staticmethod
    def write(text: str) -> int:
        return len(text)

    @staticmethod
    def flush() -> None:
        pass


class _suppress_console:
    """Context manager to silence the indexing pipeline's console output."""

    def __enter__(self) -> None:
        import sys
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = _NullWriter()  # type: ignore[assignment]
        sys.stderr = _NullWriter()  # type: ignore[assignment]

    def __exit__(self, *args: Any) -> None:
        import sys
        sys.stdout = self._stdout
        sys.stderr = self._stderr
