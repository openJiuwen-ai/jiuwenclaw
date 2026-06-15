# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from jiuwenclaw.agentserver.tool_concurrency import resolve_concurrency_policy


def test_resolve_concurrency_policy_via_config_provider():
    policy = resolve_concurrency_policy(
        config_provider=lambda: {
            "react": {
                "concurrency": {
                    "enabled": True,
                    "tool_limits": {"web_search": 4},
                }
            }
        }
    )
    assert policy.tools["web_search"].limit == 4


def test_resolve_policy_reads_concurrency_tool_limits(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "jiuwenclaw.config",
        SimpleNamespace(
            get_config=lambda: {
                "react": {
                    "concurrency": {
                        "enabled": True,
                        "tool_limits": {
                            "web_search": {"limit": 2},
                            "bash": 5,
                        },
                    }
                }
            }
        ),
    )
    policy = resolve_concurrency_policy()
    assert policy.tools["web_search"].limit == 2
    assert policy.tools["bash"].limit == 5


def test_resolve_concurrency_policy_integer_shorthand(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "jiuwenclaw.config",
        SimpleNamespace(
            get_config=lambda: {
                "react": {
                    "concurrency": {
                        "enabled": True,
                        "tool_limits": {"spawn_subagent": 3},
                    }
                }
            }
        ),
    )
    policy = resolve_concurrency_policy()
    assert policy.tools["spawn_subagent"].limit == 3


def test_resolve_concurrency_policy_disabled(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "jiuwenclaw.config",
        SimpleNamespace(
            get_config=lambda: {
                "react": {
                    "concurrency": {
                        "enabled": False,
                        "tool_limits": {"spawn_subagent": 5},
                    }
                }
            }
        ),
    )
    policy = resolve_concurrency_policy()
    assert policy.enabled is False
    assert policy.tools == {}
