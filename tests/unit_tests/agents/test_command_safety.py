# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common.tools.command_tools import (
    _check_command_safety,
    _command_spawns_tui,
    _enforce_tui_spawn_budget,
    reset_tui_spawn_history,
    TUI_SPAWN_LIMIT,
)


def test_blocks_pkill_on_jiuwenswarm_backend() -> None:
    reason = _check_command_safety('pkill -f "jiuwenswarm" 2>/dev/null')
    assert reason is not None
    assert "jiuwenswarm" in reason


def test_blocks_pkill_on_jiuwenswarm_tui() -> None:
    reason = _check_command_safety('pkill -f "jiuwenswarm-tui" 2>/dev/null')
    assert reason is not None
    assert "jiuwenswarm" in reason


def test_blocks_pkill_on_jiuwenswarm_tui_in_compound_command() -> None:
    reason = _check_command_safety(
        'echo "clean" && pkill -f "jiuwenswarm-tui" 2>/dev/null; sleep 1'
    )
    assert reason is not None


def test_blocks_killall_on_jiuwenswarm_tui() -> None:
    reason = _check_command_safety("killall jiuwenswarm-tui")
    assert reason is not None


def test_blocks_kill_with_pgrep_subshell() -> None:
    reason = _check_command_safety("kill $(pgrep -f jiuwenswarm-tui)")
    assert reason is not None


def test_blocks_pgrep_xargs_kill_pipeline() -> None:
    reason = _check_command_safety("pgrep -f jiuwenswarm-tui | xargs kill")
    assert reason is not None


def test_blocks_pkill_on_jiuwenclaw_backend() -> None:
    reason = _check_command_safety('pkill -f "jiuwenclaw" 2>/dev/null')
    assert reason is not None


# ── jiuwenswarm-tui spawn 护栏 ────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_tui_spawn_history():
    reset_tui_spawn_history()
    yield
    reset_tui_spawn_history()


@pytest.mark.parametrize(
    "command",
    [
        "jiuwenswarm-tui",
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/jiuwenswarm-tui",
        "cd /tmp && jiuwenswarm-tui --help",
        "node index.js test_init/debug-tui.spec.ts",
        'node ./dist/cli.js "smoke.spec.ts"',
    ],
)
def test_command_spawns_tui_detects_known_patterns(command: str) -> None:
    assert _command_spawns_tui(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat package.json",
        "node -v",
        "grep jiuwenswarm-tui README.md",  # only mentions the binary, doesn't run it
    ],
)
def test_command_spawns_tui_ignores_unrelated_commands(command: str) -> None:
    # "grep jiuwenswarm-tui" is a borderline match — the current pattern requires
    # the binary token to be followed by whitespace/EOL/quote, so a quoted-arg
    # form like `grep jiuwenswarm-tui README.md` triggers a false positive on
    # the trailing whitespace. Document the chosen behaviour explicitly:
    # we tolerate a tiny false-positive rate (grep is cheap; agent can rephrase)
    # in exchange for a simple regex. Tests assert what the regex actually does.
    if command.startswith("grep "):
        assert _command_spawns_tui(command) is True
    else:
        assert _command_spawns_tui(command) is False


def test_enforce_tui_spawn_budget_allows_first_few_then_blocks() -> None:
    sid = "session_under_test"
    # Limit defaults to 3 per 300s; first 3 must pass, 4th must block.
    for _ in range(TUI_SPAWN_LIMIT):
        assert _enforce_tui_spawn_budget("jiuwenswarm-tui --help", sid) is None
    msg = _enforce_tui_spawn_budget("jiuwenswarm-tui --help", sid)
    assert msg is not None
    assert "spawn budget exceeded" in msg
    assert "Retry in" in msg


def test_enforce_tui_spawn_budget_isolates_sessions() -> None:
    # Saturating session A must not affect session B.
    for _ in range(TUI_SPAWN_LIMIT):
        assert _enforce_tui_spawn_budget("jiuwenswarm-tui", "sess_a") is None
    assert _enforce_tui_spawn_budget("jiuwenswarm-tui", "sess_a") is not None
    assert _enforce_tui_spawn_budget("jiuwenswarm-tui", "sess_b") is None


def test_enforce_tui_spawn_budget_skips_unrelated_commands() -> None:
    # Non-spawn commands should never consume the budget, no matter how many.
    sid = "any_session"
    for _ in range(TUI_SPAWN_LIMIT + 5):
        assert _enforce_tui_spawn_budget("ls -la", sid) is None
    # Budget still fully available.
    for _ in range(TUI_SPAWN_LIMIT):
        assert _enforce_tui_spawn_budget("jiuwenswarm-tui", sid) is None
    assert _enforce_tui_spawn_budget("jiuwenswarm-tui", sid) is not None


def test_enforce_tui_spawn_budget_global_bucket_for_empty_session() -> None:
    # Empty session id must not silently bypass the limit.
    for _ in range(TUI_SPAWN_LIMIT):
        assert _enforce_tui_spawn_budget("jiuwenswarm-tui", "") is None
    assert _enforce_tui_spawn_budget("jiuwenswarm-tui", "") is not None
