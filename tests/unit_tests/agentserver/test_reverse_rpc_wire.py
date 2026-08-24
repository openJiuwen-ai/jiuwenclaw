from __future__ import annotations

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_RESPONSE_STATUS_SUCCEEDED,
)
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire


def test_reverse_rpc_server_push_can_be_non_final_and_in_progress() -> None:
    wire = build_server_push_wire(
        {
            "request_id": "rrpc-push-1",
            "response_kind": "reverse_rpc.request",
            "status": E2A_RESPONSE_STATUS_IN_PROGRESS,
            "is_final": False,
            "body": {"rpc_id": "rpc-1"},
        }
    )

    assert wire["status"] == E2A_RESPONSE_STATUS_IN_PROGRESS
    assert wire["is_final"] is False
    assert wire["response_kind"] == "reverse_rpc.request"


def test_existing_named_server_push_defaults_remain_final_and_succeeded() -> None:
    wire = build_server_push_wire(
        {
            "request_id": "existing-push-1",
            "response_kind": "ext",
            "body": {"value": "unchanged"},
        }
    )

    assert wire["status"] == E2A_RESPONSE_STATUS_SUCCEEDED
    assert wire["is_final"] is True
    assert wire["response_kind"] == "ext"
