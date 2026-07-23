"""Parse the single JSON result document emitted by `claude -p --output-format json`.

Shape pinned from Phase 0 characterization
(CLAUDE_CLI_PHASE0_CHARACTERIZATION_2026-07-23.md). Load-bearing facts encoded here:

* The outer document's ``is_error`` and ``terminal_reason`` are authoritative.
  ``subtype`` is NOT a health signal - an auth-failed turn was observed with
  ``"subtype": "success"`` and ``"is_error": true``. Never branch on ``subtype``.
* On success the model's output lives in ``result`` as a string; for this
  provider that string must itself be the JSON object matching the strict
  response schema, which is then validated by ``parse_claude_final_response``.
* ``num_turns`` must be 1 - the envelope plus disabled tools make one inference;
  anything else means the harness looped and the "single inference" contract is
  broken.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .claude_auth_seam import ClaudeAuthState
from .claude_constants import MAX_CLAUDE_STDOUT_BYTES
from .claude_contracts import ProviderTurnResult, ProviderUsage, parse_claude_final_response
from .errors import ClaudeProviderError, claude_auth_not_configured


def _load_document(stdout: bytes) -> dict[str, Any]:
    if len(stdout) > MAX_CLAUDE_STDOUT_BYTES:
        raise ClaudeProviderError("invalid_output", "The Claude CLI produced oversized output.")
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ClaudeProviderError("invalid_output", "The Claude CLI produced non-UTF-8 output.") from exc
    stripped = text.strip()
    if not stripped:
        raise ClaudeProviderError("invalid_output", "The Claude CLI produced no result document.")
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError as exc:
        # A single JSON document is required; multiple concatenated documents or
        # any trailing content both fail json.loads here and are rejected.
        raise ClaudeProviderError("invalid_output", "The Claude CLI produced a malformed result document.") from exc
    if not isinstance(document, dict):
        raise ClaudeProviderError("invalid_output", "The Claude CLI produced an invalid result document.")
    return document


def _looks_like_auth_gap(document: dict[str, Any]) -> bool:
    if document.get("terminal_reason") == "api_error" and not document.get("api_error_status"):
        result = document.get("result")
        if isinstance(result, str) and "logged in" in result.lower():
            return True
    return False


def classify_auth_state(document: dict[str, Any]) -> ClaudeAuthState:
    """Observed-only auth classification for the preflight capability signal."""
    if document.get("is_error") and _looks_like_auth_gap(document):
        return ClaudeAuthState.NOT_CONFIGURED
    return ClaudeAuthState.READY


def _usage_from_document(document: dict[str, Any]) -> ProviderUsage | None:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return None

    def _int(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and value >= 0 else 0

    return ProviderUsage(
        input_tokens=_int("input_tokens"),
        cached_input_tokens=_int("cache_read_input_tokens"),
        output_tokens=_int("output_tokens"),
    )


def cost_usd(document: dict[str, Any]) -> float | None:
    value = document.get("total_cost_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def parse_claude_result(
    stdout: bytes,
    returncode: int,
    *,
    allowed_tool_names: set[str],
) -> ProviderTurnResult:
    """Turn one CLI result document into a validated provider turn result."""

    document = _load_document(stdout)

    if document.get("is_error"):
        if _looks_like_auth_gap(document):
            raise claude_auth_not_configured()
        raise ClaudeProviderError("provider_failed", "Claude could not complete the model turn.")

    # An error-free document must still come from a clean process exit.
    if returncode != 0:
        raise ClaudeProviderError("provider_failed", "Claude could not complete the model turn.")

    num_turns = document.get("num_turns")
    if num_turns != 1:
        raise ClaudeProviderError(
            "invalid_output",
            "Claude did not run as a single inference for this turn.",
        )

    result_text = document.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        raise ClaudeProviderError("invalid_output", "Claude returned an empty result.")

    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError as exc:
        raise ClaudeProviderError(
            "invalid_output", "Claude returned output that is not the required JSON object."
        ) from exc

    turn = parse_claude_final_response(payload, allowed_tool_names=allowed_tool_names)
    usage = _usage_from_document(document)
    if usage is not None:
        turn = dataclasses.replace(turn, usage=usage)
    return turn
