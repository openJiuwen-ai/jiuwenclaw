# 本地仓库工作区对齐（任务前必做）

凡涉及**读/写业务代码仓**的任务（检视 PR、开发实现、跑测试、collect diff、G7a 打包等），在拉 diff、改文件、跑测试**之前**，必须先确认本地 `repo-root` 与任务要求的**目标分支或 commit** 一致；不一致则 **fetch + checkout**（必要时 stash）后再继续。

> **铁律**：禁止在「错误分支 / 错误 commit / 陈旧 HEAD」上检视、开发或提交结论。上一轮任务残留的分支不算证据。

## 1. 明确期望基线（Expected）

按任务类型取值，优先级：**任务卡 / PR API 元数据 > 用户明确指定 > `gitcode-repo.json` > 禁止臆猜 develop/main**。

| 任务类型 | 期望基线 | 典型来源 |
|----------|----------|----------|
| **检视 PR/MR** | PR **`head`**（源分支 tip 或 `head.sha`） | `pr_creator.py --number N` 返回的 `head` / `head.sha`；跨仓时注意 `fork_owner:branch` |
| **对比 PR diff（本地 git）** | `--base` = PR **`base`**（合入目标）；`--head` = PR **`head`** | 同上；`integration_base` ≠ `head`，勿混用 |
| **Aidlc G1–G6 改代码** | 任务卡 **`branch_base`**（工作区对齐点） | Leader G0 锁定；对齐 `<fork.remote_name>/<branch_base>` |
| **Aidlc G7a 建特性分支** | 自 **`branch_base`** 分叉 | [dev-leader workflow §Git 基线](../../dev-leader/references/workflow.md) |
| **独立开发 Issue** | Issue 指定分支，或自 **`upstream.base_branch` / 任务给定 base** 建特性分支 | Issue 正文、`gitcode-repo.json` |
| **仅文档/脚本（无 repo）** | 跳过本节 | — |

PR 检视时：**必须先拿到 PR 的 `base` 与 `head`（含 sha）**，再对齐本地仓；不要假设当前 `git branch` 就是 PR head。

## 2. 核对本地状态（Actual）

**本地仓尚不存在时**：先在本 skill 根目录对应的 [gitcode-repo](../../gitcode-repo/SKILL.md) 下执行 `python scripts/repo_manager.py --ensure-clone --config gitcode-repo.json --workspace <name>`（可选 `--clone-dir`），**禁止**手写 `git clone`。成功后将返回/配置的 `local_repo.path` 作为 **`repo-root`**，再执行下列 git 命令。

在 **`repo-root`**（或 `gitcode-repo.json` 里 `local_repo.path`）执行：

```powershell
git -C "<repo-root>" fetch --all --prune
git -C "<repo-root>" branch --show-current
git -C "<repo-root>" rev-parse HEAD
git -C "<repo-root>" status --porcelain
```

若任务给定 **commit sha**，用 `git rev-parse <ref>` 解析期望与当前 HEAD 比较。

若任务给定 **分支名**（如 `feature/foo` 或 `origin/feature/foo`）：

```powershell
git -C "<repo-root>" rev-parse "<branch_or_remote_branch>"
git -C "<repo-root>" rev-parse HEAD
```

**一致**：当前 HEAD 的 sha == 期望 ref 的 sha（或当前分支名与期望分支一致且 tip 相同）。

**不一致**：进入 §3，**不得**继续 collect / 改代码 / 跑门禁测试。

## 3. 对齐工作区（Checkout）

1. **有未提交改动**：`git stash push -u -m "pre-task-sync-<简短任务标识>"`（冲突则中止并向用户/report 说明，勿强切）
2. **fetch** 相关 remote（fork / upstream）：  
   `git -C "<repo-root>" fetch <remote> <branch>`
3. **checkout 到期望 ref**（择一）：
   - 分支：`git -C "<repo-root>" checkout <branch>` 或 `checkout -B <local_branch> <remote>/<branch>`
   - 精确 commit（检视只读时可 detached）：`git -C "<repo-root>" checkout <head_sha>`
4. **复检**：`rev-parse HEAD` 必须等于期望 sha
5. **记录**：在汇报/任务摘要中写一行 `repo sync: expected=<ref|sha> actual_before=<sha> action=checkout`

### PR 检视推荐顺序

1. `pr_creator.py --number <N> …` 取 JSON → 记下 `base`、`head`、必要时 `head` 的 commit sha
2. §2 核对本地是否在 **head** 上（或至少 collect 用的 `--head` 可解析到相同 sha）
3. 不在则 checkout 到 **PR head 分支**（跨仓 MR：fetch fork remote 再 checkout）
4. 再跑 `code_review_runner.py collect …`（`--base` / `--head` 与 PR 元数据一致）

本地 `--base`/`--head` 与 PR 不一致时，runner 的 diff 会错，**Must Fix 行号也会错**。

## 4. 多工作区

`gitcode-repo.json` 有多条 `workspaces[]` 时，**当前任务的 `repo-root` 必须与 PR/Issue 所属仓库匹配**；先按 [gitcode-repo SKILL §工作区选择](../../gitcode-repo/SKILL.md) 选对 workspace（**无出厂默认仓**，须按用户/任务/URL 确定），再做本节对齐。

## 5. 禁止

- 禁止跳过对齐直接 `collect` / 改业务代码 / 提交行评
- 禁止默认 `git checkout main` / `develop` 代替任务指定的 `base` / `branch_base`
- 禁止用「上次任务还在同一分支」代替 sha 级核对（他人可能已 push）
- Aidlc G1–G6 子阶段：**禁止** checkout 到与任务卡 `branch_base` 无关的分支（Leader G0 已锁定的除外）

## 6. 与 Aidlc Git 基线的关系

- **G0 `branch_base` / `integration_base`**：Leader 锁定；G1–G6 工作区对齐到 `branch_base` → 见 dev-leader [workflow.md §Git 基线](../../dev-leader/references/workflow.md)
- **本节**：所有角色、含 **standalone 检视/开发/测试**，在**每次任务开始时**仍须执行期望 vs 实际核对；G0 不能替代「下一个 PR 任务前」的再核对
