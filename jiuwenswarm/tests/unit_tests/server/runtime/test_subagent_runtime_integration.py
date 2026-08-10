# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from jiuwenswarm.common.config import is_subagent_runtime_enabled
from openjiuwen.harness.subagent_runtime import SUBAGENT_UPDATED_EVENT_TYPE


class TestSubagentRuntimeConfig:
    def test_disabled_by_default(self) -> None:
        assert is_subagent_runtime_enabled({"react": {}}) is False

    def test_enabled_when_configured(self) -> None:
        config = {"react": {"subagent_runtime": {"enabled": True}}}
        assert is_subagent_runtime_enabled(config) is True


def _map_subagent_updated_chunk(chunk_type: str, payload: dict) -> dict | None:
    """Mirror of JiuWenSwarmDeepAdapter._parse_stream_chunk subagent branch."""
    if chunk_type != SUBAGENT_UPDATED_EVENT_TYPE:
        return None
    projection = payload.get("subagent_updated")
    if not isinstance(projection, dict):
        return None
    return {"event_type": "chat.subtask_update", **projection}


class TestSubagentStreamMapping:
    def test_maps_subagent_updated_to_chat_subtask_update(self) -> None:
        projection = {
            "subagent_id": "sess_sub_general_abc123",
            "status": "running",
            "display_name": "Researcher",
        }
        parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": projection},
        )
        assert parsed is not None
        assert parsed["event_type"] == "chat.subtask_update"
        assert parsed["subagent_id"] == projection["subagent_id"]
        assert parsed["status"] == "running"

    def test_invalid_subagent_updated_payload_is_skipped(self) -> None:
        assert _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": "bad"},
        ) is None
