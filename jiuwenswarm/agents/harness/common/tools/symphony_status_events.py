"""Frontend-visible Symphony planning status events."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema

from jiuwenswarm.common.utils import logger


@dataclass(frozen=True)
class SymphonyStatusContext:
    session: Session
    operation_id: str


_CURRENT_CONTEXT: ContextVar[SymphonyStatusContext | None] = ContextVar(
    "symphony_status_context",
    default=None,
)


def begin_symphony_status_events(
    session: Session | None,
    operation_id: str,
) -> Token[SymphonyStatusContext | None] | None:
    event_id = str(operation_id or "").strip()
    if session is None or not event_id:
        return None
    return _CURRENT_CONTEXT.set(
        SymphonyStatusContext(session=session, operation_id=event_id)
    )


def reset_symphony_status_events(
    token: Token[SymphonyStatusContext | None] | None,
) -> None:
    if token is None:
        return
    try:
        _CURRENT_CONTEXT.reset(token)
    except Exception:
        logger.debug("failed to reset Symphony status context", exc_info=True)


async def emit_symphony_status(
    phase: str,
    content: str,
    *,
    status: str = "in_progress",
    detail: str | None = None,
) -> None:
    context = _CURRENT_CONTEXT.get()
    if context is None:
        return
    phase_text = str(phase or "").strip()
    content_text = str(content or "").strip()
    if not phase_text or not content_text:
        return
    payload: dict[str, Any] = {
        "source": "symphony_compose_score",
        "operation_id": context.operation_id,
        "phase": phase_text,
        "content": content_text,
        "status": str(status or "").strip() or "in_progress",
    }
    detail_text = str(detail or "").strip()
    if detail_text:
        payload["detail"] = detail_text
    try:
        await context.session.write_stream(
            OutputSchema(
                type="chat.symphony_status",
                index=0,
                payload=payload,
            )
        )
    except Exception:
        logger.debug("Symphony status event emit failed", exc_info=True)
