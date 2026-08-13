# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 在线执行模块（薄工具层 + 全复用 DeepAgent ReAct 原生能力）.

模块清单：
- schema_loader: turbo 面发现 + schema 加载 +7SKILL_TURBO.md frontmatter 解析
- param_validator: 轻量入参校验（skill_name / scenario / plan_name / node_inputs）
- executor_single: 隔离加载单个 PlanNode + runtime callback 注入 + mtime 缓存
- skill_turbo_tool: 薄、无状态的在线执行工具（activate + execute）
"""

from __future__ import annotations
