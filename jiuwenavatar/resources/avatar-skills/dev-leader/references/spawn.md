# Claude Code 子 agent 派发

## 机制

- 每阶段 **必须** 启动 Claude Code 子 agent
- **禁止** 仅在对话里 `@dev-*` 或口头指派后由 Main 代劳
- spawn 标识 = `.claude/agents/<name>.md` frontmatter 的 `name`
- 子 agent 加载 `skills/<同名>/SKILL.md`；路径相对任务卡 **`skills_root`**，见 `skills/aidlc-common/references/skills-paths.md`
- 默认 **一阶段一 Gate**；G1–G3/G6 **一阶段一 spawn**，失败 **重派同一 `name`**，附 Gate 失败原因与路径
- **G4/G5 例外**：plan 含有效 `PG-*` 且 `partition_check` PASS → Leader 必须在同一 Gate 内并行 spawn 多个同名 worker；拟改 serial → `user-interact`；每个 prompt 必须含 Shard Contract

## Claude Code 操作

1. Main 用 Subagent 启动，agent 名与 G1–G6 表一致
2. prompt 含任务卡：`module`、`repo-root`、**`skills_root`（绝对路径）**、`branch_base`、环境、`python` 路径、Gate、输入输出路径；`skills_root` 见 `skills/aidlc-common/references/skills-paths.md`
3. 子 agent 返回后 Main **停步**，按 [gates.md](gates.md) 复核再进下一阶段
4. G1/G2 首轮无 **澄清答复** → 子 agent 须 `NEEDS_DISCUSSION` → Leader **user-interact** → 本回合结束 → 下一回合重派

## G4/G5 并行派发

按需读取 `skills/dispatch-parallel/SKILL.md` 与 `skills/dispatch-parallel/references/aidlc-pipeline.md`。仅 G4/G5 可使用本节；G1–G3/G6 仍保持单 agent。

**G4-P / G5-P（Leader 分片）**

1. 读取 `doc/<module>/dev_plan.md` 的 `## 可并行组（G4）`，或 `test_plan.md` 的 `## 可并行组（G5）`
2. 生成 `doc/<module>/dispatch/manifest.yaml`，`max_shards` 默认 3
3. 运行：

```powershell
& $PYTHON skills/dispatch-parallel/scripts/partition_check.py --module <module> --repo-root <repo-root> --phase <g4|g5>
```

4. 无 `PG-*` 或仅 1 组 → serial 单 spawn；有效 `PG-*` 且校验 PASS → 并行；拟改 serial → `user-interact`；校验失败或 touch 冲突 → `user-interact` 或返工 G3

**G4-W / G5-W（worker prompt 必含）**

```markdown
## Shard Contract

- shard_id: S1
- phase: g4
- items: [3.1, 3.2]
- touch_allow:
  - src/pkg/infra/gateway.py
- touch_forbid:
  - src/pkg/core/service.py
- worker_summary: doc/<module>/dispatch/g4-S1-summary.md
```

worker 只能修改 shard `items`、`touch_allow` 及自己的 summary；发现必须越界时停止并汇报 Leader。

**G4-I / G5-I（Leader 整合）**

1. 收齐全部 `doc/<module>/dispatch/g4-S*-summary.md` 或 `g5-S*-summary.md`
2. 写入 `doc/<module>/dispatch/g4-integration.md` 或 `g5-integration.md`：scope 外仍 `[ ]` 的 item、原因、责任 Gate
3. 运行：

```powershell
& $PYTHON skills/dispatch-parallel/scripts/partition_check.py --module <module> --repo-root <repo-root> --phase integrate
```

4. 再按 [gates.md](gates.md) 跑 G4/G5 全量 verify 与 `reviewer_plan_check.py status`
5. 全部通过后才可宣布 G4/G5 PASS

**禁止**：G4 worker 与 G5 worker 同时存在；跨 Gate 合并派发；worker 未齐或仅凭自报宣布 PASS。

## 派发完成定义

- 子 agent 落盘预期产物 → 自行跑校验，同一 `--repo-root` 与任务卡 `python`
- Leader **不得** 信任自报 PASS
- spawn 任务卡建议附 `issue_class`：`infra-bug | shell-io | agent-tool | default`

## G1/G2 用户确认

- §4.6 / §协作讨论记录 **2–3 条**；每条经 `skills/user-interact` 确认后方可 `- [x]`
- `NEEDS_DISCUSSION` → Leader **先** user-interact，本回合结束；无用户答案不得重派
- 预澄清：任务卡澄清答复覆盖全部 Q-xxx 且 `source` 合法 → 首轮可定稿
- **禁止** Leader 改讨论项或代填澄清答复（细则 → `dev-leader` SKILL §G1/G2 澄清禁令）

### G1/G2 派发禁语

- 默认定案
- 无需讨论
- 无歧义可直接定稿
- 建议默认即采用
- Leader 已定案
- 采纳建议默认

## Skill 路径（G0 锁定）

- G0 按 `skills/aidlc-common/references/skills-paths.md` 解析 `skills_root` 并写入任务卡（与 `repo-root` 可分离）。
- 业务仓无 `<repo-root>/skills/` 时：**禁止**假定宿主全局 skills 目录对子 agent 自动可见（各平台路径见 `skills/aidlc-common/references/skills-paths.md` §Platform）；须在任务卡给出 `skills_root` 或把 `skills/` 装入业务仓。
- Gate / 脚本：`$PYTHON skills/dev-*/scripts/... --repo-root {repo-root}`，相对 `skills_root`。

## 环境

G0 锁定环境；spawn prompt **必须**含任务卡 `python` / `node_root` / `pm`。

## 读 skill 的边界

Main 读 dev-leader 与各 skill **仅用于** 派工文案、Gate 校验、PR 收口 — **不用于** 代执行 G1–G6。
