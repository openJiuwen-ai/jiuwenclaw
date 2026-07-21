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
