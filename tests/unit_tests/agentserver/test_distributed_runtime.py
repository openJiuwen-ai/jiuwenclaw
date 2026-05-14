"""Unit tests for distributed runtime config normalization helpers."""

import pytest

from jiuwenclaw.agents.harness.team.distributed_runtime import (
    normalize_distributed_transport_fields,
    parse_port,
)


def test_normalize_distributed_transport_fields_does_not_mutate_input():
    config_base = {
        "team": {
            "runtime": {"mode": "distributed", "role": "leader"},
        }
    }
    team_cfg = {
        "leader": {"member_name": "team_leader"},
        "transport": {
            "type": "pyzmq",
            "params": {
                "leader": {"host": "127.0.0.1", "direct_port": 18555, "pub_port": 18556, "sub_port": 18557},
                "teammate": {"host": "127.0.0.1", "direct_port": 18600},
            },
        },
        "predefined_members": [{"member_name": "teammate_1"}],
    }

    normalized = normalize_distributed_transport_fields(config_base, team_cfg)

    # Ensure helper keeps input immutable for better determinism in repeated calls.
    assert "direct_addr" not in team_cfg["transport"]["params"]
    assert "pubsub_publish_addr" not in team_cfg["transport"]["params"]
    assert "metadata" not in team_cfg["transport"]["params"]

    assert normalized["transport"]["params"]["direct_addr"] == "tcp://0.0.0.0:18555"
    assert normalized["transport"]["params"]["pubsub_publish_addr"] == "tcp://127.0.0.1:18556"
    assert normalized["transport"]["params"]["metadata"]["pubsub_bind"] is True


def test_parse_port_uses_default_for_blank_string():
    assert parse_port("  ", 18555, "team.transport.params.leader.direct_port") == 18555


def test_parse_port_raises_for_non_numeric_value():
    with pytest.raises(ValueError, match="team\\.transport\\.params\\.leader\\.pub_port"):
        parse_port("abc", 18556, "team.transport.params.leader.pub_port")


def test_parse_port_raises_for_out_of_range_value():
    with pytest.raises(ValueError, match="1\\.\\.65535"):
        parse_port(70000, 18557, "team.transport.params.leader.sub_port")
