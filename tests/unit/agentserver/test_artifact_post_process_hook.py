from __future__ import annotations

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.artifact_emitter import (
    _trigger_artifact_post_process_hook,
    should_detect_artifacts,
)
from jiuwenclaw.schema import AgentServerHookEvents
from jiuwenclaw.schema.hooks_context import ArtifactPostProcessHookContext


def test_should_detect_artifacts_includes_text_to_image() -> None:
    assert should_detect_artifacts("text_to_image") is True
    assert should_detect_artifacts("write_file") is True
    assert should_detect_artifacts("unknown_tool") is False


@pytest.mark.asyncio
async def test_trigger_artifact_post_process_hook_invokes_extension_registry() -> None:
    artifacts = [
        {
            "path": "/tmp/demo.png",
            "name": "demo.png",
            "extension": ".png",
            "size": 10,
            "exists": True,
        }
    ]
    registry = MagicMock()
    registry.trigger = AsyncMock()

    with patch(
        "jiuwenclaw.extensions.registry.ExtensionRegistry.get_instance",
        return_value=registry,
    ):
        await _trigger_artifact_post_process_hook(
            session_id="sess-1",
            tool_name="text_to_image",
            task_id="task-1",
            subagent_id=None,
            artifacts=artifacts,
            log_prefix="[Test]",
        )

    registry.trigger.assert_awaited_once()
    event, hook_ctx = registry.trigger.await_args.args
    assert event == AgentServerHookEvents.ARTIFACT_POST_PROCESS
    assert isinstance(hook_ctx, ArtifactPostProcessHookContext)
    assert hook_ctx.session_id == "sess-1"
    assert hook_ctx.tool_name == "text_to_image"
    assert hook_ctx.task_id == "task-1"
    assert hook_ctx.artifact_paths == ["/tmp/demo.png"]


@pytest.mark.asyncio
async def test_trigger_artifact_post_process_hook_refreshes_size_after_file_mutation(
    tmp_path,
) -> None:
    image_path = tmp_path / "demo.png"
    image_path.write_bytes(b"original")

    async def _mutate(ctx: ArtifactPostProcessHookContext) -> None:
        image_path.write_bytes(b"watermarked-longer")

    artifacts = [
        {
            "path": str(image_path),
            "name": "demo.png",
            "extension": ".png",
            "size": image_path.stat().st_size,
            "exists": True,
        }
    ]

    registry = MagicMock()

    async def _trigger(event, ctx):
        assert event == AgentServerHookEvents.ARTIFACT_POST_PROCESS
        assert ctx.artifact_paths == [str(image_path)]
        await _mutate(ctx)

    registry.trigger = AsyncMock(side_effect=_trigger)

    with patch(
        "jiuwenclaw.extensions.registry.ExtensionRegistry.get_instance",
        return_value=registry,
    ):
        await _trigger_artifact_post_process_hook(
            session_id="sess-2",
            tool_name="text_to_image",
            task_id=None,
            subagent_id="subagent_abc",
            artifacts=artifacts,
            log_prefix="[Test]",
        )

    assert artifacts[0]["name"] == "demo.png"
    assert artifacts[0]["size"] == image_path.stat().st_size


@pytest.mark.asyncio
async def test_trigger_artifact_post_process_hook_skips_when_registry_not_initialized() -> None:
    artifacts = [
        {
            "path": "/tmp/demo.png",
            "name": "demo.png",
            "extension": ".png",
            "size": 10,
            "exists": True,
        }
    ]
    original_artifacts = [dict(item) for item in artifacts]

    with patch(
        "jiuwenclaw.extensions.registry.ExtensionRegistry.get_instance",
        side_effect=RuntimeError("not initialized"),
    ):
        await _trigger_artifact_post_process_hook(
            session_id="sess-runtime",
            tool_name="text_to_image",
            task_id=None,
            subagent_id=None,
            artifacts=artifacts,
            log_prefix="[Test]",
        )

    assert artifacts == original_artifacts


@pytest.mark.asyncio
async def test_trigger_artifact_post_process_hook_skips_on_import_error() -> None:
    artifacts = [
        {
            "path": "/tmp/demo.png",
            "name": "demo.png",
            "extension": ".png",
            "size": 10,
            "exists": True,
        }
    ]
    original_artifacts = [dict(item) for item in artifacts]
    real_import = builtins.__import__

    def _import_with_registry_failure(name, globalns=None, localns=None, fromlist=(), level=0):
        if name == "jiuwenclaw.extensions.registry":
            raise ImportError("registry unavailable")
        return real_import(name, globalns, localns, fromlist, level)

    with patch("builtins.__import__", side_effect=_import_with_registry_failure):
        await _trigger_artifact_post_process_hook(
            session_id="sess-import",
            tool_name="text_to_image",
            task_id=None,
            subagent_id=None,
            artifacts=artifacts,
            log_prefix="[Test]",
        )

    assert artifacts == original_artifacts


@pytest.mark.asyncio
async def test_trigger_artifact_post_process_hook_empty_paths_does_not_clear_artifacts() -> None:
    artifacts = [
        {
            "path": "",
            "name": "demo.png",
            "extension": ".png",
            "size": 10,
            "exists": True,
        }
    ]
    registry = MagicMock()
    registry.trigger = AsyncMock()

    with patch(
        "jiuwenclaw.extensions.registry.ExtensionRegistry.get_instance",
        return_value=registry,
    ):
        await _trigger_artifact_post_process_hook(
            session_id="sess-empty",
            tool_name="text_to_image",
            task_id=None,
            subagent_id=None,
            artifacts=artifacts,
            log_prefix="[Test]",
        )

    registry.trigger.assert_awaited_once()
    event, hook_ctx = registry.trigger.await_args.args
    assert event == AgentServerHookEvents.ARTIFACT_POST_PROCESS
    assert hook_ctx.artifact_paths == []
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "demo.png"


@pytest.mark.asyncio
async def test_emit_artifact_generated_triggers_hook_before_stream_write(tmp_path) -> None:
    from jiuwenclaw.agentserver.deep_agent.artifact_emitter import (
        ArtifactEmitContext,
        emit_artifact_generated,
    )

    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"png")
    artifact = {
        "path": str(image_path),
        "name": "generated.png",
        "extension": ".png",
        "size": 3,
        "exists": True,
    }

    session = SimpleNamespace(
        get_session_id=lambda: "sess-3",
        write_stream=AsyncMock(),
    )
    emit_ctx = ArtifactEmitContext(
        session=session,
        tool_result="tool result placeholder",
        tool_name="text_to_image",
        workspace_base=tmp_path,
        tool_start_time=None,
        task_id="task-3",
    )

    hook_trigger = AsyncMock()
    call_order: list[str] = []

    async def _record_hook(**kwargs) -> None:
        call_order.append("hook")

    async def _record_write_stream(*args, **kwargs) -> None:
        call_order.append("write_stream")

    session.write_stream.side_effect = _record_write_stream
    hook_trigger.side_effect = _record_hook

    with (
        patch(
            "jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail._extract_artifact_paths_from_tool_result",
            return_value=[artifact],
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.artifact_emitter._trigger_artifact_post_process_hook",
            hook_trigger,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail._is_recently_sent",
            return_value=False,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail._mark_as_sent",
        ),
    ):
        emitted = await emit_artifact_generated(emit_ctx)

    assert emitted is True
    hook_trigger.assert_awaited_once()
    session.write_stream.assert_awaited_once()
    assert call_order == ["hook", "write_stream"]
