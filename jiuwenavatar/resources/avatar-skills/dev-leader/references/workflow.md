# dev-leader 工作流

按需阅读：阶段边界、产物依赖、澄清中继、G7 收口。Gate 表与复核命令 → [gates.md](gates.md)；返工/SF → [rework.md](rework.md)。

## 编排步骤

默认严格按文档链；跳步须 user-interact `skip-pipeline` 并登记风险。

1. **G0**：锁定 `module`、`repo-root`、**`skills_root`**（见 `skills/aidlc-common/references/skills-paths.md`）、§Git 基线；**环境** → `skills/env-setup` + `env_bootstrap.py`（`ok: true` 后写入任务卡）。`module`/`repo-root`/`skills_root`/`branch_base`/环境齐全且无冲突 → **自动 PASS**；禁问「是否启动流水线」。缺省/跳步 → `g0-bootstrap`
2. **Issue（可选）**：按 `skills/gitcode-repo` 拉取（主仓 `--source upstream`；fork/bench `--source fork`）；摘要供后续派发
3. **G1** spawn `dev-analyzer` → `requirements.md`；`NEEDS_DISCUSSION` → §G1/G2 澄清禁令；**不得** PASS
4. **G2** spawn `dev-designer` → `design.md`；同上
5. **G3** spawn `dev-planner` → `dev_plan.md`、`test_plan.md`
6. **G4** serial spawn `dev-coder`，或按 `skills/dispatch-parallel` 执行 G4-P→G4-W→G4-I → 代码 + dev_plan 勾选
7. **G5** serial spawn `dev-tester`，或按 `skills/dispatch-parallel` 执行 G5-P→G5-W→G5-I → 测试 + test_plan 勾选
8. **G6** spawn `dev-reviewer` → `review.md`（临时证据 `doc/<module>/review/`）；MUST-FIX/**本轮必改** SF → [rework.md](rework.md)
9. **G7a → G7b**（仅 G6 PASS）：见下文 §G7

## 协作澄清中继（G1/G2）

**触发**：`NEEDS_DISCUSSION`；§4.6 或 §协作讨论记录 存在 `- [ ]`。

**Leader**：加载 `skills/user-interact`（`scenario=g1g2-clarify`）；**怎么问/怎么记** 以该 skill 为准。本节约束：

- 提问或 fallback 后 **本回合结束**；全部需问项写入澄清答复（见 `user-interact/references/record-templates.md`）后再重派同一子 agent
- **禁止**：同回合重派/定案；「建议默认」当答复；Leader 改讨论项勾选或「用户决定」
- **预澄清**：任务卡已含覆盖全部 Q-xxx 的澄清答复 → 首轮可定稿
- 澄清轮：`check_*` 预期失败；定稿轮：子 agent 按澄清答复勾选 `- [x]` → `check_*` PASS → [gates.md](gates.md) 复核

`brainstorming` 由子 agent 调用；Visual Companion 由 Leader **单独一题** 确认。

澄清答复示例（每答一题追加一行，`source` 必填）：

```markdown
## 澄清答复（Leader → 子 agent）

| Q-id | 用户选择 | source |
|------|----------|--------|
| Q-001 | 选 B：最小修复，不顺带重构 | AskQuestion |
```

### G0 vs G1/G2 澄清

- **G0**：锁定 module/范围/§Git 基线/§G0 环境；非「是否跑流水线」之门
- **G1 前**：`repo-root`、环境、`branch_base` 写入任务卡
- **G1/G2**：阶段内具体歧义，可多次往返

## 产物链

`doc/<module>/` 相对 **`repo-root`**；Gate 脚本 `--repo-root` 须与派发一致。

| 顺序 | Skill | 输入 | 输出 |
|------|-------|------|------|
| 1 | dev-analyzer | 需求/Issue 摘要 | `requirements.md` |
| 2 | dev-designer | requirements | `design.md` |
| 3 | dev-planner | requirements + design | `dev_plan.md`, `test_plan.md` |
| 4 | dev-coder | dev_plan | 代码 + dev_plan 勾选 |
| 5 | dev-tester | test_plan | 测试 + test_plan 勾选 |
| 6 | dev-reviewer | diff + doc | `review.md`；临时 `review/` |

缺上游产物 **不得** 启动下一阶段。

## G4/G5 阶段内并行

G4/G5 可按 `skills/dispatch-parallel` 在阶段内并行，但阶段顺序不变。Leader 使用以下子步：

| 子步 | G4 | G5 | 说明 |
|------|----|----|------|
| P | 读 `dev_plan.md` 的 `## 可并行组（G4）` | 读 `test_plan.md` 的 `## 可并行组（G5）` | 生成 `doc/<module>/dispatch/manifest.yaml`，运行 `partition_check.py --phase g4/g5` |
| W | 并行 spawn `dev-coder` shards | 并行 spawn `dev-tester` shards | 每个 prompt 含 Shard Contract；最多 3 个 shard |
| I | 收 `g4-S*-summary.md` | 收 `g5-S*-summary.md` | 运行 `partition_check.py --phase integrate`，再跑 [gates.md](gates.md) 的全量 G4/G5 复核；`doc/<module>/dispatch/` 为临时证据，**不得**提交 |

plan 无 `PG-*` 或仅一组 → serial 单 agent。plan 含有效 `PG-*` 且 `partition_check` PASS → 必须并行；Leader 拟改 serial → `user-interact`。校验失败或 touch 冲突 → `user-interact` 或返工 G3。禁止 G4/G5 同时派发，也禁止在 worker 未全部返回时宣布 Gate PASS。

## Git 基线

G0 任务卡必填；spawn 附带 `branch_base`。

| 字段 | 作用 | 取值 |
|------|------|------|
| `branch_base` | 工作区；G7a 分叉点 | 用户/Issue 分支；缺 → `user-interact`；**禁**默认 develop/main/`origin/HEAD` |
| `integration_base` | G7b `--base` / PR 合入目标 | 默认 = `branch_base`；bench 或跨 upstream MR → `upstream.base_branch` |

G0：`fetch` → checkout 对齐 `<fork.remote_name>/<branch_base>`（工作区干净；通常为 `origin/<branch_base>`）
G1–G6：**禁** checkout/merge/rebase 其它分支；**每个 PR/任务开始前**仍须按 [skills/aidlc-common/references/repo-workspace-sync.md](../../aidlc-common/references/repo-workspace-sync.md) 核对 HEAD 与任务期望 ref 一致（防上一任务残留错误基线）

`branch_base` 是修复起点，`integration_base` 是 PR/MR 合入目标；bench 场景常见 `branch_base=bench-issue-N`、`integration_base=develop`，禁止把 bench 基线误作 PR `--base`。

## GitCode 分工

- **仅 Leader** 执行 `skills/gitcode-repo` 脚本
- **禁止** 在子 agent 派发中要求调 API、跑 gitcode 脚本或读令牌
- 下游需要 Issue 正文 → Leader 先拉取，给**纯文本**或写入 `doc/<module>/` 引用段

## 监督检查点

- **范围**：产出覆盖用户/Issue 验收点
- **可追溯**：设计↔需求；代码/测试↔ plan checklist
- **质量闸**：MUST-FIX/**本轮必改** SF 须重派闭环（见 [rework.md](rework.md)），Leader 不代改代码
- **层级对齐**：根因层级 ↔ 设计深度 ↔ 任务顺序 ↔ diff 位置 ↔ 测试证据需一致；不一致默认 REWORK

## G7a Git 收口

**时机**：仅 G6 PASS。**禁止** G0–G6 创建/推送特性分支；子 agent **不得** `checkout -b` / `commit` / `push`。

G6 PASS 后、暂存前：

0. 删除 `doc/<module>/review/`，保留 `review.md`；删除 `doc/<module>/dispatch/`
1. 对照 G6 `DIFF_SCOPE` 与 `review.md` 变更列表
2. `git status`：子 agent 本地 commit → **G7a FAIL**
3. scope 外改动 → 清理或 user-interact 登记
4. `git diff --cached --name-only` 不得含 `review/`、`dispatch/` 路径

**Leader 在 `repo-root`**：

1. 确认无 skill 临时文件误落盘
2. 跨 shell/路径场景优先 `git -C "<repo-root>" ...`（避免 cwd 漂移）
3. `git fetch <fork.remote> <branch_base>`
4. checkout 可能冲突时：`git stash push -u -m aidlc-g7a`
5. `git checkout -b <head> <fork.remote_name>/<branch_base>`；禁 merge/rebase
6. 有 stash 则 `git stash pop`；冲突未解 → **G7a FAIL**
7. **显式** `git add`：doc 链五文件 + `review.md` 列出的代码/测试路径；禁 `git add -A`、禁整目录 `doc/<module>/`
8. Conventional Commits 提交；无待提交变更 → **G7a FAIL**
9. `git push -u <fork.remote> <head>`
10. `git rev-list --count <branch_base>..HEAD` 须 **> 0**；first-parent 无 merge commit

### G7a 失败与恢复

| 情况 | 处理 |
|------|------|
| checkout / stash pop 冲突 | 中止；清理或 user-interact；未解不 commit |
| 子 agent 违规 commit | G7a FAIL；`reset --soft <base>` 或 user-interact `git-danger` |
| push 失败 | 保留分支；修 network 后重试 |
| 无待提交 / commit 数为 0 | 勿 push；退回 coder/tester |
| G7a PASS 后打包错误 | 禁止 G7b；修正后重派 reviewer |

## G7b PR 收口

- [ ] G7a PASS
- [ ] `integration_guard.py` exit 0；`--base` = `integration_base`、`--branch-base` = `branch_base`；**禁**省略 `--base`
- [ ] 按 `skills/gitcode-repo/SKILL.md`；脚本首次用或换子命令时 `--help`
- [ ] 从 `pr_template.md` 生成 `pr-body.md`，填入 `review.md` 摘要
- [ ] `--create` 成功或 `--dry-run` 后 **删除** `pr-body.md`；业务仓无临时文件
- [ ] 跨 upstream MR：`--list`/`--create` 加 `--target-project upstream`
- [ ] 创建前 `pr_creator.py --list --head <branch>` 查重
- [ ] 无 token/网络或 `skip-pr` → 降级 CLI + 草稿正文，**不捏造**已创建 PR

### G7b 失败与恢复

| 情况 | 处理 |
|------|------|
| `integration_guard` FAIL | 禁止建 PR；在 `branch_base` 工作区修 scope；重走 G6；可用 `git push --force-with-lease` 更新 fork 特性分支 |
| `pr_creator` FAIL | 保留 G7a 分支；修 token/network 重试；仍失败则降级 CLI 草稿 |
