# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Entry point for the process-style JiuwenSwarm CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jiuwenswarm-process",
        description="Run one JiuwenSwarm command in one local Runtime process.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Instruction to execute; omit it to enter the interactive CLI.",
    )
    parser.add_argument("--session", help="Resume an existing Runtime session ID.")
    parser.add_argument("--cwd", help="Working directory; defaults to the current directory.")
    parser.add_argument("--project-dir", help="Stable project directory; defaults to cwd.")
    parser.add_argument(
        "--trusted-dir",
        action="append",
        default=[],
        help="Trusted directory; repeat for multiple directories.",
    )
    parser.add_argument("--mode", default="code.normal", help="Existing Runtime mode.")
    parser.add_argument(
        "--work-mode",
        choices=("code", "work"),
        default="code",
        help="Existing Runtime work-mode profile.",
    )
    parser.add_argument(
        "--output",
        choices=("human", "json", "jsonl"),
        default="human",
        help="Rendering of the same Runtime event stream.",
    )
    parser.add_argument("--timeout", type=float, help="Total execution timeout in seconds.")
    parser.add_argument("--show-reasoning", action="store_true")
    parser.add_argument("--show-tools", action="store_true")
    parser.add_argument(
        "--_interactive-worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--_session-result-file",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.prompt is None and args.output != "human":
        parser.error("interactive mode only supports --output human")
    try:
        if args.prompt is None:
            from jiuwenswarm.channels.process_cli.repl import run_repl

            code = asyncio.run(run_repl(args))
            sys.exit(code)

        # Some existing Runtime dependencies still log to stdout.  Keep the
        # CLI data stream clean by routing those diagnostics to stderr while
        # the renderer retains the original stdout handle.
        data_stdout = sys.stdout
        diagnostic_stderr = sys.stderr
        with contextlib.redirect_stdout(diagnostic_stderr):
            from jiuwenswarm.channels.process_cli.app import run

            code = asyncio.run(
                run(
                    args,
                    stdout=data_stdout,
                    stderr=(
                        data_stdout
                        if args._interactive_worker
                        else diagnostic_stderr
                    ),
                )
            )
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
