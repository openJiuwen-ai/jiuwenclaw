# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Shared WebSocket payload limits for the Gateway-AgentServer link."""

import logging
import os

logger = logging.getLogger(__name__)

# Default WebSocket max message size: 8 MB
DEFAULT_WS_MAX_MESSAGE_BYTES = 8 * 2**20
# Absolute maximum: 64 MB
ABSOLUTE_MAX_WS_MESSAGE_BYTES = 64 * 2**20
# Send budget keeps a 2 MB gap below the receive limit
_WS_SEND_GAP_BYTES = 2 * 2**20

# Legacy constants — preserved for backward compatibility with code that
# imports them directly.  New code should call get_ws_max_message_bytes().
AGENT_WS_MAX_MESSAGE_BYTES = DEFAULT_WS_MAX_MESSAGE_BYTES
AGENT_WS_SEND_BUDGET_BYTES = DEFAULT_WS_MAX_MESSAGE_BYTES - _WS_SEND_GAP_BYTES


def get_ws_max_message_bytes() -> int:
    """Return the WebSocket max message size in bytes.

    Resolution order:
    1. ``JIUWENSWARM_WS_MAX_MESSAGE_MB`` environment variable (integer MB).
    2. Fallback to ``DEFAULT_WS_MAX_MESSAGE_BYTES`` (8 MB).

    The value is clamped to ``[8 MB, 64 MB]``.  Invalid values (non-integer,
    out of range) are logged and the default is used.
    """
    raw = os.getenv("JIUWENSWARM_WS_MAX_MESSAGE_MB")
    if raw is None:
        return DEFAULT_WS_MAX_MESSAGE_BYTES

    try:
        mb = int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "JIUWENSWARM_WS_MAX_MESSAGE_MB=%r is not a valid integer; "
            "falling back to default %d MB",
            raw,
            DEFAULT_WS_MAX_MESSAGE_BYTES // (1024 * 1024),
        )
        return DEFAULT_WS_MAX_MESSAGE_BYTES

    value = mb * 2**20
    min_bytes = 8 * 2**20
    if value < min_bytes:
        logger.warning(
            "JIUWENSWARM_WS_MAX_MESSAGE_MB=%d is below minimum (8 MB); "
            "clamping to 8 MB",
            mb,
        )
        return min_bytes
    if value > ABSOLUTE_MAX_WS_MESSAGE_BYTES:
        logger.warning(
            "JIUWENSWARM_WS_MAX_MESSAGE_MB=%d exceeds maximum (64 MB); "
            "clamping to 64 MB",
            mb,
        )
        return ABSOLUTE_MAX_WS_MESSAGE_BYTES
    return value


def get_ws_send_budget_bytes() -> int:
    """Return the WebSocket send budget (receive limit minus 2 MB gap)."""
    return get_ws_max_message_bytes() - _WS_SEND_GAP_BYTES
