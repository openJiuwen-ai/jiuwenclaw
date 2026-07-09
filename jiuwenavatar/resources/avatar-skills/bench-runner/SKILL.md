---
name: bench-runner
description: 执行 Agent PR 相对评分基准评测（六阶段 S1–S6）。支持 AVA（Agent vs Agent，含 doc 链）与 HVA（Human vs Agent，仅代码/PR/CI）。只要用户提到 bench runner、跑评测、ava_bench、hva_bench、PR 对比评分、Agent 相对分、六阶段评分、bench 打分、或填写/执行 bench JSON，就必须使用本 skill。依赖 gitcode-repo 拉取 PR/Issue；Windows 上 git 优先 git -C 本地仓路径。
metadata:
  short-description: Run six-stage AVA/HVA PR relative scoring benchmarks with bundled schemas and repo_root resolver.
  category: bench
  load_policy: explicit
  depends_on:
    - gitcode-repo
---

# Bench Runner（bench-runner）

按 **六阶段相对评分** 对比两个 PR：基准侧固定为 **1.0**，候选侧输出 **相对分**（0–2，封顶 2.0）。本 skill 内聚 bench 配置模板、路径解析脚本；PR/Issue 拉取走 **`gitcode-repo`**。

## 路径约定（通用）

执行命令前设定 **skill 根目录** `$BenchRunnerRoot`（任选其一）：

| 场景 | `$BenchRunnerRoot` |
|------|-----------------|
| 本仓库（AiDlcSkills） | `<仓库根>/skills/bench-runner` |
| 已安装到用户 skills | `~/.agents/skills/bench-runner` 或 `~/.claude/skills/bench-runner` |

后续示例均写作 `$BenchRunnerRoot`；脚本在 `$BenchRunnerRoot/scripts/`。

`inputs.paths.skills_root` 留空时，自动推断为 `$BenchRunnerRoot` 的上级 `skills/`（与 `gitcode-repo`、`dev-*` 同级）。

## 两种模式

| 模式 | 模板 | 基准 | 候选 | 证据范围 |
|------|------|------|------|----------|
| **AVA** | `assets/ava_bench.json` | Agent A PR | Agent B PR | doc 链 + diff + CI + review |
| **HVA** | `assets/hva_bench.json` | 人工 PR | Agent PR | 仅代码/测试/PR/CI/检视，**不用 doc** |

选择依据：

- 两侧均为 Aidlc 流水线产出（含 `doc/<module>/`）→ **AVA**
- 仅有人工参考 PR 与 Agent PR → **HVA**
- 用户已指定文件名或模式时，按其指定执行

## 目录结构

```text
skills/bench-runner/
├── SKILL.md
├── assets/
│   ├── ava_bench.json
│   └── hva_bench.json
└── scripts/
    ├── bench_context.py          # 共享：gitcode 发现、占位符
    ├── resolve_repo_root.py      # 仅解析 repo_root
    ├── resolve_bench_context.py  # repo + skills + 展开 gate 命令
    └── validate_bench_result.py  # 校验结果 JSON
```

单元测试（改 `bench_context.py` 后可选）：仓库根 `scripts/test_bench_context.py`。

## 执行流程（按序）

### Step 0 — 确认模式与输入

向用户确认（若未给出）：

- 模式：**AVA** 或 **HVA**
- 基准 PR URL / `head_ref`、候选 PR URL / `head_ref`
- 关联 Issue URL（`shared_context.issue_url`，强烈建议）
- 本地仓库：`repo_root.name` 或 `repo_root.path`（至少其一）
- **AVA**：`baseline.module` / `candidate.module`；`shared_context.analysis_type`（`Bug|Feature|Refactor|Docs`，G1 必填）

### Step 1 — 准备 bench 配置

1. 复制模板到工作目录并编辑：

   ```powershell
   Copy-Item "$BenchRunnerRoot\assets\ava_bench.json" .\my_ava_bench.json
   ```

2. 填写 `inputs` 主要字段：

   | 字段 | 说明 |
   |------|------|
   | `baseline` / `candidate` | `pr_url`、`head_ref`；AVA 另填 `module` |
   | `repo_root.name` 或 `path` | 至少其一；查找规则与 **gitcode-repo** `config_loader` 一致 |
   | `repo_root.gitcode_config` | 可选，显式指定配置文件 |
   | `paths.skills_root` | 可选，dev-* gate 脚本根目录 |
   | `shared_context.issue_url` | 共同目标 Issue |
   | `shared_context.analysis_type` | AVA G1 必填；覆盖侧级 `analysis_type` |

3. `shared_context.same_issue_scope`（AVA，默认 `true`）：表示两侧 PR 针对同一 Issue，评分须对照 `issue_url`。

### Step 2 — 解析运行上下文

**仅仓库根：**

```powershell
python "$BenchRunnerRoot\scripts\resolve_repo_root.py" --bench .\my_ava_bench.json
python "$BenchRunnerRoot\scripts\resolve_repo_root.py" --bench .\my_ava_bench.json --format path
```

**完整上下文（推荐 AVA，含展开后的 gate 命令）：**

```powershell
python "$BenchRunnerRoot\scripts\resolve_bench_context.py" --bench .\my_ava_bench.json --side baseline
python "$BenchRunnerRoot\scripts\resolve_bench_context.py" --bench .\my_ava_bench.json --side candidate
```

也可直接传参：

```powershell
python "$BenchRunnerRoot\scripts\resolve_repo_root.py" --name my-workspace
python "$BenchRunnerRoot\scripts\resolve_repo_root.py" --path "D:\repos\agent-core"
```

解析失败时检查：`gitcode-repo.json` / `issue-resolver.json` 是否在 cwd、`skills/gitcode-repo/` 或已安装 gitcode-repo skill 目录；`local_repo.path` 是否存在且为 Git 工作区。

**`repo_root.name` 与 `path` 同填**：以 `path` 为准；若与 `name` 解析结果不一致，输出 `notes` 说明。

### Step 3 — 收集证据

**共同**（AVA / HVA）：

1. 经 **`gitcode-repo`** 拉取两侧 PR、diff、CI、Review
2. 有 `issue_url` 时拉取 Issue 正文（优先于 `issue_summary`）
3. 在 `repo_root` 下 checkout 各 PR `head_ref`，收集 diff 与测试变更

**AVA 额外**：

- 读取 `doc/<module>/` 文档链与 **`doc/<module>/review.md`**（临时证据目录 `review/` 仅作 diff/上下文参考）
- 可选执行 `optional_gate_checks`：用 `resolve_bench_context.py` 输出的 `optional_gate_checks_expanded`，或按模板 `placeholders` 手动替换 `{repo_root}`、`{skills_root}`、`{module}`、`{analysis_type}`

**HVA**：禁止用 doc 作为评分证据。

### Step 4 — 逐维度评分

六阶段权重各 **1/6**（见模板 `dimensions[].weight`）。子点权重见 `subpoints[]`。

**相对分**（`scoring_policy`）：

```
sub_relative = candidate_absolute / baseline_absolute   # 封顶 2.0
major_relative = Σ(sub.weight × sub.relative)
overall_relative = Σ(major.weight × major.relative)
```

**边界**（`edge_cases`）：双方 0 → 1.0；基准 0 候选 >0 → 2.0；基准 >0 候选 0 → 0.0。

### Step 5 — 输出结果

按 `result_template` 生成 `bench_result_<mode>_<timestamp>.json`。

| 模式 | scores 键名 | verdict 含义 |
|------|-------------|--------------|
| AVA | `baseline` / `candidate` / `relative` | 相对 Agent A：`better`=候选更好 |
| HVA | `human` / `agent` / `relative` | 相对人工：`better`=Agent 更好 |

| overall_relative | verdict |
|------------------|---------|
| > 1.05 | `better` |
| 0.95 – 1.05 | `equal` |
| < 0.95 | `worse` |

**校验结果（可选）：**

```powershell
python "$BenchRunnerRoot\scripts\validate_bench_result.py" --bench .\my_ava_bench.json --result .\bench_result_ava.json
```

### Step 6 — 向用户汇报

1. 模式与输入摘要  
2. 总体相对分与 verdict  
3. 各 stage major 相对分表  
4. 显著差异子点  
5. 结果文件路径  

## AVA 可选 Gate

| ID | 脚本 |
|----|------|
| G1 | `check_requirements.py`（需 `--type` / `analysis_type`） |
| G2 | `check_design.py` |
| G3 | `check_plan.py`（dev / test） |
| plan_status | `reviewer_plan_check.py` |

Gate 仅作参考；最终分仍按 rubric 评判。

## 脚本自测（可选）

修改 `bench_context.py` 或配置解析逻辑后，可在本机快速回归：

```powershell
python scripts/test_bench_context.py
```

不跑评测流程也能验证 gitcode 查找、占位符、`repo_root` 解析等；需要本机已安装 `git`。

## 前置条件

1. 已配置 **gitcode-repo**（`gitcode-repo.json` 或 `issue-resolver.json`）
2. 本地已 clone 目标仓，或 `repo_root.path` 有效
3. Python 3.8+
4. AVA gate：本机存在 `{skills_root}/dev-*` 脚本（可选）

## 与 bench-creator 的关系

- **bench-creator**：从已合入 PR **造题**
- **bench-runner**：对已有 PR **打分评测**

## 常见错误

| 现象 | 处理 |
|------|------|
| `repo_root.name 与 path 至少填其一` | 填写 name 或 path |
| `未找到 gitcode-repo.json` | 配置 gitcode-repo 或设 `gitcode_config` |
| `analysis_type 必须是 ...` | 填写 `shared_context.analysis_type` |
| HVA 误用 doc | 仅 Issue/PR/代码/测试 |
| Gate 命令找不到脚本 | 设置 `paths.skills_root` 或检查 AiDlcSkills `skills/` 布局 |

## 示例命令链

```powershell
$BenchRunnerRoot = "D:\my_skills\AiDlcSkills\skills\bench-runner"   # 按实际修改

Copy-Item "$BenchRunnerRoot\assets\hva_bench.json" .\run.json
# 编辑 run.json …

python "$BenchRunnerRoot\scripts\resolve_bench_context.py" --bench .\run.json
# gitcode-repo 拉 PR/Issue → 评分 → 写出 bench_result_*.json
python "$BenchRunnerRoot\scripts\validate_bench_result.py" --bench .\run.json --result .\bench_result_hva.json
```
