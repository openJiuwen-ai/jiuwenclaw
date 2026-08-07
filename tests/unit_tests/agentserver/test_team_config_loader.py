# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team config loading."""

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.team.config_loader import (
    TeamTemplateNotFoundError,
    _resolve_enable_permissions,
    load_team_spec_dict,
    resolve_team_sqlite_db_path,
)


def test_resolve_enable_permissions_follows_global_only() -> None:
    """Team rails follow permissions.enabled; team-local flag is ignored."""
    assert _resolve_enable_permissions(
        {"permissions": {"enabled": True}},
        {"enable_permissions": False},
    ) is True
    assert _resolve_enable_permissions(
        {"permissions": {"enabled": False}},
        {"enable_permissions": True},
    ) is False
    assert _resolve_enable_permissions({}, {"enable_permissions": True}) is False


def test_resolve_team_sqlite_db_path_defaults_to_agent_teams_home(monkeypatch, tmp_path):
    """Missing connection_string should fall back to openjiuwen agent-teams team.db."""
    config = {
        "modes": {
            "team": {
                "jiuwen_team": {
                    "storage": {
                        "type": "sqlite",
                        "params": {},
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: tmp_path / ".agent_teams",
    )

    db_path = resolve_team_sqlite_db_path()

    assert db_path == Path(tmp_path / ".agent_teams" / "team.db")


def test_load_team_spec_dict_selects_bound_template_not_first_entry():
    """chat.send team_name must select that modes.team key, not the first preset."""
    config = {
        "modes": {
            "team": {
                "oc_team_preset-product-research": {
                    "team_name": "oc_team_preset-product-research",
                    "lifecycle": "persistent",
                    "leader": {"agent_id": "product", "name": "product", "display_name": "产品"},
                },
                "oc_team_team-user": {
                    "team_name": "oc_team_team-user",
                    "lifecycle": "persistent",
                    "leader": {"agent_id": "office", "name": "office", "display_name": "助手"},
                },
            }
        },
        "models": {"defaults": []},
    }

    assert load_team_spec_dict(config)["team_name"] == "oc_team_preset-product-research"
    assert (
        load_team_spec_dict(config, template_id="oc_team_team-user")["team_name"]
        == "oc_team_team-user"
    )
    with pytest.raises(TeamTemplateNotFoundError):
        load_team_spec_dict(config, template_id="oc_team_missing")


def test_load_team_spec_dict_maps_persona_and_prompt_hint_to_desc_prompt():
    """Relay syncs persona/prompt_hint; TeamMemberSpec only keeps desc/prompt."""
    config = {
        "modes": {
            "team": {
                "oc_team_debate": {
                    "team_name": "oc_team_debate",
                    "lifecycle": "persistent",
                    "leader": {
                        "member_name": "team_leader",
                        "name": "Leader",
                        "persona": "天才项目管理专家",
                    },
                    "predefined_members": [
                        {
                            "member_name": "assistant",
                            "name": "助理",
                            "persona": "逻辑清晰的综合助理",
                            "prompt_hint": "先认领再写",
                        },
                        {
                            "member_name": "user-research",
                            "name": "用研",
                            "persona": "用户研究专家",
                        },
                    ],
                }
            }
        },
        "models": {"defaults": []},
    }

    spec = load_team_spec_dict(config, template_id="oc_team_debate")
    assert spec["leader"]["desc"] == "天才项目管理专家"
    members = {m["member_name"]: m for m in spec["predefined_members"]}
    assert members["assistant"]["desc"] == "逻辑清晰的综合助理"
    assert "先认领再写" in members["assistant"]["prompt"]
    assert "persona" not in members["assistant"]
    assert "prompt_hint" not in members["assistant"]
    assert members["user-research"]["desc"] == "用户研究专家"


def test_load_team_spec_dict_hydrates_credentials_by_member_tip(monkeypatch):
    """Each team role hydrates from catalog agent_id tip (plan-equivalent)."""
    from jiuwenclaw.local_env_config import (
        bind_agent_env_ns,
        replace_active_env,
        reset_agent_env_ns,
    )

    replace_active_env(
        {
            "API_BASE": "https://bound.example/v1",
            "API_KEY": "sk-bound",
            "MODEL_NAME": "glm-bound",
            "MODEL_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="agentteam",
    )
    replace_active_env(
        {
            "API_BASE": "https://leader.example/v1",
            "API_KEY": "sk-leader",
            "MODEL_NAME": "glm-leader",
            "MODEL_PROVIDER": "OpenAI",
            "ENABLED_SKILLS": "research-brief,web_free_search",
        },
        service_id="default",
        agent_id="expert-chief-researcher",
    )
    replace_active_env(
        {
            "API_BASE": "https://mate.example/v1",
            "API_KEY": "sk-mate",
            "MODEL_NAME": "glm-mate",
            "MODEL_PROVIDER": "OpenAI",
            "ENABLED_SKILLS": "market-scan",
        },
        service_id="default",
        agent_id="expert-market-intelligence-researcher",
    )

    config = {
        "modes": {
            "team": {
                "demo": {
                    "team_name": "demo",
                    "leader": {
                        "member_name": "chief-researcher",
                        "display_name": "首席研究官",
                        "agent_id": "expert-chief-researcher",
                    },
                    "predefined_members": [
                        {
                            "member_name": "market-intelligence-researcher",
                            "display_name": "市场与情报研究员",
                            "role_type": "teammate",
                            "agent_id": "expert-market-intelligence-researcher",
                        }
                    ],
                    "agents": {
                        "leader": {
                            "model": {
                                "model_client_config": {
                                    "client_provider": "OpenAI",
                                    "api_base": "${API_BASE}",
                                    "api_key": "${API_KEY}",
                                },
                                "model_request_config": {"model": "glm-leader"},
                            }
                        },
                        "market-intelligence-researcher": {
                            "model": {
                                "model_client_config": {
                                    "client_provider": "OpenAI",
                                    "api_base": "${API_BASE}",
                                    "api_key": "${API_KEY}",
                                },
                                "model_request_config": {"model": "glm-mate"},
                            }
                        },
                    },
                }
            }
        },
        "models": {"defaults": []},
    }

    token = bind_agent_env_ns("default", "agentteam")
    try:
        spec = load_team_spec_dict(config_base=config)
    finally:
        reset_agent_env_ns(token)

    leader_mcc = spec["agents"]["leader"]["model"]["model_client_config"]
    mate_mcc = spec["agents"]["market-intelligence-researcher"]["model"]["model_client_config"]
    assert leader_mcc["api_base"] == "https://leader.example/v1"
    assert leader_mcc["api_key"] == "sk-leader"
    assert mate_mcc["api_base"] == "https://mate.example/v1"
    assert mate_mcc["api_key"] == "sk-mate"
    assert leader_mcc["api_key"] != "sk-bound"
    assert mate_mcc["api_key"] != "sk-bound"
    assert spec["agents"]["leader"]["skills"] == ["research-brief", "web_free_search"]
    assert spec["agents"]["market-intelligence-researcher"]["skills"] == ["market-scan"]


def test_transform_front_team_agent_spec_persists_tip_skills():
    """Sync writes skills on agents.*.skills; tip fills only when front omits."""
    from jiuwenclaw.config import _transform_front_team_agent_spec
    from jiuwenclaw.local_env_config import replace_active_env

    replace_active_env(
        {"ENABLED_SKILLS": "alpha,beta"},
        service_id="default",
        agent_id="expert-demo",
    )
    # Front skills win (same field as HEAD).
    with_front = _transform_front_team_agent_spec(
        "expert-demo",
        {"max_iterations": 50, "skills": ["front-only"]},
    )
    assert with_front["max_iterations"] == 50
    assert with_front["skills"] == ["front-only"]
    assert "model" not in with_front

    # Tip fills the same skills field when front omits it.
    from_tip = _transform_front_team_agent_spec(
        "expert-demo",
        {"max_iterations": 50},
    )
    assert from_tip["skills"] == ["alpha", "beta"]
    assert "model" not in from_tip


def test_load_team_spec_dict_does_not_rematch_other_agent_tip(monkeypatch):
    """Member tip without credentials must not borrow another agent's tip."""
    from jiuwenclaw.local_env_config import (
        bind_agent_env_ns,
        replace_active_env,
        reset_agent_env_ns,
    )

    replace_active_env(
        {
            "API_BASE": "https://other.example/v1",
            "API_KEY": "sk-other",
            "MODEL_NAME": "glm-5.2",
            "MODEL_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="assistant",
    )
    replace_active_env(
        {"MODEL_NAME": "glm-5.2", "MODEL_PROVIDER": "OpenAI"},
        service_id="default",
        agent_id="expert-chief-researcher",
    )

    config = {
        "modes": {
            "team": {
                "demo": {
                    "team_name": "demo",
                    "leader": {
                        "member_name": "chief-researcher",
                        "display_name": "Lead",
                        "agent_id": "expert-chief-researcher",
                    },
                    "agents": {
                        "leader": {
                            "model": {
                                "model_client_config": {
                                    "client_provider": "OpenAI",
                                    "api_base": "${API_BASE}",
                                    "api_key": "${API_KEY}",
                                },
                                "model_request_config": {"model": "glm-5.2"},
                            }
                        }
                    },
                }
            }
        },
        "models": {"defaults": []},
    }

    token = bind_agent_env_ns("default", "assistant")
    try:
        spec = load_team_spec_dict(config_base=config)
    finally:
        reset_agent_env_ns(token)

    mcc = spec["agents"]["leader"]["model"]["model_client_config"]
    assert not str(mcc.get("api_base") or "").strip()
    assert not str(mcc.get("api_key") or "").strip()


def test_load_team_spec_dict_without_agent_id_uses_bound_tip(monkeypatch):
    """Missing roster agent_id hydrates from bound tip only (no inventing ids)."""
    from jiuwenclaw.local_env_config import (
        bind_agent_env_ns,
        replace_active_env,
        reset_agent_env_ns,
    )

    replace_active_env(
        {
            "API_BASE": "https://bound.example/v1",
            "API_KEY": "sk-bound",
            "MODEL_NAME": "glm-5.2",
            "MODEL_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="agentteam",
    )
    replace_active_env(
        {
            "API_BASE": "https://expert.example/v1",
            "API_KEY": "sk-expert",
            "MODEL_NAME": "glm-5.2",
            "MODEL_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="expert-chief-researcher",
    )

    config = {
        "modes": {
            "team": {
                "demo": {
                    "team_name": "demo",
                    "leader": {
                        "member_name": "chief-researcher",
                        # no agent_id → cannot address expert tip
                    },
                    "agents": {
                        "leader": {
                            "model": {
                                "model_client_config": {
                                    "client_provider": "OpenAI",
                                    "api_base": "${API_BASE}",
                                    "api_key": "${API_KEY}",
                                },
                                "model_request_config": {"model": "glm-5.2"},
                            }
                        }
                    },
                }
            }
        },
        "models": {"defaults": []},
    }

    token = bind_agent_env_ns("default", "agentteam")
    try:
        spec = load_team_spec_dict(config_base=config)
    finally:
        reset_agent_env_ns(token)

    mcc = spec["agents"]["leader"]["model"]["model_client_config"]
    assert mcc["api_base"] == "https://bound.example/v1"
    assert mcc["api_key"] == "sk-bound"


def test_load_team_spec_dict_hydrates_from_bound_tip_when_no_member(monkeypatch):
    """Without roster agent_id, credentials come from bound tip."""
    from jiuwenclaw.local_env_config import (
        bind_agent_env_ns,
        replace_active_env,
        reset_agent_env_ns,
    )

    replace_active_env(
        {
            "API_BASE": "https://bound.example/v1",
            "API_KEY": "sk-bound",
            "MODEL_NAME": "glm-5.2",
            "MODEL_PROVIDER": "OpenAI",
        },
        service_id="default",
        agent_id="office",
    )

    config = {
        "modes": {
            "team": {
                "demo": {
                    "team_name": "demo",
                    "agents": {
                        "leader": {
                            "model": {
                                "model_client_config": {
                                    "api_base": "",
                                    "api_key": "",
                                    "client_provider": "OpenAI",
                                },
                                "model_request_config": {"model": "glm-5.2"},
                            }
                        }
                    },
                }
            }
        },
        "models": {"defaults": []},
    }

    token = bind_agent_env_ns("default", "office")
    try:
        spec = load_team_spec_dict(config_base=config)
    finally:
        reset_agent_env_ns(token)

    mcc = spec["agents"]["leader"]["model"]["model_client_config"]
    assert mcc["api_base"] == "https://bound.example/v1"
    assert mcc["api_key"] == "sk-bound"


def test_load_team_spec_dict_adds_teammate_when_leader_and_extra_keys():
    """Missing teammate must be filled even if agents has non-role keys."""
    config = {
        "modes": {
            "team": {
                "demo": {
                    "team_name": "demo",
                    "lifecycle": "persistent",
                    "agents": {
                        "leader": {},
                        "chief-researcher": {},
                    },
                }
            }
        },
        "models": {"defaults": []},
    }
    spec = load_team_spec_dict(config_base=config)
    assert "teammate" in spec["agents"]
    assert "leader" in spec["agents"]
