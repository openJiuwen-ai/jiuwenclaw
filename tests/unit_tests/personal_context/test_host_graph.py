from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata

from jiuwenswarm.server.personal_context.host_api import PersonalContextHostAPI


def _write_source(home: Path) -> tuple[str, Path]:
    source_root = home / "workspace" / "source-meta"
    source_id = upsert_source_metadata(
        source_root,
        RawChangeItem(
            logical_id="github:pull:42",
            revision_id="revision-1",
            operation="upsert",
            title="GitHub PR 42",
            content="source body must not be persisted",
            original_ref="https://github.com/openjiuwen/agent-core/pull/42",
            metadata={"resource": "pull_request"},
        ),
        provider="github",
        service_id="github-main",
        observed_at="2026-08-12T00:00:00Z",
    )
    return source_id, source_root / f"{source_id}.md"


def _link(page: Path, target: Path) -> str:
    relative = os.path.relpath(target, start=page.parent).replace("\\", "/")
    return f"[PR 42]({relative})"


@pytest.mark.asyncio
async def test_graph_is_empty_without_root_description(tmp_path: Path) -> None:
    host = PersonalContextHostAPI(home=tmp_path / "personal_context")
    assert await host.get_graph() == {
        "context_ready": False,
        "nodes": [],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_host_returns_atomic_source_graph_without_rebuilding_fields(
    tmp_path: Path,
) -> None:
    home = tmp_path / "personal_context"
    context = home / "workspace" / "context"
    page = context / "topics" / "agent.md"
    page.parent.mkdir(parents=True)
    source_id, source_path = _write_source(home)
    (context / "description.md").write_text(
        "# Context\n\n- [Topics](topics/description.md)\n",
        encoding="utf-8",
    )
    (context / "topics" / "description.md").write_text(
        "# Topics\n\n- [Agent](agent.md)\n",
        encoding="utf-8",
    )
    page.write_text(
        "# Agent\n\nSee [root](../description.md) and "
        + _link(page, source_path)
        + ".\n",
        encoding="utf-8",
    )

    graph = await PersonalContextHostAPI(home=home).get_graph()

    assert graph["context_ready"] is True
    assert {node["id"]: (node["kind"], node["subkind"]) for node in graph["nodes"]} == {
        "page:description.md": ("directory", "directory.0"),
        "page:topics/description.md": ("directory", "directory.1"),
        "page:topics/agent.md": ("document", "document.0"),
        f"source:{source_id}": ("source", "source.0"),
    }
    assert {
        (edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]
    } >= {
        ("page:description.md", "page:topics/description.md", "contains"),
        ("page:topics/description.md", "page:topics/agent.md", "contains"),
        ("page:description.md", "page:topics/description.md", "navigates_to"),
        ("page:topics/agent.md", f"source:{source_id}", "links_to"),
    }
    assert all(edge["kind"] != "derived_from" for edge in graph["edges"])


@pytest.mark.asyncio
async def test_host_returns_source_metadata_through_shared_page_detail(
    tmp_path: Path,
) -> None:
    home = tmp_path / "personal_context"
    source_id, source_path = _write_source(home)
    markdown = source_path.read_text(encoding="utf-8")

    assert await PersonalContextHostAPI(home=home).get_graph_page(
        f"source:{source_id}"
    ) == {
        "node_id": f"source:{source_id}",
        "title": "GitHub PR 42",
        "path": f"{source_id}.md",
        "markdown": markdown,
    }
