# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Transport-level Web request identity helpers.

The Web shell supplies ``user_id/group_id/bot_id`` outside business payloads:
WebSocket uses its handshake query and HTTP uses trusted identity headers. This
module normalizes the resulting Gateway metadata without assigning any
authentication or authorization meaning to those routing values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

WEB_ROUTING_ID_FIELDS = ("user_id", "group_id", "bot_id")


def _identity_value(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def web_routing_identity(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Extract routing IDs from Gateway Web metadata.

    Direct metadata values take precedence over the preserved handshake/header
    query. This makes the already-resolved connection ``user_id`` authoritative.
    """

    if not isinstance(metadata, Mapping):
        return {}
    query = metadata.get("query")
    query_mapping = query if isinstance(query, Mapping) else {}
    identity: dict[str, str] = {}
    for field in WEB_ROUTING_ID_FIELDS:
        value = _identity_value(metadata.get(field)) or _identity_value(
            query_mapping.get(field)
        )
        if value:
            identity[field] = value
    return identity


def bind_web_routing_identity(
    params: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    *,
    override: bool = True,
) -> dict[str, Any]:
    """Return a copy of ``params`` with Web transport routing IDs bound.

    ``override=True`` is appropriate at a trusted transport boundary: payload
    fields cannot impersonate the connection/header routing scope. Callers that
    only need a compatibility fallback may opt into ``override=False``.
    """

    result = dict(params or {})
    for field, value in web_routing_identity(metadata).items():
        if override or field not in result:
            result[field] = value
    return result


__all__ = (
    "WEB_ROUTING_ID_FIELDS",
    "bind_web_routing_identity",
    "web_routing_identity",
)
