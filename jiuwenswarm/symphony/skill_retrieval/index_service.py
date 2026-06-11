from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import SkillRetrievalSettings, load_settings
from .dispatch_imports import dispatch_import_path
from .inventory import SkillInventory, scan_skill_inventory
from .markdown import render_build_failure, render_build_success, render_disabled

TREE_INDEX_FILENAME = "tree_index.yaml"
CATALOG_FILENAME = "catalog.jsonl"
MANIFEST_FILENAME = "manifest.json"
STATE_FILENAME = "state.json"
LOGGER = logging.getLogger(__name__)


class SkillIndexService:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def status(self) -> dict[str, Any]:
        settings = load_settings()
        inventory = scan_skill_inventory(self._manager)
        index_dir = _index_dir(settings)
        state = _read_state(settings)
        expected = _expected_fingerprint(inventory, settings)
        complete = _is_complete_index(index_dir)
        fresh = complete and state.get("fingerprint") == expected
        return {
            "enabled": settings.enabled,
            "artifact_root": str(settings.artifact_root),
            "index_dir": str(index_dir),
            "index_exists": complete,
            "fresh": fresh,
            "installed_enabled_count": inventory.count,
            "indexed_count": int(state.get("indexed_count") or 0) if complete else 0,
            "built_at": str(state.get("built_at") or ""),
            "inventory_fingerprint": inventory.fingerprint,
            "fingerprint": str(state.get("fingerprint") or ""),
            "default_compact_codes_enabled": settings.retrieve.compact_codes_enabled,
            "default_flatten_tree": settings.retrieve.flatten_tree,
        }

    def build_index(self) -> dict[str, Any]:
        started = time.monotonic()
        settings = load_settings()
        if not settings.enabled:
            return {"success": False, "result": render_disabled()}

        inventory = scan_skill_inventory(self._manager)
        if inventory.count == 0:
            return {
                "success": False,
                "result": render_build_failure(
                    "No enabled installed skills were found under the agent skills directory."
                ),
            }

        settings.artifact_root.mkdir(parents=True, exist_ok=True)
        _tmp_dir(settings).mkdir(parents=True, exist_ok=True)
        expected = _expected_fingerprint(inventory, settings)

        recovered = self._recover_index(settings=settings, inventory=inventory, expected_fingerprint=expected)
        state = _read_state(settings)
        if _is_complete_index(_index_dir(settings)) and state.get("fingerprint") == expected:
            return {
                "success": True,
                "result": render_build_success(
                    reused=True,
                    inventory=inventory,
                    index_dir=str(_index_dir(settings)),
                    elapsed_seconds=time.monotonic() - started,
                ),
                "recovered": recovered,
            }

        if not settings.llm.model or not settings.llm.api_key:
            return {
                "success": False,
                "result": render_build_failure(
                    "Offline dispatch tree build requires a model and API key. "
                    "Configure `models.defaults[0].model_client_config` or `symphony.skill_retrieval.llm`."
                ),
            }

        build_root = (
            _tmp_dir(settings) / f"build-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{time.time_ns()}"
        )
        build_index_dir = build_root / "index"
        try:
            build_index_dir.mkdir(parents=True, exist_ok=True)
            self._run_dispatch_build(settings=settings, inventory=inventory, output_dir=build_index_dir)
            if not _is_complete_index(build_index_dir):
                raise RuntimeError("dispatch build finished without complete index artifacts")
            _publish_index(settings=settings, candidate_dir=build_index_dir)
            _write_state(settings, inventory=inventory, fingerprint=expected)
        except Exception as exc:
            return {"success": False, "result": render_build_failure(str(exc))}
        finally:
            if _is_complete_index(_index_dir(settings)) and build_root.exists():
                shutil.rmtree(build_root, ignore_errors=True)

        return {
            "success": True,
            "result": render_build_success(
                reused=False,
                inventory=inventory,
                index_dir=str(_index_dir(settings)),
                elapsed_seconds=time.monotonic() - started,
            ),
        }

    @staticmethod
    def tree() -> dict[str, Any]:
        settings = load_settings()
        if not settings.enabled:
            return {"success": False, "result": render_disabled()}

        index_dir = _index_dir(settings)
        if not _is_complete_index(index_dir):
            return {
                "success": False,
                "result": (
                    "# Skill Index Tree\n\n"
                    "The installed-skill retrieval index has not been built yet.\n\n"
                    "Use `skill_index_build` or the web page build button to build the index, "
                    "or ignore retrieval and continue with the original jiuwenswarm flow."
                ),
            }

        tree_path = index_dir / TREE_INDEX_FILENAME
        try:
            payload = yaml.safe_load(tree_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return {"success": False, "result": f"# Skill Index Tree\n\nFailed to read `{tree_path}`: {exc}"}

        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if not isinstance(nodes, list):
            return {
                "success": False,
                "result": f"# Skill Index Tree\n\n`{tree_path}` does not contain a valid nodes list.",
            }

        branch_count = sum(1 for node in nodes if isinstance(node, dict) and node.get("type") == "branch")
        leaf_count = sum(1 for node in nodes if isinstance(node, dict) and node.get("type") == "leaf")
        tree_nodes = _tree_node_payload(nodes)
        return {
            "success": True,
            "result": (
                "# Skill Index Tree\n\n"
                f"- Index directory: `{index_dir}`\n"
                f"- Branch nodes: {branch_count}\n"
                f"- Skill leaves: {leaf_count}\n\n"
                f"{_render_tree_outline(nodes)}"
            ),
            "nodes": tree_nodes,
            "branch_count": branch_count,
            "leaf_count": leaf_count,
            "index_dir": str(index_dir),
        }

    @staticmethod
    def _run_dispatch_build(
        *,
        settings: SkillRetrievalSettings,
        inventory: SkillInventory,
        output_dir: Path,
    ) -> None:
        with dispatch_import_path():
            from indexing.workflows.artifacts import (
                BuildConfig,
                BuildExecutionConfig,
                BuildLLMConfig,
                BuildOutputConfig,
                TaxonomyBuildConfig,
            )
            from indexing.tree.builder import TreeBuilder
            from indexing.workflows.index_builder import IndexBuilder

            _ensure_tree_builder_compat(TreeBuilder)

            build = settings.build
            config = BuildConfig(
                llm_config=BuildLLMConfig(
                    model=settings.llm.model,
                    api_key=settings.llm.api_key,
                    base_url=settings.llm.base_url,
                    seed=settings.llm.seed,
                ),
                taxonomy_config=TaxonomyBuildConfig(
                    branching_factor=build.branching_factor,
                    max_depth=build.max_depth,
                    root_categories=build.root_categories,
                    postprocess_enabled=build.postprocess_enabled,
                    postprocess_max_passes=build.postprocess_max_passes,
                    postprocess_min_skills=build.postprocess_min_skills,
                    equivalence_enabled=build.equivalence_enabled,
                ),
                execution_config=BuildExecutionConfig(
                    max_workers=build.max_workers,
                    max_retries=build.max_retries,
                    request_timeout_seconds=build.request_timeout_seconds,
                    classification_batch_limit=build.classification_batch_limit,
                    discovery_seed=build.discovery_seed,
                ),
                output_config=BuildOutputConfig(generate_html=False),
            )
            IndexBuilder.build(
                item_paths=inventory.item_paths,
                output_dir=output_dir,
                item_type="skill",
                config=config,
            )

    @staticmethod
    def _recover_index(
        *,
        settings: SkillRetrievalSettings,
        inventory: SkillInventory,
        expected_fingerprint: str,
    ) -> bool:
        if _is_complete_index(_index_dir(settings)):
            return False
        for candidate in _recovery_candidates(settings):
            try:
                if not _is_complete_index(candidate):
                    continue
                if not _manifest_matches_inventory(candidate, inventory):
                    continue
                _publish_index(settings=settings, candidate_dir=candidate)
                _write_state(settings, inventory=inventory, fingerprint=expected_fingerprint)
                return True
            except Exception as exc:
                _record_recovery_failure(candidate, exc)
        return False


def _index_dir(settings: SkillRetrievalSettings) -> Path:
    return settings.artifact_root / "index"


def _ensure_tree_builder_compat(tree_builder_cls: type) -> None:
    default_attrs = {
        "_skill_profiles_enabled": False,
        "_cache_observability": False,
    }
    for name, value in default_attrs.items():
        if not hasattr(tree_builder_cls, name):
            setattr(tree_builder_cls, name, value)
    if not hasattr(tree_builder_cls, "_write_yaml"):
        setattr(tree_builder_cls, "_write_yaml", _write_tree_yaml)


def _write_tree_yaml(tree_builder: Any, tree_dict: dict[str, Any]) -> None:
    writer = getattr(tree_builder, "_preset_writer")
    writer.write_yaml(tree_dict)


def _record_recovery_failure(candidate: Path, exc: Exception) -> None:
    LOGGER.debug("Skipping unusable skill retrieval index recovery candidate %s: %s", candidate, exc)


def _tmp_dir(settings: SkillRetrievalSettings) -> Path:
    return settings.artifact_root / "tmp"


def _state_file(settings: SkillRetrievalSettings) -> Path:
    return settings.artifact_root / STATE_FILENAME


def _expected_fingerprint(inventory: SkillInventory, settings: SkillRetrievalSettings) -> str:
    import hashlib

    payload = {
        "inventory": inventory.fingerprint,
        "build": asdict(settings.build),
        "llm": {
            "model": settings.llm.model,
            "base_url": settings.llm.base_url,
            "seed": settings.llm.seed,
        },
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read_state(settings: SkillRetrievalSettings) -> dict[str, Any]:
    path = _state_file(settings)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(settings: SkillRetrievalSettings, *, inventory: SkillInventory, fingerprint: str) -> None:
    payload = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "indexed_count": inventory.count,
        "index_dir": str(_index_dir(settings)),
        "inventory": inventory.to_state_payload(),
    }
    _state_file(settings).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_complete_index(path: Path) -> bool:
    return (
        (path / TREE_INDEX_FILENAME).is_file()
        and (path / CATALOG_FILENAME).is_file()
        and (path / MANIFEST_FILENAME).is_file()
    )


def _publish_index(*, settings: SkillRetrievalSettings, candidate_dir: Path) -> None:
    final_dir = _index_dir(settings)
    backup_dir = settings.artifact_root / f"index.backup-{time.time_ns()}"
    if final_dir.exists():
        final_dir.rename(backup_dir)
    try:
        candidate_dir.rename(final_dir)
    except Exception:
        if backup_dir.exists() and not final_dir.exists():
            backup_dir.rename(final_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def _recovery_candidates(settings: SkillRetrievalSettings) -> list[Path]:
    candidates: list[Path] = []
    root = settings.artifact_root
    if root.exists():
        candidates.extend(sorted(root.glob("index.backup-*"), key=lambda path: path.stat().st_mtime, reverse=True))
    tmp = _tmp_dir(settings)
    if tmp.exists():
        for build_root in sorted(tmp.glob("build-*"), key=lambda path: path.stat().st_mtime, reverse=True):
            candidates.append(build_root / "index")
            candidates.append(build_root)
    return candidates


def _manifest_matches_inventory(index_dir: Path, inventory: SkillInventory) -> bool:
    try:
        payload = json.loads((index_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return False
    raw_paths = payload.get("item_paths") if isinstance(payload, dict) else None
    if not isinstance(raw_paths, list):
        return False
    expected = {str(Path(path).expanduser().resolve()) for path in inventory.item_paths}
    actual = {str(Path(str(path)).expanduser().resolve()) for path in raw_paths}
    return expected == actual


def _render_tree_outline(nodes: list[Any], *, max_nodes: int = 400) -> str:
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

    for child_list in children.values():
        child_list.sort(key=lambda item: (item.count("."), item.lower()))

    lines: list[str] = []
    emitted = 0

    def label_for(cid: str) -> str:
        node = by_cid.get(cid, {})
        name = cid.rsplit(".", 1)[-1]
        description = _compact_text(str(node.get("description") or ""), limit=120)
        if str(node.get("type") or "") == "leaf":
            worker_id = str(node.get("worker_id") or name).strip()
            suffix = f" - {description}" if description else ""
            return f"`{worker_id}`{suffix}"
        suffix = f" - {description}" if description else ""
        return f"{name}{suffix}"

    def walk(parent: str, depth: int) -> None:
        nonlocal emitted
        for cid in children.get(parent, []):
            if emitted >= max_nodes:
                return
            lines.append(f"{'  ' * depth}- {label_for(cid)}")
            emitted += 1
            walk(cid, depth + 1)

    walk("", 0)
    if emitted < len(by_cid):
        lines.append(f"\n... {len(by_cid) - emitted} more nodes omitted")
    return "\n".join(lines) if lines else "(empty tree)"


def _tree_node_payload(nodes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    known_cids: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("cid") or "").strip()
        if cid:
            known_cids.add(cid)

    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("cid") or "").strip()
        if not cid:
            continue
        parent_cid = cid.rsplit(".", 1)[0] if "." in cid else ""
        if parent_cid not in known_cids:
            parent_cid = ""
        node_type = str(raw.get("type") or "").strip() or "branch"
        worker_id = str(raw.get("worker_id") or "").strip()
        fallback_label = worker_id if node_type == "leaf" and worker_id else cid.rsplit(".", 1)[-1]
        out.append(
            {
                "cid": cid,
                "parent_cid": parent_cid,
                "type": node_type,
                "label": _node_label(raw, fallback_label=fallback_label),
                "description": str(raw.get("description") or "").strip(),
                "select_when": str(raw.get("select_when") or "").strip(),
                "dont_select_when": str(raw.get("dont_select_when") or "").strip(),
                "source_description": str(raw.get("source_description") or "").strip(),
                "worker_id": worker_id,
                "category": str(raw.get("category") or "").strip(),
                "keywords": _string_list(raw.get("keywords")),
                "examples": _string_list(raw.get("examples")),
            }
        )
    out.sort(key=lambda item: (str(item.get("cid") or "").count("."), str(item.get("cid") or "").lower()))
    return out


def _node_label(node: dict[str, Any], *, fallback_label: str) -> str:
    for key in ("name", "display_name", "worker_id"):
        value = str(node.get(key) or "").strip()
        if value:
            return value
    return fallback_label


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _compact_text(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:max(0, limit - 3)].rstrip() + "..."
