# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import builtins
import json
from typing import Any

import pytest

from jiuwenswarm.common.config import is_file_operation_history_enabled
from jiuwenswarm.server.runtime import file_operation_history_patch as history_patch


_FILESYSTEM_HELPERS = (
    "_append_op_history",
    "_record_rm_targets_before_deletion",
    "_detect_and_record_deletions",
)
_SHELL_HELPERS = (
    "_record_rm_targets_before_deletion",
    "_detect_and_record_deletions",
)


@pytest.fixture
def restored_history_helpers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Restore Agent-Core module bindings after process-wide patch tests."""
    import openjiuwen.harness.tools.filesystem as filesystem
    import openjiuwen.harness.tools.shell.bash._tool as bash_tool
    import openjiuwen.harness.tools.shell.powershell._tool as powershell_tool

    modules = {
        "filesystem": filesystem,
        "bash": bash_tool,
        "powershell": powershell_tool,
    }
    originals = {
        (module_name, helper_name): getattr(module, helper_name)
        for module_name, module in modules.items()
        for helper_name in (
            _FILESYSTEM_HELPERS if module_name == "filesystem" else _SHELL_HELPERS
        )
    }
    for (module_name, helper_name), original in originals.items():
        monkeypatch.setattr(modules[module_name], helper_name, original)
    monkeypatch.setattr(history_patch, "_PATCHED", False)
    yield modules


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, True),
        (None, True),
        ({"file_operation_history": {}}, True),
        ({"file_operation_history": {"enabled": True}}, True),
        ({"file_operation_history": {"enabled": False}}, False),
        ({"file_operation_history": {"enabled": "false"}}, True),
        ({"file_operation_history": {"enabled": 0}}, True),
        ({"file_operation_history": "false"}, True),
    ],
)
def test_file_operation_history_config_defaults_and_strict_bool(config, expected) -> None:
    assert is_file_operation_history_enabled(config) is expected


def test_disable_file_operation_history_patches_all_direct_bindings(
    restored_history_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_messages: list[str] = []
    monkeypatch.setattr(
        history_patch.logger,
        "info",
        lambda message, *args: info_messages.append(message % args if args else message),
    )

    history_patch.disable_file_operation_history()
    history_patch.disable_file_operation_history()

    filesystem = restored_history_helpers["filesystem"]
    bash_tool = restored_history_helpers["bash"]
    powershell_tool = restored_history_helpers["powershell"]

    assert history_patch._PATCHED is True
    assert filesystem._append_op_history is history_patch._noop_async
    assert filesystem._record_rm_targets_before_deletion is history_patch._noop_async
    assert filesystem._detect_and_record_deletions is history_patch._noop_async
    assert bash_tool._record_rm_targets_before_deletion is history_patch._noop_async
    assert bash_tool._detect_and_record_deletions is history_patch._noop_async
    assert powershell_tool._record_rm_targets_before_deletion is history_patch._noop_async
    assert powershell_tool._detect_and_record_deletions is history_patch._noop_async
    assert any("file_operation_history.enabled=false" in message for message in info_messages)


def test_missing_agent_core_import_fails_open(
    restored_history_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem = restored_history_helpers["filesystem"]
    original_append = filesystem._append_op_history
    warning_messages: list[str] = []
    monkeypatch.setattr(
        history_patch.logger,
        "warning",
        lambda message, *args: warning_messages.append(
            message % args if args else message
        ),
    )
    original_import = builtins.__import__

    def blocked_agent_core_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("openjiuwen.harness.tools"):
            raise ImportError("Agent-Core helpers unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_agent_core_import)

    history_patch.disable_file_operation_history()

    assert history_patch._PATCHED is False
    assert filesystem._append_op_history is original_append
    assert any("保持原行为" in message for message in warning_messages)


def test_missing_agent_core_helpers_are_not_created(
    restored_history_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem = restored_history_helpers["filesystem"]
    missing_name = "_detect_and_record_deletions"
    monkeypatch.delattr(filesystem, missing_name)
    warning_messages: list[str] = []
    monkeypatch.setattr(
        history_patch.logger,
        "warning",
        lambda message, *args: warning_messages.append(
            message % args if args else message
        ),
    )

    history_patch.disable_file_operation_history()

    assert not hasattr(filesystem, missing_name)
    assert filesystem._append_op_history is history_patch._noop_async
    assert any(missing_name in message for message in warning_messages)


def test_enabled_configuration_keeps_original_helpers(
    restored_history_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem = restored_history_helpers["filesystem"]
    original_append = filesystem._append_op_history
    info_messages: list[str] = []
    monkeypatch.setattr(
        history_patch.logger,
        "info",
        lambda message, *args: info_messages.append(message % args if args else message),
    )

    history_patch.configure_file_operation_history(
        {"file_operation_history": {"enabled": True}}
    )

    assert history_patch._PATCHED is False
    assert filesystem._append_op_history is original_append
    assert any("file_operation_history.enabled=true" in message for message in info_messages)


@pytest.mark.asyncio
async def test_enabled_helper_writes_history_and_disabled_noop_preserves_existing_file(
    tmp_path,
    restored_history_helpers: dict[str, Any],
) -> None:
    filesystem = restored_history_helpers["filesystem"]
    history_path = tmp_path / ".agent_history" / "file_ops_agent_session.json"

    await filesystem._append_op_history(
        str(history_path),
        "before.txt",
        "write",
        None,
        "before",
    )
    original_history = history_path.read_bytes()
    assert json.loads(original_history)["before.txt"][0]["action"] == "write"

    history_patch.disable_file_operation_history()
    await filesystem._append_op_history(
        str(history_path),
        "after.txt",
        "write",
        None,
        "after",
    )

    assert history_path.read_bytes() == original_history


@pytest.mark.asyncio
async def test_disabled_helpers_do_not_create_history_path(
    tmp_path,
    restored_history_helpers: dict[str, Any],
) -> None:
    filesystem = restored_history_helpers["filesystem"]
    history_path = tmp_path / ".agent_history" / "file_ops_agent_session.json"

    history_patch.disable_file_operation_history()
    await filesystem._append_op_history(
        str(history_path),
        "file.txt",
        "edit",
        "old",
        "new",
    )
    await filesystem._record_rm_targets_before_deletion(
        str(history_path),
        ["file.txt"],
        object(),
    )
    await filesystem._detect_and_record_deletions(str(history_path))

    assert not history_path.exists()
