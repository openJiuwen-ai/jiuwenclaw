# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Design mode prompt builder — derives from code profile, aligns with WorkBuddy design mode."""

from jiuwenswarm.agents.harness.design.prompt.design_plan_prompts import (
    DESIGN_PLAN_ALLOWED_TOOLS,
    design_enter_plan_instructions,
    design_exit_plan_notification,
    design_plan_mode_system_note,
)
from jiuwenswarm.agents.harness.design.prompt.design_prompt_builder import (
    build_design_system_prompt,
)

__all__ = [
    "DESIGN_PLAN_ALLOWED_TOOLS",
    "build_design_system_prompt",
    "design_enter_plan_instructions",
    "design_exit_plan_notification",
    "design_plan_mode_system_note",
]
