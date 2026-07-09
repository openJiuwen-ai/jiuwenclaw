---
name: dev-coder
description: 指导 Agent 根据 `doc/<module>/dev_plan.md`（或独立模式下的 Issue/用户目标）进行编码开发：先理解仓库与计划，再做最小可验证改动；完成对应任务后更新 checklist 或经 gitcode-repo 交付 PR。Aidlc G4 或用户/触发器直接要求实现、排障、重构时使用；测试交给 dev-tester，审查交给 dev-reviewer；详见「运行模式」。
metadata:
  short-description: Develop from doc/<module>/dev_plan.md, verify changes, and mark completed checklist tasks.
  category: pipeline
  load_policy: on-spawn
  depends_on:
    - aidlc-common
    - env-setup
  gates:
    - G4
  agent_id: dev-coder
---

# 编码开发（dev-coder）

按本 skill 行事：先理解仓库和开发计划，再做能解决问题的最小改动，完成验证，并把结果说明清楚。

## 运行模式（决定谁 commit、怎么交付）

本 skill 有两种运行模式，**Git 与 PR 交付方式不同**，开工前先判定自己处于哪种：

- **团队流水线模式（Aidlc）**：被 `dev-leader` 派发执行 G4。你**只**在 `repo-root` 工作区改代码、跑验证、勾选 `dev_plan.md`；**不得**自行运行 `skills/gitcode-repo` 脚本或创建/推送特性分支、`git commit`、`git push`——由 Leader 在 **G7a/G7b** 统一打包并建 PR。门禁结论、范围变动与 Issue/MR 须汇报 Leader。
- **独立 / 自主模式（standalone）**：没有 Leader 编排——由用户直接要求、或由**定时触发器（cron / webhook）**驱动你「实现某 Issue / 改某功能 / 提交 PR」。此模式下**你必须自行完成端到端交付**：改代码 → 验证 →（按需）自测/检视 → 经 `gitcode-repo` **建分支、commit、push、建 PR**（及关联 Issue 评论）。你是调用方，不是「只改代码等 Leader 打包」的子 agent。

**判定规则**：任务经 Leader 派发（含任务卡 `Gate: G4`、`## Shard Contract` 等）→ 团队模式；否则（用户、触发器或开发分身直接驱动）→ 独立模式。

| 维度 | Aidlc 流水线 | 独立 / 自主 |
|------|--------------|-------------|
| 谁驱动 | `dev-leader` | 用户 / cron / webhook / 开发分身 |
| `dev_plan.md` | **必须**存在且按 checklist 勾选 | 有则跟 plan；无则先确认范围，小任务可最小改动后口头汇报 |
| Git / PR | **禁止**自行 commit/push | **必须**经 `gitcode-repo` 完成交付（除非用户明确只要本地 patch） |
| 测试 / 检视 | 交给 G5 `dev-tester`、G6 `dev-reviewer` | 改动非 trivial 时**建议**加载 `dev-tester` / `dev-reviewer` 自检后再提 PR |

## 前置条件

1. 已确认模块名 `module`（缺失时须先向用户或 Leader 确认；独立小任务可暂用 Issue/目录名作 module）
2. **团队模式**：`doc/<module>/dev_plan.md` 已存在且可读；缺失时提醒完成计划阶段（`skills/dev-planner`）。**独立模式**：有 plan 则跟 plan；无 plan 时先与用户确认范围与验收标准，再动手
3. 目标仓库已 clone，待改源码、测试与构建/运行方式可访问
4. **Python 环境**：见 `skills/env-setup/references/python-env.md`。
5. **Node.js 环境**：见 `skills/env-setup/references/node-env.md`。
6. **团队模式**：仅在被 Leader 派发后执行；可与 `tester` 对齐实现细节，但不得越权 GitCode。**独立模式**：允许且应当使用 `skills/gitcode-repo` 完成 Issue/PR 操作（见下「独立模式交付」）
7. 若 Leader 派发中包含 **Shard Contract**，必须先阅读 `skills/dispatch-parallel/references/aidlc-pipeline.md`，并按 shard 范围执行（**仅团队模式**）

## Skill 定位

Aidlc 流水线从左到右串行推进；方括号为 **agent 简称**。本 skill 目录名 `dev-coder`（路径 `skills/dev-coder`）；派发标识使用 **`dev-coder`**。

```
用户/Issue(经 Leader) → … → [planner] → dev_plan.md → [coder] → 代码 + checklist → [tester] → [reviewer]
                                                              ▲
                                                        本阶段（agent coder）
```

- **上游**：`dev_plan.md`（由 `skills/dev-planner` 按 `dev_plan_template.md` 生成；结构门禁为 `check_plan.py --plan dev`；本阶段**只勾选** `[ ]`→`[x]`，不重排章节或顶层分类）
- **下游**：`tester` 按 `test_plan.md` 验证；审查结论经 `reviewer` → Leader

## 必读参考

开始编码前，先阅读 [references/principles.md](references/principles.md)。

该文件是本 skill 的权威说明，包含：

- 通用开发原则与交付要求
- 代码组织、可维护性、测试与验证规范
- Agent 的执行与交付行为要求
- Python 语言示例的引用入口

如需 Python 具体约定，继续阅读 [references/python-examples.md](references/python-examples.md)。
涉及 Shell/IO/进程类问题时，补充阅读 [references/infra-patterns.md](references/infra-patterns.md)。

开始实现前，补充阅读 [skills/aidlc-common/references/layer-alignment.md](../../aidlc-common/references/layer-alignment.md) 以确认修复层级与机制边界。

Python 环境见 `skills/env-setup/references/python-env.md`；Node.js 环境见 `skills/env-setup/references/node-env.md`。

## Shard 模式（G4 并行派发）

当 Leader prompt 含 `## Shard Contract` 时，本阶段只处理该 shard：

- 只实现 `items` 中列出的 `dev_plan.md` checklist 项，并只勾选这些项
- 只修改 `touch_allow` 中的代码/配置文件，以及自己的 `worker_summary`
- 不得修改 `touch_forbid` 或其它 shard 的文件；若必须越界，停止并向 Leader 汇报
- 保持 `dev_plan.md` 结构不变；共享计划文件中仅允许 `[ ]` → `[x]`
- 收尾写入 `doc/<module>/dispatch/g4-<shard_id>-summary.md`（若 Leader 指定 `worker_summary`，以指定路径为准）

summary 须含 Done、Deferred in shard、Out of shard。shard `items` 须全 `[x]`；阻塞则勿勾并 FAIL shard。Shard 自查不能替代 Leader G4-I。

## 计划进度（必守）

计划 checklist 有三种操作，**分工明确、不可混用**：

| 操作 | 方式 | 时机 |
|------|------|------|
| **查询** | Read `doc/<module>/dev_plan.md`，或运行 `status` | 开始前了解待办；需要结构化摘要时 |
| **勾选** | 编辑 `dev_plan.md`：`[ ]`→`[x]`；子项全勾后勾父项 | 每完成一条已验证任务 |
| **校验** | 运行 `verify` | **全部相关勾选更新完成后，收尾一次性**运行 |

```powershell
# 查询（可选；Read md 亦可）
& <python> skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> status

# 收尾校验（必做）
& <python> skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> verify
```

- **禁止** agent 调用 `set`；**禁止**用 `status`/`verify` 代替编辑 plan；**禁止**每勾一项就 bash 一次脚本。
- 调用 `status`/`verify`：**直接执行脚本**，用任务卡 `python` 绝对路径；禁止 stdin 管道与 `python -c "…"`。
- **父子联动（双向）**：子项未全 `[x]` 则父项不得 `[x]`；子项全 `[x]` 则父项须 `[x]`。`--allow-parent` 仅豁免前者（Leader 书面批准）。
- `verify` 只校验格式与父子一致性，**不能**单独证明 scope 完成；汇报须含可复现验证命令与退出码/摘要；G4 须 Leader 重跑 `verify` + `reviewer_plan_check.py status --plan dev`。

## 命令超时（必守）

**双层、禁止无限挂起**：Agent 外层等待 **≥** 命令/脚本超时。

| 场景 | 外层上限 |
|------|----------|
| `coder_plan_check.py status\|verify` | 60s |
| build / test / lint | 120–300s（更重告知 Leader） |

**命令层**（bash/cmd/PowerShell；测试/构建必设）：`timeout <秒> <cmd>`，或工具/框架 `--timeout`（如 `pytest --timeout=120`）。禁止裸长跑。

超时不得勾选 `dev_plan.md`；须汇报退出码/摘要。

## 核心职责

- 基于 `doc/<module>/dev_plan.md` 执行开发任务。
- 以最小修改完成目标，并通过测试、构建、Lint 或可执行检查验证结果。
- 完成已实现且已验证的计划任务后，更新 `dev_plan.md` checklist。

## 工作流

### Step 0: 对齐本地仓库（必做）

动手改代码或跑验证**之前**，按 [skills/aidlc-common/references/repo-workspace-sync.md](../../aidlc-common/references/repo-workspace-sync.md)：

- 从 Issue/任务卡/`gitcode-repo.json` 明确期望 **`branch_base` 或特性分支或 commit**
- `fetch` 后核对 `HEAD` 与期望是否同一 sha；**否** → checkout（有脏工作区先 stash）
- 在汇报中记录 `repo sync` 一行

Aidlc 团队模式：期望分支以 Leader 任务卡的 **`branch_base`** 为准。

### Step 1: 复述目标并确认模块

用一句话复述用户/Leader 目标；确认 `module` 并读取 `doc/<module>/dev_plan.md`。

### Step 2: 理解上下文

阅读相关源码、测试、配置与 `requirements.md`/`design.md`（按需）；从 `dev_plan.md` 或 `status` 识别待办 checklist 项与成功标准。
在开始编码前做一次层级对齐检查：若 `design.md` 文件清单要求基础设施层改动（如 `utils.py`/IO 模块），但当前方案仅改 `_tool.py` 或格式化层，需先向 Leader 报告并确认，不得私自降层实现。

### Step 3: 最小实现

只做完成任务所必需的改动；避免顺手重构无关代码。

### Step 4: 验证并勾选

在 **`repo-root` 虚拟环境**内运行测试、构建、Lint 或最接近的局部检查。验证通过后编辑 `dev_plan.md` 勾选对应项；子项全勾后同步勾父项。无法覆盖处说明原因，并写明解释器路径与命令。

### Step 5: 收尾校验并汇报

**团队模式**：向 Leader 说明改动、验证结果、剩余风险（门禁/范围问题须上报）；**勿**自行 commit/push（Leader **G7a** 会统一打包）。

**独立模式**：向用户说明改动、验证结果与残余风险；若任务要求交付远端，继续 **Step 6**。

若存在 `dev_plan.md`，全部勾选更新完成后，**一次性**运行：

```powershell
& <python> skills/dev-coder/scripts/coder_plan_check.py --module <module> --repo-root <repo-root> verify
```

校验失败则修正 Markdown 后重跑，直至通过。

### Step 6: 独立模式交付（仅 standalone）

当用户或触发器要求「落地到 GitCode / 提 PR」时，在验证通过后：

1. 加载 `skills/gitcode-repo/SKILL.md`；操作前在本 skill 根目录跑对应脚本 `--help`
2. 自 `<fork.remote_name>/<branch_base>`（或配置中的集成基线）建特性分支 → `git commit` → `git push` 到 fork
3. 按 `references/pr_guide.md` 建 PR/MR，正文关联 Issue；必要时在 Issue 下评论进展摘要
4. 非 trivial 改动：提 PR 前**建议**加载 `dev-tester`（`module` 或 `pr-gate`）与 `dev-reviewer` 做自检；Must Fix 未清则勿 silent 提 PR
5. **禁止**只改本地代码却声称「已完成」——若任务含 Issue/PR 交付，必须给出 PR URL 或说明阻塞原因

## 必守规则

- 仅在歧义会影响实现方式、安全边界或对外行为时才追问。
- 不要擅自增加未被要求的功能、抽象层、配置或通用框架。
- 只修改完成任务所必需的文件，避免顺手重构无关内容。
- **严格禁止**修改 Python 虚拟环境、`site-packages` 或 `node_modules` 内第三方包源码；见 `skills/env-setup/references/python-env.md`、`skills/env-setup/references/node-env.md`。
- 若无法运行理想检查，应运行最接近的局部检查，并明确说明未覆盖部分。
- 详细的开发原则、代码质量要求和交付标准，以 `references/principles.md` 为准。

## dev_plan.md 更新规则

`dev_plan.md` 是开发进度的来源。更新时保持 `Planner` 生成的 Markdown 格式不变：

- 只勾选本次已验证的任务；保持 Planner 格式，勿重排编号、标题或需求引用。
- 子项全勾后同步勾父项。
- `status` 查进度；收尾运行 `verify`。报错则修正后重跑。
- shard `items` 不得无说明留 `[ ]`；scope 外项勿勾。`*可选*` 未验证保留 `[ ]` 并说明。
- 计划与实现不一致时先说明；仅经确认或明显笔误才改任务描述。
- **团队模式**禁止使用 `skills/gitcode-repo` 或自行提拉 GitCode Issue/MR（Aidlc 中仅 Leader）。
- **独立模式**禁止把自己当成 Aidlc 子 agent（只改代码不提交）；交付动作由你本人经 `gitcode-repo` 完成。
