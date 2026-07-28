"""Sanitized errors exposed by the subscription-provider boundary."""

from __future__ import annotations


class CodexProviderError(RuntimeError):
    """A stable provider error whose message is safe to return to Jiuwen clients."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(f"{code}: {message}")


def provider_error_payload(
    exc: CodexProviderError,
    *,
    consumer: str | None = None,
) -> dict[str, str]:
    payload = {
        "error": exc.safe_message,
        "code": exc.code,
        "provider": "AI4ResearchCodex",
    }
    if consumer:
        payload["consumer"] = consumer
    return payload


def auth_required() -> CodexProviderError:
    return CodexProviderError(
        "auth_required",
        "Codex is not connected to a ChatGPT account for this Jiuwen instance.",
    )


def unsupported_cli() -> CodexProviderError:
    return CodexProviderError(
        "unsupported_cli",
        "The installed Codex CLI version is not supported by this provider.",
    )


class ClaudeProviderError(RuntimeError):
    """A stable Claude provider error whose message is safe to return to Jiuwen clients."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(f"{code}: {message}")


def claude_provider_error_payload(
    exc: ClaudeProviderError,
    *,
    consumer: str | None = None,
) -> dict[str, str]:
    payload = {
        "error": exc.safe_message,
        "code": exc.code,
        "provider": "AI4RnDClaude",
    }
    if consumer:
        payload["consumer"] = consumer
    return payload


def claude_auth_not_configured() -> ClaudeProviderError:
    return ClaudeProviderError(
        "auth_not_configured",
        "Claude Code is not logged in for this environment. "
        "Log in with the Claude CLI on the server; this product does not manage Claude credentials.",
    )


def claude_unsupported_cli() -> ClaudeProviderError:
    return ClaudeProviderError(
        "unsupported_cli",
        "The installed Claude CLI version is not supported by this provider.",
    )


def claude_login_required() -> ClaudeProviderError:
    return ClaudeProviderError(
        "auth_login_required",
        "Claude Code is not logged in. Log in with the Claude CLI (a Claude "
        "subscription) on the server; this product does not manage Claude credentials.",
    )


def claude_wrong_auth_method() -> ClaudeProviderError:
    return ClaudeProviderError(
        "auth_wrong_method",
        "The Claude CLI is authenticated with a non-subscription method. This "
        "provider is subscription-only and does not permit API-key or cloud "
        "(Bedrock/Vertex/Foundry) billing; log in with a Claude subscription.",
    )


def claude_auth_unverifiable() -> ClaudeProviderError:
    return ClaudeProviderError(
        "auth_unverifiable",
        "The Claude subscription login could not be verified. The turn is refused "
        "because the effective billing method could not be positively confirmed.",
    )


def claude_provider_unavailable() -> ClaudeProviderError:
    return ClaudeProviderError(
        "provider_unavailable",
        "Claude is unavailable until a prior turn's process group is confirmed gone.",
    )
