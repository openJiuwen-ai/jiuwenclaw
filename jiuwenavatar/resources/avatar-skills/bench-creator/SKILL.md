---
name: bench-creator
description: 从 GitCode upstream 已合入 PR 创建 bench 基准：提取修复前父提交、在 fork 上建 bench-issue-N 分支、按 gitcode-repo 模板编写并在 fork 提交 Issue。只要用户提到 bench、bench-issue、从 PR 造题、合入 PR 反推 bug 基准、评测数据集、复现分支、或「按 bench 流程」就必须使用本 skill。依赖 skills/gitcode-repo 的配置与 Issue 脚本；Windows 上 git 用 `git -C <path> …`。
metadata:
  short-description: Create bench-issue-N branch at pre-fix commit from merged upstream PR and open fork Issue via gitcode-repo.
  category: bench
  load_policy: explicit
  depends_on:
    - gitcode-repo
---

# Bench Creator（bench-creator）

将 **已合入 upstream 的修复 PR** 转化为可评测、可派工的最小闭环：

1. **提取** PR → `parent_sha`（修复前）+ 变更证据  
2. **分支** `bench-issue-N` @ `parent_sha`，推送到 **fork（origin）**  
3. **Issue** 在 **fork** 创建，正文对齐 `gitcode-repo` 模板，供 `dev-leader` → `dev-analyzer` 使用  

本 skill **编排流程与 bench 专用脚本**；GitCode 通用 API、Issue 模板与创建命令由 **`skills/gitcode-repo`** 负责。

## upstream / fork 职责（必守）

| 目标仓 | 允许的操作 | 禁止的操作 |
|--------|------------|------------|
| **upstream**（主仓，如 `openJiuwen/agent-core`） | 只读：拉 PR/MR 详情、commits、files、commit 元数据；`git fetch upstream` | 创建/推送分支、创建 Issue/PR、评论、改标签 |
| **fork**（个人 fork，如 `NickFuryXXX/agent-core`） | 创建并推送 `bench-issue-N`；创建 bench Issue；后续修复 PR 也默认发 fork | 把 bench 题误发到 upstream |

**Agent 自检（交付前）**

- bench 分支：`git -C <path> push` 的目标 remote 为 `fork.remote_name`（通常 `origin`），**不得**为 `upstream`。
- 新 Issue 的 `html_url` 路径须含 **`fork.owner`**（如 `NickFuryXXX/agent-core/issues/N`），**不得**仅为 upstream 域名下的主仓 Issue。
- 创建 Issue 时必须显式：`issue_fetcher.py --create ... --source fork`（默认已是 fork，但仍须写出以防脚本回退）。

## 何时使用

- 用户给出 **已合入 PR 编号/链接**，要造 bench 题  
- 需要 `bench-issue-1`、`bench-issue-2`… 分支命名  
- 评测 / 数据集 / 「从 PR 反推 bug」  
- 文档 `bench创建指导.md` 中的流程  

## 何时不用

- 仅查询 Issue/PR、普通 fork 协作 → `gitcode-repo`  
- 完整需求到 PR 开发流水线 → `dev-leader`（其中 GitCode 仍只经 Leader + `gitcode-repo`）  

## 前置条件

1. 已配置 `skills/gitcode-repo/gitcode-repo.json`（`upstream`、`fork`、`local_repo.path`、`gitcode_token` 或 `GITCODE_TOKEN`）。  
2. 本地已 clone fork 对应仓库，或可通过 `repo_manager.py --ensure-clone` 准备。  
3. Python 依赖：`requests`（经 gitcode-repo 脚本间接使用）。  
4. 目标 PR 在 **upstream 已 merged**（未合入则与用户确认是否仍要基于 PR head 造题）。  

**Aidlc**：GitCode 写操作（创建 Issue）与 token 仍建议由 **Leader** 执行；本 skill 可由 Leader 加载，子 agent 仅接收脱敏后的 Issue 摘要。

## 推荐目录结构

```text
skills/bench-creator/
├── SKILL.md
├── references/
│   ├── workflow.md          ← 分步清单与命令
│   ├── issue_authoring.md   ← bench Issue 防泄题（题面≠答案）
│   └── commit_selection.md  ← parent_sha 规则与排错
├── scripts/
│   ├── bench_gitcode.py     ← gitcode-repo.json + GitCode API（本 skill 内聚）
│   ├── bench_from_pr.py     ← PR → fix/parent/files JSON
│   └── bench_git.py         ← 建分支、fetch、push、可选验证
└── evals/evals.json         ← 评测用 prompt（可选）
```

## 执行流程（按序）

### Step 0 — 收集输入

向用户确认（若未给出）：

- upstream **PR 编号**  
- **工作区**名（`gitcode-repo.json` 的 `workspaces[].name`）  
- bench 序号 `N`（或自动递增）  
- 推断的 **`module`**（用于 Issue 与 `doc/<module>/`）  

### Step 1 — 提取 PR 上下文（仅 upstream 只读）

在 `skills/bench-creator` 下执行（`bench_from_pr.py` **只**调用 upstream 的 GET API，无写操作）：

```bash
python scripts/bench_from_pr.py --pr <PR_NUMBER> \
  --config ../gitcode-repo/gitcode-repo.json \
  --workspace <WORKSPACE> \
  --bench-index <N> \
  --format json
```

保存 JSON 中的 `parent_sha`、`fix_sha`、`files`、`pr_title`。  
若有 `warnings`，阅读 [references/commit_selection.md](references/commit_selection.md) 并决定是否加 `--fix-sha` / `--parent-sha` 重跑。

### Step 2 — 同步本地 Git

按 `gitcode-repo` skill：**先读** `gitcode-repo.json` 与 `git -C <local_repo.path> remote -v`，再操作。

需要时：

```bash
python ../gitcode-repo/scripts/repo_manager.py --ensure-clone \
  --config ../gitcode-repo/gitcode-repo.json --workspace <WORKSPACE>
```

### Step 3 — 创建并推送 bench 分支（仅 fork）

分支名：`bench-issue-<N>`（与 `--bench-index` 一致）。  
`--upstream-url` 仅用于 `git fetch`；`--push` 只会推到 **`fork.remote_name`**（默认 `origin`），脚本会拒绝向 `upstream` 推送。

```bash
python scripts/bench_git.py \
  --repo-path <local_repo.path> \
  --parent-sha <parent_sha> \
  --branch bench-issue-<N> \
  --upstream-url https://gitcode.com/<upstream-owner>/<repo>.git \
  --push-remote <fork.remote_name> \
  --push
```

可选：用 PR 中修复前的代码片段验证：

```bash
python scripts/bench_git.py ... \
  --verify-file path/from/pr.py \
  --verify-pattern 'content = read_result\.content'
```

（`--verify-*` 需与 Step 3 其它参数同一次调用；仅用于确认仍为 bug 状态。）

### Step 4 — 编写 Issue 正文（bench 防泄题）

**必读**：[references/issue_authoring.md](references/issue_authoring.md)（比普通 `issue_guide` 更严）。

1. 阅读 `skills/gitcode-repo/references/issue_guide.md`（通用格式）  
2. 复制 `skills/gitcode-repo/assets/issue_template.md` → 临时 `issue-body.md`  
3. 用 Step 1 的 PR **仅作内部取材**：题面只写**用户可见现象**、复现、实际/期望结果；**不得**把 patch、行号、根因链、修复 PR 写进正文  
4. **「初步分析」保留但宜短（1–3 条）**：仅粗粒度（子系统/目录/可见事件序列），禁止函数名+行号+根因+修复 PR（见 [issue_authoring.md](references/issue_authoring.md)）  
5. 发布前按 [issue_authoring.md](references/issue_authoring.md) 检查清单过一遍；删除模板注释与填写指引  

标题示例：`[Bug] <module>：<用户可见问题摘要>`（勿直接用 upstream 的 `fix(scope): ...` 修复式标题）。

### Step 5 — 在 fork 创建 Issue（禁止 upstream）

```bash
cd ../gitcode-repo
python scripts/issue_fetcher.py --create \
  --title "<TITLE>" \
  --body-file <path-to-issue-body.md> \
  --config gitcode-repo.json \
  --workspace <WORKSPACE> \
  --source fork
```

创建后核对返回 JSON 的 `html_url` 含 `fork.owner`；若落在 upstream 主仓则**视为失败**，勿交付。标签用 `issue_commenter.py` 另行添加（同样针对 fork Issue 编号）。

### Step 6 — 汇总交付

向用户输出表格：

| 项 | 值 |
|----|-----|
| 原 PR | URL |
| parent_sha / fix_sha | … |
| bench 分支 | 名 + fork tree URL |
| 新 Issue | 编号 + URL |

详细检查项见 [references/workflow.md](references/workflow.md)。

## 脚本说明

| 脚本 | 作用 |
|------|------|
| `bench_gitcode.py` | 读取 `gitcode-repo.json`、调用 GitCode API（仅供本 skill 脚本） |
| `bench_from_pr.py` | upstream PR → JSON（fix/parent/files/建议分支名） |
| `bench_git.py` | fetch **upstream**；建分支并 push **fork**（拒绝 push 到 `upstream` remote） |

二者 `--help` 优先于本文中的示例参数。

**配置查找**：`bench_from_pr.py` 通过同目录 `bench_gitcode.py` 读取 `gitcode-repo.json`（`--config`、当前目录、`skills/gitcode-repo/` 等常见路径）；不 import 其他 skill 的 Python 模块。

## 关键原则

1. **基点必须是 parent_sha**，不是 fix_sha。  
2. **upstream 只读、fork 可写**：读 PR / fetch 用 upstream；**分支与 Issue 只在 fork**。  
3. **module** 必须与后续 `doc/<module>/` 一致。  
4. Issue 是**题面不是答案**：现象与可测期望写全；「初步分析」只做**最小粗粒度**指路，禁止细粒度泄题（行号、应改函数、根因结论、upstream 修复 PR 等）。详见 [issue_authoring.md](references/issue_authoring.md)。  
5. 完成后删除临时正文文件；勿泄露 token。  
6. 误建到 upstream 的 Issue/分支不算 bench 交付物，须改在 fork 重做。  

## 与 dev-leader 的衔接

bench Issue 创建后，Leader 可：

1. 拉 Issue → G0 写 `branch_base`（Issue 环境分支，`integration_base` 默认同值）并对齐工作区  
2. 派 `dev-analyzer` → `requirements.md`  
3. 在 `branch_base` 工作区派 `dev-coder`  

## 进一步阅读

- 完整分步与 API 表：[references/workflow.md](references/workflow.md)  
- **Bench Issue 防泄题**：[references/issue_authoring.md](references/issue_authoring.md)  
- 提交选择规则：[references/commit_selection.md](references/commit_selection.md)  
- Issue 模板与创建：[../gitcode-repo/references/issue_guide.md](../gitcode-repo/references/issue_guide.md)  
- 源文档（仓库根）：`bench创建指导.md`  
