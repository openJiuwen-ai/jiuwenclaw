# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse
import ast
import asyncio
import io
import json
from pathlib import Path

import pytest

from jiuwenswarm.channels.process_cli import app
from jiuwenswarm.runtime.events import RuntimeEvent


class FakeClient:
    latest: FakeClient | None = None
    delay = 0.0

    def __init__(self) -> None:
        type(self).latest = self
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def create_or_resume_session(
        self,
        *,
        channel_id: str,
        session_id: str | None,
    ) -> str:
        self.calls.append(f"session:{channel_id}:{session_id or ''}")
        return session_id or "runtime-session"

    async def stream(self, request):
        self.calls.append(f"stream:{request.session_id}")
        if self.delay:
            await asyncio.sleep(self.delay)
        yield RuntimeEvent(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            payload={"event_type": "chat.delta", "delta": "hello"},
        )
        yield RuntimeEvent(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            payload={"event_type": "chat.final", "content": "hello"},
            is_complete=True,
        )

    async def answer_interaction(self, request):
        return []

    async def cancel(self, request) -> None:
        self.calls.append(
            f"cancel:{request.session_id}:{request.request_id}:"
            f"{request.params.get('target_request_id')}"
        )

    async def cleanup_session(self, *, channel_id: str, session_id: str) -> bool:
        self.calls.append(f"cleanup:{channel_id}:{session_id}")
        return True

    async def close(self) -> None:
        self.calls.append("close")


class ErrorClient(FakeClient):
    async def stream(self, request):
        self.calls.append(f"stream:{request.session_id}")
        yield RuntimeEvent.error(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            error=RuntimeError("request failed"),
        )


class StartFailureClient(FakeClient):
    async def start(self) -> None:
        self.calls.append("start")
        raise RuntimeError("start failed")


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    values = {
        "prompt": "say hello",
        "session": None,
        "cwd": str(tmp_path),
        "project_dir": str(tmp_path),
        "trusted_dir": [],
        "mode": "code.normal",
        "work_mode": "code",
        "output": "jsonl",
        "timeout": None,
        "show_reasoning": False,
        "show_tools": False,
        "_interactive_worker": False,
        "_session_result_file": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.asyncio
async def test_one_command_owns_one_runtime_lifecycle(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    FakeClient.delay = 0.0
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)

    result = await app.run(_args(tmp_path))

    client = FakeClient.latest
    assert result == 0
    assert client is not None
    assert client.calls == [
        "start",
        "session:process_cli:",
        "stream:runtime-session",
        "cleanup:process_cli:runtime-session",
        "close",
    ]
    output = capsys.readouterr().out
    assert '"event_type": "chat.delta"' in output
    assert '"event_type": "chat.final"' in output


@pytest.mark.asyncio
async def test_timeout_precisely_cancels_then_cleans_up(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeClient.delay = 0.1
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)

    result = await app.run(_args(tmp_path, timeout=0.01))

    client = FakeClient.latest
    assert result == 124
    assert client is not None
    assert client.calls[0:3] == [
        "start",
        "session:process_cli:",
        "stream:runtime-session",
    ]
    cancel_call = next(call for call in client.calls if call.startswith("cancel:"))
    _kind, session_id, request_id, target_request_id = cancel_call.split(":")
    assert session_id == "runtime-session"
    assert request_id == target_request_id
    assert client.calls[-2:] == [
        "cleanup:process_cli:runtime-session",
        "close",
    ]


@pytest.mark.asyncio
async def test_timeout_keeps_jsonl_error_machine_compatible(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeClient.delay = 0.1
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)
    stdout = io.StringIO()

    try:
        result = await app.run(
            _args(tmp_path, timeout=0.01),
            stdout=stdout,
            stderr=io.StringIO(),
        )
    finally:
        FakeClient.delay = 0.0

    document = json.loads(stdout.getvalue().strip())
    assert result == 124
    assert document["payload"]["event_type"] == "runtime.error"
    assert document["payload"]["error"] == "process CLI execution timed out"
    assert "\033[" not in stdout.getvalue()


@pytest.mark.asyncio
async def test_runtime_error_returns_failure_and_cleans_up(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app, "InProcessRuntimeClient", ErrorClient)

    result = await app.run(_args(tmp_path))

    client = ErrorClient.latest
    assert result == 1
    assert client is not None
    assert client.calls[-2:] == [
        "cleanup:process_cli:runtime-session",
        "close",
    ]


@pytest.mark.asyncio
async def test_task_cancellation_precisely_cancels_then_cleans_up(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeClient.delay = 10.0
    FakeClient.latest = None
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)
    try:
        task = asyncio.create_task(app.run(_args(tmp_path)))
        while FakeClient.latest is None or not any(
            call.startswith("stream:") for call in FakeClient.latest.calls
        ):
            await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        client = FakeClient.latest
        assert client is not None
        cancel_call = next(call for call in client.calls if call.startswith("cancel:"))
        _kind, session_id, request_id, target_request_id = cancel_call.split(":")
        assert session_id == "runtime-session"
        assert request_id == target_request_id
        assert client.calls[-2:] == [
            "cleanup:process_cli:runtime-session",
            "close",
        ]
    finally:
        FakeClient.delay = 0.0


@pytest.mark.asyncio
async def test_human_task_cancellation_shows_interrupted_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeClient.delay = 10.0
    FakeClient.latest = None
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)
    monkeypatch.setenv("NO_COLOR", "1")
    output = io.StringIO()
    try:
        task = asyncio.create_task(
            app.run(
                _args(tmp_path, output="human"),
                stdout=output,
                stderr=output,
            )
        )
        while FakeClient.latest is None or not any(
            call.startswith("stream:") for call in FakeClient.latest.calls
        ):
            await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "! 已中断" in output.getvalue()
    finally:
        FakeClient.delay = 0.0


@pytest.mark.asyncio
async def test_start_failure_still_closes_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app, "InProcessRuntimeClient", StartFailureClient)

    with pytest.raises(RuntimeError, match="start failed"):
        await app.run(_args(tmp_path))

    client = StartFailureClient.latest
    assert client is not None
    assert client.calls == ["start", "close"]


@pytest.mark.asyncio
async def test_interactive_worker_reports_real_runtime_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeClient.delay = 0.0
    monkeypatch.setattr(app, "InProcessRuntimeClient", FakeClient)
    result_file = tmp_path / "session.txt"

    result = await app.run(
        _args(
            tmp_path,
            output="human",
            _interactive_worker=True,
            _session_result_file=str(result_file),
        )
    )

    assert result == 0
    assert result_file.read_text(encoding="utf-8") == "runtime-session"


def test_interactive_worker_enables_runtime_interactions(tmp_path: Path) -> None:
    request = app._build_request(
        _args(
            tmp_path,
            output="human",
            _interactive_worker=True,
        ),
        session_id="runtime-session",
        request_id="runtime-request",
    )

    assert request.params["supports_user_interaction"] is True


def test_process_cli_has_no_server_or_transport_dependencies() -> None:
    package = Path(app.__file__).resolve().parent
    forbidden = (
        "jiuwenswarm.gateway",
        "jiuwenswarm.server.agent_ws_server",
        "websockets",
    )
    violations: list[str] = []
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        violations.append(f"{source.name}:{node.lineno}:{alias.name}")
            if module.startswith(forbidden):
                violations.append(f"{source.name}:{node.lineno}:{module}")
    assert violations == []


def test_new_entry_does_not_replace_existing_remote_cli() -> None:
    pyproject = Path(app.__file__).parents[3] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'jiuwenswarm = "jiuwenswarm.channels.cli.main:main"' in text
    assert 'jiuwenswarm-process = "jiuwenswarm.channels.process_cli.main:main"' in text
