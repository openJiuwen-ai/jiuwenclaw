# 场景细则

与 `dev-leader/references/workflow.md` 配套：**workflow 定义何时阻塞**；本文件定义 **如何提问**（有工具 = 必须调工具，不是聊天假等待）。

## G0（`g0-bootstrap`）

**自动 PASS**（不问）：`module` 无冲突；`branch_base` 可解析；`repo-root` 已写入任务卡。

**必须提问**（有工具 → 调工具；无工具 → fallback，仍 **每次单题**）：

| 条件 | `question_id` | 选项要点 |
|------|---------------|----------|
| `module` 缺失或多义 | `g0-module` | 候选 module +「其他」 |
| `repo-root` 无法可靠解析 | `g0-repo-root` | 工作区候选绝对路径 +「其他」 |
| `branch_base` 不明 | `g0-branch-base` | 候选分支 +「其他」（勿默认 develop/main） |
| 跳步 / 仅某阶段 / 不建 PR 须登记 | `g0-scope` | 完整链 / 指定阶段 / 跳过文档链 + 其他 |

**禁止**：`是否启动完整流水线`、`是否开始 G1`。`repo-root` 解析失败 **不得** 静默假设。

> `branch_base` = G0 工作区与 G7a 分叉点；PR 合入目标为 `integration_base`（常同值；bench 场景可能不同）。见 `workflow.md` §Git 基线。

## G1/G2（`g1g2-clarify`）

**进入条件**（任一）：

- 子 agent 返回 `状态: NEEDS_DISCUSSION`
- `requirements.md` §4.6 或 `design.md` §协作讨论记录 存在未勾选 `- [ ]`

**流程**：

1. 汇总未决项，P0 → P1 排序。
2. **单题分轮**：取最高优先级 **一道** → 工具或 fallback（**禁止**一次 `questions` 多项或一卡多题）。
3. 选项来自子 agent 澄清表 +「其他」；范例 → [tool-payload.md](tool-payload.md) §G1/G2。
4. 获答案 → [record-templates.md](record-templates.md) 澄清答复 **追加一行** → 仍有未决则 **结束本回合**，下一轮问下一道。
5. 全部需问项已记 → 重派**同一**子 agent；`check_*` PASS 且 `gates.md` 语义复核通过前 **禁止** 判 G1/G2 PASS。

**Visual Companion**：澄清表标注需要时 → **单独一题 P0**（对齐对应 `Q-xxx`）→ 再写澄清行。

**禁止**：同回合提问与重派/定案并行；采纳「建议默认」；Leader 改讨论项勾选或代填「用户决定」。

## G6

### 残留风险（`g6-risk`）

MUST-FIX 或 Leader 标 **本轮必改** 的 SF 无法关闭、拟「接受残留」过 G6：

- **逐条 id、P0 分轮**（每 MF/CR 单独一轮，或「其他」中说明后拆多行登记）。
- 写入「折中与风险登记」或主表；全部拟接受项登记后，方可视该项关闭。

### 三轮返工阻塞（`g6-blocked`）

第 3 轮仍 REWORK → `BLOCKED`，**暂停 G7**：

- 必须提问；选项见 tool-payload §G6 blocked。
- **禁止**在本题选项含「接受残留」；残留须 **另起** `g6-risk` 逐条登记。
- **未获答案前** 不得 checkout 特性分支或创建 PR。

### SF 升格（`g6-sf-escalate`）

默认 Leader **单方**分拣，**不**问用户。仅 SF 影响 scope/安全且无法判断时：

- `scenario=g6-sf-escalate`，单题分轮；
- 答案写入折中登记或 SF 表后再继续 G6。

## 策略（`skip-pipeline`）

用户要求跳过文档链、合并阶段或书面承担风险：

- 首条消息**未**明确范围 → **必须**单题确认跳步与风险接受。
- 答案写入任务卡 + 折中/决策记录；可能仍保留 reviewer + PR。

## 运维（`git-danger` / `pr-abort`）

| ID | 条件 | 要点 |
|----|------|------|
| `git-danger` | `git reset --hard` 等 | 说明将丢失未提交变更；确认/取消 + 其他 |
| `pr-abort` | PR `--create` 反复失败 | 重试 / 放弃保留 `pr-body.md` / 仅 CLI 草稿 + 其他 |

`skip-pr`、无 token：用户消息**已**声明 → 不重复问；否则单题确认「仅输出 CLI + 草稿」。

## 依赖（`deps-gonogo`，jiuwenswarm）

`aidlc-dev-team` 巡检后，仅 `required: true` 缺失或环境冲突：

- 必须提问：继续（承担风险）/ 暂停修依赖 / 其他。
- 可降级依赖缺失：**不**提问，只登记报告。

## 不需本 skill

- 周期性 Pipeline 状态汇报（单向）
- Leader 跑超重命令时的**告知**（非确认）
- SF 无歧义的延期/接受登记
- G0 字段齐全且无跳步登记需求
