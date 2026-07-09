# 提问内容与选项

以 **问题句 + 选项列表** 为准。有工具 → 映射为工具参数并调用；**禁止**同语义只写在聊天正文。无工具 → [fallback-chat.md](fallback-chat.md)。

## 通用规则

| 规则 | 说明 |
|------|------|
| 选项 | ≥2；**必须**含「其他（请说明）」 |
| 题量 | 每次 1 题；`questions` 长度 **= 1** |
| 优先级 | P0 先于 P1 提问，**不得**为省轮次合并多题 |
| `question_id` | 对齐 `Q-XXX` 或场景 id（`q-001` ↔ `Q-001`） |
| 呈现 | IM/飞书：一问题一卡片 |

## 分轮（多未决时）

1. P0→P1 排序，取 **一道** 未决题。  
2. 有工具 → 调用（payload 仅该题）；无工具 → fallback 单题。  
3. 答案 → [record-templates.md](record-templates.md) **追加一行**。  
4. 仍有未决 → **结束本回合**；下一轮再问下一道。  
5. 全部记完 → 重派同一子 agent 或继续 Gate。

## 选项来源

| `scenario` | 选项从哪来 |
|------------|------------|
| `g1g2-clarify` | 子 agent §4.6 / §协作讨论表 |
| `g0-*` | 工作区、gitcode-repo.json、远端分支等 **实际候选** |
| `g6-risk` | `review.md` / `result.json` 的 MF-xxx、CR-xxx |
| `g6-blocked` | 见 §范例 G6 blocked |
| 其余 | 见 [scenarios.md](scenarios.md) |

## 范例（构造 payload 时对照）

### G1/G2 澄清（`g1g2-clarify`）

- **question_id**：`Q-001`（对齐澄清表）
- **问题**：验收范围是否包含旧 API 的兼容层？
- **选项**：包含兼容层（维护 v1/v2）/ 仅新 API / 其他（请说明）

**Visual Companion**（单独一题，P0）：

- **问题**：是否为 Q-001 启用 Visual Companion？
- **选项**：启用 / 不启用，仅文字澄清 / 其他

### G0 branch_base（`g0-branch-base`）

- **问题**：本任务应基于哪个 `branch_base`？
- **选项**：按候选填写（如 `bench-issue-2`、`develop`）+ 其他  
- 勿把 bench 环境分支误当作 PR `--base`（`integration_base` 可能不同）。

### G6 blocked（`g6-blocked`）

- **问题**：第 3 轮审查仍为 REWORK，如何继续？
- **选项**：缩小范围后继续返工 / 不缩小范围继续返工 / 暂停流水线人工介入 / 其他  
- **禁止**在本题加入「接受残留 MUST-FIX/SF」。

### G6 残留（`g6-risk`，一次一 id）

- **问题**（示例）：是否接受 **MF-003**（缺少输入校验）以残留风险通过 G6？
- **选项**：接受该项残留 / 不接受，继续返工关闭 / 其他

### skip-pr（`pr-abort` 变体）

用户未声明时单题：**是否仅输出 CLI + PR 草稿（不调用 API）？** → 是 / 否，先配置 token / 其他

## 工具字段映射

| 语义 | 常见字段 |
|------|----------|
| 问题 | `prompt` / `question` / `text` |
| 选项 | `options[].label` |
| ID | `questions[].id` |
| 题量 | `questions` 长度 **1**；默认单选 |

平台工具名 → [platforms.md](platforms.md)。

## 禁止的提问方式

- 有工具时只写「请回复 A/B/C」（假降级）
- 有工具却说暂停等待但未调工具
- 未等返回即写入「用户决定」
- G0 问是否启动完整流水线
- 多未决合并为一题或 `questions` 多项
- `g6-blocked` 选项含接受残留
