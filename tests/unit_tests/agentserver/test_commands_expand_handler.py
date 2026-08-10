# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``commands.expand`` handler and the root it is bounded to.

The handler is the point where a user-defined command stops being a file and
becomes a prompt, so the two things worth pinning here are that it refuses to
run a command that is not active, and that the ``@file`` root is the session's
locked project, never the one the request asked for.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.server.agent_ws_server import (
    AgentWebSocketServer,
    _request_builtin_names,
    resolve_command_workspace_dir,
    resolve_locked_command_workspace_dir,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _request(**params) -> SimpleNamespace:
    """A duck-typed AgentRequest: the handler only reads attributes."""
    return SimpleNamespace(
        request_id="req-1",
        channel_id="chan-1",
        session_id=params.pop("session_id", "s-1"),
        req_method="commands.expand",
        metadata={},
        params=params,
    )


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _expand(request: SimpleNamespace) -> dict:
    """Drive the handler and return the decoded payload it sent.

    ``_handle_commands_expand`` never touches ``self``, so an unbound call
    avoids constructing a whole server for a read-only RPC.
    """
    ws = _FakeWs()

    async def _run() -> None:
        await AgentWebSocketServer._handle_commands_expand(
            None, ws, request, asyncio.Lock()
        )

    asyncio.run(_run())
    assert len(ws.sent) == 1
    wire = json.loads(ws.sent[0])
    return wire


def _payload(wire: dict) -> dict:
    """Dig the payload out of the wire frame, whichever shape it took."""
    found = _find_key(wire, "text") or _find_key(wire, "error")
    assert found is not None, f"no payload in {wire}"
    return found


def _find_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return node
        for value in node.values():
            hit = _find_key(value, key)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for value in node:
            hit = _find_key(value, key)
            if hit is not None:
                return hit
    return None


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user_home = tmp_path / "userhome"
    (user_home / "commands").mkdir(parents=True)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.command_config_service.get_user_workspace_dir",
        lambda: user_home,
    )
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    return ws_dir


@pytest.fixture
def locked_session(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> Path:
    """Expand only runs against a session-locked project_dir."""

    def _metadata(session_id, **kw):
        return {"project_dir": str(workspace)}

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        _metadata,
    )
    return workspace


# ---------------------------------------------------------------- expansion


def test_a_command_expands_arguments_and_files(locked_session: Path) -> None:
    workspace = locked_session
    _write(workspace / "src" / "a.py", "CODE")
    _write(
        workspace / ".jiuwenswarm" / "commands" / "review.md",
        "Review @$1 for $ARGUMENTS",
    )

    payload = _payload(_expand(_request(name="review", args="src/a.py carefully")))
    assert "CODE" in payload["text"]
    assert "src/a.py carefully" in payload["text"]
    assert payload["embedded"] == ["src/a.py"]
    assert payload["errors"] == []


def test_an_unreadable_file_is_reported_not_silently_dropped(locked_session: Path) -> None:
    """A prompt with a hole in it is worse than a visible error."""
    _write(locked_session / ".jiuwenswarm" / "commands" / "review.md", "Look at @gone.py")

    payload = _payload(_expand(_request(name="review", args="")))
    assert payload["errors"]
    assert "could not read @gone.py" in payload["text"]


def test_an_unknown_command_fails(locked_session: Path) -> None:
    payload = _payload(_expand(_request(name="nope", args="")))
    assert "unknown or inactive" in payload["error"]


def test_a_reserved_command_never_expands(locked_session: Path) -> None:
    """Listed so the UI can explain it; never run."""
    _write(locked_session / ".jiuwenswarm" / "commands" / "help.md", "Body")

    payload = _payload(_expand(_request(name="help", args="")))
    assert "unknown or inactive" in payload["error"]


def test_a_client_builtin_never_expands(locked_session: Path) -> None:
    """Listing and expansion must agree on what is active."""
    _write(locked_session / ".jiuwenswarm" / "commands" / "model.md", "Body")

    allowed = _payload(_expand(_request(name="model", args="")))
    assert "text" in allowed

    refused = _payload(
        _expand(_request(name="model", args="", builtin_names=["model"]))
    )
    assert "unknown or inactive" in refused["error"]


def test_a_shadowed_definition_never_expands(
    locked_session: Path, tmp_path: Path,
) -> None:
    _write(tmp_path / "userhome" / "commands" / "dup.md", "USER BODY")
    _write(locked_session / ".jiuwenswarm" / "commands" / "dup.md", "PROJECT BODY")

    payload = _payload(_expand(_request(name="dup", args="")))
    assert "PROJECT BODY" in payload["text"]
    assert "USER BODY" not in payload["text"]


def test_expansion_cannot_escape_the_workspace(
    locked_session: Path, tmp_path: Path,
) -> None:
    """The root bounds a user-supplied argument, so the check has to be real."""
    _write(tmp_path / "secret.txt", "SECRET")
    _write(locked_session / ".jiuwenswarm" / "commands" / "leak.md", "Show @$1")

    payload = _payload(_expand(_request(name="leak", args="../secret.txt")))
    assert "SECRET" not in payload["text"]
    assert payload["errors"]


def test_a_missing_name_fails(locked_session: Path) -> None:
    payload = _payload(_expand(_request(name="", args="x")))
    assert "required" in payload["error"]


def test_expand_without_locked_session_fails() -> None:
    payload = _payload(_expand(_request(name="review", args="", session_id="")))
    assert "session workspace not locked" in payload["error"]


def test_expand_rejects_an_unlocked_project_dir_claim(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id, **kw: {},
    )
    _write(workspace / ".jiuwenswarm" / "commands" / "review.md", "Body")
    payload = _payload(
        _expand(_request(name="review", args="", project_dir=str(workspace)))
    )
    assert "session workspace not locked" in payload["error"]


# ---------------------------------------------------------------- the root


def test_the_locked_session_project_dir_wins_over_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client free to name the root could name ``/`` and read anything."""
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id, **kw: {"project_dir": "/locked/project"},
    )
    request = _request(session_id="s-1", project_dir="/attacker/claim")
    assert resolve_command_workspace_dir(request) == "/locked/project"
    assert resolve_locked_command_workspace_dir(request) == "/locked/project"


def test_the_request_is_the_fallback_before_a_value_is_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing to list commands before the first message is a worse trade."""
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id, **kw: {},
    )
    request = _request(session_id="s-1", project_dir="/from/request")
    assert resolve_command_workspace_dir(request) == "/from/request"
    assert resolve_locked_command_workspace_dir(request) is None


def test_an_unreadable_session_falls_back_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(session_id, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        _boom,
    )
    request = _request(session_id="s-1", project_dir="/from/request")
    assert resolve_command_workspace_dir(request) == "/from/request"
    assert resolve_locked_command_workspace_dir(request) is None


def test_declared_builtin_names_are_normalised() -> None:
    request = _request(builtin_names=["Help", "  model  ", "", 7, None])
    from jiuwenswarm.server.runtime.command_config_service import RESERVED_NAMES

    assert _request_builtin_names(request) == {"help", "model"} | set(RESERVED_NAMES)


def test_a_client_that_declares_nothing_gets_the_reserved_floor() -> None:
    from jiuwenswarm.server.runtime.command_config_service import RESERVED_NAMES

    assert _request_builtin_names(_request()) == set(RESERVED_NAMES)
    assert _request_builtin_names(_request(builtin_names="help")) == set(RESERVED_NAMES)
