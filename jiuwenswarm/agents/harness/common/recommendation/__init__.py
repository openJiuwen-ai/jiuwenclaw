# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Proactive recommendation system — background engine for profile extraction,
pain-point reasoning, task reminder, need exploration, and skill matching.

Modules:
- profile_extractor.py  — UserProfile data model + persistence
- proactive_engine.py    — Background tick loop (ProactiveEngine)
- proactive_actions.py   — LLM prompts, decision types, skill discovery, push
- situation_report.py    — Multi-session context aggregation for LLM
"""

from jiuwenswarm.agents.harness.common.recommendation.profile_extractor import (
    UserProfile,
    load_user_profile,
    save_user_profile,
)
from jiuwenswarm.agents.harness.common.recommendation.proactive_engine import (
    ProactiveEngine,
)

__all__ = [
    "UserProfile",
    "load_user_profile",
    "save_user_profile",
    "ProactiveEngine",
]
