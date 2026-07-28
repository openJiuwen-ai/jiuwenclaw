"""Claude-provider identity and limits.

Neutral limits (message/tool/byte/timeout ceilings) are reused from
``constants`` by reference; only Claude-specific identity and any Claude-only
limits live here.
"""

from __future__ import annotations

CLAUDE_PROVIDER_NAME = "AI4RnDClaude"
CLAUDE_MODEL_ALIAS = "claude-code"

# A normal, visible, default-enabled Jiuwen provider (like the Codex provider).
# The only difference from an ordinary provider is authentication ownership: the
# operator signs in to the official ``claude`` CLI beforehand, and the provider
# positively verifies a Claude.ai subscription before every turn (API-key and
# unverifiable authentication fail closed). This product never initiates a Claude
# login, receives passwords, copies/stores credentials, or performs logout.

# Pinned from Phase 0 characterization (CLAUDE_CLI_PHASE0_CHARACTERIZATION_2026-07-23.md).
# Bump only after re-running the full flag + fail-closed verification against the
# new binary; a version change invalidates the invocation contract, not just tests.
SUPPORTED_CLAUDE_VERSION = "2.1.218"

# The CLI emits one JSON result document on stdout in --output-format json mode.
# Bounded independently of Codex's JSONL stream ceiling.
MAX_CLAUDE_STDOUT_BYTES = 8 * 1024 * 1024
MAX_CLAUDE_STDERR_BYTES = 128 * 1024

# Claude billing mode label surfaced in response metadata. Subscription login is
# resolved natively by the CLI; cost is known only when the CLI reports
# total_cost_usd (typically absent for a subscription login).
CLAUDE_BILLING_MODE = "anthropic_native_credentials"
