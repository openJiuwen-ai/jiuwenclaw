"""Non-authenticated integration tests against the REAL installed Claude CLI.

No credentials and no live model call. These re-assert the Phase 0
characterization forever: binary discovery, the exact pinned version gate, and
the fail-closed no-credential behavior (the CLI must return a clean error
document and the runner must map it to auth_not_configured - never hang, never
prompt for login). CI-safe: each test skips when the pinned CLI is absent.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_binary import (
    resolve_claude_binary,
    verify_claude_version,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_constants import (
    SUPPORTED_CLAUDE_VERSION,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_process import (
    ClaudeProcessRunner,
    build_claude_environment,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError


def _installed_version() -> str | None:
    path = shutil.which("claude")
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=15
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    # e.g. "2.1.218 (Claude Code)"
    return out.stdout.strip().split()[0] if out.stdout.strip() else None


_INSTALLED = _installed_version()
_pinned = pytest.mark.skipif(
    _INSTALLED != SUPPORTED_CLAUDE_VERSION,
    reason=f"pinned Claude CLI {SUPPORTED_CLAUDE_VERSION} not installed (found {_INSTALLED})",
)
_present = pytest.mark.skipif(shutil.which("claude") is None, reason="Claude CLI not installed")


@pytest.fixture(autouse=True)
def _clear_version_cache():
    claude_binary._VERIFIED_EXECUTABLES.clear()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()


@_present
def test_real_binary_discovers_and_is_executable():
    binary = resolve_claude_binary()
    assert binary.exists()


@_pinned
@pytest.mark.asyncio
async def test_real_version_gate_accepts_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    binary = resolve_claude_binary()
    env = build_claude_environment(binary=binary, turn_dir=tmp_path)
    # Must not raise for the exact supported version.
    await verify_claude_version(binary, env, tmp_path)


@_pinned
@pytest.mark.asyncio
async def test_real_cli_no_credentials_fails_closed(tmp_path, monkeypatch):
    """No login -> the auth-status preflight fails closed before any inference."""
    # Isolate HOME to an empty dir and remove any ambient Anthropic credentials
    # so the CLI's native resolution finds no subscription login.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)

    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)

    runner = ClaudeProcessRunner()
    # Bounded so a hypothetical hang fails the test rather than blocking forever.
    with pytest.raises(ClaudeProviderError) as exc:
        await asyncio.wait_for(
            runner.run(messages=[{"role": "user", "content": "say ok"}], tools=[], timeout=60),
            timeout=90,
        )
    # `claude auth status --json` reports loggedIn:false -> login_required, mapped
    # to auth_login_required at the preflight (inference never runs).
    assert exc.value.code == "auth_login_required"
