# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 在线执行模块 —— 主 Agent 驱动逐 PlanTask 执行。

替代现有 ``skill_acceleration_exec`` 批量自主编排，改为：
主 Agent LLM 在 PlanTask 之间参与推理，逐个 PlanTask 推理 → 调
``skill_turbo_tool`` 跑单节点（activate → execute 两阶段）。

组件：
- ``schema_loader``: turbo 产物探测 + schema 加载 + frontmatter 解析
- ``context_store``: session 级 accumulator + 持久化
- ``flow_scheduler``: 候选集计算（execution_flow + when + 并行分组）
- ``executor_single``: 单节点执行 API（从 executor.py 抽出）
- ``param_validator``: 参数校验（必填齐全 + 超集校验）
- ``fallback_policy``: 在线 fallback 计数 + 整任务回退策略
- ``skill_turbo_tool``: skill_turbo_tool 工具定义（activate/execute 两模式）

设计文档：``文档/需求/skill在线执行/方案设计/Jiuwenclaw-skill加速器在线执行方案.md``
实施指导：``文档/需求/skill在线执行/实施指导/Jiuwenclaw-skill加速器在线执行-实施指导.md``
"""

from __future__ import annotations

__all__: list[str] = []
