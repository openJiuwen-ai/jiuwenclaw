"""Fail-closed consumer policy for the Codex subscription provider."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from enum import Enum
from typing import Any, Iterator

from .constants import CODEX_PROVIDER_NAME
from .errors import CodexProviderError


CODEX_SUBSCRIPTION_ENABLED_ENV = "JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED"
CODEX_CALL_PERMIT_KWARG = "_codex_call_permit"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
SAFE_JIUWEN_TOOL_NAMES = frozenset({"cron_list_jobs"})


class CodexConsumer(str, Enum):
    """Stable identifiers for every currently known Codex model consumer."""

    UNCLASSIFIED = "unclassified"
    CONFIG_VALIDATION = "config_validation"
    DIRECT_AGENT_FAST = "direct_agent_fast"
    PLAN = "plan"
    CODE = "code"
    TEAM = "team"
    AUTO_HARNESS = "auto_harness"
    CRON = "cron"
    PROACTIVE = "proactive"
    CONTEXT_COMPACTION = "context_compaction"
    MEMORY = "memory"
    SYMPHONY = "symphony"


_ALLOWED_CONSUMERS = frozenset({
    CodexConsumer.CONFIG_VALIDATION,
    CodexConsumer.DIRECT_AGENT_FAST,
})
_CURRENT_CONSUMER: ContextVar[CodexConsumer] = ContextVar(
    "ai4research_codex_consumer",
    default=CodexConsumer.UNCLASSIFIED,
)
_PERMIT_FACTORY_SENTINEL = object()


class CodexCallPermit:
    """Opaque, exact-client, one-use authorization for one Codex model call."""

    __slots__ = ("_client", "_consumer", "_lock", "_used")

    def __init__(
        self,
        client: object,
        consumer: CodexConsumer,
        *,
        _factory_sentinel: object,
    ) -> None:
        if _factory_sentinel is not _PERMIT_FACTORY_SENTINEL:
            raise CodexProviderError(
                "invalid_call_permit",
                "Codex call permits must be issued by the consumer policy.",
            )
        self._client = client
        self._consumer = consumer
        self._lock = threading.Lock()
        self._used = False

    def __copy__(self) -> CodexCallPermit:
        raise CodexProviderError(
            "invalid_call_permit", "Codex call permits cannot be copied."
        )

    def __deepcopy__(self, memo: dict[int, object]) -> CodexCallPermit:
        del memo
        raise CodexProviderError(
            "invalid_call_permit", "Codex call permits cannot be copied."
        )

    def _consume(self, client: object) -> CodexConsumer:
        with self._lock:
            if self._client is not client:
                raise CodexProviderError(
                    "invalid_call_permit",
                    "The Codex call permit belongs to a different model client.",
                )
            if self._used:
                raise CodexProviderError(
                    "invalid_call_permit", "The Codex call permit was already used."
                )
            self._used = True
            return self._consumer


def codex_subscription_enabled() -> bool:
    """Return the fail-closed per-process feature-switch state.

    The feature remains seamless for existing installations: an absent or empty
    value enables it. Explicit false values disable it, while any other nonempty
    value is treated as invalid and therefore disabled.
    """

    raw = os.getenv(CODEX_SUBSCRIPTION_ENABLED_ENV)
    if raw is None or not raw.strip():
        return True
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return False


def provider_name(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def is_codex_provider(value: object) -> bool:
    return provider_name(value) == CODEX_PROVIDER_NAME


def is_codex_model(model: object) -> bool:
    config = getattr(model, "model_client_config", None)
    return is_codex_provider(getattr(config, "client_provider", None))


def current_codex_consumer() -> CodexConsumer:
    return _CURRENT_CONSUMER.get()


@contextmanager
def codex_consumer_scope(consumer: CodexConsumer) -> Iterator[None]:
    token: Token[CodexConsumer] = _CURRENT_CONSUMER.set(consumer)
    try:
        yield
    finally:
        _CURRENT_CONSUMER.reset(token)


def require_codex_enabled() -> None:
    if not codex_subscription_enabled():
        raise CodexProviderError(
            "provider_disabled",
            "Codex subscription is disabled for this Jiuwen instance.",
        )


def require_codex_consumer(consumer: CodexConsumer | None = None) -> None:
    """Reject disabled, unsupported, or unclassified Codex consumers."""

    require_codex_enabled()
    resolved = consumer or current_codex_consumer()
    if resolved is CodexConsumer.UNCLASSIFIED:
        raise CodexProviderError(
            "consumer_unclassified",
            "Codex subscription blocked an unclassified model consumer.",
        )
    if resolved not in _ALLOWED_CONSUMERS:
        raise CodexProviderError(
            "unsupported_consumer",
            "Codex subscription is not enabled for this consumer in v1.",
        )


def issue_codex_call_permit(client: object, consumer: CodexConsumer) -> CodexCallPermit:
    """Issue one call authorization bound to an exact model-client object."""

    require_codex_consumer(consumer)
    return CodexCallPermit(
        client,
        consumer,
        _factory_sentinel=_PERMIT_FACTORY_SENTINEL,
    )


def consume_codex_call_permit(
    client: object,
    permit: object,
) -> CodexConsumer:
    """Consume a one-use permit bound to the exact model-client object."""

    require_codex_enabled()
    if isinstance(permit, CodexCallPermit):
        consumer = permit._consume(client)
        require_codex_consumer(consumer)
        return consumer
    if current_codex_consumer() is CodexConsumer.UNCLASSIFIED:
        require_codex_consumer(CodexConsumer.UNCLASSIFIED)
    raise CodexProviderError(
        "missing_call_permit",
        "Codex subscription requires a call-bound consumer authorization.",
    )


def require_codex_model_consumer(model: object, consumer: CodexConsumer) -> None:
    if is_codex_model(model):
        require_codex_consumer(consumer)


def default_model_uses_codex() -> bool:
    """Inspect the exact configured default without constructing a model client."""

    try:
        from jiuwenswarm.common.config import get_default_models

        entries = get_default_models()
    except Exception:
        return False
    if not entries:
        return False
    entry = next((item for item in entries if item.get("is_default") is True), entries[0])
    config = entry.get("model_client_config") if isinstance(entry, dict) else None
    return is_codex_provider(
        config.get("client_provider") if isinstance(config, dict) else None
    )


def require_default_codex_consumer(consumer: CodexConsumer) -> None:
    if default_model_uses_codex():
        require_codex_consumer(consumer)


def classify_agent_request(request: object) -> CodexConsumer:
    """Classify a Jiuwen request without normalizing unknown modes to plan."""

    params = getattr(request, "params", None)
    params = params if isinstance(params, dict) else {}
    metadata = getattr(request, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    method = provider_name(getattr(request, "req_method", None)).lower()
    channel_id = str(getattr(request, "channel_id", "") or "").strip().lower()

    if method == "proactive.tick":
        return CodexConsumer.PROACTIVE
    if channel_id == "__cron__" or isinstance(params.get("cron"), dict) or isinstance(metadata.get("cron"), dict):
        return CodexConsumer.CRON
    if method == "chat.user_answer":
        request_id = params.get("request_id")
        answers = params.get("answers")
        is_bound_regular_approval = (
            getattr(request, "subscription_continuation_bound", False) is True
            and isinstance(request_id, str)
            and bool(request_id.strip())
            and isinstance(answers, list)
            and bool(answers)
            and params.get("source") == "skill_evolution_approval"
            and params.get("approval_schema")
            == "openjiuwen.skill_evolution_approval.v1"
        )
        if (
            is_bound_regular_approval
            and str(params.get("mode") or "").strip().lower() == "agent.fast"
        ):
            return CodexConsumer.DIRECT_AGENT_FAST
        return CodexConsumer.UNCLASSIFIED
    if method not in {"", "chat.send"}:
        return CodexConsumer.UNCLASSIFIED

    mode = str(params.get("mode") or "").strip().lower()
    if mode == "agent.fast":
        return CodexConsumer.DIRECT_AGENT_FAST
    if mode in {"agent.plan", "code.plan"}:
        return CodexConsumer.PLAN
    if mode == "code.normal":
        return CodexConsumer.CODE
    if mode in {"team", "team.plan", "code.team"}:
        return CodexConsumer.TEAM
    if mode == "auto_harness" or mode.startswith("auto_harness."):
        return CodexConsumer.AUTO_HARNESS
    return CodexConsumer.UNCLASSIFIED


def filter_codex_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only source-reviewed, non-LLM Jiuwen tools to Codex v1."""

    filtered: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if name in SAFE_JIUWEN_TOOL_NAMES:
            filtered.append(tool)
    return filtered
