# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import sys


def test_team_package_does_not_eagerly_import_runtime():
    """Normal AgentServer startup must not eagerly load optional Team APIs."""
    sys.modules.pop("jiuwenclaw.agentserver.team", None)
    sys.modules.pop("jiuwenclaw.agentserver.team.team_manager", None)

    import jiuwenclaw.agentserver.team as team

    assert "jiuwenclaw.agentserver.team.team_manager" not in sys.modules
    assert "get_team_manager" in team.__all__
