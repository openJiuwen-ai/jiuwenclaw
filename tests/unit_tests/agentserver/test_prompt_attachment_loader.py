from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachment,
    PromptAttachmentKind,
    PromptAttachmentManager,
    PromptAttachmentScope,
    PromptAttachmentUpdate,
)

from jiuwenswarm.server.runtime.prompt_attachment_loader import (
    SESSION_SOURCE,
    TURN_SOURCE,
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


def test_user_friendly_for_context_add_markdown_hides_session_and_turn(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    ctx = SimpleNamespace(session_id="sess1", invoke_turn_id="req1")

    item = loader.for_context(ctx).add_markdown(
        name="hint.md",
        scope="turn",
        content="turn hint",
        priority=42,
    )
    items = loader.load_turn_attachments("sess1", "req1")

    assert item.id == "turn.sess1.req1.hint"
    assert item.priority == 42
    assert item.kind == PromptAttachmentKind.TEXT
    assert item.source == TURN_SOURCE
    assert item.metadata["origin_source"] == "jiuwenswarm.prompt_attachment.user"
    assert [loaded.id for loaded in items] == ["turn.sess1.req1.hint"]
    assert [loaded.content for loaded in items] == ["turn hint"]


def test_context_store_update_get_delete_and_list_use_bound_ids(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    store = loader.for_context(SimpleNamespace(session_id="sess1", invoke_turn_id="req1"))
    created = store.add_markdown(name="hint.md", content="turn hint")

    updated = store.update_markdown(
        created.id,
        scope=PromptAttachmentScope.TURN,
        content="turn hint v2",
    )
    listed = store.list(scope=PromptAttachmentScope.TURN)

    assert updated.content == "turn hint v2"
    assert store.get(created.id, scope=PromptAttachmentScope.TURN).content == "turn hint v2"
    assert [item.id for item in listed] == ["turn.sess1.req1.hint"]
    assert store.delete(created.id, scope=PromptAttachmentScope.TURN) is True
    assert store.get(created.id, scope=PromptAttachmentScope.TURN) is None


def test_file_store_add_markdown_validates_paths_and_turn_context(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)

    with pytest.raises(ValueError):
        loader.file_store.add_markdown(
            session_id="sess1",
            scope=PromptAttachmentScope.TURN,
            name="note.md",
            content="missing turn",
        )
    with pytest.raises(ValueError):
        loader.file_store.add_markdown(session_id="sess1", name="../unsafe.md", content="unsafe")
    with pytest.raises(ValueError):
        loader.file_store.add_markdown(session_id="sess1", name=".hidden.md", content="hidden")


def test_update_markdown_preserves_frontmatter_and_merges_metadata(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    session_store = loader.for_session("sess1")
    created = session_store.add_session_markdown(
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


def test_file_store_json_prompt_attachment_syncs_to_manager(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    created = loader.file_store.add(PromptAttachment(
        id="external.release_notes",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.TEXT,
        content="json content",
        priority=12,
        source="external.user",
        session_id="sess1",
        metadata={"owner": "qa"},
        content_kind="text/markdown",
    ))

    updated = loader.file_store.update(
        "external.release_notes",
        PromptAttachmentUpdate(content="json content v2", metadata={"ticket": "123"}),
    )
    items = loader.load_session_attachments("sess1")

    assert created.id == "external.release_notes"
    assert (root / "sess1" / "session" / "external.release_notes.json").exists()
    assert updated.content == "json content v2"
    assert updated.metadata["owner"] == "qa"
    assert updated.metadata["ticket"] == "123"
    assert [item.id for item in items] == ["external.release_notes"]
    assert [item.content for item in items] == ["json content v2"]
    assert [item.priority for item in items] == [12]
    assert [item.source for item in items] == [SESSION_SOURCE]
    assert [item.metadata["origin_source"] for item in items] == ["external.user"]
    assert [item.metadata["owner"] for item in items] == ["qa"]
    assert [item.metadata["ticket"] for item in items] == ["123"]


def test_session_bound_crud_does_not_find_json_from_other_sessions(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    loader.file_store.add(PromptAttachment(
        id="external.secret",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.TEXT,
        content="sess b secret",
        session_id="sessB",
    ))

    sess_a = loader.for_session("sessA")

    assert sess_a.get("external.secret") is None
    assert sess_a.delete("external.secret") is False
    with pytest.raises(FileNotFoundError):
        sess_a.update_markdown("external.secret", content="overwrite")
    assert loader.for_session("sessB").get("external.secret").content == "sess b secret"


def test_update_markdown_does_not_rewrite_json_attachment(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    loader.file_store.add(PromptAttachment(
        id="external.release_notes",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.TEXT,
        content="json content",
        session_id="sess1",
    ))
    json_path = root / "sess1" / "session" / "external.release_notes.json"
    before = json_path.read_text(encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        loader.for_session("sess1").update_markdown("external.release_notes", content="markdown content")

    assert json_path.read_text(encoding="utf-8") == before
    assert loader.for_session("sess1").get("external.release_notes").content == "json content"


def test_file_store_list_without_turn_id_includes_session_and_turn_items(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    store = loader.for_session("sess1", invoke_turn_id="req1")

    store.add_session_markdown(name="session.md", content="session content")
    store.add_current_turn_markdown(name="turn.md", content="turn content")
    items = loader.file_store.list(session_id="sess1")

    assert [item.id for item in items] == ["session.sess1.session", "turn.sess1.req1.turn"]
    assert [item.content for item in items] == ["session content", "turn content"]


def test_file_store_auto_name_uses_unique_time_ns_and_thread_id(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    store = loader.for_session("sess1")

    first = store.add_session_markdown(content="one")
    second = store.add_session_markdown(content="two")

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
async def test_sync_to_agent_removes_deleted_user_source_turn_files(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    store = loader.for_session("sess1", invoke_turn_id="req1")

    created = store.add_current_turn_markdown(name="hint.md", content="turn hint")
    assert created.source == TURN_SOURCE
    assert created.metadata["origin_source"] == "jiuwenswarm.prompt_attachment.user"

    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    loaded = await agent.prompt_attachment_manager.list_by_filter(
        session_id="sess1",
        invoke_turn_id="req1",
        scope=PromptAttachmentScope.TURN,
    )
    assert [item.id for item in loaded] == ["turn.sess1.req1.hint"]
    assert [item.source for item in loaded] == [TURN_SOURCE]

    assert store.delete(created.id, scope=PromptAttachmentScope.TURN) is True
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")

    assert await agent.prompt_attachment_manager.list_by_filter(
        session_id="sess1",
        invoke_turn_id="req1",
        scope=PromptAttachmentScope.TURN,
    ) == []


@pytest.mark.asyncio
async def test_sync_to_agent_hot_reloads_session_modify_and_delete(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    session_dir = root / "sess1" / "session"
    session_dir.mkdir(parents=True)
    runtime_path = session_dir / "runtime.md"

    runtime_path.write_text("v1", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    assert [item.content for item in items] == ["v1"]

    runtime_path.write_text("v2", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req2")
    items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req2")
    assert [item.content for item in items] == ["v2"]

    runtime_path.unlink()
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req3")
    items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req3")
    assert items == []


@pytest.mark.asyncio
async def test_sync_to_agent_keeps_turn_attachments_isolated_by_request(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    turn_dir = root / "sess1" / "turn"
    req1_dir = turn_dir / "req1"
    req2_dir = turn_dir / "req2"
    req1_dir.mkdir(parents=True)
    req2_dir.mkdir(parents=True)

    (req1_dir / "request_context.md").write_text("turn 1 only", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    req1_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    assert [item.content for item in req1_items] == ["turn 1 only"]

    (req2_dir / "request_context.md").write_text("turn 2 only", encoding="utf-8")
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req2")
    req2_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req2")
    assert [item.content for item in req2_items] == ["turn 2 only"]
    stale_turn_items = await agent.prompt_attachment_manager.list_by_filter(
        source=TURN_SOURCE,
        session_id="sess1",
        scope=PromptAttachmentScope.TURN,
    )
    assert [item.invoke_turn_id for item in stale_turn_items] == ["req1", "req2"]
    req1_contents = [
        item.content
        for item in await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    ]
    assert req1_contents == ["turn 1 only"]

    (turn_dir / "req3").mkdir()
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req3")
    req3_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req3")
    assert req3_items == []

    await loader.clear_turn_from_agent(agent, session_id="sess1", invoke_turn_id="req1")
    await loader.clear_turn_from_agent(agent, session_id="sess1", invoke_turn_id="req2")
    assert await agent.prompt_attachment_manager.list_by_filter(source=TURN_SOURCE, session_id="sess1") == []


@pytest.mark.asyncio
async def test_sync_to_agent_reads_isolated_turn_directory_only(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    turn_root = root / "sess1" / "turn"
    req1_dir = turn_root / "req1"
    req2_dir = turn_root / "req2"
    req1_dir.mkdir(parents=True)
    req2_dir.mkdir(parents=True)
    (req1_dir / "request_context.md").write_text("req1 only", encoding="utf-8")
    (req2_dir / "request_context.md").write_text("req2 only", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req2")

    req1_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    req2_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req2")
    assert [item.content for item in req1_items] == ["req1 only"]
    assert [item.content for item in req2_items] == ["req2 only"]
    assert [item.id for item in req1_items] == ["turn.sess1.req1.request_context"]
    assert [item.id for item in req2_items] == ["turn.sess1.req2.request_context"]


@pytest.mark.asyncio
async def test_file_loaded_attachments_render_inject_and_turn_cleanup(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()

    session_dir = root / "sess1" / "session"
    turn_dir = root / "sess1" / "turn" / "req1"
    session_dir.mkdir(parents=True)
    turn_dir.mkdir(parents=True)
    (session_dir / "runtime.md").write_text("SESSION_FILE_MARKER", encoding="utf-8")
    (turn_dir / "request_context.md").write_text("TURN_FILE_MARKER", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    manager = agent.prompt_attachment_manager
    rendered = manager.render(await manager.collect_for_turn("sess1", "req1"))
    messages = [
        SystemMessage(content="STATIC_SYSTEM_PROMPT"),
        UserMessage(content="original query"),
    ]

    injected = manager.inject_messages(messages, rendered)

    assert messages[-1].content == "original query"
    assert injected[0].content == "STATIC_SYSTEM_PROMPT"
    assert "SESSION_FILE_MARKER" not in injected[0].content
    assert "TURN_FILE_MARKER" not in injected[0].content
    assert "original query" in injected[-1].content
    assert "<system-reminder>" in injected[-1].content
    assert "SESSION_FILE_MARKER" in injected[-1].content
    assert "TURN_FILE_MARKER" in injected[-1].content

    await loader.clear_turn_from_agent(agent, session_id="sess1", invoke_turn_id="req1")
    after_clear = await manager.collect_for_turn("sess1", "req1")
    assert [item.content for item in after_clear] == ["SESSION_FILE_MARKER"]


@pytest.mark.asyncio
async def test_flat_turn_files_are_ignored(tmp_path):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    turn_root = root / "sess1" / "turn"
    turn_root.mkdir(parents=True)
    (turn_root / "request_context.md").write_text("legacy flat", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")

    req1_items = await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    assert req1_items == []


@pytest.mark.asyncio
async def test_sync_to_agent_read_failure_does_not_block_or_keep_old_turn(tmp_path, monkeypatch):
    root = tmp_path / "prompt_attachment"
    loader = PromptAttachmentLoader(root)
    agent = FakeDeepAgent()
    turn_dir = root / "sess1" / "turn" / "req1"
    turn_dir.mkdir(parents=True)
    (turn_dir / "request_context.md").write_text("turn 1", encoding="utf-8")

    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req1")
    assert await agent.prompt_attachment_manager.list_by_filter(source=TURN_SOURCE, session_id="sess1")

    req2_dir = root / "sess1" / "turn" / "req2"
    req2_dir.mkdir(parents=True)
    (req2_dir / "request_context.md").write_text("turn 2", encoding="utf-8")

    def fail_read(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(loader, "_read_text_file", fail_read)
    await loader.sync_to_agent(agent, session_id="sess1", invoke_turn_id="req2")

    assert await agent.prompt_attachment_manager.collect_for_turn("sess1", "req2") == []
    req1_contents = [
        item.content
        for item in await agent.prompt_attachment_manager.collect_for_turn("sess1", "req1")
    ]
    assert req1_contents == ["turn 1"]
    session_items = await agent.prompt_attachment_manager.list_by_filter(source=SESSION_SOURCE, session_id="sess1")
    assert session_items == []
