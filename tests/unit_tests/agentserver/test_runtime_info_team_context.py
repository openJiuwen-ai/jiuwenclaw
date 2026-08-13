# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 1: RuntimeInfo carries session_id/request_id for leader rail gating."""

from __future__ import annotations

from jiuwenclaw.agentserver.team.team_runtime_inheritance import RuntimeInfo


def test_runtime_info_has_session_id_and_request_id_fields():
    ri = RuntimeInfo(
        channel="officeclaw",
        language="cn",
        session_id="officeclaw_s1",
        request_id="req-1",
    )
    assert ri.session_id == "officeclaw_s1"
    assert ri.request_id == "req-1"


def test_runtime_info_defaults_keep_existing_callers_working():
    # Existing RuntimeInfo(channel=, language=) call sites must still work.
    ri = RuntimeInfo(channel="web", language="en")
    assert ri.session_id == ""
    assert ri.request_id is None


def test_member_rails_threads_context_into_runtime_info(monkeypatch):
    """_build_platform_member_rails must populate RuntimeInfo from SwarmBuildContext."""
    from jiuwenclaw.agentserver.swarm.providers import member_rails as mr
    from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext

    captured: dict = {}

    real_build = mr.build_member_rails

    def spy(member_info=None, runtime=None, team_workspace=None):
        captured["session_id"] = getattr(runtime, "session_id", "<missing>")
        captured["request_id"] = getattr(runtime, "request_id", "<missing>")
        return real_build(member_info=member_info, runtime=runtime, team_workspace=team_workspace)

    monkeypatch.setattr(mr, "build_member_rails", spy)
    # Mock heavy deps so _build_platform_member_rails reaches build_member_rails.
    monkeypatch.setattr(mr, "get_team_backend", lambda ctx: None)
    monkeypatch.setattr(mr, "get_messager", lambda ctx: None)
    monkeypatch.setattr(mr, "get_permissions_override", lambda ctx: None)
    monkeypatch.setattr(mr, "get_default_model_name", lambda config=None: "glm-5")
    monkeypatch.setattr(mr, "resolve_member_catalog_agent_id", lambda *a, **k: "")
    monkeypatch.setattr(mr, "_resolve_member_enabled_skills", lambda *a, **k: None)

    seed = {
        "session_id": "officeclaw_s2",
        "request_id": "req-2",
        "channel": "officeclaw",
        "mode": "team",
        "team_id": "t1",
        "language": "cn",
    }
    ctx = SwarmBuildContext.from_seed(seed, config={}, trajectory_registry=None)

    params = {"enable_permissions": False, "leader_member_name": "leader"}
    # Rail assembly may raise on missing workspace/config; we only assert the
    # RuntimeInfo plumbing reached the spy with the context's ids.
    try:
        mr._build_platform_member_rails(params, ctx)
    except Exception:
        pass
    assert captured.get("session_id") == "officeclaw_s2", captured
    assert captured.get("request_id") == "req-2", captured
