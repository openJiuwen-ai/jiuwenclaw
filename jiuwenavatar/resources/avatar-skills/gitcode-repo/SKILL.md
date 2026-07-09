---
name: gitcode-repo
description: GitCode 与 Git 远程仓操作助手，辅助 agent 完成 Issue 获取/搜索/创建/评论/标签、PR/MR 获取/搜索/列表/创建/评论/标签、本地仓 clone/更新/读文件/列目录、upstream+fork 协作，以及按 `gitcode-repo.json` 读写多工作区列表与 token、轮询配置；操作前应主动核对各工作区本地 Git remote/分支并回写 JSON。Windows 上对本地仓执行 git 时优先使用 `git -C <path> …`，并注意 PowerShell 与 Git Bash 路径写法（`D:\…`/`D:/…` 与 `/d/…`）。当用户提到 GitCode、Issue、PR/MR、fork/upstream、remote、clone、repo、poller、`gitcode-repo.json`、或需要操作远程仓时使用；详细命令与前置条件见正文与 `references/gitcode_api_reference.md`。
metadata:
  short-description: GitCode API + Git repo helper for issues, PRs, clone/update, fork-upstream workflow, multi-workspace gitcode-repo.json, and proactive git remote sync; use when user mentions GitCode, issues, MRs, remotes, or poller.
  category: gitcode
  load_policy: explicit
  depends_on: []
  gates:
    - G7
---

# GitCode 与 Git 远程仓助手（gitcode-repo）

本技能只负责 **GitCode API 操作** 和 **Git 本地/远程仓辅助操作**。它不承担需求分析、架构设计、代码开发、测试、提交规范等工程流程；这些工作由调用者或其它技能处理。

## Skill 定位

- **路径**：`skills/gitcode-repo`（由原 `issue-resolver` 合并而来）
- **Aidlc 流水线**：**仅 Leader**（`dev-leader`）经本 skill 拉 Issue、列/建 PR/MR；其它 agent 禁止运行本 skill 脚本或接触 `GITCODE_TOKEN` / `gitcode_token`
- **独立 / 自主模式（standalone，无 Leader 编排）**：执行任务的数字分身**本身即调用方**，**允许且应当**直接运行本 skill 脚本。典型场景：
  - **检视**（`dev-reviewer`）：`pr_commenter.py` 提交行评/评论（cron 定时检视 PR 等）
  - **开发**（`dev-coder`）：拉 Issue、建分支、`commit`/`push`、建 PR/MR、Issue 评论（开发分身独立实现 Issue 等）
  上面的「仅 Leader」限制**只适用于 Aidlc 团队流水线**，不适用于独立模式
- **Aidlc G7b 提 PR**：**G7a PASS 后** Leader **必须**按 `references/pr_guide.md` 在 **本 skill 根目录** 使用 `assets/pr_template.md` 生成临时 `pr-body.md`；**`--create` 成功或 `--dry-run` 正常结束后删除**（失败时保留至重试）；**禁止** commit 到业务仓（见 `pr_guide.md`「临时正文」；本目录 `.gitignore` 兜底）；正文中须填入 **`doc/<module>/review.md` 审查摘要**（见各平台 `dev-leader` skill）
- **配置**：优先 `gitcode-repo.json`；仍自动识别同目录下的旧名 `issue-resolver.json`（迁移期兼容）

## 范围

保留并优先使用现有 `gitcode-repo` 读写机制：

- 运行时配置：`gitcode-repo.json`（**出厂 `workspaces` 为空，无默认仓库**；须按用户/任务要求填写 token 与仓库信息，或从 `assets/gitcode-repo.example.json` 复制后改名再填；勿将含密钥的文件提交入库）
- 配置样例：`assets/gitcode-repo.example.json`
- Issue 指引与模板：`references/issue_guide.md`、`assets/issue_template.md`
- PR 指引与模板：`references/pr_guide.md`、`assets/pr_template.md`
- 模板与样例：`assets/`（`issue_template.md`、`pr_template.md`、`gitcode-repo.example.json`）
- API 参考：`references/gitcode_api_reference.md`
- 操作指引：`references/issue_guide.md`、`references/pr_guide.md`
- 脚本目录：`scripts/`
- 轮询状态文件：`.issue-poller-state.json`（由 `issue_poller.py` 自动读写）

主要能力：

- **Issue**：获取/搜索/创建 Issue、评论、标签均支持 **fork/upstream**（CLI 默认 **fork**）。
- **PR/MR**：获取/搜索/列出 PR/MR，评论与打标签，默认创建到 fork；需要时可显式创建到 upstream。
- **本地仓库**：clone、更新、检查本地路径、读取文件内容、列目录。
- **双仓协作**：使用 `upstream` 表示目标主仓，使用 `fork` 表示个人 fork 或源仓。
- **轮询**：按 assign、mention、标签检查新 Issue 事件，并保存已处理状态。
- **Webhook**：管理 GitCode 仓库 Webhook，把 PR/Issue/Push 等事件推送到 JiuwenAvatar Gateway 的 `/webhook/*` 端点。

不做的事：

- 不强制创建需求分析或架构设计文档。
- 不强制进入修复分支、运行测试或提交代码。
- 不替用户决定修复方案。
- 不把测试 Issue/PR 发到正式仓，除非用户明确要求。

## 前置条件

1. 安装 `git`。
2. 安装 Python 依赖：`requests`。
3. 配置 `GITCODE_TOKEN` 环境变量，或在 `gitcode-repo.json` 中填写 `gitcode_token`。
4. 在 `gitcode-repo.json` 的 `workspaces` 列表中**按当前任务**配置对应工作区，并填写其 `upstream` 与 `fork` 字段（见下文「工作区选择」）。

Token 读取优先级：

1. 环境变量 `GITCODE_TOKEN`
2. `gitcode-repo.json` 中的 `gitcode_token`
3. 脚本交互式输入

GitCode API v5 使用 `access_token` query 参数认证。不要把 token
放在 `Authorization: Bearer ...` 或其它请求头里，否则写操作可能返回
`403 Forbidden`。

## 任务前：本地仓库对齐（凡涉及业务代码仓必做）

读 diff、改代码、跑测试、collect、建 PR **之前**，必须先执行 [skills/aidlc-common/references/repo-workspace-sync.md](../aidlc-common/references/repo-workspace-sync.md)：

1. 从 PR/Issue/任务卡取得期望 **`base` / `head` / `branch_base` / commit sha**
2. 在对应 `local_repo.path` 上 `fetch`，比较 `HEAD` 与期望 ref 的 sha
3. **不一致** → checkout 到期望分支或 commit（必要时 stash）；**一致** → 继续后续脚本

PR 检视：务必先对齐到 PR **`head`**，再让下游 runner 或 `git diff` 使用与 PR 一致的 `--base`/`--head`。

## 配置文件

默认配置文件位于**本 skill 根目录**（可从模板复制后再改）：

```text
gitcode-repo.json                   ← 运行时配置（自建，通常不入库）
assets/gitcode-repo.example.json    ← 配置样例（复制后改名）
```

配置文件采用 **工作区列表** 结构，顶层共享 `gitcode_token`，每条 `workspaces[]` 记录一组 upstream/fork/本地路径（及可选 `poller`）：

```json
{
  "gitcode_token": "",
  "workspaces": [
    {
      "name": "唯一工作区名",
      "upstream": {
        "owner": "主仓 owner",
        "repo": "主仓 repo",
        "base_branch": "develop"
      },
      "fork": {
        "owner": "fork owner",
        "repo": "fork repo",
        "remote_name": "origin"
      },
      "local_repo": {
        "path": "D:/path/to/local/repo",
        "auto_clone": true,
        "auto_update": true
      },
      "poller": {}
    }
  ]
}
```

字段说明：

- `workspaces[].name`：工作区唯一标识，便于在对话或命令中指定当前目标。
- `upstream` / `fork` / `local_repo`：与旧版单条配置含义相同，但归属到具体工作区条目。
- `poller`：可选；仅在该工作区启用轮询时需要。

**脚本用法**：所有 CLI 均支持 `--config` 与 `--workspace <name>`。配置含多条 `workspaces` 时必须指定工作区名；仅一条时可省略。仍兼容旧版顶层扁平格式（无 `workspaces` 时直接读 `upstream`/`fork`）。

### 工作区选择（必守，无默认仓库）

**内置 `gitcode-repo.json` 的 `workspaces` 为空，不存在出厂默认仓。** 发起任何 Issue/PR/clone/git 操作前，必须先确定当前任务对应哪条工作区：

| 优先级 | 来源 |
|--------|------|
| 1 | 用户/任务**明确指定**的仓库 URL、owner/repo、或 workspace `name` |
| 2 | PR/Issue URL 解析出的 `owner/repo`，在 `workspaces[]` 中查找匹配条目 |
| 3 | 用户已有本地 clone 路径，按 remote URL 反查并新建/更新对应 workspace |
| 4 | 以上均无法确定 → **`user-interact` 向用户确认**后再写入配置 |

**禁止**：臆测 `workspaces[0]`、文档示例名、或他人开发环境里的仓库作为默认目标。`workspaces` 为空且任务未指定仓库时，**不得**直接调 API，须先确认并补全配置。

### 主动同步工作区 Git 信息

**在读写 `gitcode-repo.json`、发起 Issue/PR/clone 等操作之前，Agent 必须先读取并核对 JSON 中的工作区信息，再核对本地 Git 状态并回写**——不要仅凭记忆、模板或跳过配置直接调用 API。

推荐流程（对配置中的每条 `local_repo.path`，或用户明确指定的本地仓路径）：

1. **读取并核对 `gitcode-repo.json`（必须先做）**  
   - 使用本 skill 根目录下的运行时文件 `gitcode-repo.json`（见上文「配置文件」）；**只读该文件，不要将 `assets/gitcode-repo.example.json` 当作运行时配置加载**。  
   - 按 `local_repo.path` 的绝对路径，或用户指定的工作区 `name`，在 `workspaces[]` 中查找匹配条目。  
   - **无匹配条目**：参照模板字段结构**补充**一条新工作区，至少填写 `name`、`local_repo.path`（目标本地仓绝对路径），并预留 `upstream` / `fork` 待后续步骤填入。  
   - **已有匹配条目**：通读并**校验一遍** `upstream`、`fork`、`local_repo` 是否完整、路径是否与当前目标一致；记下与本地 Git 待核对的不一致项，**不要**在未读 JSON 的情况下直接调 API。  
2. **确认本地路径存在且为 Git 仓库**  
   路径使用步骤 1 中的 `local_repo.path`；若为空，先向用户确认目标本地仓绝对路径后再继续（不要隐式假定 IDE/编辑器工作区）：  
   `git -C "<path>" rev-parse --is-inside-work-tree`
3. **列出 remote**  
   `git -C "<path>" remote -v`
4. **读取默认分支 / 当前分支**  
   `git -C "<path>" symbolic-ref refs/remotes/<upstream_remote>/HEAD`（若有 upstream remote）  
   `git -C "<path>" branch --show-current`
5. **解析 GitCode URL** → `owner` / `repo`  
   常见形式：`https://gitcode.com/<owner>/<repo>.git`、`git@gitcode.com:<owner>/<repo>.git`
6. **区分 upstream 与 fork**  
   - 若存在名为 `upstream` 的 remote，通常其 URL 对应 `upstream`，`origin` 对应 `fork`。  
   - 若仅有 `origin`，需结合仓库是否为用户 fork（API 或页面）判断；个人 fork 时 `origin` → `fork`，主仓信息写入 `upstream`。
7. **回写 `gitcode-repo.json`**  
   - 更新步骤 1 中选定或新建的 `workspaces[]` 条目。  
   - 写入 `upstream.owner/repo/base_branch`、`fork.owner/repo/remote_name`、`local_repo.path`。  
   - 若 remote 名称或 URL 与 JSON 不一致，以 **当前 `git remote -v` 实测结果** 为准。

PowerShell 示例（路径按需替换）：

```powershell
git -C "D:\claw\my_aidlc_skills" remote -v
git -C "D:\claw\my_aidlc_skills" branch --show-current
git -C "D:\claw\my_aidlc_skills" remote get-url origin
```

使用前应校验（针对 **当前选定** 的工作区条目）：

- `upstream.owner` 与 `upstream.repo` 指向目标主仓。
- `fork.owner` 与 `fork.repo` 指向个人 fork 或源仓。
- `fork.remote_name` 与本地 `git remote` 名称一致，且不要误指向主仓。
- `local_repo.path` 为空时，脚本使用默认目录 `~/.jiuwenclaw/repos`；多工作区场景建议显式填写绝对路径。

## 脚本入口

### Main/Leader 调用前：先 `--help` 确认参数

**Aidlc 仅 Main（`dev-leader`）** 执行本 skill 脚本。在**首次调用**某脚本、或切换子命令（`--list` / `--create` / `--number` 等）前，须先在 **本 skill 根目录** 运行 `--help`，再拼正式命令——**禁止**凭记忆或其它脚本参数名直接试错（如 `issue_fetcher` 用 `--number` 而非 `pr_creator` 的关联 `--issue`）。

```powershell
Set-Location "D:\path\to\skills\gitcode-repo"   # 含本 SKILL.md 的目录
python scripts/issue_fetcher.py --help
python scripts/pr_creator.py --help
python scripts/pr_commenter.py --help
# 确认必选参数；issue_fetcher 模式互斥（--number/--issue、--list、--create 三选一）后再执行，例如：
python scripts/issue_fetcher.py --number 42 --config gitcode-repo.json          # 默认 --source fork
python scripts/issue_fetcher.py --number 42 --source upstream --config gitcode-repo.json
```

| 时机 | 动作 |
|------|------|
| 本会话首次用某脚本 | `python scripts/<脚本名>.py --help` |
| 从 `--list` 改为 `--create`（或反向） | 对该脚本再跑一次 `--help` |
| 参数报错 `unrecognized arguments` | 先 `--help`，再改命令；勿连猜三次 |

解释器：任意已安装 `requests` 的 Python 即可（`pip install requests`）；与 skill 目录无关，勿误用业务仓 `repo-root/.venv`。

**超时（必守）**：调用本 skill 下 `scripts/*.py` 或配合的 `git` 命令时须设显式 shell 等待上限，禁止无限阻塞。`--help`、单条 Issue/PR 查询等 **60s**；`repo_manager` clone/大仓更新、`pr_creator --create` 等 **300s**。`gitcode_client` HTTP 内置约 30s，不等同于整条命令上限。超时后记录输出与退出码，勿向用户谎称 API/PR 已成功。

### 常见误用

| 场景 | 正确做法 |
|------|----------|
| 本地仓不存在 / 要 clone | **`repo_manager.py --ensure-clone --workspace <name>`**；禁止手写 `git clone` 或 shell 拼 token URL |
| 获取 Issue | `issue_fetcher --number N` 或 `--issue N`（与 `--list`/`--create` **三选一**）；**主仓 Issue 须** `--source upstream`；bench/个人 fork 题用 `--source fork` |
| `--source` | `fork`（默认，且须在 JSON 配置 `fork.owner`）或 `upstream`；对 `issue_fetcher` 的 `--number`/`--list`/`--create` 与 `issue_commenter` 均生效 |
| `--issue` 含义 | **fetch**：`issue_fetcher` 取编号；**关联**：`pr_creator --create … --issue N`；勿写 `issue_fetcher --create --issue N` |
| 获取 PR | `pr_creator --number N`（与 `--list`/`--create` **三选一**）；主仓 PR 用 `--target-project upstream` |
| 列出/按作者筛 PR | `pr_creator --list [--state open] [--author <login>]`；**「只检视某人提交的 PR」必须加 `--author <login>`**（按 `user` 字段精确过滤，含本账号自己提交的跨仓 MR 会被排除），勿凭标题/记忆猜作者 |
| PR 评论/标签 | `pr_commenter --number N`：长 Markdown 用 `--comment-file`；`dev-reviewer render-comments` 生成的代码审查行评也必须每条 finding 独立 `--comment-file` + `--path`/`--position`（**合并后新文件行号**，先跑 `skills/dev-reviewer/scripts/code_review_runner.py resolve-positions` / `validate-comments`，见 pr_guide）。`pr_commenter.py` 会拒绝将带 `dev-reviewer` 签名或 `[Must Fix]` / `[Should Fix]` 标签的检视意见作为普通评论发布；只有架构/文档类例外才可显式加 `--allow-review-discussion-comment`。简单短评仍可用 `--comment`。**Issue/PR 非特定内容必须用简体中文**（见 `issue_guide`/`pr_guide`「正文语言」；评论见 `skills/dev-reviewer/SKILL.md`「评论语言」）。勿把长 Markdown 塞进 `--comment`，否则 shell 可能截断参数并误报缺 `--workspace` |
| 检视意见解决状态 | 行评加 `--need-to-resolve` 标记为待闭环 discussion；复检闭环后 `pr_commenter --number N --resolve <discussion_id>` 置为已解决（`--reopen` 反向重开）。**`discussion_id` 必须用 `pr_creator --number N` 评论列表里的 `discussion_id`（哈希串），切勿用数字 `id`**，否则报 `discussion not found`；评论的 `resolved` 字段为当前解决状态 |
| 全部闭环后审批 | `pr_commenter --number N --approve` 在评论区发 `/approve` 与 `/lgtm`；**仅当** `pr_creator --number N` 返回 `unresolved_discussions_count == 0` 且无遗留 Must Fix 时才可执行 |
| 创建 PR | `pr_creator --create --head <branch> --base <integration_base> --branch-base <branch_base> …`（任务卡；**禁**省略 `--base`） |

所有脚本位于本 skill 的 `scripts/` 目录。**执行下列命令前，将 shell 工作目录切换到本 skill 根目录**（包含本 SKILL.md 的目录）：

```text
scripts/
```

| 脚本 | 用途 |
|------|------|
| `config_loader.py` | 加载 `gitcode-repo.json`、解析 `workspaces[]`（供其它脚本调用）。 |
| `gitcode_client.py` | GitCode API v5 客户端，供其它脚本调用。 |
| `issue_fetcher.py` | 获取、搜索、创建 Issue（`--source fork\|upstream`，默认 fork）。 |
| `issue_commenter.py` | 发表评论、从文件读取评论、添加标签（`--source fork\|upstream`，默认 fork）。 |
| `pr_creator.py` | 获取、搜索/列出、创建 PR/MR（默认 fork；勿手写 GitLab 式 merge_requests API）。**`--create` 默认跑合入校验**（见 `integration_guard.py`）。 |
| `pr_commenter.py` | 发表 PR 评论、从文件读评论、代码行评（`--path`/`--position`）、添加标签（`--target-project fork\|upstream`）。 |
| `integration_guard.py` | G7b 合入前校验：禁止 first-parent 上的 merge commit、夹带非本次 commit、（可选）超 review scope 文件。 |
| `repo_manager.py` | clone、更新、读取文件、列目录。 |
| `issue_poller.py` | 轮询 assign、mention、标签触发的 Issue 事件。 |
| `webhook_manager.py` | 管理 GitCode 仓库 Webhook（列出 / 创建 / 删除），把事件推送到 Gateway。 |

Windows 中文环境下，若命令输出包含中文 JSON，优先使用能保持 UTF-8 的执行环境；如果出现乱码，可设置控制台编码或用 IDE/代码执行器运行脚本。

### Windows：`git`、路径与 Shell 选择

同一台机器上，**PowerShell / cmd** 里能成功的命令，换到 **Git Bash / MSYS bash** 可能失败（或 `cd` 未落到预期目录），常见原因如下：

| 现象 | 说明 |
|------|------|
| `cd "D:\foo\bar"` | 在部分 bash 中，`\` 与盘符写法不是 Unix 语义；即使有引号，`cd` 也可能失败或未切到你以为的目录。 |
| 起始目录 | Agent 自动化终端常见默认 shell 为 **PowerShell**；与用户本地手动打开的 **bash** 会话起始目录不一定相同。 |

**约定（检出本地仓、核对 `git remote -v` 等）：**

1. **最稳妥**：**不依赖 `cd`**，用 `git -C <仓库路径> <子命令> …`（Git 在指定目录下执行，避免「以为已 cd 成功其实没有」）。例如（`<父路径>`、`owner`、`repo` 均为占位，按需替换为你的本地目录与克隆结构；下面假定盘符为 `D:`）  
   PowerShell / cmd：`git -C "D:\<父路径>\owner\repo" remote -v`
   Git Bash：`git -C /d/<父路径>/owner/repo remote -v`
2. **若必须写相对路径或脚本里多次 git**：再考虑先 `cd`，但仍建议用 **绝对路径** + 上文 `git -C`；跨 shell 时路径规则仍按下面两条。
3. **Windows 宿主**：路径优先 `D:\…` 或 `D:/…`（正斜杠在多数场景可用）。
4. **若必须坚持 bash 且手写 `cd`**：盘符目录用 **MSYS/Git Bash 写法**  
   `cd /d/<父路径>/owner/repo && git remote -v`（把 `D:\` 对应为 `/d/`；`<父路径>` 等为占位）。
5. **若仍用「当前目录 + git」**、非 `git -C`：先确认目录（bash：`pwd`；PowerShell：`Get-Location`），再下结论。对用户给可复制命令时按对方 shell 给 **一套**示例，避免混用路径风格。

本节以下各命令示例以 bash 风格书写，在 Unix/macOS/Linux 或 **已使用 `/x/…` 盘符映射的 Git Bash** 上最直接；Windows 宿主上由 Agent 代跑终端命令时，**默认偏向 PowerShell** 与同路径的 `git`/`python` 调用。

## Issue 与 PR

Issue / PR 的**模板填写、分步流程、CLI 示例与排错**见独立指引（操作前切换到本 skill 根目录）：

| 主题 | 指引 | 正文模板 |
|------|------|----------|
| Issue | [`references/issue_guide.md`](references/issue_guide.md) | [`assets/issue_template.md`](assets/issue_template.md) |
| PR/MR | [`references/pr_guide.md`](references/pr_guide.md) | [`assets/pr_template.md`](assets/pr_template.md) |

要点：Issue **创建**（bench 等）默认 `--source fork`（须配置 `fork.owner`）；**读取主仓 Issue** 须 `--source upstream`；`issue_poller` 监控 upstream，`--auto-trigger` 会带 `upstream` 参数触发处理；长正文用 `--body-file`；创建前先核对 `gitcode-repo.json` 与工作区分支；标签须已在目标仓库存在。

## 本地仓库操作

**Clone / 首次落盘（必守）**：本地仓不存在、`local_repo.path` 为空、或目录下无 `.git` 时，**必须**用本 skill 的 `repo_manager.py`（读 `gitcode-repo.json` + `GITCODE_TOKEN` / `gitcode_token`，内部 `oauth2:<token>` URL，clone 后 scrub remote）。

**禁止**手写 `git clone`（含 `https://token@…`、`https://oauth2:…`、PowerShell/python 拼 URL）；禁止在 shell 里暴露 token。Windows 上 raw clone 易触发 GCM 弹窗，Agent 无法点击会挂死。

多工作区须加 `--workspace <name>`；自定义根目录用 `--clone-dir`（见下）。

确保 upstream 仓库已 clone；存在则更新：

```bash
python scripts/repo_manager.py --ensure-clone --config gitcode-repo.json --workspace <name>
```

更新本地仓库：

```bash
python scripts/repo_manager.py --update --config gitcode-repo.json
```

读取文件或列目录：

```bash
python scripts/repo_manager.py --get-file path/to/file.py --config gitcode-repo.json
python scripts/repo_manager.py --list-files path/to/dir --config gitcode-repo.json
```

如果需要自定义 clone 目录：

```bash
python scripts/repo_manager.py --ensure-clone \
  --clone-dir "D:/repos" \
  --config gitcode-repo.json
```

## 轮询

轮询配置在对应工作区条目的 `poller` 字段中（合并为单工作区配置后再传给脚本）。脚本会将已处理 Issue 和评论写入 `.issue-poller-state.json`，避免重复通知。

单次轮询：

```bash
python scripts/issue_poller.py --once --config gitcode-repo.json
```

持续轮询：

```bash
python scripts/issue_poller.py --config gitcode-repo.json
```

自动触发模式会调用本机 `claude` CLI（命令形如 `/gitcode-repo upstream <number>`，对应主仓 Issue）；只有在明确需要时才使用：

```bash
python scripts/issue_poller.py --auto-trigger --config gitcode-repo.json
```

## Webhook（实时推送）

Webhook 是替代轮询的更高效方式：GitCode 仓库在发生指定事件时直接 POST 请求到你的 `webhook_url`，无需本地持续轮询。

### 数据流

```text
GitCode 事件 → GitCode Webhook POST → ngrok/frp → Gateway http://localhost:29002/webhook/{path} → TriggerEngine → 分身执行
```

### 前提条件

1. Gateway 已启动，并设置 `WEBHOOK_ENABLED=true` 开启 webhook 端点；`WEBHOOK_HOST` 默认 `127.0.0.1`，`WEBHOOK_PORT` 默认 `29002`，都可通过环境变量覆盖。
2. 你的 Gateway 需要有一个 GitCode 可访问的公网 URL。推荐隧道工具：

   | 工具 | 安装 |
   |------|------|
   | [ngrok](https://ngrok.com/) | `ngrok http 29002` 获得公网 URL `https://xxx.ngrok-free.app` |
   | [frp](https://github.com/fatedier/frp) | 需自建 frps 服务端 |
   | [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) | 需域名 |

3. 在 JiuwenAvatar 前端或后台 API 为该分身创建 type=webhook 的触发器，`webhook_path` 与 Persona 模板中定义的一致（如 `/webhook/gitcode/pr-assigned`）。

### 创建 GitCode 仓库 Webhook

使用 `webhook_manager.py`：

```bash
# 列出已有 webhook
python scripts/webhook_manager.py --list --workspace 工作区名

# 创建 webhook（PR 事件 → 通知 committer 检视）
python scripts/webhook_manager.py --create \
  --url https://xxx.ngrok-free.app/webhook/gitcode/pr-assigned \
  --events pull_request \
  --secret "你的 webhook 密钥" \
  --workspace 工作区名

# 创建 webhook（Issue 事件 → 通知开发者分析）
python scripts/webhook_manager.py --create \
  --url https://xxx.ngrok-free.app/webhook/gitcode/issue-assigned \
  --events issues \
  --secret "你的 webhook 密钥" \
  --workspace 工作区名

# 删除 webhook
python scripts/webhook_manager.py --delete 123 --workspace 工作区名
```

### 在 GitCode 管理页面手动配置

你也可以在 GitCode 仓库页面的「设置 → Webhooks」中手动添加，填写公网 URL（如 `https://xxx.ngrok-free.app/webhook/gitcode/pr-assigned`）、选择事件类型和密码（对应 JiuwenAvatar 的 `webhook_secret`）。

### 注意事项

- `--url` 的最后一段路径必须与分身 trigger 的 `webhook_path` 一致（如 `/webhook/gitcode/pr-assigned`），否则 TriggerEngine 找不到匹配的触发器。
- `--secret` 可选；设置了则 GitCode 每次 POST 会用该密钥进行 HMAC-SHA256 签名，Gateway 端的 `webhook_secret` 必须匹配才能通过验证。
- 当前 Gateway 默认不启用 webhook；启用后默认仅监听 `127.0.0.1:29002`。若要公网直连可显式设置 `WEBHOOK_HOST=0.0.0.0`，但生产环境更建议由 Nginx/frp/ngrok 转发到本机端口，并结合 IP 白名单或签名机制限制来源。
- Webhook 适用于实时触发场景（PR assigned / Issue created / Push）；轮询 (`issue_poller.py`) 可作为兜底补充，比如每天一次检查是否有 Webhook 漏掉的事件。

## 使用原则

- **Main 调用脚本前**：在 skill 根目录对目标脚本执行 `python scripts/<name>.py --help`，确认参数后再调 API（见上文「先 `--help` 确认参数」）。
- 先读 `gitcode-repo.json`：按 `name`、`local_repo.path` 或用户指定的本地仓路径匹配 `workspaces[]`；无条目则补充，有则校验后再选定当前工作区。
- **主动同步**：操作前用本地 `git -C …` 核对 remote/分支，发现与 JSON 不一致时先更新配置再调 API（见上文「主动同步工作区 Git 信息」）。
- 涉及 Git remote 时，优先用 **`git -C <本地仓绝对路径> remote -v`** 或 **`git -C … remote get-url <name>`** 核对（不依赖 `cd`，见上文「路径与 Shell 选择」），不要把 fork 操作误发到主仓。若用户用 bash 报错而 PowerShell 正常，对照该节，勿把路径/Shell 问题误判成仓库配置错误。
- **创建 Issue**：阅读 `references/issue_guide.md`，在 **本 skill 根目录** 从 `assets/issue_template.md` 复制 `issue-body.md`；长正文用 `--body-file`；**标题与正文叙述须简体中文**（特定内容例外见 `issue_guide`「正文语言」）；成功或 `--dry-run` 正常结束后 **删除**（失败时保留至重试）；评论草稿 `comment.md` 同理；**禁止** commit 到业务仓。
- **创建 PR**：G7b 读 `pr_guide.md`；`--create` 须显式 `--base`/`--branch-base`（任务卡）；`pr-body.md` 仅在本 skill 目录，**标题与正文叙述须简体中文**（见 `pr_guide`「正文语言」），用完删
- 对正式仓的写操作应由用户明确授权；测试写操作优先使用测试仓或 dry run。
- 脚本标准输出通常是 JSON；失败时读取 `error`、`status_code` 和 API 返回体定位问题。

## 参考资料

- `references/gitcode_api_reference.md`：GitCode API 路径与字段速查。
- `assets/gitcode-repo.example.json`：配置字段样例（复制为 `gitcode-repo.json` 或任意路径，并在命令中加 `--config`）。
- `references/issue_guide.md`：Issue 模板流程、CLI 与注意事项。
- `assets/issue_template.md`：Issue 正文模板（配合 `--body-file`）。
- `references/pr_guide.md`：PR/MR 模板流程、CLI 与常见错误。
- `assets/pr_template.md`：PR 正文模板（含 `/kind` 与社区自检清单）。
