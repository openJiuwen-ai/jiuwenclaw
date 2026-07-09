---
name: brainstorming
description: dev-analyzer / dev-designer 的辅助 skill（G1/G2 协作澄清与 Visual Companion）。仅由上述子 agent 在任务中加载；禁止 Leader 或用户单独 @ 调用。不产出 doc 文件。
metadata:
  short-description: G1/G2 collaboration clarify helper for analyzer and designer subagents only.
  category: reference
  load_policy: bundled
  depends_on: []
---

# Brainstorming（analyzer / designer 辅助 skill）

## 定位

| 项 | 说明 |
|----|------|
| **调用主体** | 仅 **`dev-analyzer`**、**`dev-designer`** 子 agent |
| **加载时机** | 子 agent 执行 G1/G2 任务时，与主 skill 一并阅读 |
| **禁止** | Leader、Main、用户 **不得** 单独 `@brainstorming` 或按本 skill 独立跑流程 |
| **产出** | **无**；讨论结论由调用方写入 `requirements.md` §4.6 或 `design.md` §协作讨论记录 |

主 skill 负责模板、落盘与 Gate；本 skill 只提供**协作澄清方法**与 **Visual Companion** 工具说明。

## 子 agent 阅读顺序

1. `skills/dev-analyzer/SKILL.md` 或 `skills/dev-designer/SKILL.md`
2. 对应 `references/principles.md`
3. **本文件**（只读协作澄清原则）
4. 仅当澄清请求标注 `建议 Visual Companion: 是` 且 Leader/用户同意时，才读 `skills/brainstorming/visual-companion.md`

## 协作澄清原则

- **G1/G2 强制 2–3 条用户确认项**；正文细节按证据定案，不替代 checklist。
- 子 agent 不直连用户：`NEEDS_DISCUSSION` → Leader **`skills/user-interact`** → **澄清答复** → 继续
- Leader 提问规则见 **`skills/user-interact`**
- 统一澄清表列：`编号` / `优先级` / `主题` / `为何需要` / `选项` / `建议默认` / `建议 Visual Companion` / `不澄清风险`
- **YAGNI**

### 问题来源（P0/P1/冲突）

§4.6 / §协作讨论记录 **强制 2–3 条**核心问题，每条**必须**取自下列维度（按优先级择高填入）：

- **P0**：不澄清无法写出 SR/AC 或设计约束，或会改变 scope、验收标准、架构方向、外部接口/数据模型。
- **P1**：存在 ≥2 个合理理解，且默认选择一旦错了会导致返工或误交付。
- **冲突**：Issue/任务卡与代码证据、既有文档或用户约束明显冲突。

若真实未决项不足 2 条，**优先**补充验收口径或范围边界的确认项凑满 2 条；**禁止**编造无验收/接口影响的问题充数。

### 协作讨论 checklist（G1/G2 共用）

- **analyzer** 落盘 `requirements.md` → `### 4.6 协作讨论记录`
- **designer** 落盘 `design.md` → `## 概述` 下 `### 协作讨论记录`
- **必须**列出 **2–3 条**核心问题，聚焦 scope、验收、架构/边界取舍
- 每条须给出 **选项** 与 **建议默认**；用户确认前为 `- [ ]`，确认后改 `- [x]` 并回填「用户决定」
- Agent **不得**以「默认决策」代填「用户决定」

### 定稿条件

- 任务卡含**澄清答复**，且 checklist 已全部 `- [x]` 且「用户决定」已回填
- 分析/设计正文中可按证据定案的细节，**不替代** checklist 中的 2–3 条核心问题

**预澄清任务卡（首轮即定稿）**：若 Leader 派工时任务卡**已含**覆盖全部 2–3 条核心问题的**澄清答复**，子 agent 可在首轮直接勾选 `- [x]`、回填「用户决定」并运行 `check_*` 至 PASS，**无需**先返回 `NEEDS_DISCUSSION`；任务卡未覆盖的问题仍按常规 `NEEDS_DISCUSSION` 流程处理。

澄清请求格式见调用方主 skill（`dev-analyzer` / `dev-designer` 的「协作澄清」节）。

## Visual Companion

由**子 agent**在需要时按 `visual-companion.md` 操作（写 HTML、必要时启动 `scripts/start-server.sh` 或 Windows 下 `scripts/start-server.ps1`）。

流程：

1. 子 agent 在澄清请求某行标注 `建议 Visual Companion: 是`
2. Leader 经 **`skills/user-interact`** 向用户确认；同意后写入 **澄清答复**（如 `Q-001: Visual Companion 已启用`）
3. **子 agent** 在收到答复的下一轮启动/使用 Visual Companion，并将结论写入 §4.6 或 §协作讨论记录

会话目录：`<project>/.brainstorm/`（建议 `.gitignore`）。

## 与 Aidlc 文档约束

G1/G2 各只允许一份落盘文件（`requirements.md` / `design.md`）。本 skill **不得**引导写入其它路径或文件类型。
