# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Chat command orchestration: parse args, connect Gateway, stream response."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import signal
import sys
import uuid as uuid_module
from pathlib import Path

from jiuwenswarm.cli._terminal import write_stderr, write_stdout
from jiuwenswarm.cli.gateway_client import GatewayClient
from jiuwenswarm.cli.events import (
    event_kind,
    is_terminal_event,
)
from jiuwenswarm.cli.render import HumanRenderer, JsonRenderer, JsonlRenderer

logger = logging.getLogger(__name__)

MODE_ALIASES: dict[str, str] = {
    "agent": "agent.plan",
    "fast": "agent.fast",
    "code": "code.normal",
    "normal": "code.normal",
    "team.normal": "team",
}

VALID_MODES = frozenset({
    "agent.plan", "agent.fast", "code.plan", "code.normal", "code.team", "team",
})


def resolve_mode(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized in MODE_ALIASES:
        normalized = MODE_ALIASES[normalized]
    if normalized in VALID_MODES:
        return normalized
    raise ValueError(
        f"invalid mode: {raw!r}.  valid values: {', '.join(sorted(VALID_MODES))}"
    )


def _build_default_gateway_url(path: str = "/tui") -> str:
    port = os.getenv("GATEWAY_PORT", "19001")
    host = os.getenv("GATEWAY_HOST", "127.0.0.1")
    return f"ws://{host}:{port}{path}"


def _generate_session_id() -> str:
    now = datetime.datetime.now(datetime.UTC)
    short = uuid_module.uuid4().hex[:8]
    return f"cli-{now.strftime('%Y%m%d-%H%M%S')}-{short}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jiuwenswarm chat",
        description="Chat with JiuwenSwarm from the command line.",
    )
    p.add_argument(
        "prompt", nargs="*",
        help="Prompt text (joined with spaces).  If omitted and stdin is piped, read stdin.",
    )
    p.add_argument(
        "--mode", default="code.normal",
        help="Execution mode (default: code.normal).",
    )
    p.add_argument(
        "--session",
        help="Reuse or create a named session id.",
    )
    p.add_argument(
        "--cwd",
        help="Working directory for file mentions and agent context.",
    )
    p.add_argument(
        "--project-dir",
        help="Project identity for session and agent cache.  Defaults to --cwd.",
    )
    p.add_argument(
        "--trusted-dir", action="append", default=None,
        help="Trusted directory (repeatable).  Defaults to --project-dir.",
    )
    p.add_argument(
        "--gateway-url",
        help="Explicit Gateway WebSocket URL.",
    )
    p.add_argument(
        "--name",
        help="Named instance for env isolation.",
    )
    p.add_argument(
        "--dotenv",
        help="Path to .env file for env isolation.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Print one final JSON object.",
    )
    p.add_argument(
        "--jsonl", action="store_true",
        help="Print each Gateway event frame as JSON Lines.",
    )
    p.add_argument(
        "--show-reasoning", action="store_true",
        help="Include reasoning output (stderr).",
    )
    p.add_argument(
        "--show-tools", action="store_true",
        help="Include compact tool call/result status (stderr).",
    )
    p.add_argument(
        "--timeout", type=float,
        help="Total response timeout in seconds.",
    )
    return p


def _validate_args(args: argparse.Namespace) -> int | None:
    try:
        args.mode = resolve_mode(args.mode)
    except ValueError as exc:
        logger.error("invalid mode: %s", exc)
        return 2
    if args.json and args.jsonl:
        logger.error("--json and --jsonl are mutually exclusive")
        return 2
    if args.show_reasoning and args.jsonl:
        pass
    if args.timeout is not None and args.timeout <= 0:
        logger.error("--timeout must be positive")
        return 2
    return None


def _build_request(args: argparse.Namespace, prompt: str) -> dict:
    session_id = args.session or _generate_session_id()
    cwd = str(Path(args.cwd or os.getcwd()).resolve())
    project_dir = str(Path(args.project_dir or cwd).resolve())
    trusted_dirs = args.trusted_dir
    if not trusted_dirs:
        trusted_dirs = [project_dir]
    else:
        trusted_dirs = [str(Path(d).resolve()) for d in trusted_dirs]

    return {
        "type": "req",
        "id": f"chat-{uuid_module.uuid4().hex[:12]}",
        "method": "chat.send",
        "is_stream": True,
        "params": {
            "session_id": session_id,
            "content": prompt,
            "query": prompt,
            "mode": args.mode,
            "cwd": cwd,
            "project_dir": project_dir,
            "trusted_dirs": trusted_dirs,
        },
    }


async def _spinner_loop(renderer: HumanRenderer) -> None:
    while renderer.loading:
        renderer.tick_spinner()
        await asyncio.sleep(0.2)


async def _run_interactive_loop(
    client: GatewayClient,
    renderer: HumanRenderer,
    request: dict,
    *,
    timeout: float | None = None,
) -> int:
    interrupted = False
    per_recv_timeout = timeout if timeout and timeout > 0 else 300.0

    loop = asyncio.get_running_loop()

    def _on_first_sigint() -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("Interrupted. Sending cancel (press Ctrl+C again to force exit)...")
        try:
            loop.add_signal_handler(signal.SIGINT, _on_second_sigint)
        except NotImplementedError:
            pass

    def _on_second_sigint() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    try:
        loop.add_signal_handler(signal.SIGINT, _on_first_sigint)
    except NotImplementedError:
        pass

    await client.send_request(request)

    renderer.ensure_loading()
    spinner = asyncio.create_task(_spinner_loop(renderer))

    try:
        while True:
            try:
                data = await asyncio.wait_for(client.recv(), timeout=per_recv_timeout)
            except asyncio.TimeoutError:
                logger.warning("response timed out")
                return 1
            except ConnectionError:
                return 0

            if interrupted:
                await client.send_request({
                    "type": "req",
                    "id": f"interrupt-{uuid_module.uuid4().hex[:8]}",
                    "method": "chat.interrupt",
                    "is_stream": False,
                    "params": {
                        "session_id": request["params"]["session_id"],
                        "intent": "cancel",
                        "mode": request["params"]["mode"],
                    },
                })
                return 130

            if data.get("type") != "event":
                continue

            event_type = data.get("event", "")
            payload = data.get("payload", {})
            kind = event_kind(event_type)

            if kind == "delta":
                renderer.handle_delta(payload)
            elif kind == "reasoning":
                renderer.handle_reasoning(payload)
            elif kind == "tool_call":
                renderer.handle_tool_call(payload)
            elif kind == "tool_result":
                renderer.handle_tool_result(payload)
            elif kind == "final":
                renderer.handle_final(payload)
            elif kind == "error":
                renderer.handle_error(payload)
                return 1
            elif kind == "processing_status":
                if payload.get("is_processing", False):
                    renderer.ensure_loading()
            elif kind == "interactive":
                renderer.clear_loading()
                if sys.stdin.isatty():
                    write_stderr(
                        f"\n[{event_type}] "
                        f"{payload.get('question') or payload.get('message', 'Input needed')}\n"
                    )
                    answer = input("> ").strip()
                    try:
                        await client.send_request({
                            "type": "req",
                            "id": f"answer-{uuid_module.uuid4().hex[:12]}",
                            "method": "chat.user_answer",
                            "is_stream": False,
                            "params": {
                                "session_id": request["params"]["session_id"],
                                "answer": answer,
                                "request_id": payload.get("request_id", ""),
                            },
                        })
                    except ConnectionError:
                        return 0
                else:
                    logger.error("interactive input required but stdin is not a TTY: %s", event_type)
                    return 4

            if is_terminal_event(event_type, payload):
                renderer.clear_loading()
                if renderer.streamed_text and not renderer.streamed_text.endswith("\n"):
                    write_stdout("\n")
                return 0

    finally:
        spinner.cancel()
        try:
            await spinner
        except asyncio.CancelledError:
            pass
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, ValueError):
            pass


async def _run_jsonl_loop(
    client: GatewayClient,
    renderer: JsonlRenderer,
    request: dict,
) -> int:
    await client.send_request(request)
    while True:
        data = await client.recv()
        if data.get("type") != "event":
            continue
        event_type = data.get("event", "")
        payload = data.get("payload", {})
        renderer.handle_event(event_type, payload)
        if event_type == "chat.error":
            return 1
        if is_terminal_event(event_type, payload):
            return 0


async def _run_json_loop(
    client: GatewayClient,
    renderer: JsonRenderer,
    request: dict,
) -> int:
    await client.send_request(request)
    has_error = False
    while True:
        data = await client.recv()
        if data.get("type") != "event":
            continue
        event_type = data.get("event", "")
        payload = data.get("payload", {})
        if event_type == "chat.final":
            renderer.handle_event(event_type, payload)
        elif event_type == "chat.error":
            renderer.handle_error(payload)
            has_error = True
        elif event_type == "chat.delta":
            pass
        else:
            renderer.handle_event(event_type, payload)
        if is_terminal_event(event_type, payload):
            renderer.output()
            return 1 if has_error else 0


async def _run_chat(
    args: argparse.Namespace,
    prompt: str,
) -> int:
    gateway_url = args.gateway_url or _build_default_gateway_url()

    client = GatewayClient(gateway_url)

    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)
    except asyncio.TimeoutError:
        _print_connection_hint(gateway_url, args)
        return 3
    except ConnectionError:
        _print_connection_hint(gateway_url, args)
        return 3
    except OSError:
        _print_connection_hint(gateway_url, args)
        return 3

    request = _build_request(args, prompt)

    try:
        if args.jsonl:
            renderer: JsonlRenderer | JsonRenderer | HumanRenderer = JsonlRenderer()
            return await _run_jsonl_loop(client, renderer, request)
        elif args.json:
            renderer = JsonRenderer()
            return await _run_json_loop(client, renderer, request)
        else:
            renderer = HumanRenderer(
                show_reasoning=args.show_reasoning,
                show_tools=args.show_tools,
            )
            return await _run_interactive_loop(
                client, renderer, request,
                timeout=args.timeout,
            )
    finally:
        await client.close()


def _print_connection_hint(url: str, args: argparse.Namespace) -> None:
    logger.error("Cannot connect to JiuwenSwarm Gateway at %s.", url)
    if args.name:
        logger.error("Start services with: jiuwenswarm-start app --name %s", args.name)
    else:
        logger.error("Start services with: jiuwenswarm-start app")


def run_chat(args: argparse.Namespace) -> int:
    if args.prompt:
        prompt = " ".join(args.prompt)
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        return _run_repl(args)

    if not prompt:
        logger.error("no prompt provided and stdin is empty")
        return 2

    error = _validate_args(args)
    if error is not None:
        return error

    return asyncio.run(_run_chat(args, prompt))


def _run_repl(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        logger.error("REPL mode requires interactive terminal")
        return 2

    error = _validate_args(args)
    if error is not None:
        return error

    session_id = args.session or _generate_session_id()
    args.session = session_id

    logger.info("Session: %s", session_id)
    logger.info("Type your prompt and press Enter.  Ctrl+C to interrupt, Ctrl+D to exit.")

    exit_code = 0
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                logger.info("<EOF>")
                break
            if not line.strip():
                continue
            exit_code = asyncio.run(_run_chat(args, line.strip()))
            if exit_code == 130:
                break
            if exit_code != 0:
                logger.warning("[exit code: %s]", exit_code)
    except KeyboardInterrupt:
        logger.info("<KeyboardInterrupt>")
    return exit_code
