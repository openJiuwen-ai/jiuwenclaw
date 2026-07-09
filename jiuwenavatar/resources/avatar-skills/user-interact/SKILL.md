---
name: user-interact
description: Leader 结构化用户决策：会话有 AskUserQuestion/AskQuestion 等工具则必须调用（每次一题）；仅确无工具时 chat-fallback。写入用户决策记录/澄清答复后恢复编排。触发：G0、G1/G2 澄清、G6、跳步、Git/PR、依赖 go/no-go。
metadata:
  short-description: Leader-only user decisions via structured ask tools; one question per turn.
  category: orchestration
  load_policy: on-gate
  depends_on: []
  gates:
    - G0
    - G1
    - G2
    - G6
---

# user-interact

## 定位

| 项 | 说明 |
|----|------|
| **调用主体** | **Leader / Main**（`dev-leader`）及需阻塞等用户输入的编排角色 |
| **禁止** | 子 agent（`dev-analyzer` … `dev-tester`）**不得**加载本 skill 向用户提问 |
| **分工** | **何时问** → `dev-leader`（`workflow.md` / `gates.md` / `rework.md`）；**怎么问、怎么记** → 本 skill |

子 agent 通过 `NEEDS_DISCUSSION` + 落盘 §4.6 / §协作讨论记录 → Leader **必须先**加载本 skill 再向用户提问。

## 核心原则

用户决策不是进度汇报。会话有 `AskUserQuestion`、`AskQuestion` 或等价结构化提问工具时，必须把 **问题 + 选项** 交给工具，**调用后结束本回合** 等待返回。

**禁止**在工具可用时只在正文写 Markdown 问题、编号选项或「请回复 A/B/C」——这是 **假降级**，会丢失 Gate 审计且无法稳定映射记录。

## 调用契约（每轮一问）

| 字段 | 要求 |
|------|------|
| `scenario` | 场景 ID（下表） |
| `question_id` | `Q-001` 或 `g0-module` 等，与澄清表/审查 id 对齐 |
| `prompt` | 一句问题 |
| `options` | ≥2，**必须**含「其他」或等价自由输入 |
| `record` | 见 [record-templates.md](references/record-templates.md) |

不必手写 JSON；有工具时映射为工具参数，`questions` **长度必须为 1**。字段映射 → [tool-payload.md](references/tool-payload.md)。

## 提问路径（两档）

| 档 | 条件 | 动作 |
|----|------|------|
| **工具** | 工具列表含 `AskUserQuestion` / `AskQuestion` 或等价名 | 调用其一 → **结束本回合** 直至返回 |
| **fallback** | 工具不存在，或返回 `unknown tool` / `not found` | [fallback-chat.md](references/fallback-chat.md) |

弹窗关闭、hook 拒绝、payload 可修正、超时 → **不算**无工具；修正后重试（≤2 次），仍失败再 fallback。平台对照 → [platforms.md](references/platforms.md)。

## 硬约束

1. 凡需用户输入才能继续流水线 → 有工具则 **必须**调工具并结束本回合；无工具才合规 fallback 并等待下一条用户消息。
2. **禁止**有工具时用 Markdown / 自然语言确认代替工具。
3. **禁止**未收到用户选择就默认、代决、判 Gate PASS 或重派子 agent。
4. **每次仅 1 题**（含 fallback）；`questions` 至多 1 项；飞书/IM **一问题一卡片**。
5. 多未决题 → **分多轮**（答完 → 记一行 → 结束回合 → 下一轮下一题）→ [tool-payload.md](references/tool-payload.md) §分轮。
6. 收到答案后 **先写入记录位**，再恢复 Gate / 重派。
7. G0 **禁止**问「是否启动完整流水线」「是否开始 G1」。

## 场景 ID

| ID | 用途 | Leader 何时（细则） |
|----|------|---------------------|
| `g0-bootstrap` | module / repo-root / branch_base / 跳步 | [scenarios.md](references/scenarios.md) §G0 |
| `g1g2-clarify` | G1/G2 澄清 | `NEEDS_DISCUSSION` 或 §4.6 / §协作讨论 `- [ ]` |
| `g6-risk` | 接受 MUST-FIX / 本轮必改残留 | G6，`rework.md` |
| `g6-blocked` | 第 3 轮仍 REWORK | G6 BLOCKED，暂停 G7 |
| `g6-sf-escalate` | SF 影响 scope/安全，Leader 无法单方定案 | 默认不问；见 scenarios |
| `skip-pipeline` | 跳步 / 轻量交付 | 用户要求跳步且未书面明确 |
| `git-danger` | `reset --hard` 等 | G7a 运维 |
| `pr-abort` | PR 创建失败 | G7b |
| `deps-gonogo` | 必需依赖缺失 | jiuwenswarm Team 巡检 |

**不需本 skill**：进度汇报、超时告知、G0 自动 PASS、无歧义 SF 单方分拣、Leader 仅告知超重命令（非确认）。

## Leader 执行步骤

1. 确认 `scenario` + `question_id`（见 [scenarios.md](references/scenarios.md)）。
2. 从子 agent 澄清表 / 工作区候选 / `review.md` 构造 `prompt` + `options`（范例 → [tool-payload.md](references/tool-payload.md) §范例）。
3. 按 **两档** 提问；工具路径调用后 **本回合结束**。
4. 答案写入 [record-templates.md](references/record-templates.md)（G1/G2 须能让子 agent 读到 `## 澄清答复`）。
5. 若仍有未决题 → 下一轮重复 2–4；全部记完 → 重派**同一**子 agent 或继续 Gate。

## 输出记录（写哪）

| 场景 | 写入 |
|------|------|
| G1/G2 | `## 澄清答复（Leader → 子 agent）` 或主表 + 等价行 |
| G0 跳步 / G6 风险 | `## 折中与风险登记` 或主表 `scenario=g6-risk` 等 |
| G0 scope | 任务卡 + 决策记录 |
| Git/PR | 决策表一行 + Pipeline 摘要 |

`source` 填实际工具名或 `chat-fallback`。**每答一题追加一行**，勿等全部答完再编造。

## 与子 agent 的边界

- `dev-analyzer` / `dev-designer`：只产出澄清请求与 `NEEDS_DISCUSSION`；**不**调用本 skill。
- `brainstorming`：由子 agent 加载；**Visual Companion** 须 Leader **单独一题**（P0）确认后再写入澄清行。
- G1/G2：无未决项且 `check_*` PASS 前，Leader **禁止**判该 Gate PASS（见 `gates.md`）。
