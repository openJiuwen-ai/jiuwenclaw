# Gate 判定与 Leader 复核

子 agent 返回后、宣布 PASS 前，Leader **必须**对磁盘与脚本证据复核（`--repo-root`、`--module` 与派发一致）。**禁止**仅信任子 agent 回报。

**执行规范**：子 agent **直接写入** `<repo-root>/doc/<module>/` 再校验；**禁止**临时文件落盘、stdin 管道、多行 `python -c` 内联。细则见各 `skills/dev-*/SKILL.md`「脚本执行」节。

**$PYTHON**：G0 任务卡 venv 解释器。Leader 与子 agent 跑脚本均用该路径；**禁止**系统全局 `python`。

**`skills_root`**：G0 任务卡绝对路径；下表 `skills/...` 相对该目录，见 `skills/aidlc-common/references/skills-paths.md`。

## 命令执行（跨 shell）

- 约定：`$PYTHON` = 任务卡 venv；`$SKILLS` = 任务卡 `skills_root` 绝对路径。
- 分裂布局（`skills_root ≠ repo-root`）时，优先使用 `"$SKILLS/dev-*/scripts/..."` 绝对脚本路径；避免依赖当前目录。
- PowerShell 示例保留 `& $PYTHON ...`；bash 示例使用 `"$PYTHON" ...`。
- Gate 命令模板与排障见 `skills/aidlc-common/references/skills-paths.md`（Gate example / Troubleshooting）。

## Gate 表

| Gate | 执行者 | PASS 条件（磁盘） |
|------|--------|-------------------|
| G0 | **Leader** | `module`、`repo-root`、`branch_base`/`integration_base`、环境；已对齐 `<fork.remote_name>/<branch_base>`；齐全 **自动 PASS** |
| G1 | `dev-analyzer` | `requirements.md` 落盘；任务卡 **澄清答复** 完整且每行含 `source`；协作澄清 PASS；Leader 重跑 `check_requirements.py` **exit 0**；语义复核含根因层级/机制 AC |
| G2 | `dev-designer` | `design.md` 落盘；任务卡 **澄清答复** 完整且每行含 `source`；协作澄清 PASS；Leader 重跑 `check_design.py` **exit 0**；语义复核含机制设计与文件清单 |
| G3 | `dev-planner` | `dev_plan.md`、`test_plan.md` 落盘；Leader 重跑 `check_plan.py` dev/test **各 exit 0**（任务区 checklist 均为 `[ ]`）；并复核基础设施任务顺序/机制测试块；若声明 `PG-*`，须与 design 文件清单/任务 touch 范围一致 |
| G4 | `dev-coder` | 派发 scope：manifest 或 serial prompt 所列 dev_plan items 均已 `[x]`；plan 其余 `[ ]` 不阻 G4。并行须 G4-I：shard summary、`doc/<module>/dispatch/g4-integration.md`、`partition_check.py --phase integrate` exit 0；`coder_plan_check.py verify` exit 0；`reviewer_plan_check.py status --plan dev` 与证据一致 |
| G5 | `dev-tester` | 派发 scope：manifest 或 serial prompt 所列 test_plan items 均已 `[x]`；plan 其余 `[ ]` 不阻 G5。并行须 G5-I：shard summary、`doc/<module>/dispatch/g5-integration.md`、`partition_check.py --phase integrate` exit 0；`tester_plan_check.py verify` exit 0；`reviewer_plan_check.py status --plan test` 与证据一致 |
| G6 | `dev-reviewer` | `review.md` 落盘；Leader 按 §G6 复核（`report` exit 0、MUST-FIX/**本轮必改** SF 闭环或用户接受、SF 分拣完成） |
| G7a | **Leader** | 删 `review/`、`dispatch/` 临时目录，保留 `review.md`；从 `branch_base` 新建分支；scope 内代码 + doc 链五文件 + `review.md`，不含 `review/`、`dispatch/`，commit 并 push fork；first-parent 无 merge；`git rev-list --count <branch_base>..HEAD` **> 0** |
| G7b | **Leader** + gitcode-repo | **`integration_guard.py` exit 0**；PR 已创建或已输出降级命令；临时 `pr-body.md` 已删除 |

**FAIL**：不得进下一 Gate；**重派同一**子 agent `name`，附失败原因。Leader **禁止**为通过 Gate 在本回合写入 G1–G6 产物。

子 agent 定义：`.claude/agents/dev-analyzer.md` … `dev-reviewer.md`

## G1–G5 复核命令

**G1**（从 `requirements.md` 读 `本次分析类型：<Type>` 作 `--type`）：

```powershell
& $PYTHON skills/dev-analyzer/scripts/check_requirements.py --module <module> --type <Bug|Feature|Refactor|Docs> --repo-root <repo-root>
```

```bash
"$PYTHON" "$SKILLS/dev-analyzer/scripts/check_requirements.py" --module <module> --type <Bug|Feature|Refactor|Docs> --repo-root <repo-root>
```

**G2**：

```powershell
& $PYTHON skills/dev-designer/scripts/check_design.py --module <module> --repo-root <repo-root>
```

```bash
"$PYTHON" "$SKILLS/dev-designer/scripts/check_design.py" --module <module> --repo-root <repo-root>
```

**G3**（dev、test 各一次）：

```powershell
& $PYTHON skills/dev-planner/scripts/check_plan.py --module <module> --plan dev --repo-root <repo-root>
& $PYTHON skills/dev-planner/scripts/check_plan.py --module <module> --plan test --repo-root <repo-root>
```

```bash
"$PYTHON" "$SKILLS/dev-planner/scripts/check_plan.py" --module <module> --plan dev --repo-root <repo-root>
"$PYTHON" "$SKILLS/dev-planner/scripts/check_plan.py" --module <module> --plan test --repo-root <repo-root>
```

**G4**：

```powershell
& $PYTHON skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> verify
& $PYTHON skills/dev-reviewer/scripts/reviewer_plan_check.py --module <module> --repo-root <repo-root> status --plan dev --format json
```

```bash
"$PYTHON" "$SKILLS/dev-coder/scripts/coder_plan_check.py" --module <module> --repo-root <repo-root> verify
"$PYTHON" "$SKILLS/dev-reviewer/scripts/reviewer_plan_check.py" --module <module> --repo-root <repo-root> status --plan dev --format json
```

**G5**：

```powershell
& $PYTHON skills/dev-tester/scripts/tester_plan_check.py --module <module> --repo-root <repo-root> verify
& $PYTHON skills/dev-reviewer/scripts/reviewer_plan_check.py --module <module> --repo-root <repo-root> status --plan test --format json
```

```bash
"$PYTHON" "$SKILLS/dev-tester/scripts/tester_plan_check.py" --module <module> --repo-root <repo-root> verify
"$PYTHON" "$SKILLS/dev-reviewer/scripts/reviewer_plan_check.py" --module <module> --repo-root <repo-root> status --plan test --format json
```

G1–G3 须 exit 0 且 stdout 含 `[OK] Validated`。复核失败 → Gate **FAIL**。

### G1–G3 语义复核

- **G1/G2 协作澄清**：任务卡含 **澄清答复** 且每行 `source` 为 AskQuestion 或 chat-fallback；§4.6 / §协作讨论记录 无 `- [ ]`；「用户决定」不得与「建议默认」逐字相同；本 Gate 未见 user-interact 提问 → **FAIL**
- **G1**：`requirements.md` 需出现根因层级 L0/L1/L2/L3 或明确不涉及；Bug 类 AC 至少一条机制行为或显式「不涉及机制 AC」
- **G2**：若 G1 指向 L2/L3，`design.md` 需包含 `## 机制设计（按需）` 且非占位；`## 实现注意事项` 含文件修改清单
- **G3**：基础设施相关问题中，`dev_plan` 不得把 Tool/API 层任务排在基础设施层前；`test_plan` 需含机制测试块。
- **G3 PG-\***：`## 可并行组（G4/G5）` 可为 `无（serial）`；若声明 `PG-*`，Leader 须确认 `items` 存在、`touch` 非空且组间无交集。校验可参考 `skills/dispatch-parallel/scripts/partition_check.py --phase g4/g5`。

若报「缺少必需章节」但标题可见：检查 **UTF-8 无 BOM**（PS5 `Set-Content -Encoding utf8` 会写 BOM）。

## G6 Leader 复核

**G6 REWORK**：未关闭 MUST-FIX / **本轮必改** SF，或 `report` exit 1 → **只改派**，Leader **禁止**代修（见 [rework.md](rework.md)「G6 REWORK 禁令」）。

`dev-reviewer` 返回后、宣布 G6 PASS 或进 G7a **之前**，Leader **必须**按序完成：

1. **落盘**：确认 `<repo-root>/doc/<module>/review.md` 存在；返工轮核对 `doc/<module>/review/` 与派发 `DIFF_SCOPE`、`REWORK_ROUND` 一致。
2. **result.json**：读取 `doc/<module>/review/result.json`，核对 `gate_verdict`、`verdict`、Must Fix 及关闭状态、Should Fix（含 `leader_escalate`）。
3. **报告渲染**（Leader 重跑，与子 agent 同一 `--module` / `--repo-root`）：

```powershell
& $PYTHON skills/dev-reviewer/scripts/code_review_runner.py report --module <module> --repo-root <repo-root>
```

```bash
"$PYTHON" "$SKILLS/dev-reviewer/scripts/code_review_runner.py" report --module <module> --repo-root <repo-root>
```

须 **exit 0**（`verdict=PASS` 且 `gate_verdict=PASS`）。exit 1 → Gate **FAIL**，重派 `dev-reviewer` 或走 [rework.md](rework.md) 返工。

4. **MUST-FIX / 本轮必改 SF**：逐条核对已关闭；未关闭且不接受残留 → **FAIL**。用户接受残留 → 经 `user-interact` `g6-risk`，逐条写入「折中与风险登记」。
5. **SF 分拣**：对全部 Should Fix 完成分拣（**本轮必改** / **延期** / **接受**）；**本轮必改** 须已关闭或用户接受残留。细则见 [rework.md](rework.md)「SHOULD-FIX 分拣」。
6. **取证（可选）**：`reviewer_plan_check.py status --plan both`；必要时 `layer_alignment_check.py`（命令见下节）。

全部通过后 → G6 **PASS**。

## G7b 合入校验

G7b **`--create` 前**（或 `pr_creator.py --create` 默认内建）须 exit 0：

```powershell
& $PYTHON skills/gitcode-repo/scripts/integration_guard.py `
  --repo-root <repo-root> `
  --head <特性分支> `
  --base <integration_base> `
  --branch-base <branch_base> `
  --module <module>
```

```bash
"$PYTHON" "$SKILLS/gitcode-repo/scripts/integration_guard.py" \
  --repo-root <repo-root> \
  --head <特性分支> \
  --base <integration_base> \
  --branch-base <branch_base> \
  --module <module>
```

FAIL → 禁止建 PR；常见原因：first-parent 上存在 merge commit、夹带非本次 commit、PR diff 超出 `review.md` scope。恢复见 [workflow.md](workflow.md) §G7b 失败与恢复。

`--base` = `integration_base`；`--branch-base` = `branch_base`。bench 勿把 `bench-issue-N` 当作 PR `--base`。

## Plan scope

- G3：任务区初始均为 `[ ]`，表示全生命周期清单，不等于本轮回须全勾。
- G4 scope：派发 `items` 或 manifest 各 shard `items`，scope 内须全 `[x]`。
- G5 scope：同上，针对 test_plan。
- scope 外 `[ ]` 不阻 G4/G5；Leader 在 `g4-integration.md` / `g5-integration.md` 登记原因与责任 Gate。
- PG 所列 item 不得在 scope 内仍为 `[ ]` 而 PASS。
- G6：`leader_escalate: true` 且 plan 对应项仍 `[ ]` → 须 user-interact 延期或返工，禁静默 PASS。

## Plan checklist 补充（G4/G5/G6）

**禁止**用 `dev-planner` 的 `check_plan.py` 代替 G4/G5 复核。

```powershell
# G6 / 返工取证（只读）
& $PYTHON skills/dev-reviewer/scripts/reviewer_plan_check.py --module <module> --repo-root <repo-root> status --plan both --format json

# 子 agent 自查（不能替代 Leader G4/G5）
& $PYTHON skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> status
& $PYTHON skills/dev-tester/scripts/tester_plan_check.py --module <module> --repo-root <repo-root> status

# 可选层级对齐复核（G4/G6）
& $PYTHON skills/dev-reviewer/scripts/layer_alignment_check.py --module <module> --repo-root <repo-root>

# 父项先于子项（须 Leader 书面批准 + Gate Evidence 登记）
& $PYTHON skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> verify --allow-parent
& $PYTHON skills/dev-tester/scripts/tester_plan_check.py --module <module> --repo-root <repo-root> verify --allow-parent
```

```bash
# G6 / 返工取证（只读）
"$PYTHON" "$SKILLS/dev-reviewer/scripts/reviewer_plan_check.py" --module <module> --repo-root <repo-root> status --plan both --format json

# 子 agent 自查（不能替代 Leader G4/G5）
"$PYTHON" "$SKILLS/dev-coder/scripts/coder_plan_check.py" --module <module> --repo-root <repo-root> status
"$PYTHON" "$SKILLS/dev-tester/scripts/tester_plan_check.py" --module <module> --repo-root <repo-root> status

# 可选层级对齐复核（G4/G6）
"$PYTHON" "$SKILLS/dev-reviewer/scripts/layer_alignment_check.py" --module <module> --repo-root <repo-root>

# 父项先于子项（须 Leader 书面批准 + Gate Evidence 登记）
"$PYTHON" "$SKILLS/dev-coder/scripts/coder_plan_check.py" --module <module> --repo-root <repo-root> verify --allow-parent
"$PYTHON" "$SKILLS/dev-tester/scripts/tester_plan_check.py" --module <module> --repo-root <repo-root> verify --allow-parent
```

- coder/tester **每完成任务就编辑** plan（`[ ]` → `[x]`），不用 `set`
- Leader 只查询与判 Gate，**不直接修改** coder/tester 的 checklist
- 默认禁止父 `[x]` 子 `[ ]`；父项例外须 Leader 批准 → agent 收尾 `verify --allow-parent`
