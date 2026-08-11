"""JSON/deep-copy safe serialization helpers for :mod:`invocation_context`."""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any

from .models import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    XiaoyiInvocationContext,
)


INVOCATION_CONTEXT_EXTRA_KEY = "jiuwenswarm_invocation"

logger = logging.getLogger(__name__)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        # Do not silently turn objects into identities.  Request IDs are wire
        # fields and accepting arbitrary objects here would make the payload
        # non-serializable after the persistent round deep-copy.
        raise ValueError(f"{field_name} must be a non-empty string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    value = value.strip()
    return value or None


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object or null")
    # Return a detached object so callers cannot mutate an active context by
    # retaining a reference to the wire payload.
    return copy.deepcopy(dict(value))


def invocation_context_to_dict(context: InvocationContext) -> dict[str, Any]:
    """Encode *context* into a detached, JSON-compatible mapping.

    A hand-written mapping is used instead of ``dataclasses.asdict`` to keep
    the wire contract explicit and to make accidental future fields opt-in.
    ``deepcopy`` protects the persistent Agent input from mutable metadata
    owned by the builder or a caller.
    """

    if not isinstance(context, InvocationContext):
        raise TypeError("context must be an InvocationContext")

    xiaoyi_payload: dict[str, Any] | None = None
    if context.xiaoyi is not None:
        xiaoyi_payload = {
            "root_session_id": context.xiaoyi.root_session_id,
            "params_session_id": context.xiaoyi.params_session_id,
            "task_id": context.xiaoyi.task_id,
            "message_id": context.xiaoyi.message_id,
            "device_id": context.xiaoyi.device_id,
            "scheduled_device": copy.deepcopy(context.xiaoyi.scheduled_device),
            "cron": copy.deepcopy(context.xiaoyi.cron),
        }

    return {
        "version": int(context.version),
        "invocation_id": context.invocation_id,
        "request_id": context.request_id,
        "session_id": context.session_id,
        "channel_id": context.channel_id,
        "chat_id": context.chat_id,
        "xiaoyi": xiaoyi_payload,
        "metadata": copy.deepcopy(dict(context.metadata or {})),
    }


def invocation_context_from_dict(payload: dict[str, Any]) -> InvocationContext:
    """Decode a validated invocation payload.

    Only version 1 is accepted.  Unknown optional keys are ignored, while
    malformed known fields fail closed so Device/GUI tools never operate with
    ambiguous routing data.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("InvocationContext payload must be an object")
    if "version" not in payload:
        raise ValueError("InvocationContext version is required")
    version = payload.get("version")
    if version != INVOCATION_CONTEXT_VERSION:
        raise ValueError(f"unsupported InvocationContext version: {version!r}")

    invocation_id = _required_text(payload.get("invocation_id"), "invocation_id")
    request_id = _required_text(payload.get("request_id"), "request_id")
    channel_id = _required_text(payload.get("channel_id"), "channel_id")
    session_id = _optional_text(payload.get("session_id"), "session_id")
    chat_id = _optional_text(payload.get("chat_id"), "chat_id")

    xiaoyi_raw = payload.get("xiaoyi")
    xiaoyi: XiaoyiInvocationContext | None
    if xiaoyi_raw is None:
        xiaoyi = None
    elif isinstance(xiaoyi_raw, Mapping):
        xiaoyi = XiaoyiInvocationContext(
            root_session_id=_optional_text(
                xiaoyi_raw.get("root_session_id"), "xiaoyi.root_session_id"
            ),
            params_session_id=_optional_text(
                xiaoyi_raw.get("params_session_id"), "xiaoyi.params_session_id"
            ),
            task_id=_optional_text(xiaoyi_raw.get("task_id"), "xiaoyi.task_id"),
            message_id=_optional_text(
                xiaoyi_raw.get("message_id"), "xiaoyi.message_id"
            ),
            device_id=_optional_text(xiaoyi_raw.get("device_id"), "xiaoyi.device_id"),
            scheduled_device=_optional_mapping(
                xiaoyi_raw.get("scheduled_device"), "xiaoyi.scheduled_device"
            ),
            cron=_optional_mapping(xiaoyi_raw.get("cron"), "xiaoyi.cron"),
        )
    else:
        raise ValueError("xiaoyi must be an object or null")

    metadata_raw = payload.get("metadata")
    if metadata_raw is None:
        metadata: dict[str, Any] = {}
    elif isinstance(metadata_raw, Mapping):
        metadata = copy.deepcopy(dict(metadata_raw))
    else:
        raise ValueError("metadata must be an object")

    return InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id=invocation_id,
        request_id=request_id,
        session_id=session_id,
        channel_id=channel_id,
        chat_id=chat_id,
        xiaoyi=xiaoyi,
        metadata=metadata,
    )


def attach_invocation_context(
    inputs: dict[str, Any],
    invocation: InvocationContext,
) -> dict[str, Any]:
    """Attach an invocation payload under ``run.context.extra``.

    The whole input mapping is detached first, then the existing ``run`` and
    ``extra`` mappings are merged.  This preserves cron/goal/heartbeat and any
    host-supplied context fields while ensuring the explicit invocation value
    wins only at its dedicated key.
    """

    if not isinstance(inputs, dict):
        raise TypeError("inputs must be a dict")
    if not isinstance(invocation, InvocationContext):
        raise TypeError("invocation must be an InvocationContext")

    result = copy.deepcopy(inputs)
    raw_run = result.get("run")
    run = dict(raw_run) if isinstance(raw_run, Mapping) else {}
    run.setdefault("kind", "normal")
    raw_context = run.get("context")
    run_context = dict(raw_context) if isinstance(raw_context, Mapping) else {}
    raw_extra = run_context.get("extra")
    extra = dict(raw_extra) if isinstance(raw_extra, Mapping) else {}
    extra[INVOCATION_CONTEXT_EXTRA_KEY] = invocation_context_to_dict(invocation)
    run_context["extra"] = extra
    run["context"] = run_context
    result["run"] = run
    logger.info(
        "[INVOCATION_CTX] ATTACHED invocation_id=%s request_id=%s session_id=%s channel_id=%s",
        invocation.invocation_id,
        invocation.request_id,
        invocation.session_id,
        invocation.channel_id,
    )
    return result
