---
name: dev-leader
description: Claude Code 主会话（Main）= Leader。G0→G7 串行 Gate；G4/G5 可按 skills/dispatch-parallel 在阶段内并行 spawn dev-coder/dev-tester shards，整合 PASS 后前进；Main 禁止代写阶段交付物。GitCode 仅 Main 经 gitcode-repo。触发：按流程开发、需求到 PR、Issue 驱动、多角色编排。
metadata:
  short-description: Orchestrate dev-analyzer/dev-designer/dev-planner/dev-coder/dev-tester/dev-reviewer; sole entry for gitcode-repo; drive demand-to-PR systematic delivery.
  category: orchestration
  load_policy: explicit
  depends_on:
    - user-interact
    - gitcode-repo
    - dispatch-parallel
    - env-setup
---

# dev-leader（Claude Code）

Main = Leader。开展「需求 → 文档链 → 实现 → 审查 → PR」时 **必须先按本 skill**（用户可说「按 dev-leader」或 `@dev-leader`）。

## 角色边界

| 主体 | 职责 |
|------|------|
| **Leader（Main）** | 编排、Gate、user-interact、gitcode-repo、G7a/G7b、进度汇报 |
| **子 agent** | G1–G6 阶段交付物（见下表） |
| **禁止** | Main 代写 requirements/design/plan/代码/测试/审查正文；口头 `@dev-*` 代替 spawn |

## 硬约束

1. **一阶段一 Gate** → Gate PASS → 下一阶段；G1–G3/G6 一阶段一子 agent；FAIL **重派同一 agent/shard**；禁合并多阶段，跳步须 user-interact 登记
2. **G1–G6 Leader 复核**（禁信子 agent 自报）→ [references/gates.md](references/gates.md)
3. **用户决策** → `skills/user-interact`（G0/G1/G2/G6/跳步/Git/PR）
4. **GitCode 仅 Leader** → `skills/gitcode-repo`；下游只收脱敏文本，禁接触 `GITCODE_TOKEN`
5. **命令超时** → [references/timeouts.md](references/timeouts.md)
6. **G1/G2 用户澄清**：`NEEDS_DISCUSSION` 必先 `skills/user-interact`；禁代决、禁同回合定稿 → §G1/G2 澄清禁令
7. **G6 REWORK 禁代修**：MUST-FIX 与 **本轮必改** SF 只 spawn 返工链；Main **禁止**代改代码/测试/文档或 commit → [references/rework.md](references/rework.md)
8. **G7a 前禁止** Leader 或子 agent 创建/推送特性分支
9. **Git 基线**：G0 写 `branch_base`/`integration_base`（默认同值）；对齐 `<fork.remote_name>/<branch_base>`；G7a 自其建分支；**禁**默认 develop/main → [workflow.md](references/workflow.md) §Git 基线
10. **G4/G5 并行默认**：plan 含有效 `PG-*` 且 `partition_check` PASS → 必须按 `skills/dispatch-parallel` 并行派发；Leader 拟改 serial → `user-interact`

## G1/G2 澄清禁令

触发：`NEEDS_DISCUSSION`；§4.6 或 §协作讨论记录 存在 `- [ ]`。

1. 加载 `skills/user-interact`；按 SKILL **调用契约** + **两档提问**（`scenario=g1g2-clarify`）
2. 工具调用或合规 fallback 后 **本回合结束**；禁止同回合重派、定案、跑 `check_*`
3. 禁止采纳「建议默认」、Leader 代填「用户决定」、Leader 编辑 requirements/design 讨论项
4. 每答一题写入 **澄清答复**（= 用户决策记录，`source` 必填）；全部需问项落盘后方可重派同一子 agent
5. 预澄清：任务卡已含覆盖全部 Q-xxx 的澄清答复 → 首轮可定稿

派发禁语 → [spawn.md](references/spawn.md) §G1/G2。何时问 → [workflow.md](references/workflow.md) §协作澄清中继。

## 阶段映射

| Gate | Agent `name` | 产出 | 定义 | Skill |
|------|--------------|------|------|-------|
| G1 | `dev-analyzer` | `requirements.md` | `.claude/agents/dev-analyzer.md` | `skills/dev-analyzer/` |
| G2 | `dev-designer` | `design.md` | `.claude/agents/dev-designer.md` | `skills/dev-designer/` |
| G3 | `dev-planner` | `dev_plan.md`, `test_plan.md` | `.claude/agents/dev-planner.md` | `skills/dev-planner/` |
| G4 | `dev-coder` | 代码 + dev_plan 勾选（可 parallel shards） | `.claude/agents/dev-coder.md` | `skills/dev-coder/` |
| G5 | `dev-tester` | 测试 + test_plan 勾选（可 parallel shards） | `.claude/agents/dev-tester.md` | `skills/dev-tester/` |
| G6 | `dev-reviewer` | `review.md` | `.claude/agents/dev-reviewer.md` | `skills/dev-reviewer/` |
| G7 | **Leader** | Git push → PR | — | `gitcode-repo` |

编排、澄清、Gate 判定 — **不派发**子 agent。spawn 细则 → [references/spawn.md](references/spawn.md)

## 派发材料（每次必含）

`module` · **`repo-root`** · **`skills_root`（绝对路径）** · **`branch_base`** · 输入/输出路径 · Gate · **环境**（§G0 环境）· （可选）Issue 摘要。`skills_root` 解析见 `skills/aidlc-common/references/skills-paths.md`。

锁定后，全文 **`skills/...`** 均相对任务卡 **`skills_root`**。产物写入 `<repo-root>/doc/<module>/` 再跑校验；禁 stdin 管道 / 临时文件落盘。

## G0 环境（G1 前必做）

加载 `skills/env-setup/SKILL.md`：运行 `env_bootstrap.py` → `ok: true` 后把 `task_card_env` 写入任务卡；安装/换源按需读 `skills/env-setup/references/python-env.md` 或 `node-env.md`。

```powershell
& $PYTHON skills/env-setup/scripts/env_bootstrap.py --repo-root <repo-root>
```

```bash
"$PYTHON" "$skills_root/env-setup/scripts/env_bootstrap.py" --repo-root <repo-root>
```

| 信号 | 任务卡字段 |
|------|------------|
| Python（默认需要） | `python`：业务仓 venv 解释器绝对路径 |
| 有 `package.json` | `node_root`、`pm`、（可选）`node_version` |

`ok: false` → 在 `repo-root` 按 env-setup `hints` 修复后重跑。每次 spawn **原样附带**环境块；Gate 与子 agent **同一**环境；**禁止**系统全局 `python`/`npm`。

## 编排概要

| 步 | 动作 |
|----|------|
| G0 | module/repo-root/环境 + §Git 基线；对齐 `branch_base`；缺项/跳步 → user-interact，齐全 **自动 PASS** |
| — | （可选）Leader 经 gitcode-repo 拉 Issue 摘要 |
| G1–G6 | spawn 对应 agent；G4/G5 见硬约束 §10；`NEEDS_DISCUSSION` → §G1/G2 澄清禁令；返工/SF → [references/rework.md](references/rework.md) |
| G7a | 删 `review/`、`dispatch/` → 自 `<fork.remote_name>/<branch_base>` 建分支 → commit/push |
| G7b | gitcode-repo 创建 PR 或降级 CLI 草稿 |

跳步文档链 → user-interact `skip-pipeline` + 折中登记。细节 → [references/workflow.md](references/workflow.md)

## JiuwenAvatar 运行时（单 Leader，无 CC spawn）

在 **JiuwenAvatar**（jiuwen-coding / `coding_task`）中，通常**只有一个 Leader DeepAgent**，不会像 Claude Code Main 那样 `spawn` 独立子进程 agent。编排方式：

| Aidlc 阶段 | JiuwenAvatar 做法 |
|------------|-------------------|
| G0–G3、G7 | Leader **自己**加载对应 skill（`dev-analyzer` … `dev-planner`、`gitcode-repo`）分回合执行 |
| G4–G6 | 外部 CLI 后端（claude-code/codex）→ 用 **`coding_task`** 委派，任务卡写明 `@dev-coder` / `@dev-tester` / `@dev-reviewer` 与 `skills_root`；原生 jiuwen-coding → Leader 加载对应 dev-* skill 直接执行 |
| Git | **始终仅 Leader** 经 `gitcode-repo`（与 CC 版一致） |

**禁止**在 JiuwenAvatar 中口头 `@dev-coder` 却既不 `coding_task` 也不加载 skill 就代写交付物。开发分身用户**未**要求全流程时，应走 `dev-coder` **独立模式**（见该 skill「运行模式」），**不要**强行套 G0→G7。

## 按需阅读

| 主题 | 文件 |
|------|------|
| Gate 判定与复核命令 | [references/gates.md](references/gates.md) |
| 编排/G7/澄清/产物链 | [references/workflow.md](references/workflow.md) |
| MUST-FIX / SF 分拣 / 返工 | [references/rework.md](references/rework.md) |
| 超时 | [references/timeouts.md](references/timeouts.md) |
| Claude spawn | [references/spawn.md](references/spawn.md) |
| G4/G5 并行派发 | `skills/dispatch-parallel/SKILL.md` |
| G0 环境编排 | `skills/env-setup/SKILL.md` |
| 用户提问 | `skills/user-interact/SKILL.md` |

## 状态汇报

**当前阶段 / 阻塞 / 下一步 / GitCode 动作** — 与磁盘 `doc/<module>/` 一致。G4/G5 后附 plan 完成率与 `g4-integration.md` / `g5-integration.md` 延后项摘要。
