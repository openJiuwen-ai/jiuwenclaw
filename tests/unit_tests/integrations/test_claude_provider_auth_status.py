"""Subscription-billing verification tests (the locked subscription-only gate).

Two layers:

* pure classifier table over the reviewed non-secret fields;
* the six required end-to-end scenarios driven through the REAL runner with a
  fake CLI whose ``auth status`` response the test controls: (1) subscription
  login succeeds; (2) no login fails closed; (3) API-key rejected; (4) cloud
  (Bedrock/Vertex/Foundry) rejected; (5) malformed/unknown fails closed;
  (6) an auth change between turns is detected because the check reruns each turn.

The fake CLI is a real subprocess. It records a sentinel when its ``-p`` branch
runs, so a test can prove inference NEVER runs when the preflight fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_auth_seam import (
    ClaudeSubscriptionAuthState as S,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_auth_status import (
    classify_subscription_auth,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_process import ClaudeProcessRunner
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError


# --------------------------------------------------------------------------- #
# Pure classifier
# --------------------------------------------------------------------------- #

def _doc(**fields) -> bytes:
    return json.dumps(fields).encode("utf-8")


def test_classifier_subscription_ready():
    out = _doc(loggedIn=True, authMethod="claude.ai", apiProvider="firstParty", subscriptionType="max")
    assert classify_subscription_auth(out, 0) is S.SUBSCRIPTION_READY


def test_classifier_login_required():
    assert (
        classify_subscription_auth(_doc(loggedIn=False, authMethod="none", apiProvider="firstParty"), 1)
        is S.LOGIN_REQUIRED
    )
    # loggedIn true but authMethod none is still logged out.
    assert (
        classify_subscription_auth(_doc(loggedIn=True, authMethod="none", apiProvider="firstParty"), 0)
        is S.LOGIN_REQUIRED
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "vertex", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "foundry", "subscriptionType": "max"},
        {"loggedIn": True, "authMethod": "console", "apiProvider": "firstParty"},
        # claude.ai login with no attached subscription plan is not proof.
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": ""},
    ],
)
def test_classifier_wrong_auth_method(fields):
    assert classify_subscription_auth(_doc(**fields), 0) is S.WRONG_AUTH_METHOD


@pytest.mark.parametrize(
    "stdout,rc",
    [
        (b"not json", 0),
        (b"", 0),
        (b"[]", 0),
        (b'{"loggedIn":"yes"}', 0),  # wrong type
        (b'{"authMethod":"claude.ai"}', 0),  # missing loggedIn
        # a subscription-looking doc but a nonzero exit is inconsistent.
        (json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}).encode(), 1),
    ],
)
def test_classifier_unverifiable(stdout, rc):
    assert classify_subscription_auth(stdout, rc) is S.AUTH_STATUS_UNVERIFIABLE


# --------------------------------------------------------------------------- #
# End-to-end through the real runner (six required scenarios)
# --------------------------------------------------------------------------- #

_SUCCESS_INFERENCE = json.dumps(
    {
        "is_error": False,
        "num_turns": 1,
        "result": json.dumps(
            {"content": "answer", "reasoning_content": "", "tool_calls": [], "finish_reason": "stop"}
        ),
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)


def _patch_workspace(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def _write_controlled_cli(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    """A real CLI reading its auth doc / exit / inference doc from files.

    Returns (binary, auth_doc_file, auth_exit_file, inference_doc_file,
    sentinel_file). The sentinel is written only when the ``-p`` branch runs, so
    a test can assert inference never ran.
    """
    auth_doc = tmp_path / "auth_doc.json"
    auth_exit = tmp_path / "auth_exit.txt"
    infer_doc = tmp_path / "infer_doc.json"
    sentinel = tmp_path / "inference_ran.sentinel"
    infer_doc.write_text(_SUCCESS_INFERENCE, encoding="utf-8")
    auth_exit.write_text("0", encoding="utf-8")
    binary = tmp_path / "claude"
    script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "2.1.218 (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "auth" ]; then\n'
        f'  cat "{auth_doc}"\n'
        f'  exit "$(cat "{auth_exit}")"\n'
        "fi\n"
        "cat >/dev/null\n"
        f'echo ran > "{sentinel}"\n'
        f'cat "{infer_doc}"\n'
    )
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o700)
    return binary, auth_doc, auth_exit, infer_doc, sentinel


@pytest.fixture(autouse=True)
def _clear_version_cache():
    claude_binary._VERIFIED_EXECUTABLES.clear()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()


@pytest.mark.asyncio
async def test_scenario_1_subscription_login_succeeds(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, _exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    auth_doc.write_text(
        json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}),
        encoding="utf-8",
    )
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)
    result = await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result.content == "answer"
    assert sentinel.exists()  # inference ran


@pytest.mark.asyncio
async def test_scenario_2_no_login_fails_closed(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, auth_exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    auth_doc.write_text(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}), encoding="utf-8")
    auth_exit.write_text("1", encoding="utf-8")
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "auth_login_required"
    assert not sentinel.exists()  # inference NEVER ran


@pytest.mark.asyncio
async def test_scenario_3_api_key_rejected(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, _exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    auth_doc.write_text(
        json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"}),
        encoding="utf-8",
    )
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "auth_wrong_method"
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_scenario_4_cloud_billing_rejected(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, _exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    auth_doc.write_text(
        json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock", "subscriptionType": "max"}),
        encoding="utf-8",
    )
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "auth_wrong_method"
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_scenario_5_malformed_status_fails_closed(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, _exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    auth_doc.write_text("this is not json", encoding="utf-8")
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "auth_unverifiable"
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_scenario_6_auth_change_between_turns_is_detected(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    binary, auth_doc, _exit, _infer, sentinel = _write_controlled_cli(tmp_path)
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)

    # Turn 1: subscription login -> succeeds.
    auth_doc.write_text(
        json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}),
        encoding="utf-8",
    )
    result = await runner.run(messages=[{"role": "user", "content": "one"}], tools=[])
    assert result.content == "answer"
    sentinel.unlink()  # reset for turn 2

    # Turn 2: login changed to an API key -> rerun of the check rejects it.
    auth_doc.write_text(
        json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "apiKeySource": "ANTHROPIC_API_KEY"}),
        encoding="utf-8",
    )
    with pytest.raises(ClaudeProviderError) as exc:
        await runner.run(messages=[{"role": "user", "content": "two"}], tools=[])
    assert exc.value.code == "auth_wrong_method"
    assert not sentinel.exists()  # turn 2 never reached inference
