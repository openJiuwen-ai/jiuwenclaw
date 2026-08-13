# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interactive launcher for one-command/one-Runtime worker processes."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from asyncio.subprocess import Process

_EXIT_COMMANDS = frozenset({"/exit", "/quit", "exit", "quit"})
_HELP = """Commands:
  /help       Show this help
  /new        Start the next instruction in a new Runtime Session
  /session    Show the current Runtime Session ID
  /exit       Exit the interactive CLI
"""


def _worker_command(
    args: argparse.Namespace,
    *,
    prompt: str,
    session_id: str | None,
    session_result_file: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "jiuwenswarm.channels.process_cli.main",
        "--output",
        "human",
        "--mode",
        args.mode,
        "--work-mode",
        args.work_mode,
        "--_interactive-worker",
        "--_session-result-file",
        session_result_file,
    ]
    if session_id:
        command.extend(("--session", session_id))
    if args.cwd:
        command.extend(("--cwd", args.cwd))
    if args.project_dir:
        command.extend(("--project-dir", args.project_dir))
    for trusted_dir in args.trusted_dir:
        command.extend(("--trusted-dir", trusted_dir))
    if args.timeout is not None:
        command.extend(("--timeout", str(args.timeout)))
    if args.show_reasoning:
        command.append("--show-reasoning")
    if args.show_tools:
        command.append("--show-tools")
    command.extend(("--", prompt))
    return command


async def _drain_runtime_logs(reader: asyncio.StreamReader) -> deque[str]:
    tail: deque[str] = deque(maxlen=20)
    while True:
        line = await reader.readline()
        if not line:
            return tail
        tail.append(line.decode(errors="replace").rstrip())


async def _interrupt_worker(process: Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.send_signal(
            signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
        )
        await asyncio.wait_for(process.wait(), timeout=15.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def _run_worker(
    args: argparse.Namespace,
    *,
    prompt: str,
    session_id: str | None,
) -> tuple[int, str | None]:
    with tempfile.TemporaryDirectory(prefix="jiuwenswarm-process-repl-") as temp_dir:
        result_path = Path(temp_dir) / "session-id.txt"
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
        process = await asyncio.create_subprocess_exec(
            *_worker_command(
                args,
                prompt=prompt,
                session_id=session_id,
                session_result_file=str(result_path),
            ),
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        assert process.stderr is not None
        log_task = asyncio.create_task(_drain_runtime_logs(process.stderr))
        try:
            return_code = await process.wait()
        except asyncio.CancelledError:
            await _interrupt_worker(process)
            raise
        finally:
            log_tail = await log_task

        next_session = session_id
        if result_path.exists():
            value = result_path.read_text(encoding="utf-8").strip()
            if value:
                next_session = value
        if return_code != 0 and not result_path.exists() and log_tail:
            print("\nWorker diagnostics:", file=sys.stderr)
            print("\n".join(log_tail), file=sys.stderr)
        return return_code, next_session


async def _read_prompt() -> str:
    return await asyncio.to_thread(input, "jiuwenswarm> ")


async def run_repl(args: argparse.Namespace) -> int:
    """Run a UI-only shell; every instruction owns a fresh worker process."""
    print("JiuwenSwarm Process CLI")
    print("Each instruction runs in a fresh process; Runtime Session is preserved.")
    print("Type /help for commands.\n")
    session_id = args.session
    while True:
        try:
            prompt = (await _read_prompt()).strip()
        except EOFError:
            print()
            return 0
        if not prompt:
            continue
        lowered = prompt.lower()
        if lowered in _EXIT_COMMANDS:
            return 0
        if lowered == "/help":
            print(_HELP)
            continue
        if lowered == "/new":
            session_id = None
            print("The next instruction will create a new Runtime Session.")
            continue
        if lowered == "/session":
            print(session_id or "No Runtime Session has been created yet.")
            continue
        _return_code, session_id = await _run_worker(
            args,
            prompt=prompt,
            session_id=session_id,
        )


__all__ = ["run_repl"]
