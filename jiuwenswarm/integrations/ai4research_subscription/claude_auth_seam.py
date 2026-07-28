"""Claude authentication seam - observed-only auth state, no controller.

The Codex provider ships an in-product authentication controller
(``auth_controller.py``: connect / status / expiry / logout, forwarded through
the gateway and surfaced in the TUI and dashboard). The Claude provider
deliberately ships **no equivalent** - no controller, no connect flow, no
credential management of any kind.

This is a deliberate, documented exclusion, not deferred work. Anthropic's
published policy prohibits third-party products from offering Claude.ai login or
routing Free/Pro/Max plan credentials on behalf of their users
(https://code.claude.com/docs/en/legal-and-compliance). Implementing an
in-product Claude login flow here would place the product on the wrong side of
that policy regardless of how the risk is allocated.

Instead, this subscription-login-only provider relies entirely on the CLI's own
native resolution of the operator's own Claude login, configured on the
operator's machine outside this product. Authentication is observed, never
managed: the provider reports only the coarse states below, computed from a
non-interactive CLI probe (``claude auth status --json``) elsewhere in the
adapter, and never from an in-product login flow.
"""

from __future__ import annotations

from enum import Enum


class ClaudeAuthState(str, Enum):
    """Coarse, observed-only authentication capability state (inference-path).

    Produced by the adapter's inference-output parser when a turn fails with an
    auth gap, never by an in-product login flow. ``READY`` means the CLI could
    authenticate; ``NOT_CONFIGURED`` means it could not. The authoritative
    billing check is the preflight ``ClaudeSubscriptionAuthState`` below.
    """

    READY = "ready"
    NOT_CONFIGURED = "not_configured"


class ClaudeSubscriptionAuthState(str, Enum):
    """Preflight billing-route verdict from ``claude auth status --json``.

    Locked requirement: this provider is subscription-only. Before every
    permitted turn the runner verifies the effective billing route and permits
    inference ONLY on ``SUBSCRIPTION_READY``. Every other state fails closed.
    These are safe, non-secret labels; the raw status document (which carries
    account identifiers) is never logged or persisted.
    """

    SUBSCRIPTION_READY = "subscription_ready"
    LOGIN_REQUIRED = "login_required"
    WRONG_AUTH_METHOD = "wrong_auth_method"
    AUTH_STATUS_UNVERIFIABLE = "auth_status_unverifiable"


class ClaudeProviderStatus(str, Enum):
    """Read-only provider status surfaced to the dashboard/TUI (no login flow).

    A cheap probe (no inference) that answers "can this operator use the Claude
    provider, and if not, why". These are safe, non-secret labels; the raw
    auth-status document is never included.
    """

    DISABLED = "disabled"  # administrator kill switch is off
    MISSING_CLI = "missing_cli"  # the `claude` CLI is not installed
    WRONG_VERSION = "wrong_version"  # installed CLI is not the pinned version
    LOGIN_REQUIRED = "login_required"  # CLI present but not logged in
    WRONG_AUTH_METHOD = "wrong_auth_method"  # logged in, but not a subscription
    AUTH_STATUS_UNVERIFIABLE = "auth_status_unverifiable"  # could not verify
    SUBSCRIPTION_READY = "subscription_ready"  # verified Claude.ai subscription
