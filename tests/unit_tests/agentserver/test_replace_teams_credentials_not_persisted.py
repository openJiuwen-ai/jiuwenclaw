# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""replace_teams_in_config must not persist api_base / api_key."""

from __future__ import annotations

from pathlib import Path

import yaml

from jiuwenclaw import config as config_mod


def _collect_credential_hits(node, *, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key in {"api_base", "api_key"}:
                hits.append(child)
            hits.extend(_collect_credential_hits(value, path=child))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            hits.extend(_collect_credential_hits(item, path=f"{path}[{idx}]"))
    return hits


def test_replace_teams_in_config_does_not_persist_api_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    """Front payload + models.defaults secrets must not land in modes.team yaml."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "defaults": [
                        {
                            "model_client_config": {
                                "model_name": "glm-5.2",
                                "client_provider": "OpenAI",
                                "api_base": "https://defaults.example/v1",
                                "api_key": "sk-defaults-secret",
                                "timeout": 1800,
                                "verify_ssl": False,
                            },
                            "model_config_obj": {"temperature": 0.6},
                        }
                    ]
                },
                "modes": {},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "_current_config_yaml_path", lambda: config_path)
    config_mod.clear_config_cache()

    payload = {
        "agents": {
            "expert-chief-researcher": {
                "model": {
                    "provider": "OpenAI",
                    "api_base": "${API_BASE}",
                    "api_key": "${API_KEY}",
                    "model": "glm-5.2#0",
                },
                "skills": ["coding"],
                "max_iterations": 200,
                "completion_timeout": 600,
            },
            "expert-market-intelligence-researcher": {
                "model": {
                    "provider": "OpenAI",
                    "api_base": "https://front.example/v1",
                    "api_key": "sk-front-secret",
                    "model": "glm-5",
                },
                "max_iterations": 200,
                "completion_timeout": 600,
            },
        },
        "team": [
            {
                "team_name": "demo_team",
                "lifecycle": "persistent",
                "team_mode": "predefined",
                "enable_swarmflow": False,
                "leader": {
                    "member_name": "lead",
                    "display_name": "Lead",
                    "persona": "lead persona",
                    "agent_id": "expert-chief-researcher",
                },
                "predefined_members": [
                    {
                        "member_name": "mate",
                        "display_name": "Mate",
                        "role_type": "teammate",
                        "persona": "mate persona",
                        "agent_id": "expert-market-intelligence-researcher",
                    }
                ],
            }
        ],
    }

    config_mod.replace_teams_in_config(payload)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    modes_team = saved.get("modes", {}).get("team", {})
    panel = saved.get("web_config_panel") or {}

    hits = _collect_credential_hits(modes_team) + _collect_credential_hits(panel)
    assert hits == [], f"credentials persisted at: {hits}"

    # model/API tip-owned (not in yaml); skills stay on agents.*.skills when front sends them.
    leader_agent = modes_team["demo_team"]["agents"]["leader"]
    mate_agent = modes_team["demo_team"]["agents"]["mate"]
    assert "model" not in leader_agent
    assert "model" not in mate_agent
    assert leader_agent.get("skills") == ["coding"]
    assert leader_agent.get("max_iterations") == 200
    assert leader_agent.get("completion_timeout") == 600
    assert modes_team["demo_team"]["leader"]["agent_id"] == "expert-chief-researcher"
    assert modes_team["demo_team"]["predefined_members"][0]["agent_id"] == (
        "expert-market-intelligence-researcher"
    )
    assert "agent_key" not in modes_team["demo_team"]["leader"]
    # Legacy duplicate registry must not be written.
    assert "agent_team_agents" not in panel
