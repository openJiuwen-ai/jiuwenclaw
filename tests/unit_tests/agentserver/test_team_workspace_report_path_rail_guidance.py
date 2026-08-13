# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 4: TeamWorkspaceReportPathRail send_file guidance gated on registration.

Eliminates the misleading prompt that told the leader to call send_file_to_user
when the tool was not registered (history.json L120/L122: "I don't have a
send_file_to_user tool... it's not in my tool list").
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from jiuwenclaw.agentserver.team.rails.team_workspace_report_path_rail import (
    TeamWorkspaceReportPathRail,
)


def _rail(enable_send_file_guidance, send_file_rail=None, root_dir="/tmp/ws"):
    r = TeamWorkspaceReportPathRail(
        root_dir=root_dir,
        team_id="t1",
        language="cn",
        enable_send_file_guidance=enable_send_file_guidance,
        send_file_rail=send_file_rail,
    )
    r.system_prompt_builder = MagicMock()
    return r


def _ctx():
    return SimpleNamespace()


async def test_guidance_injected_when_registered():
    sf_rail = SimpleNamespace(_registered=True)
    r = _rail(enable_send_file_guidance=True, send_file_rail=sf_rail)
    await r.before_model_call(_ctx())
    added = r.system_prompt_builder.add_section.call_args.args[0]
    assert "send_file_to_user" in added.content["cn"]


async def test_guidance_NOT_injected_when_gate_failed():
    # send_file rail exists but _registered False (gate not met) -> must NOT prompt.
    sf_rail = SimpleNamespace(_registered=False)
    r = _rail(enable_send_file_guidance=True, send_file_rail=sf_rail)
    await r.before_model_call(_ctx())
    added = r.system_prompt_builder.add_section.call_args.args[0]
    assert "send_file_to_user" not in added.content["cn"]


async def test_guidance_NOT_injected_when_no_send_file_rail():
    # teammate path: send_file_rail None, enable_send_file_guidance False.
    r = _rail(enable_send_file_guidance=False, send_file_rail=None)
    await r.before_model_call(_ctx())
    added = r.system_prompt_builder.add_section.call_args.args[0]
    assert "send_file_to_user" not in added.content["cn"]


async def test_workspace_path_guidance_always_present():
    # The workspace mapping bullets must remain even when send_file guidance is off.
    sf_rail = SimpleNamespace(_registered=False)
    r = _rail(enable_send_file_guidance=True, send_file_rail=sf_rail)
    await r.before_model_call(_ctx())
    added = r.system_prompt_builder.add_section.call_args.args[0]
    assert "Team Workspace Artifact Paths" in added.content["cn"]
    assert ".team/" in added.content["cn"]
