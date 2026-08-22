"""Regression test for the field-name mismatch on session.restore_files.

Frontend ``rewind.ts`` reads ``restorePayload.restore_errors`` to drive the
"Partially restored" tiered message and per-file error list. The tui channel
handler ``_session_restore_files`` returns ``restore_session_files``'s raw
result, whose error field is named ``errors``. Without an explicit mapping,
``restore_errors`` is always ``undefined`` on the frontend → ``?? []`` → the
failure-aware UI never fires and users see a misleading success message even
when files failed to restore.

These tests exercise the handler end-to-end with a REAL ``restore_session_files``
(only ``DiffService.get_files_to_restore`` is mocked). So every payload field —
``restored_files``, ``deleted_files``, ``errors`` and the ``{file, error}``
element shape — is produced by the actual service code path, not hand-written
in the mock. The mock only decides *which* files the service will try to restore.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jiuwenswarm.gateway.channel_manager.tui.tui_connect import (
    CliHandlersBindParams,
    register_cli_handlers,
)

# restore_session_files imports get_diff_service lazily, so patch the source
# module attribute (same pattern as tests/unit_tests/test_restore_session_files_newline.py).
GET_DIFF_SERVICE = "jiuwenswarm.server.utils.diff_service.get_diff_service"


class _FakeGatewayServer:
    """Minimal channel stub that records send_response payloads."""

    def __init__(self):
        self.local_handlers: dict[str, dict] = {}
        self.responses: list[dict] = []

    def register_local_handler(self, path, method, handler):
        self.local_handlers.setdefault(path, {})[method] = handler

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {
                "id": req_id,
                "ok": ok,
                "payload": payload or {},
                "error": error,
                "code": code,
            }
        )


def _register(server: _FakeGatewayServer) -> dict:
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=None,
            message_handler=None,
            on_config_saved=None,
            path="/tui",
        )
    )
    return server.local_handlers["/tui"]


def _patch_diff_service(files_to_restore: dict):
    mock_diff = MagicMock()
    mock_diff.get_files_to_restore.return_value = files_to_restore
    return patch(GET_DIFF_SERVICE, return_value=mock_diff)


@pytest.mark.asyncio
async def test_session_restore_files_surfaces_errors_as_restore_errors(tmp_path: Path):
    """When a file fails to restore, the handler MUST surface the real
    ``errors`` list (produced by restore_session_files) as ``restore_errors``
    so the frontend failure-aware branch can fire.

    Failure is induced by telling the service to "write" back a path that is
    actually an existing directory — ``Path.write_text`` on a directory raises
    and restore_session_files appends a real ``{"file", "error"}`` entry.
    """
    server = _FakeGatewayServer()
    handler = _register(server)["session.restore_files"]

    blocker = tmp_path / "blocker_dir"
    blocker.mkdir()  # real directory — write_text will fail

    files = {str(blocker): {"restore_content": "X", "action": "write"}}
    with _patch_diff_service(files):
        await handler(
            object(), "req-1", {"session_id": "sess-1", "turn_index": 1}, "sess-1"
        )

    assert len(server.responses) == 1
    resp = server.responses[0]
    assert resp["ok"] is True
    payload = resp["payload"]

    # Fields come straight from restore_session_files' real return.
    assert payload["restored_files"] == []
    errs = payload["restore_errors"]
    assert isinstance(errs, list) and len(errs) == 1
    assert errs[0]["file"] == str(blocker)
    assert isinstance(errs[0]["error"], str) and errs[0]["error"]
    # Bug check: the raw "errors" key is also still present (mapped, not renamed).
    assert payload["errors"] == errs


@pytest.mark.asyncio
async def test_session_restore_files_restore_errors_empty_on_success(tmp_path: Path):
    """Happy path: a successful restore yields ``restore_errors == []``
    (present, not missing/undefined) and the file is actually written back.
    """
    server = _FakeGatewayServer()
    handler = _register(server)["session.restore_files"]

    target = tmp_path / "src.txt"
    target.write_text("MODIFIED")

    files = {str(target): {"restore_content": "ORIGINAL", "action": "write"}}
    with _patch_diff_service(files):
        await handler(
            object(), "req-1", {"session_id": "sess-1", "turn_index": 1}, "sess-1"
        )

    resp = server.responses[0]
    assert resp["ok"] is True
    assert resp["payload"]["restore_errors"] == []
    assert resp["payload"]["errors"] == []
    assert resp["payload"]["restored_files"] == [str(target)]
    # Real side effect of restore_session_files:
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
