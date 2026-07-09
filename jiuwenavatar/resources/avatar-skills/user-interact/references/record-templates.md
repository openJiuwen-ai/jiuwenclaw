# 输出记录模板

收到用户答案（工具返回或 chat-fallback 下一条消息）后，**先写入再恢复编排**。Markdown 表格即可，**无需 JSON**。降级时 `source` = `chat-fallback`。

## 用户决策记录（主表，推荐）

```markdown
## 用户决策记录

| gate | scenario | id | 用户决定 | source |
|------|----------|-----|----------|--------|
| G1 | g1g2-clarify | Q-001 | 包含兼容层 | AskQuestion |
| G1 | g1g2-clarify | Q-002 | 不启用 Visual Companion | chat-fallback |
| G0 | g0-scope | — | 跳过 G1–G3，保留 reviewer+PR | AskUserQuestion |
| G6 | g6-risk | MF-003 | 接受残留 | AskQuestion |
```

- **每答一题追加一行**；勿等全部答完再一次性编造。
- 选「其他」时 `用户决定` 写用户说明摘要。
- `gates.md` 认 `source` 为 `AskQuestion`、`AskUserQuestion` 或 `chat-fallback`。

## 澄清答复（G1/G2 → 重派子 agent）

重派 `dev-analyzer` / `dev-designer` 时**必须**可读；可与主表等价：

```markdown
## 澄清答复（Leader → 子 agent）

| Q-id | 用户选择 | source |
|------|----------|--------|
| Q-001 | 选 B：最小修复，不顺带重构 | AskQuestion |
```

## 折中与风险登记（G0 跳步 / G6）

```markdown
## 折中与风险登记

| id | 类型 | 用户决定 | 轮次/备注 |
|----|------|----------|-----------|
| — | 跳步 | 跳过 G1–G3，保留 reviewer+PR | G0 |
| MF-003 | MUST-FIX 残留 | 接受 | G6-R2 |
| CR-010 | 本轮必改 SF 残留 | 接受 | G6-R3 |
```

与主表 `scenario=g6-risk` / `g0-scope` 行**等效**；`dev-leader` gates/rework 复核任认其一。

## 任务卡摘录（G0 / 跳步后）

与 `workflow.md` 任务卡一致，更新 scope 后写入：

```markdown
## 任务卡

- **module**：auth
- **repo-root**：`D:/proj/foo`
- **branch_base** / **integration_base**：（G0 锁定）
- **任务类型**：Feature
- **跳步/范围**：完整 G1–G7（或引用折中与风险登记）
- **Issue 摘录**：（如有）
```

## Git / PR 决策摘要

```markdown
- **场景**：git-danger / pr-abort / skip-pr
- **用户决定**：取消 reset --hard，改用手动 stash
- **后续**：继续 G7a
```

可并入主表一行，并在 Pipeline 汇报中复述。

## SF 分拣（通常不问用户）

无歧义时 Leader 单方登记：

```markdown
## SF 分拣（Leader → 派工）

| id | 决策 | 理由 |
|----|------|------|
| CR-011 | 延期 | 下迭代 Issue |
```

若经 `g6-sf-escalate` 问过用户，在 SF 表或折中登记注明用户选择。
