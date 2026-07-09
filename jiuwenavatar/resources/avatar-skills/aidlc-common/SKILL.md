---
name: aidlc-common
description: Aidlc 通用参考：分层对齐、skills 路径解析、**任务前本地仓库对齐**（分支/commit 核对与 checkout）。供 Leader 派发与各 dev-* 阶段及 gitcode-repo 共同引用。
metadata:
  short-description: Shared layer-alignment guidance for all dev-* skills.
  category: reference
  load_policy: on-spawn
  depends_on: []
---

# Aidlc 通用参考（aidlc-common）

该 skill 提供跨阶段共享规则，不直接产出 `doc/<module>/` 文件。默认由 `dev-analyzer`、`dev-designer`、`dev-planner`、`dev-coder`、`dev-tester`、`dev-reviewer` 在对应阶段按需引用。

## 必读文件

1. [references/layer-alignment.md](references/layer-alignment.md)
2. [references/skills-paths.md](references/skills-paths.md) — skill 路径解析；Leader 派发与子 agent 必读，正文英文
3. [references/repo-workspace-sync.md](references/repo-workspace-sync.md) — **任务前本地仓库对齐**（分支/commit 核对与 checkout）；凡涉及业务代码仓必读

## 适用范围

- 需求分析：判断根因层级，避免把表象当根因。
- 方案设计：保证方案深度与根因深度一致。
- 任务规划：将基础设施任务排在呈现层任务之前（适用时）。
- 代码实现：在正确抽象层做最小闭环修复。
- 测试验证：优先验证机制行为，而非只做字段断言。
- 代码审查：识别层级错位与 PATCH_RISK。
- **仓库任务**：开工前对齐本地分支/commit（见 [references/repo-workspace-sync.md](references/repo-workspace-sync.md)）。

## 关键约束

- 此 skill 是参考来源，不替代各角色自己的模板、门禁脚本与交付规范。
- 各角色文档与脚本优先级高于本 skill；冲突时先遵守角色技能并向 Leader 报告。
- 对简单 L1 逻辑问题，允许不下探到 L2/L3，但必须记录证据与边界。
