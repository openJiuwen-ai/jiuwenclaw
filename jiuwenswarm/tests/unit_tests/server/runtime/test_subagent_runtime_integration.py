# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from jiuwenswarm.common.config import is_subagent_runtime_enabled
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from openjiuwen.harness.subagent_runtime import SUBAGENT_UPDATED_EVENT_TYPE


class TestSubagentRuntimeConfig:
    def test_disabled_by_default(self) -> None:
        assert is_subagent_runtime_enabled({"react": {}}) is False

    def test_enabled_when_configured(self) -> None:
        config = {"react": {"subagent_runtime": {"enabled": True}}}
        assert is_subagent_runtime_enabled(config) is True


def _map_subagent_updated_chunk(chunk_type: str, payload: dict) -> dict | None:
    if chunk_type != SUBAGENT_UPDATED_EVENT_TYPE:
        return None
    projection = payload.get("subagent_updated")
    if not isinstance(projection, dict):
        return None
    return JiuWenSwarmDeepAdapter._project_subagent_updated_for_web(projection)


class TestSubagentStreamMapping:
    def setup_method(self) -> None:
        JiuWenSwarmDeepAdapter._subagent_progress_batches.clear()

    def test_maps_subagent_updated_to_chat_subtask_update(self) -> None:
        projection = {
            "subagent_id": "sess_sub_general_abc123",
            "parent_session_id": "parent-sess-1",
            "status": "running",
            "display_name": "Researcher",
            "task_description": "find login code",
        }
        parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": projection},
        )
        assert parsed is not None
        assert parsed["event_type"] == "chat.subtask_update"
        assert parsed["subagent_id"] == projection["subagent_id"]
        assert parsed["task_id"] == projection["subagent_id"]
        assert parsed["description"] == "Researcher"
        assert parsed["status"] == "starting"
        assert parsed["index"] == 0
        assert parsed["total"] == 1
        assert parsed["is_parallel"] is False

    def test_parallel_subagents_get_distinct_indexes(self) -> None:
        parent_session_id = "parent-sess-parallel"
        first = {
            "subagent_id": "sub-a",
            "parent_session_id": parent_session_id,
            "status": "running",
            "display_name": "general-purpose",
        }
        second = {
            "subagent_id": "sub-b",
            "parent_session_id": parent_session_id,
            "status": "running",
            "display_name": "explore",
        }
        first_parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": first},
        )
        second_parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": second},
        )
        assert first_parsed is not None
        assert second_parsed is not None
        assert first_parsed["index"] == 0
        assert second_parsed["index"] == 1
        assert first_parsed["total"] == 2
        assert second_parsed["total"] == 2
        assert first_parsed["is_parallel"] is True
        assert second_parsed["is_parallel"] is True

    def test_maps_idle_completed_to_legacy_completed(self) -> None:
        parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {
                "subagent_updated": {
                    "subagent_id": "sid-1",
                    "parent_session_id": "parent-sess-idle",
                    "status": "idle",
                    "turn_outcome": "completed",
                    "display_name": "Explorer",
                }
            },
        )
        assert parsed is not None
        assert parsed["status"] == "completed"
        assert parsed["status"] != "starting"

    def test_maps_closed_completed_to_legacy_completed(self) -> None:
        parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {
                "subagent_updated": {
                    "subagent_id": "sid-1",
                    "status": "closed",
                    "closed_reason": "completed",
                    "display_name": "Explorer",
                }
            },
        )
        assert parsed is not None
        assert parsed["status"] == "completed"

    def test_maps_closed_failed_to_legacy_error(self) -> None:
        parsed = _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {
                "subagent_updated": {
                    "subagent_id": "sid-1",
                    "status": "closed",
                    "closed_reason": "failed",
                    "error": {"code": "TIMEOUT", "message": "turn timeout"},
                }
            },
        )
        assert parsed is not None
        assert parsed["status"] == "error"
        assert parsed["message"] == "turn timeout"

    def test_invalid_subagent_updated_payload_is_skipped(self) -> None:
        assert _map_subagent_updated_chunk(
            SUBAGENT_UPDATED_EVENT_TYPE,
            {"subagent_updated": "bad"},
        ) is None
