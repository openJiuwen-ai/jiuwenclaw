# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team stream pricing helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.common.usage_cost import new_usage_accumulator
from jiuwenswarm.server.runtime.agent_adapter import team_helpers

_CFG = {
    "models": {
        "pricing": {
            "m-leader": {"input": 1.0, "output": 1.0},
            "m-reviewer": {"input": 2.0, "output": 2.0},
        }
    }
}


@pytest.fixture(autouse=True)
def _pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jiuwenswarm.common.usage_cost.get_config", lambda: _CFG)


def _chunk(model: str, member: str, *, inp: int, out: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        type="llm_usage",
        source_member=member,
        role="teammate" if member != "leader" else "leader",
        payload={
            "usage_metadata": {
                "model_name": model,
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": inp + out,
                "cache_tokens": 0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            }
        },
    )


def test_team_llm_usage_fills_by_member() -> None:
    acc = new_usage_accumulator()
    payload = team_helpers._record_team_llm_usage(
        _chunk("m-leader", "leader", inp=1_000_000),
        acc,
        is_leader=True,
    )
    assert payload is not None
    assert payload["member_name"] == "leader"
    assert payload["event_type"] == "chat.usage_metadata"

    team_helpers._record_team_llm_usage(
        _chunk("m-reviewer", "reviewer", inp=1_000_000),
        acc,
        is_leader=False,
    )
    team_helpers._record_team_llm_usage(
        _chunk("local-model", "reviewer", inp=1_000_000),
        acc,
        is_leader=False,
    )

    assert acc["by_member"]["leader"]["priced_calls"] == 1
    assert acc["by_member"]["reviewer"]["priced_calls"] == 1
    assert acc["by_member"]["reviewer"]["unpriced_calls"] == 1
    assert acc["by_member"]["leader"]["total_cost"] == pytest.approx(1.0)
    assert acc["by_member"]["reviewer"]["total_cost"] == pytest.approx(2.0)
