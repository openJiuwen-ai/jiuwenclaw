"""Read-only provider status probe tests (the 5 UI states + unverifiable).

Drives ``ClaudeProcessRunner.probe_status`` with fake CLIs, proving each state:
missing CLI, wrong version, login required, wrong auth method, subscription
ready, and auth-status-unverifiable. No inference, no secrets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_auth_seam import (
    ClaudeProviderStatus as PS,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_process import ClaudeProcessRunner


def _patch_workspace(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def _write_auth_cli(path: Path, *, version: str = "2.1.218", auth_doc: str, auth_exit: int = 0) -> Path:
    script = (
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "auth" ]; then\n'
        f"  echo '{auth_doc}'\n"
        f"  exit {auth_exit}\n"
        "fi\n"
        "exit 0\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.fixture(autouse=True)
def _clear_version_cache():
    claude_binary._VERIFIED_EXECUTABLES.clear()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()


_SUBSCRIPTION = '{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max"}'
_API_KEY = '{"loggedIn":true,"authMethod":"api_key","apiProvider":"firstParty","apiKeySource":"ANTHROPIC_API_KEY"}'
_LOGGED_OUT = '{"loggedIn":false,"authMethod":"none","apiProvider":"firstParty"}'


@pytest.mark.asyncio
async def test_status_missing_cli(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    runner = ClaudeProcessRunner(binary_path=tmp_path / "does-not-exist", enforce_version=False)
    assert await runner.probe_status() is PS.MISSING_CLI


@pytest.mark.asyncio
async def test_status_wrong_version(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", version="9.9.9", auth_doc=_SUBSCRIPTION)
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=True)
    assert await runner.probe_status() is PS.WRONG_VERSION


@pytest.mark.asyncio
async def test_status_login_required(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", auth_doc=_LOGGED_OUT, auth_exit=1)
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=False)
    assert await runner.probe_status() is PS.LOGIN_REQUIRED


@pytest.mark.asyncio
async def test_status_wrong_auth_method(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", auth_doc=_API_KEY)
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=False)
    assert await runner.probe_status() is PS.WRONG_AUTH_METHOD


@pytest.mark.asyncio
async def test_status_subscription_ready(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", auth_doc=_SUBSCRIPTION)
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=False)
    assert await runner.probe_status() is PS.SUBSCRIPTION_READY


@pytest.mark.asyncio
async def test_status_unverifiable(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", auth_doc="not json at all")
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=False)
    assert await runner.probe_status() is PS.AUTH_STATUS_UNVERIFIABLE


@pytest.mark.asyncio
async def test_status_probe_leaves_no_leftover_dir(monkeypatch, tmp_path):
    # Regression: the probe used a "status-" prefix that cleanup rejected, leaking
    # a directory per refresh. It must clean up after itself.
    _patch_workspace(monkeypatch, tmp_path)
    cli = _write_auth_cli(tmp_path / "claude", auth_doc=_SUBSCRIPTION)
    runner = ClaudeProcessRunner(binary_path=cli, enforce_version=False)
    from jiuwenswarm.integrations.ai4research_subscription.claude_process import (
        ensure_claude_runtime,
    )

    for _ in range(3):
        assert await runner.probe_status() is PS.SUBSCRIPTION_READY
    turns_dir = ensure_claude_runtime().turns_dir
    assert list(turns_dir.iterdir()) == []  # nothing leaked
