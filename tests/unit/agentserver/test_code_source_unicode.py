from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
