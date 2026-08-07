# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Soft SYNC_ENV_SCHEMA validation: missing keys warn, do not reject."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jiuwenclaw.agentserver import sync_agents_configs as sync_mod
from jiuwenclaw.agentserver.sync_agents_configs import validate_sync_payload


def test_missing_schema_keys_warn_and_accept():
    with patch.object(sync_mod.logger, "warning") as warn:
        normalized = validate_sync_payload(
            {
                "revision": "r1",
                "service_id": "default",
                "agents": [
                    {
                        "agent_id": "office",
                        "config": {},
                        "env": {"API_KEY": "x"},
                        "runtime": {},
                    }
                ],
            }
        )
    assert normalized["agents"][0]["env"] == {"API_KEY": "x"}
    warn.assert_called()
    # logger.warning(fmt, agent_id, missing_csv)
    fmt, agent_id, missing_csv = warn.call_args.args[:3]
    assert "missing schema keys" in fmt
    assert agent_id == "office"
    assert "LLM_MAX_TOKENS" in missing_csv
    assert "MODEL_NAME" in missing_csv


def test_env_not_object_still_rejected():
    with pytest.raises(ValueError, match="env must be an object"):
        validate_sync_payload(
            {
                "revision": "r1",
                "service_id": "default",
                "agents": [
                    {
                        "agent_id": "office",
                        "config": {},
                        "env": "bad",
                        "runtime": {},
                    }
                ],
            }
        )
