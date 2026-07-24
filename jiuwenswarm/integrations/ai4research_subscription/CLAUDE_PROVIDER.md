# Claude Provider (AI4RnDClaude)

A JiuwenSwarm model provider that runs one fresh, non-interactive Claude Code
CLI process per model turn (`claude -p --output-format json`) and exposes it to
Jiuwen through the standard model-client contract. It is a normal, visible,
default-enabled provider - a sibling of the Codex provider that reuses the same
provider-neutral process/lifecycle core.

**Subscription-only.** The only difference from an ordinary provider is
authentication ownership: the operator signs in to the official `claude` CLI
beforehand, and the provider verifies a Claude.ai subscription before every turn.
The product never initiates a Claude login, receives passwords, copies or stores
credentials, or performs logout.

## Authentication

**The product configures no credential and accepts no API key.** The Jiuwen
model configuration for this provider must contain neither an API key nor an API
base URL. Authentication is the operator's own Claude login, resolved **natively
by the CLI from the process environment**, the same way the `claude` command
does on its own:

* the operator logs in with the `claude` CLI **outside this product**, on the
  machine where the server runs; their login lives in `~/.claude`.

The runner passes the real `HOME` (and `CLAUDE_CONFIG_DIR` if the operator set a
non-default login location) into the CLI child, so the operator's own login is
what authenticates. No API-key variable is forwarded: `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_BASE_URL` are deliberately stripped by the
runner's environment allowlist, so a key alone will not authenticate.

**There is no Claude.ai login flow in this product.** Unlike the Codex provider,
the Claude provider ships no "connect" flow, no OAuth, no logout, and no
credential storage or management (see `claude_auth_seam.py`). It never reads,
copies, or persists a credential. What an operator configures in their own
environment - their own `claude` login - is their own action outside this
product's scope.

**Subscription verified before every turn.** The runner runs
`claude auth status --json` in the same restricted environment used for
inference and permits the turn only when it proves a Claude.ai subscription
(`loggedIn` true, `authMethod` `claude.ai`, `apiProvider` `firstParty`, a
non-empty `subscriptionType`). An API key, console/token, or a cloud provider
(Bedrock/Vertex/Foundry) is rejected (`auth_wrong_method`); a missing login is
`auth_login_required`; anything unparseable is `auth_unverifiable`. Only reviewed
non-secret fields are read; the raw status document is never logged or persisted.

## Configuration

| Field | Value |
|---|---|
| `client_provider` | `AI4RnDClaude` |
| `model_name` | `claude-code` (fixed alias) |
| `api_key` | must be empty (login comes from the environment) |
| `api_base` | must be empty (not supported) |

A configuration that places a credential in `api_key` or `api_base` is rejected
at validation time. If the environment has no usable subscription login, a model
turn fails closed at the preflight (`auth_login_required` / `auth_wrong_method` /
`auth_unverifiable`); no interactive login is ever triggered.

## Provider status (read-only)

The provider exposes a cheap, read-only status probe (no inference) that surfaces
one of six operational states so operators can see why the provider is or is not
usable:

| State | Meaning |
|---|---|
| `missing_cli` | the `claude` CLI is not installed on the server |
| `wrong_version` | the installed CLI is not the pinned supported version |
| `login_required` | the CLI is present but not logged in - run the official Claude login command on the server, then refresh/test again |
| `wrong_auth_method` | logged in, but not with a Claude.ai subscription (API key or cloud billing) |
| `auth_status_unverifiable` | the CLI status could not be verified safely; the provider fails closed |
| `subscription_ready` | a verified Claude.ai subscription; the provider is usable |

## Administrator kill switch (defaults to enabled)

The provider is **enabled by default** and needs no flag for ordinary operation.
An administrator may disable it for an instance with:

```
JIUWENSWARM_CLAUDE_SUBSCRIPTION_ENABLED=off
```

Absent, empty, or a truthy value keeps it enabled; a recognized (or unrecognized)
disable value turns it off, in which case a model turn fails with
`provider_disabled` and no CLI process is started.

## Behavior contract

* One Jiuwen model turn maps to one fresh `claude -p` process; no session is
  resumed or persisted.
* All provider-side tools, filesystem, web, MCP, and settings are disabled
  (`--tools ""`, `--setting-sources ""`, `--strict-mcp-config`,
  `--no-session-persistence`); the child performs no actions of its own.
* The full ordered Jiuwen transcript is serialized into an injection-safe
  envelope instructing the model to act as a single-inference LLM backend and
  return exactly one schema-valid result (assistant text or a Jiuwen tool-call
  request). Jiuwen executes any returned tool call.
* Output is one JSON result document; parsing is fail-closed. The document's
  `is_error` / `terminal_reason` are authoritative; `subtype` is never trusted.
  A turn that did not run as a single inference (`num_turns != 1`), returned
  reasoning content, or produced malformed/duplicate output is rejected.
* If a turn's child process group cannot be confirmed reaped, the group is
  recorded in a strict cross-turn **quarantine** and the turn fails closed
  (`provider_unavailable`). Every later turn stays blocked until that group is
  proven gone (`claude_quarantine`), then reconciles automatically.
* Token usage is reported only as faithfully as the CLI reports it. Cost is
  known only when the CLI reports `total_cost_usd` (typically absent for a
  subscription login).

## Supported CLI version

Pinned to Claude CLI `2.1.218` (`SUPPORTED_CLAUDE_VERSION` in
`claude_constants.py`). A different installed version fails the version gate.
Bump the pin only after re-running the flag and fail-closed verification against
the new binary.

## Verifying

Non-authenticated checks (no login needed):

```sh
# Contract still holds against the installed CLI
env -i PATH="$PATH" HOME=$(mktemp -d) TERM=dumb sh -c \
  'echo "say ok" | claude -p --output-format json --tools "" --setting-sources ""; echo EXIT=$?'
# EXPECT: EXIT=1, a single JSON document with "is_error": true, no hang
```

The authenticated end-to-end turn is gated behind the operator's own `claude`
login plus explicit opt-in (see
`tests/unit_tests/integrations/test_claude_provider_live_authenticated.py`,
opt-in via `CLAUDE_PROVIDER_LIVE=1`) and is the only test that spends
subscription quota.
