---
name: dev-tester
description: 统一测试技能（dev-tester）：按 doc/<module>/test_plan.md 补测/执行/勾选（module 模式），或对 PR+Issue 跑聚焦单元测试门禁（pr-gate 模式，scripts/pr_unit_test_runner.py）。触发词包括写测试、补测试、跑测试、回归、test_plan、PR 单元测试验证。Aidlc 流水线子 agent 角色 id 为 tester；通用规范见 references/principles.md。
metadata:
  short-description: Skill dev-tester; module test_plan or PR unit-test gate; mark checklist when verified.
  category: pipeline
  load_policy: on-spawn
  depends_on:
    - aidlc-common
    - env-setup
  gates:
    - G5
  agent_id: dev-tester
---

# 测试（dev-tester）

先选运行模式，再只读对应 reference。共用原则见 [references/principles.md](references/principles.md)；分层机制校验见 [skills/aidlc-common/references/layer-alignment.md](../../aidlc-common/references/layer-alignment.md)；**任务前仓库对齐**见 [skills/aidlc-common/references/repo-workspace-sync.md](../../aidlc-common/references/repo-workspace-sync.md)；Python 环境见 `skills/env-setup/references/python-env.md`；Node.js 环境见 `skills/env-setup/references/node-env.md`。

## 本地仓库对齐（任务前必做）

跑测试或 `pr_unit_test_runner.py` **之前**，按 [repo-workspace-sync.md](../../aidlc-common/references/repo-workspace-sync.md) 核对 `repo-root` 是否在期望分支/commit（PR gate 对齐 PR **head**；module 模式对齐任务卡 **`branch_base`**）。

## 计划进度（必守，module 模式）

计划 checklist 有三种操作，**分工明确、不可混用**：

| 操作 | 方式 | 时机 |
|------|------|------|
| **查询** | Read `doc/<module>/test_plan.md`，或运行 `status` | 开始前了解待办；需要结构化摘要时 |
| **勾选** | 编辑 `test_plan.md`：`[ ]`→`[x]`；子项全勾后勾父项 | 每完成一条已验证任务 |
| **校验** | 运行 `verify` | **全部相关勾选更新完成后，收尾一次性**运行 |

```powershell
# 查询（可选；Read md 亦可）
& <python> skills/dev-tester/scripts/tester_plan_check.py --module <module> --repo-root <repo-root> status

# 收尾校验（必做）
& <python> skills/dev-tester/scripts/tester_plan_check.py --module <module> --repo-root <repo-root> verify
```

- **禁止** agent 调用 `set`；**禁止**用 `status`/`verify` 代替编辑 plan；**禁止**每勾一项就 bash 一次脚本。
- 调用 `status`/`verify`：**直接执行脚本**，用任务卡 `python` 绝对路径；禁止 stdin 管道与 `python -c "…"`。
- **父子联动（双向）**：子项未全 `[x]` 则父项不得 `[x]`；子项全 `[x]` 则父项须 `[x]`。`--allow-parent` 仅豁免前者（Leader 书面批准）。
- `verify` 只校验格式与父子一致性，**不能**单独证明 scope 完成；汇报须含可复现验证命令与退出码/摘要；G5 须 Leader 重跑 `verify` + `reviewer_plan_check.py status --plan test`。
- **pr-gate 模式**：仍直接执行 `scripts/pr_unit_test_runner.py`（带 CLI 参数），禁止 stdin 管道与多行 `python -c "…"`。

## 命令超时（必守）

**双层、禁止无限挂起**：Agent 外层等待 **≥** 命令/脚本超时。

| 场景 | 外层上限 |
|------|----------|
| `tester_plan_check.py status\|verify` | 60s |
| build / test / lint（module 模式） | 120–300s（更重告知 Leader） |
| `pr_unit_test_runner.py` | `--timeout` 默认 120s；外层 **≥** 该值 |

**命令层**（bash/cmd/PowerShell；测试/构建必设）：`timeout <秒> <cmd>`，或工具/框架 `--timeout`（如 `pytest --timeout=120`）。禁止裸长跑。

**pr-gate**：`unit_test_plan.json` 的 `command` 亦须自带超时；runner `--timeout` 仅兜底。

超时不得勾选 `test_plan.md`；须汇报退出码/摘要。

## 前置条件

**共用**

1. 已明确运行模式（见下方「选择模式」）；无法判断时先向用户或 Leader 确认
2. 在 Aidlc 流水线中：仅在被 Leader 派发后执行；可与 `coder`（`skills/dev-coder`）直接对齐复现与预期，但门禁结论、豁免与 GitCode 操作须汇报 Leader；不得自行运行 `skills/gitcode-repo` 脚本；**不得**创建特性分支、`git commit`、`git push`（由 Leader 在 G7a 统一打包）

**module 模式**

3. 已确认 `module`，且 `doc/<module>/test_plan.md` 存在且可读
4. 目标仓库已 clone，测试命令、依赖与环境可按仓库约定执行（或已说明无法自动执行时的手工步骤）
5. **Python 环境**：见 `skills/env-setup/references/python-env.md`。
6. **Node.js 环境**：见 `skills/env-setup/references/node-env.md`。
7. 若 Leader 派发中包含 **Shard Contract**，必须先阅读 `skills/dispatch-parallel/references/aidlc-pipeline.md`，并按 shard 范围执行

**pr-gate 模式**

3. 已具备 PR 与 Issue 的可审查上下文（链接或 Leader 提供的正文快照）
4. 可在本 skill 根目录（`skills/dev-tester/`）执行 `scripts/pr_unit_test_runner.py`；须满足上文 Python/Node 环境要求（pr-gate 以 **LOCAL_REPO** 为准）

## Skill 定位

Aidlc 流水线中方括号为 **agent 简称**；本 skill 路径为 `skills/dev-tester`，派发标识使用 **`dev-tester`**。

```
… → [coder] → 代码 → [tester] → test_plan 勾选 / PR 测试证据 → [reviewer]
                      ▲
                本阶段（agent tester）
```

- **上游**：`test_plan.md`（module，由 `skills/dev-planner` 按 `test_plan_template.md` 生成；结构门禁为 `check_plan.py --plan test`；本阶段**只勾选** `[ ]`→`[x]`，不重排章节或顶层分类）、或 Leader 提供的 PR/Issue 快照（pr-gate）
- **下游**：`reviewer`；门禁与范围问题经 Leader

## Shard 模式（G5 并行派发）

当 Leader prompt 含 `## Shard Contract` 时，本阶段只处理该 shard：

- 只实现/执行 `items` 中列出的 `test_plan.md` checklist 项，并只勾选这些项
- 只修改 `touch_allow` 中的测试、夹具或测试配置文件，以及自己的 `worker_summary`
- 不得修改 `touch_forbid` 或其它 shard 的文件；若必须越界，停止并向 Leader 汇报
- Aidlc G5 仍使用 module 模式；不要改用 pr-gate，除非 Leader 明确派发
- 收尾写入 `doc/<module>/dispatch/g5-<shard_id>-summary.md`（若 Leader 指定 `worker_summary`，以指定路径为准）

summary 须含 Done、Deferred in shard、Out of shard。shard `items` 须全 `[x]`；阻塞则勿勾并 FAIL shard。Shard 自查不能替代 Leader G5-I。

## 选择模式

| 模式 | 何时使用 | Reference |
|------|----------|-----------|
| **module** | 有 `doc/<module>/test_plan.md`，或 Aidlc 测试阶段 | [references/module-test.md](references/module-test.md) |
| **pr-gate** | 有 PR + Issue，要用单元测试证据给出 PASS/FAIL | [references/pr-unit-test-gate.md](references/pr-unit-test-gate.md) |

无法判断时：有 `test_plan.md` → **module**；用户给 PR/Issue 验改动 → **pr-gate**。**Aidlc 流水线 G5 必须 module 模式**；pr-gate 仅 Leader 显式派发时执行。独立任务两者都有且 Leader 未指定时优先 **module**，并在结果中说明是否还需 PR gate。

**pr-gate**：将工作目录切换到 `skills/dev-tester/` 后执行 `scripts/pr_unit_test_runner.py`（细则见 [references/pr-unit-test-gate.md](references/pr-unit-test-gate.md)）。

module 模式 checklist 细则见 [references/module-test.md](references/module-test.md)。

## 共用原则（摘要）

完整条文见 [references/principles.md](references/principles.md)。要点：

- 测试前先想清楚：不掩饰不确定性；会改变验收或豁免结论的歧义回报 Leader，局部测试实现细节按仓库惯例定案。
- 简单优先：最少测试满足验证目标；跟随仓库既有写法。
- 外科手术式修改：只动必需文件。
- 目标可验证：能跑则跑，跑不了则说明缺口与手工步骤；Python 测试须在虚拟环境中执行。
- **严格禁止**修改 Python 虚拟环境、`site-packages` 或 `node_modules` 内第三方包源码；见 `skills/env-setup/references/python-env.md`、`skills/env-setup/references/node-env.md`。

## 与用户沟通

- 大范围改动前简述打算查什么、改什么。
- 最终回复侧重结果、验证方式与残余风险。
- 除非用户要求，不粘贴超长 diff 或整段日志；引用路径与关键命令即可。

## 收尾自检

- 是否走了正确的模式 reference？
- **module**：`test_plan.md` 勾选是否与证据一致？**每完成对应任务是否已编辑 checklist**？收尾是否运行 `tester_plan_check.py verify` 并通过？
- **pr-gate**：`report.md` 结论是否明确？若兼做 module，是否已映射 Evidence？
- 是否避免泄露密钥与无关改动？
