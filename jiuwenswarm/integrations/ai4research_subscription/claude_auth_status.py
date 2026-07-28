"""Fail-closed subscription-login verification from ``claude auth status --json``.

Locked requirement: ``AI4RnDClaude`` is subscription-only. Before every permitted
model turn the runner runs ``claude auth status --json`` in the SAME restricted
environment used for inference and permits inference ONLY when the status
positively proves an allowed Claude subscription login. Every other outcome fails
closed.

Characterized against Claude CLI 2.1.218 (see
``CLAUDE_CLI_AUTH_STATUS_CHARACTERIZATION_2026-07-23.md``):

* subscription: ``{"loggedIn": true, "authMethod": "claude.ai",
  "apiProvider": "firstParty", "subscriptionType": "max"}`` exit 0
* logged out:   ``{"loggedIn": false, "authMethod": "none",
  "apiProvider": "firstParty"}`` exit 1
* API key:      ``{"loggedIn": true, "authMethod": "api_key",
  "apiProvider": "firstParty", "apiKeySource": "..."}`` exit 0

This module parses ONLY the reviewed, non-secret fields ``loggedIn``,
``authMethod``, ``apiProvider``, ``subscriptionType``. It never reads, logs, or
returns ``email``, ``orgId``, ``orgName``, ``apiKeySource``, or any token
material, and the raw document is never persisted.
"""

from __future__ import annotations

import json
from typing import Any

from .claude_auth_seam import ClaudeSubscriptionAuthState

# The auth-status document is tiny; anything larger is treated as malformed.
MAX_AUTH_STATUS_BYTES = 64 * 1024

# Command tail appended to the resolved binary; ``--json`` is the default but is
# passed explicitly so a future default change cannot silently switch to text.
AUTH_STATUS_ARGV_TAIL = ("auth", "status", "--json")

# The only combination that positively proves subscription billing.
_SUBSCRIPTION_AUTH_METHOD = "claude.ai"
_FIRST_PARTY_PROVIDER = "firstParty"
_LOGGED_OUT_AUTH_METHOD = "none"


def _load_document(stdout: bytes) -> dict[str, Any] | None:
    if len(stdout) > MAX_AUTH_STATUS_BYTES:
        return None
    try:
        text = stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


def classify_subscription_auth(stdout: bytes, returncode: int) -> ClaudeSubscriptionAuthState:
    """Map one ``auth status --json`` result to a safe, fail-closed verdict.

    Only ``SUBSCRIPTION_READY`` permits inference. Unknown, missing, malformed,
    or non-subscription states all fail closed.
    """

    document = _load_document(stdout)
    if document is None:
        return ClaudeSubscriptionAuthState.AUTH_STATUS_UNVERIFIABLE

    logged_in = document.get("loggedIn")
    auth_method = document.get("authMethod")
    api_provider = document.get("apiProvider")

    # Required, strongly-typed fields. Anything else is unverifiable.
    if not isinstance(logged_in, bool) or not isinstance(auth_method, str):
        return ClaudeSubscriptionAuthState.AUTH_STATUS_UNVERIFIABLE
    if not isinstance(api_provider, str):
        return ClaudeSubscriptionAuthState.AUTH_STATUS_UNVERIFIABLE

    # Logged out (either signal) -> operator must log in outside this product.
    if not logged_in or auth_method == _LOGGED_OUT_AUTH_METHOD:
        return ClaudeSubscriptionAuthState.LOGIN_REQUIRED

    # Logged in: only a first-party claude.ai login with a real subscription plan
    # is an allowed subscription billing route. api_key, console/tokens, and the
    # cloud providers (bedrock/vertex/foundry, surfaced via apiProvider) are all
    # rejected as the wrong billing method.
    if api_provider != _FIRST_PARTY_PROVIDER:
        return ClaudeSubscriptionAuthState.WRONG_AUTH_METHOD
    if auth_method != _SUBSCRIPTION_AUTH_METHOD:
        return ClaudeSubscriptionAuthState.WRONG_AUTH_METHOD

    subscription_type = document.get("subscriptionType")
    if not isinstance(subscription_type, str) or not subscription_type.strip():
        # A claude.ai login with no attached subscription plan is not proof of
        # subscription billing.
        return ClaudeSubscriptionAuthState.WRONG_AUTH_METHOD

    # A subscription login exits 0; a zero-content inconsistency is not trusted.
    if returncode != 0:
        return ClaudeSubscriptionAuthState.AUTH_STATUS_UNVERIFIABLE

    return ClaudeSubscriptionAuthState.SUBSCRIPTION_READY
