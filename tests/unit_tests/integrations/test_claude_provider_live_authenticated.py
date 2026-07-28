"""Authenticated live end-to-end turn for the Claude provider.

This is the single "real success" gate: it makes a real Claude call through the
actual pinned CLI and asserts a genuine assistant answer comes back.

**Subscription-login-only.** The credential comes from the operator's own
``claude`` login (performed outside this product), resolved natively by the CLI
from the real ``HOME``/``~/.claude`` that the runner passes into the child. No
API key is forwarded - ``ANTHROPIC_API_KEY`` and friends are deliberately
stripped by the runner's allowlist - so a key alone will NOT authenticate here.

Because a real turn spends your subscription quota, this test is **opt-in**: it
is SKIPPED unless you explicitly set ``CLAUDE_PROVIDER_LIVE=1`` AND the pinned
CLI is installed. The provider is enabled by default. Run it when your Claude CLI
is logged in with a subscription.

Mock-only success is prohibited by project rule; this test is the non-mock
success proof. No credential is written to evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

from jiuwenswarm.integrations.ai4research_subscription.claude_constants import (
    CLAUDE_MODEL_ALIAS,
    CLAUDE_PROVIDER_NAME,
    SUPPORTED_CLAUDE_VERSION,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_model_client import (
    ClaudeSubscriptionModelClient,
)


def _installed_version() -> str | None:
    path = shutil.which("claude")
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip().split()[0]


_OPTED_IN = os.environ.get("CLAUDE_PROVIDER_LIVE", "").strip() == "1"
_PINNED = _installed_version() == SUPPORTED_CLAUDE_VERSION

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _OPTED_IN,
        reason="live turn is opt-in: set CLAUDE_PROVIDER_LIVE=1 (with the CLI logged in)",
    ),
    pytest.mark.skipif(not _PINNED, reason=f"pinned Claude CLI {SUPPORTED_CLAUDE_VERSION} not installed"),
]


def _client() -> ClaudeSubscriptionModelClient:
    # Credential-free config: the CLI resolves the operator's own Claude login
    # from the environment. Never place a credential in the config.
    return ClaudeSubscriptionModelClient(
        model_config=ModelRequestConfig(model_name=CLAUDE_MODEL_ALIAS, temperature=0),
        model_client_config=ModelClientConfig(
            client_id="claude-live",
            client_provider=CLAUDE_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=120,
            max_retries=0,
        ),
    )


@pytest.mark.asyncio
async def test_live_text_turn_returns_real_answer():
    client = _client()
    response = await client.invoke(
        [{"role": "user", "content": "Reply with exactly the word: pong"}],
    )
    assert response.finish_reason == "stop"
    assert isinstance(response.content, str) and response.content.strip()
    assert "pong" in response.content.lower()
    assert response.metadata["model_provider"] == CLAUDE_PROVIDER_NAME
    # No credential material must appear in the surfaced response.
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        assert key not in json.dumps(response.metadata)


@pytest.mark.asyncio
async def test_live_multi_turn_preserves_history():
    client = _client()
    response = await client.invoke(
        [
            {"role": "user", "content": "My favorite number is 41. Remember it."},
            {"role": "assistant", "content": "Noted, your favorite number is 41."},
            {"role": "user", "content": "What is my favorite number plus one? Reply with just the number."},
        ],
    )
    assert response.finish_reason == "stop"
    assert "42" in response.content


@pytest.mark.asyncio
async def test_live_tool_call_is_wellformed_when_it_occurs():
    """Observe (do not prove) a live tool-call request for an allowlisted tool.

    This is NOT the proof that tool-call round-tripping works: whether the model
    chooses to call a tool on any given live turn is model behavior we cannot
    force deterministically, so a direct text answer is an accepted outcome here
    and this test never fails for that reason. The deterministic, non-flaky proof
    that a tool-call document is parsed and converted correctly lives offline in
    ``test_claude_provider_runner.py::test_invoke_converts_tool_calls``.

    All this live test guarantees is that IF the model requests a tool, the
    request is well-formed: the allowlisted tool name with a decoded-object
    argument, and a ``tool_calls`` finish reason.
    """
    client = _client()
    tools = [
        {
            "function": {
                "name": "cron_list_jobs",
                "description": "List the scheduled cron jobs for the current user.",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    ]
    response = await client.invoke(
        [{"role": "user", "content": "List my scheduled cron jobs using the available tool."}],
        tools=tools,
    )
    if response.finish_reason == "tool_calls":
        assert response.tool_calls[0].name == "cron_list_jobs"
        assert isinstance(json.loads(response.tool_calls[0].arguments), dict)
    else:
        # Model answered directly; that is an accepted (unforceable) outcome.
        assert response.finish_reason == "stop"
