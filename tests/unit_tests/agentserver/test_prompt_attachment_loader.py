from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
    PromptAttachmentManager,
)

from jiuwenswarm.server.runtime.prompt_attachment_loader import (
    SESSION_SOURCE,
    PromptAttachmentLoader,
    sanitize_session_id,
)
import jiuwenswarm.server.runtime.prompt_attachment_loader as prompt_attachment_loader_module


class FakeDeepAgent:
    def __init__(self) -> None:
        self.prompt_attachment_manager = PromptAttachmentManager()


def test_sanitize_session_id_is_path_safe():
    assert sanitize_session_id("") == "default"
    assert sanitize_session_id("default") == "default"
    assert "/" not in sanitize_session_id("../unsafe/session")
    assert "\\" not in sanitize_session_id("a\\b")
    assert sanitize_session_id("..") != ".."


def test_loader_ensure_layout_and_loads_stable_order_kind_and_ids(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    loader.ensure_layout()

    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "z_note.txt").write_text("z", encoding="utf-8")
    (session_dir / "runtime.md").write_text("runtime", encoding="utf-8")
    (session_dir / "diagnostics.md").write_text("diag", encoding="utf-8")
    (session_dir / ".hidden.md").write_text("hidden", encoding="utf-8")
    (session_dir / "image.png").write_text("ignored", encoding="utf-8")

    items = loader.load_session_attachments("sess1")

    assert (root / "README.md").exists()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "not user-uploaded attachments" in readme
    assert "turn" not in readme.lower()
    assert all(ord(char) < 128 for char in readme)
    assert [item.id for item in items] == [
        "session.sess1.diagnostics",
        "session.sess1.runtime",
        "session.sess1.z_note",
    ]
    assert [item.kind.value if hasattr(item.kind, "value") else item.kind for item in items] == [
        "diagnostic",
        "runtime",
        "text",
    ]


def test_loader_skips_empty_files_and_truncates_large_files(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root, max_file_chars=5)
    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "empty.md").write_text("  \n", encoding="utf-8")
    (session_dir / "runtime.md").write_text("x" * 20, encoding="utf-8")

    items = loader.load_session_attachments("sess1")

    assert [item.id for item in items] == ["session.sess1.runtime"]
    assert items[0].content.startswith("xxxxx")
    assert "truncated by jiuwenswarm loader" in items[0].content


def test_context_store_add_update_get_delete_and_list_use_bound_session(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    store = loader.bind_context(SimpleNamespace(session_id="sess1"))
    created = store.add_markdown(name="hint.md", content="session hint", priority=42)

    updated = store.update_markdown(created.id, content="session hint v2")
    listed = store.list()

    assert created.id == "session.sess1.hint"
    assert updated.content == "session hint v2"
    assert updated.priority == 42
    assert store.get(created.id).content == "session hint v2"
    assert [item.id for item in listed] == ["session.sess1.hint"]
    assert store.delete(created.id) is True
    assert store.get(created.id) is None


def test_file_store_add_markdown_validates_paths(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)

    with pytest.raises(ValueError):
        loader.file_store.add_markdown(session_id="sess1", name="../unsafe.md", content="unsafe")
    with pytest.raises(ValueError):
        loader.file_store.add_markdown(session_id="sess1", name=".hidden.md", content="hidden")


def test_update_markdown_preserves_frontmatter_and_merges_metadata(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    session_store = loader.for_session("sess1")
    created = session_store.add_markdown(
        name="release.md",
        content="old content",
        priority=80,
        source="external.user",
        metadata={"owner": "qa"},
    )

    updated = session_store.update_markdown(
        created.id,
        content="new content",
        metadata={"ticket": "123"},
    )
    loaded = loader.load_session_attachments("sess1")[0]
    raw = (root / "sess1" / "session" / "release.md").read_text(encoding="utf-8")

    assert updated.content == "new content"
    assert loaded.priority == 80
    assert loaded.source == SESSION_SOURCE
    assert loaded.metadata["origin_source"] == "external.user"
    assert loaded.metadata["owner"] == "qa"
    assert loaded.metadata["ticket"] == 123
    assert "priority: 80" in raw
    assert "owner: qa" in raw
    assert "ticket: 123" in raw


def test_file_store_auto_name_uses_unique_time_ns_and_thread_id(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    store = loader.for_session("sess1")

    first = store.add_markdown(content="one")
    second = store.add_markdown(content="two")

    assert first.id != second.id
    assert [item.content for item in store.list()] == ["one", "two"]


def test_loader_skips_symlinked_prompt_attachment_paths(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "runtime.md").write_text("inside", encoding="utf-8")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("outside secret", encoding="utf-8")
    link_dir = session_dir / "linked"
    try:
        os.symlink(outside_dir, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    items = loader.load_session_attachments("sess1")

    assert [item.content for item in items] == ["inside"]


def test_loader_skips_reparse_marked_prompt_attachment_paths(tmp_path, monkeypatch):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    session_dir = root / "sess1" / "session"
    linked_dir = session_dir / "linked"
    linked_dir.mkdir(parents=True)
    (linked_dir / "secret.md").write_text("outside secret", encoding="utf-8")
    (session_dir / "runtime.md").write_text("inside", encoding="utf-8")

    def fake_is_reparse_path(path):
        return path == linked_dir

    monkeypatch.setattr(prompt_attachment_loader_module, "_is_reparse_path", fake_is_reparse_path)

    items = loader.load_session_attachments("sess1")

    assert [item.content for item in items] == ["inside"]


@pytest.mark.asyncio
async def test_sync_to_agent_hot_reloads_session_modify_and_delete(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    runtime_path = session_dir / "runtime.md"

    runtime_path.write_text("v1", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1")
    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert [item.content for item in items] == ["v1"]

    runtime_path.write_text("v2", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1")
    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert [item.content for item in items] == ["v2"]

    runtime_path.unlink()
    await loader.sync_to_agent(agent, session_id="sess1")
    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert items == []


@pytest.mark.asyncio
async def test_file_loaded_attachments_render_and_inject(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()

    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "runtime.md").write_text("SESSION_FILE_MARKER", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1")
    manager = agent.prompt_attachment_manager
    rendered = manager.render(await manager.collect_for_session("sess1"))
    messages = [
        SystemMessage(content="STATIC_SYSTEM_PROMPT"),
        UserMessage(content="original query"),
    ]

    injected = manager.inject_messages(messages, rendered)

    assert messages[-1].content == "original query"
    assert injected[0].content == "STATIC_SYSTEM_PROMPT"
    assert injected[1].content == "original query"
    assert "SESSION_FILE_MARKER" not in injected[0].content
    assert isinstance(injected[-1], UserMessage)
    assert "original query" not in injected[-1].content
    assert "<system-reminder>" in injected[-1].content
    assert "SESSION_FILE_MARKER" in injected[-1].content


@pytest.mark.asyncio
async def test_sync_to_agent_read_failure_does_not_block_or_clear_old_state(tmp_path, monkeypatch):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "runtime.md").write_text("v1", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1")
    assert await agent.prompt_attachment_manager.list_by_filter(source=SESSION_SOURCE, session_id="sess1")

    def fail_list(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(loader, "load_session_attachments", fail_list)
    await loader.sync_to_agent(agent, session_id="sess1")

    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert [item.content for item in items] == ["v1"]
