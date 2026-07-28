"""Runner and model-client tests for the Claude provider.

The runner tests spawn a REAL controlled fake-CLI child process (a genuine
subprocess, used for deterministic failure/success injection) - not a mock of
the runner. The single live authenticated turn is a separate gate that needs a
disposable API key and is not run here. The model-client layer is exercised with
a stubbed runner to isolate its conversion logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

from jiuwenswarm.integrations.ai4research_subscription.claude_constants import (
    CLAUDE_MODEL_ALIAS,
    CLAUDE_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_consumer_policy import (
    CLAUDE_SUBSCRIPTION_ENABLED_ENV,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_contracts import (
    ProviderToolCall,
    ProviderTurnResult,
    ProviderUsage,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_model_client import (
    ClaudeSubscriptionModelClient,
)
from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_process import (
    ClaudeProcessRunner,
    build_claude_environment,
    ensure_claude_runtime,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError


# --------------------------------------------------------------------------- #
# Pure argv / env contract
# --------------------------------------------------------------------------- #

def test_argv_is_the_pinned_contract():
    runner = ClaudeProcessRunner()
    argv = runner._argv(Path("/opt/claude"))
    assert argv == [
        "/opt/claude",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    # v1 must not pass a model or any bypass/dangerous flag.
    assert "--model" not in argv
    assert not any("dangerously" in a for a in argv)
    assert "--resume" not in argv and "--continue" not in argv


def test_env_allowlist_enables_login_resolution_and_strips_others(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.poison.invalid")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/some/dir")
    monkeypatch.setenv("SOME_JIUWEN_SECRET", "must-not-pass")
    monkeypatch.setenv("HTTPS_PROXY", "http://poison.invalid")
    env = build_claude_environment(binary=tmp_path / "bin" / "claude", turn_dir=tmp_path / "t")
    # Subscription-login-only: HOME (so the CLI reads ~/.claude) and a non-default
    # login config dir pass through; NO API-key path variable is ever forwarded.
    assert env["HOME"] == str(tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == "/some/dir"
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "SOME_JIUWEN_SECRET" not in env
    assert "HTTPS_PROXY" not in env  # not inherited


def test_env_requires_home(monkeypatch, tmp_path):
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(ClaudeProviderError) as exc:
        build_claude_environment(binary=tmp_path / "claude", turn_dir=tmp_path)
    assert exc.value.code == "unsafe_runtime"


# --------------------------------------------------------------------------- #
# Real fake-CLI runner tests
# --------------------------------------------------------------------------- #

def _patch_workspace(monkeypatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    return workspace


_SUBSCRIPTION_AUTH_DOC = (
    '{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max"}'
)


def _write_fake_cli(
    path: Path, *, version: str = "2.1.218", body: str, auth_doc: str = _SUBSCRIPTION_AUTH_DOC,
    auth_exit: int = 0,
) -> Path:
    """A real executable answering --version, `auth status`, and `-p` (body).

    The auth-status preflight now runs before every inference turn, so the fake
    CLI must answer it. By default it reports a valid subscription login. Built
    without indentation so a multi-line `body` cannot break the shebang.
    """
    script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{version} (Claude Code)"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "auth" ]; then\n'
        f"  echo '{auth_doc}'\n"
        f"  exit {auth_exit}\n"
        "fi\n"
        f"{body}\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path


def _success_doc_file(tmp_path: Path, inner: dict, **extra) -> Path:
    doc = {"is_error": False, "num_turns": 1, "result": json.dumps(inner)}
    doc.update(extra)
    p = tmp_path / "success.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clear_version_cache():
    claude_binary._VERIFIED_EXECUTABLES.clear()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()


@pytest.mark.asyncio
async def test_runner_success_via_real_fake_cli(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    inner = {"content": "hi from fake", "reasoning_content": "", "tool_calls": [], "finish_reason": "stop"}
    doc = _success_doc_file(tmp_path, inner, usage={"input_tokens": 7, "output_tokens": 2})
    fake = _write_fake_cli(tmp_path / "claude", body=f'cat >/dev/null\ncat "{doc}"\n')

    runner = ClaudeProcessRunner(binary_path=fake)
    result = await runner.run(messages=[{"role": "user", "content": "hello"}], tools=[])
    assert isinstance(result, ProviderTurnResult)
    assert result.content == "hi from fake"
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 7 and result.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_runner_rejects_wrong_version(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    fake = _write_fake_cli(tmp_path / "claude", version="9.9.9", body='cat >/dev/null\necho "{}"\n')
    runner = ClaudeProcessRunner(binary_path=fake)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "unsupported_cli"


@pytest.mark.asyncio
async def test_runner_nonzero_exit_fails_closed(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # Exit nonzero with an is_error document (mirrors the real not-logged-in path).
    fake = _write_fake_cli(
        tmp_path / "claude",
        body='cat >/dev/null\necho \'{"is_error":true,"terminal_reason":"api_error","api_error_status":null,"result":"Not logged in"}\'\nexit 1\n',
    )
    runner = ClaudeProcessRunner(binary_path=fake)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "auth_not_configured"


@pytest.mark.asyncio
async def test_runner_timeout_kills_child(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    fake = _write_fake_cli(tmp_path / "claude", body="sleep 30\n")
    runner = ClaudeProcessRunner(binary_path=fake)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[], timeout=0.4)
    assert exc.value.code == "timeout"


@pytest.mark.asyncio
async def test_runner_num_turns_not_one_fails_closed(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    inner = {"content": "x", "reasoning_content": "", "tool_calls": [], "finish_reason": "stop"}
    doc = _success_doc_file(tmp_path, inner, num_turns=4)
    fake = _write_fake_cli(tmp_path / "claude", body=f'cat >/dev/null\ncat "{doc}"\n')
    runner = ClaudeProcessRunner(binary_path=fake)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "invalid_output"


def test_runtime_is_credential_free(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    runtime = ensure_claude_runtime()
    # The scratch runtime holds only a turns dir; it never creates a credential
    # home, auth file, or config.
    assert runtime.turns_dir.name == "turns"
    assert runtime.turns_dir.is_dir()
    contents = list(runtime.root.iterdir())
    assert contents == [runtime.turns_dir]


# --------------------------------------------------------------------------- #
# Model-client conversion layer (stubbed runner)
# --------------------------------------------------------------------------- #

def _make_client(monkeypatch, *, api_key: str = "", api_base: str = "") -> ClaudeSubscriptionModelClient:
    return ClaudeSubscriptionModelClient(
        model_config=ModelRequestConfig(model_name=CLAUDE_MODEL_ALIAS, temperature=0),
        model_client_config=ModelClientConfig(
            client_id="claude-test",
            client_provider=CLAUDE_PROVIDER_NAME,
            api_key=api_key,
            api_base=api_base,
            timeout=25,
            max_retries=0,
        ),
    )


def test_client_accepts_credential_free_config(monkeypatch):
    # The supported shape: no credential in config (creds come from the env/login).
    client = _make_client(monkeypatch)
    assert isinstance(client, ClaudeSubscriptionModelClient)


def test_client_rejects_config_api_key(monkeypatch):
    # A credential in Jiuwen config is rejected; the CLI resolves creds natively.
    with pytest.raises(ClaudeProviderError) as exc:
        _make_client(monkeypatch, api_key="sk-operator")
    assert exc.value.code == "invalid_config"


def test_client_rejects_api_base(monkeypatch):
    with pytest.raises(ClaudeProviderError) as exc:
        _make_client(monkeypatch, api_base="https://example.invalid")
    assert exc.value.code == "invalid_config"


@pytest.mark.asyncio
async def test_invoke_happy_path_with_stubbed_runner(monkeypatch):
    # Provider is enabled by default (no opt-in needed).
    client = _make_client(monkeypatch)

    async def _fake_run(*, messages, tools, timeout):
        return ProviderTurnResult(
            content="answer",
            finish_reason="stop",
            tool_calls=(),
            reasoning_content="",
            usage=ProviderUsage(input_tokens=10, output_tokens=4, cached_input_tokens=1),
        )

    monkeypatch.setattr(client._runner, "run", _fake_run)
    response = await client.invoke([{"role": "user", "content": "q"}])
    assert response.content == "answer"
    assert response.finish_reason == "stop"
    assert response.metadata["model_provider"] == CLAUDE_PROVIDER_NAME
    assert response.metadata["billing_mode"] == "anthropic_native_credentials"
    assert response.usage_metadata.input_tokens == 10
    assert response.usage_metadata.cache_tokens == 1


@pytest.mark.asyncio
async def test_invoke_converts_tool_calls(monkeypatch):
    # Provider is enabled by default (no opt-in needed).
    client = _make_client(monkeypatch)

    async def _fake_run(*, messages, tools, timeout):
        return ProviderTurnResult(
            content="",
            finish_reason="tool_calls",
            tool_calls=(ProviderToolCall("c1", "cron_list_jobs", {"scope": "all"}),),
            reasoning_content="",
            usage=None,
        )

    monkeypatch.setattr(client._runner, "run", _fake_run)
    response = await client.invoke([{"role": "user", "content": "list crons"}])
    assert response.tool_calls[0].name == "cron_list_jobs"
    assert json.loads(response.tool_calls[0].arguments) == {"scope": "all"}


@pytest.mark.asyncio
async def test_invoke_fails_closed_when_provider_disabled(monkeypatch):
    client = _make_client(monkeypatch)

    async def _fake_run(*, messages, tools, timeout):  # should never run
        raise AssertionError("runner must not be invoked when the provider is disabled")

    monkeypatch.setattr(client._runner, "run", _fake_run)
    monkeypatch.setenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV, "off")
    with pytest.raises(ClaudeProviderError) as exc:
        await client.invoke([{"role": "user", "content": "q"}])
    assert exc.value.code == "provider_disabled"
