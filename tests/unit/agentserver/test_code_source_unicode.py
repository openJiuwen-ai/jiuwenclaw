from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jiuwenclaw.agentserver.code_source_unicode import (
    is_code_unicode_readable_enabled,
    normalize_python_script_file,
    normalize_python_source_unicode_literals,
    register_code_source_unicode_hook,
)
from jiuwenclaw.schema.hooks_context import ArtifactPostProcessHookContext


def test_normalize_unicode_escape_to_readable() -> None:
    source = 'doc.add_paragraph("\\u6807\\u9898")\n'
    normalized, count = normalize_python_source_unicode_literals(source)
    assert count == 1
    assert '"标题"' in normalized


def test_raw_string_unchanged() -> None:
    source = 'value = r"\\u6587"\n'
    normalized, count = normalize_python_source_unicode_literals(source)
    assert count == 0
    assert normalized == source


def test_surrogate_escape_pair_unchanged() -> None:
    """Split surrogate escapes must not be decoded to lone surrogates (invalid UTF-8)."""
    source = 'x = "\\ud83d\\ude00"\n'
    normalized, count = normalize_python_source_unicode_literals(source)
    assert count == 0
    assert normalized == source


def test_normalize_python_script_file_surrogate_preserves_file(tmp_path: Path) -> None:
    source = 'x = "\\ud83d\\ude00"\n'
    script = tmp_path / "demo.py"
    script.write_text(source, encoding="utf-8")
    assert normalize_python_script_file(script) == 0
    assert script.read_text(encoding="utf-8") == source


def test_config_disabled() -> None:
    assert is_code_unicode_readable_enabled({"code_generation": {"unicode_readable": False}}) is False


@pytest.mark.asyncio
async def test_artifact_hook_normalizes_file(tmp_path: Path) -> None:
    from jiuwenclaw.agentserver.code_source_unicode import _normalize_code_artifact_hook

    script = tmp_path / "demo.py"
    script.write_text('x = "\\u6807\\u9898"\n', encoding="utf-8")
    ctx = ArtifactPostProcessHookContext(
        session_id="s1",
        tool_name="write_file",
        artifact_paths=[str(script)],
    )
    await _normalize_code_artifact_hook(ctx)
    assert script.read_text(encoding="utf-8") == 'x = "标题"\n'


def test_normalize_python_script_file(tmp_path: Path) -> None:
    script = tmp_path / "demo.py"
    script.write_text('x = "\\u6807\\u9898"\n', encoding="utf-8")
    assert normalize_python_script_file(script) == 1
    assert script.read_text(encoding="utf-8") == 'x = "标题"\n'


def test_register_hook_idempotent() -> None:
    registry = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "jiuwenclaw.extensions.registry.ExtensionRegistry.get_instance",
            lambda: registry,
        )
        mp.setattr(
            "jiuwenclaw.agentserver.code_source_unicode._HOOK_REGISTERED",
            False,
        )
        register_code_source_unicode_hook()
        register_code_source_unicode_hook()
    assert registry.register.call_count == 1


@pytest.mark.asyncio
async def test_artifact_hook_does_not_block_event_loop(tmp_path: Path) -> None:
    """asyncio.to_thread 确保同步文件 I/O 不阻塞事件循环。"""
    from jiuwenclaw.agentserver.code_source_unicode import (
        _normalize_code_artifact_hook,
    )

    script = tmp_path / "demo.py"
    script.write_text('x = "\\u6807\\u9898"\n', encoding="utf-8")
    ctx = ArtifactPostProcessHookContext(
        session_id="s1",
        tool_name="write_file",
        artifact_paths=[str(script)],
    )

    ticks: list[float] = []

    async def _heartbeat() -> None:
        for _ in range(10):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    start = time.monotonic()
    await asyncio.gather(
        _heartbeat(),
        _normalize_code_artifact_hook(ctx),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert len(ticks) >= 8
    assert script.read_text(encoding="utf-8") == 'x = "标题"\n'


@pytest.mark.asyncio
async def test_artifact_hook_skips_non_python_file_without_blocking(
    tmp_path: Path,
) -> None:
    """非 .py 文件（如 .docx）应立即跳过，不执行文件 I/O。"""
    from jiuwenclaw.agentserver.code_source_unicode import (
        _normalize_code_artifact_hook,
    )

    docx = tmp_path / "report.docx"
    docx.write_bytes(b"fake docx content")
    ctx = ArtifactPostProcessHookContext(
        session_id="s2",
        tool_name="write_file",
        artifact_paths=[str(docx)],
    )

    start = time.monotonic()
    await _normalize_code_artifact_hook(ctx)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert docx.read_bytes() == b"fake docx content"


@pytest.mark.asyncio
async def test_artifact_hook_uses_to_thread_for_blocking_io(
    tmp_path: Path,
) -> None:
    """验证 normalize_python_script_file 通过 asyncio.to_thread 调用。"""
    from jiuwenclaw.agentserver import code_source_unicode as mod

    script = tmp_path / "demo.py"
    script.write_text('x = "ok"\n', encoding="utf-8")
    ctx = ArtifactPostProcessHookContext(
        session_id="s3",
        tool_name="write_file",
        artifact_paths=[str(script)],
    )

    call_was_sync: list[bool] = []

    def _spy_normalize(path: str | Path) -> int:
        # 在线程池中执行时 current_thread 不是主线程
        import threading
        call_was_sync.append(
            threading.current_thread() is threading.main_thread()
        )
        return 0

    with patch.object(mod, "normalize_python_script_file", _spy_normalize):
        await mod._normalize_code_artifact_hook(ctx)

    assert len(call_was_sync) == 1
    assert call_was_sync[0] is False
