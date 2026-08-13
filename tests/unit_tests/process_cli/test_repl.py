# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse
import sys

import pytest

from jiuwenswarm.channels.process_cli import repl
from jiuwenswarm.channels.process_cli.main import build_parser


def _args(**overrides) -> argparse.Namespace:
    values = {
        "session": None,
        "cwd": None,
        "project_dir": None,
        "trusted_dir": [],
        "mode": "code.normal",
        "work_mode": "code",
        "output": "human",
        "timeout": None,
        "show_reasoning": False,
        "show_tools": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_parser_enters_interactive_mode_without_prompt() -> None:
    args = build_parser().parse_args([])

    assert args.prompt is None
    assert args.output == "human"


def test_worker_command_uses_a_fresh_process_entry_and_runtime_session() -> None:
    command = repl._worker_command(
        _args(timeout=30.0, trusted_dir=["D:/trusted"]),
        prompt="inspect this project",
        session_id="process_cli_session_1",
        session_result_file="D:/temp/session.txt",
    )

    assert command[:3] == [
        sys.executable,
        "-m",
        "jiuwenswarm.channels.process_cli.main",
    ]
    assert "--_interactive-worker" in command
    assert command[command.index("--session") + 1] == "process_cli_session_1"
    assert command[-2:] == ["--", "inspect this project"]


@pytest.mark.asyncio
async def test_repl_runs_every_instruction_in_a_new_worker_and_reuses_session(
    monkeypatch,
    capsys,
) -> None:
    prompts = iter(("first", "second", "/new", "third", "/session", "/exit"))
    calls: list[tuple[str, str | None]] = []

    async def fake_read_prompt() -> str:
        return next(prompts)

    async def fake_run_worker(
        args,
        *,
        prompt: str,
        session_id: str | None,
    ) -> tuple[int, str]:
        calls.append((prompt, session_id))
        return 0, session_id or f"runtime-session-{len(calls)}"

    monkeypatch.setattr(repl, "_read_prompt", fake_read_prompt)
    monkeypatch.setattr(repl, "_run_worker", fake_run_worker)

    result = await repl.run_repl(_args())

    assert result == 0
    assert calls == [
        ("first", None),
        ("second", "runtime-session-1"),
        ("third", None),
    ]
    output = capsys.readouterr().out
    assert "JiuwenSwarm Process CLI" in output
    assert "runtime-session-3" in output
