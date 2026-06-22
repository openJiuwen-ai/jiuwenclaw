# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import patch

from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.agentserver import tenant_agent_pool
from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool, filter_cached_agent_managers


def _attach_capture_handler(logger: logging.Logger) -> tuple[list[logging.LogRecord], Callable[[], None]]:
    """jiuwenclaw.* loggers use propagate=False; caplog cannot see their records."""
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _CaptureHandler(level=logging.WARNING)
    saved_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    def _detach() -> None:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)

    return records, _detach


class _FakeClaw:
    def __init__(self, name: str, catalog: list[dict[str, str]]) -> None:
        self.name = name
        self._catalog = catalog

    def get_registered_tools_catalog(self) -> list[dict[str, str]]:
        return list(self._catalog)


def _make_manager_with_claws(*claws: _FakeClaw) -> AgentManager:
    manager = AgentManager(agent_id="tenant_a", service_id="service_a")
    manager.agents = {
        "web": {
            "agent": {f"session_{index}": claw for index, claw in enumerate(claws)},
        }
    }
    return manager


def test_iter_jiuwenclaw_instances_collects_non_null_instances() -> None:
    claw_a = _FakeClaw("a", [])
    claw_b = _FakeClaw("b", [])
    manager = AgentManager(agent_id="t", service_id="s")
    manager.agents = {
        "web": {
            "agent": {"s1": claw_a, "s2": None, "s3": claw_b},
        }
    }

    assert manager.iter_jiuwenclaw_instances() == [claw_a, claw_b]


def test_iter_jiuwenclaw_instances_skips_malformed_nested_dicts() -> None:
    claw = _FakeClaw("only", [])
    manager = AgentManager(agent_id="t", service_id="s")
    # Simulate corrupted runtime agents tree (not valid per AgentManager.agents typing).
    manager.agents = cast(
        Any,
        {
            "bad_channel": "not-a-dict",
            "web": {
                "bad_mode": "not-a-dict",
                "agent": {"s1": claw},
            },
        },
    )

    assert manager.iter_jiuwenclaw_instances() == [claw]


def test_filter_cached_agent_managers_filters_and_warns() -> None:
    manager = _make_manager_with_claws()
    log_records, detach = _attach_capture_handler(tenant_agent_pool.logger)

    try:
        managers = filter_cached_agent_managers([manager, 123, None])
    finally:
        detach()

    assert managers == [manager]
    assert any("skip non-AgentManager cache entry" in record.message for record in log_records)
    assert any("int" in record.message for record in log_records)


def test_iter_agent_managers_nowait_returns_empty_without_cached_managers() -> None:
    pool = TenantAgentPool()
    assert pool.iter_agent_managers_nowait() == []


def test_collect_runtime_tools_catalog_nowait_unions_manager_catalogs() -> None:
    manager_one = _make_manager_with_claws(
        _FakeClaw(
            "c1",
            [{"name": "bash", "description": "Run shell.", "short_description": "Run shell."}],
        ),
    )
    manager_two = _make_manager_with_claws(
        _FakeClaw(
            "c2",
            [{"name": "read_file", "description": "Read files.", "short_description": "Read files."}],
        ),
        _FakeClaw(
            "c3",
            [{"name": "bash", "description": "Longer bash desc.", "short_description": "Longer bash desc."}],
        ),
    )
    pool = TenantAgentPool()

    with patch.object(pool, "iter_agent_managers_nowait", return_value=[manager_one, manager_two]):
        catalog = pool.collect_runtime_tools_catalog_nowait()

    assert set(catalog) == {"bash", "read_file"}
    assert catalog["bash"]["short_description"] == "Longer bash desc."
    assert catalog["read_file"]["short_description"] == "Read files."


def test_collect_runtime_tools_catalog_nowait_returns_empty_without_managers() -> None:
    pool = TenantAgentPool()

    with patch.object(pool, "iter_agent_managers_nowait", return_value=[]):
        assert pool.collect_runtime_tools_catalog_nowait() == {}
