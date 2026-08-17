# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse
import sys

import pytest

from jiuwenswarm.channels.process_cli import repl
from jiuwenswarm.channels.process_cli.display_context import (
    select_configured_model_name,
)
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


def test_process_cli_help_uses_chinese_labels() -> None:
    help_text = build_parser().format_help()

    assert help_text.startswith("用法：")
    assert "位置参数：" in help_text
    assert "选项：" in help_text
    assert "显示帮助信息并退出" in help_text


def test_invalid_choice_error_is_fully_chinese(capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        build_parser().parse_args(["--work-mode", "invalid", "task"])

    error = capsys.readouterr().err
    assert "参数 --work-mode 的值无效" in error
    assert "可选值：'code', 'work'" in error
    assert "invalid choice" not in error


@pytest.mark.asyncio
async def test_interactive_prompt_keeps_existing_text(monkeypatch) -> None:
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "/exit"

    monkeypatch.setattr("builtins.input", fake_input)

    assert await repl._read_prompt(None) == "/exit"
    assert prompts == ["jiuwenswarm> "]


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
    assert command[command.index("--mode") + 1] == "code.normal"
    assert command[command.index("--work-mode") + 1] == "code"
    assert command[-2:] == ["--", "inspect this project"]


def test_select_configured_model_name_follows_runtime_entry_order() -> None:
    entries = [
        {"model_client_config": {"model_name": "model-a"}},
        {
            "model_client_config": {"model_name": "model-b"},
            "is_default": True,
        },
    ]

    assert select_configured_model_name(entries) == "model-a"


def test_select_configured_model_name_falls_back_to_first_valid_entry() -> None:
    entries = [
        {"model_client_config": {}},
        {"model_client_config": {"model_name": "model-a"}},
        {"model_client_config": {"model_name": "model-b"}},
    ]

    assert select_configured_model_name(entries) == "model-a"


def test_configured_model_name_reads_cli_config_without_runtime_imports(
    monkeypatch,
    tmp_path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        """\
models:
  defaults:
    - model_client_config:
        model_name: ${MODEL_NAME:-fallback-model}
      is_default: true
""",
        encoding="utf-8",
    )
    (config_dir / ".env").write_text("MODEL_NAME=dotenv-model\n", encoding="utf-8")
    monkeypatch.setenv("JIUWENSWARM_CONFIG_DIR", str(config_dir))

    assert repl._resolve_configured_model_name() == "dotenv-model"


@pytest.mark.parametrize(
    ("mode", "work_mode", "expected"),
    [
        ("code.normal", "code", "code.normal"),
        ("agent", "work", "agent"),
        ("agent", "code", "code.normal"),
        ("agent.plan", "code", "code.plan"),
    ],
)
def test_display_mode_collapses_mode_and_work_mode(
    mode: str,
    work_mode: str,
    expected: str,
) -> None:
    assert repl._resolve_display_mode(mode, work_mode) == expected


@pytest.mark.asyncio
async def test_repl_runs_every_instruction_in_a_new_worker_and_reuses_session(
    monkeypatch,
    capsys,
) -> None:
    prompts = iter(("first", "second", "/new", "third", "/session", "/exit"))
    calls: list[tuple[str, str | None]] = []

    async def fake_read_prompt(_session) -> str:
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
    monkeypatch.setattr(repl, "_create_prompt_session", lambda: None)
    monkeypatch.setattr(repl, "_run_worker", fake_run_worker)
    monkeypatch.setattr(
        repl,
        "_resolve_configured_model_name",
        lambda: "gpt-5.6-sol",
    )

    result = await repl.run_repl(_args())

    assert result == 0
    assert calls == [
        ("first", None),
        ("second", "runtime-session-1"),
        ("third", None),
    ]
    output = capsys.readouterr().out
    assert ">_ JiuwenSwarm" in output
    assert "模型：  gpt-5.6-sol" in output
    assert "模式：  code.normal" in output
    assert "工作模式" not in output
    assert "进程式 CLI · 本地 Runtime" in output
    assert "每条指令均在独立进程中运行" in output
    assert "下一条指令将创建新的 Runtime 会话" in output
    assert "runtime-session-3" in output
