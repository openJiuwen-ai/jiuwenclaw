# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enterprise cron gate / identity sticky unit tests."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from jiuwenclaw.gateway.cron.cron_job_mutations import apply_cron_job_patch, build_new_cron_job
from jiuwenclaw.gateway.cron.enterprise_gate import (
    enterprise_cron_enabled,
    extract_routing_triple,
    job_matches_routing,
    routing_triple_complete,
    strip_sticky_identity_fields,
)
from jiuwenclaw.gateway.cron.models import CronJob


def test_build_new_cron_job_persists_routing_triple() -> None:
    job = build_new_cron_job(
        name="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="desc",
        targets="web",
        group_id="g1",
        bot_id="b1",
        user_id="u1",
    )
    assert job.group_id == "g1"
    assert job.bot_id == "b1"
    assert job.user_id == "u1"
    d = job.to_dict()
    assert d["group_id"] == "g1"
    restored = CronJob.from_dict(d)
    assert restored.user_id == "u1"


def test_apply_cron_job_patch_identity_sticky() -> None:
    job = build_new_cron_job(
        name="n",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="d",
        targets="web",
        group_id="g1",
        bot_id="b1",
        user_id="u1",
    )
    updated = apply_cron_job_patch(
        job,
        {"name": "n2", "group_id": "hack", "bot_id": "hack", "user_id": "hack"},
    )
    assert updated.name == "n2"
    assert updated.group_id == "g1"
    assert updated.bot_id == "b1"
    assert updated.user_id == "u1"


def test_strip_sticky_identity_fields() -> None:
    out = strip_sticky_identity_fields(
        {"name": "x", "group_id": "g", "bot_id": "b", "user_id": "u", "job_id": "j"}
    )
    assert out == {"name": "x"}


def test_extract_routing_triple_priority() -> None:
    g, b, u = extract_routing_triple(
        {"group_id": "from_params", "bot_id": "bp"},
        {"group_id": "from_meta", "user_id": "um", "query": {"bot_id": "bq", "user_id": "uq"}},
    )
    assert g == "from_params"
    assert b == "bp"
    assert u == "um"


def test_job_matches_routing_and() -> None:
    job = CronJob(
        id="j1",
        name="n",
        enabled=True,
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        targets="web",
        group_id="g1",
        bot_id="b1",
        user_id="u1",
    )
    assert job_matches_routing(job, group_id="g1", bot_id="b1", user_id="u1")
    assert not job_matches_routing(job, group_id="g1", bot_id="b1", user_id="other")
    assert job_matches_routing(
        CronJob(
            id="j2",
            name="n",
            enabled=True,
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            targets="web",
        ),
        group_id="g1",
        bot_id="b1",
        user_id="u1",
        include_unbound=True,
    )


def test_enterprise_cron_enabled_requires_jiuwenclaw_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", "1")
    monkeypatch.delenv("JIUWENCLAW_ID", raising=False)
    with patch(
        "jiuwenclaw.gateway.cron.enterprise_gate.get_bound_jiuwenclaw_id",
        return_value=None,
    ):
        assert enterprise_cron_enabled(deployment_mode="standalone") is False

    with patch(
        "jiuwenclaw.gateway.cron.enterprise_gate.get_bound_jiuwenclaw_id",
        return_value="jid-1",
    ):
        assert enterprise_cron_enabled(deployment_mode="standalone") is True
        assert enterprise_cron_enabled(deployment_mode="distributed") is False


def test_routing_triple_complete() -> None:
    assert routing_triple_complete("g", "b", "u")
    assert not routing_triple_complete("g", "b", None)
    assert not routing_triple_complete("", "b", "u")
