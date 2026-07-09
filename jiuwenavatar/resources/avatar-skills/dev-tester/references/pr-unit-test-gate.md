# 模式 B：PR 单元测试门禁（`pr-unit-test-gate`）

在用户提供 **PR + Issue**（或 Leader 下发的等价快照），且要用**单元测试证据**验证 PR 改动是否满足 Issue 时使用本路径。

本路径**刻意不包含**：E2E、浏览器/UI 自动化、性能/稳定性门禁、纯风格审查。

Runner：`scripts/pr_unit_test_runner.py`（在 skill 根目录 `skills/dev-tester/` 下执行下列命令）。

**Python 环境**：见 `skills/env-setup/references/python-env.md`。

**Node.js 环境**：见 `skills/env-setup/references/node-env.md`。

## 必需输入

- PR URL 或 PR 标识
- issue URL、issue 文件路径或 issue 正文
- 含 PR 代码的本地仓库/worktree **绝对路径**
- 私有或需登录的 GitCode PR/Issue 需提供 access token

## GitCode 认证

GitCode 且可能需登录时，在开始时索取 token，接受：

- `collect` 的 `--gitcode-token "<TOKEN>"`
- 环境变量 `GITCODE_TOKEN` 或 `GITCODE_ACCESS_TOKEN`

不要把 token 打印或写入报告。runner 仅记录是否提供了 token。

若 GitCode 返回 HTTP 401/403 或登录页，停止流程并请用户提供 token 后重跑 `collect`。

## Aidlc 中的 GitCode 边界

在 **aidlc-dev-team** 中：**禁止** tester 自行拉取 GitCode API。应由 **Leader** 经 `skills/gitcode-repo` 拉取后提供 Issue/PR 快照；或 Leader 预置 `context.json` / `pr.diff` / `issue.txt` 到 `<REPORT_DIR>` 后从 `init-plan` 起执行。

独立会话（非 Aidlc）且用户明确授权时，可直接 `collect`。

## 执行方式

将 shell **工作目录切换到** `skills/dev-tester/`（或命令中使用该目录的绝对路径）。仓库与报告目录使用 **宿主绝对路径**（Windows `D:\...` / Unix `/...`）。

## 产物规则

- `<REPORT_DIR>\unit_test_plan.json` 为 Agent 到 runner 的交接物。
- 产物集中在 `<REPORT_DIR>`；代码改动仅限 `<LOCAL_REPO>` 内聚焦的单元测试文件。
- `test_artifacts`、报告与用户可见摘要中的测试脚本路径一律使用 **宿主绝对路径**。
- 不保留中间推理草稿文件。

## 工作流

将 `<SKILL_DIR>` 设为 `skills/dev-tester` 的绝对路径；`$PYTHON` 设为 `<LOCAL_REPO>\.venv\Scripts\python.exe`（Unix：`<LOCAL_REPO>/.venv/bin/python`）或 Leader 指定的解释器。

**1. 收集上下文**

默认优先使用 **PR URL** 的 diff；仅当远程 diff 不可用时才回退本地 git。若需强制本地 diff，传 `--prefer-local` 或同时指定 `--base` / `--head`。

```powershell
& $PYTHON "<SKILL_DIR>\scripts\pr_unit_test_runner.py" collect --pr "<PR_URL>" --issue "<ISSUE_URL_OR_FILE_OR_TEXT>" --repo "<LOCAL_REPO>" --out-dir "<REPORT_DIR>"
```

GitCode 私有仓：

```powershell
& $PYTHON "<SKILL_DIR>\scripts\pr_unit_test_runner.py" collect --pr "<PR_URL>" --issue "<ISSUE_URL_OR_FILE_OR_TEXT>" --repo "<LOCAL_REPO>" --out-dir "<REPORT_DIR>" --gitcode-token "<TOKEN>"
```

**2. 初始化可编辑计划**

```powershell
& $PYTHON "<SKILL_DIR>\scripts\pr_unit_test_runner.py" init-plan --out-dir "<REPORT_DIR>"
```

**3. 编写用例并更新计划**

当前 Agent 审阅 `context.json`、`pr.diff`、`issue.txt` 与仓库既有测试约定，在 `<LOCAL_REPO>` 内增补聚焦单元测试，并更新 `<REPORT_DIR>\unit_test_plan.json`。

每个用例须含：`id`、`title`、`issue_requirement`、`target_changed_files`、`test_artifacts`（绝对路径）、`command`（venv 可运行，遵守 [SKILL.md](../SKILL.md)「命令超时」节）、`expected_behavior`。

**4. 执行并出报告**

```powershell
& $PYTHON "<SKILL_DIR>\scripts\pr_unit_test_runner.py" execute-report --out-dir "<REPORT_DIR>" --timeout 120
```

外层 Shell 等待 **≥** `--timeout`（默认 120s；更重告知 Leader）。细则见 [SKILL.md](../SKILL.md)「命令超时」节。

## 裁决规则

- **PASS**：计划内全部用例成功，且直接验证 Issue 要求的 PR 行为。
- **FAIL**：任一用例失败、无法执行、无具体命令，或证据不足以验证 PR 行为。

无 `NEEDS-HUMAN-REVIEW`；证据不足一律 **FAIL** 并说明缺什么。

在 Aidlc Gate 中：PR 子流程 **FAIL** 映射为团队 **REWORK**。

## 报告要求

报告须含：目标 PR/Issue、变更文件、用例 ID 与脚本绝对路径映射、每条命令与结果、日志路径、最终 PASS/FAIL、风险与限制。

Runner 产出：`context.json`、`pr.diff`、`issue.txt`、`unit_test_plan.json`、`unit_test_results.json`、`report.md`、`report.json`、`test_script_sources/*`、各用例 stdout/stderr 日志。

## 测试范围

- 优先沿用仓库既有单元测试约定。
- 仅为 PR 行为增补聚焦测试。
- 不因格式问题阻断执行（除非导致无法跑测）。
- 不得仅凭静态阅读断言 Issue 已修复。
- 不修改无关文件。

## 与模块模式衔接

若任务同时有 `doc/<module>/test_plan.md`，将本路径的 `report.md` 写入角色 **Evidence**；仅覆盖到的 checklist 项可勾 `[x]`。详见 [module-test.md](module-test.md)。
