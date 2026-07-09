# PR/MR 操作指引

> 正文模板文件：`assets/pr_template.md`  
> 执行命令前将 shell 工作目录切换到 `skills/gitcode-repo`（本 skill 根目录）。  
> **Main**：`--list` 与 `--create` 参数不同；切换模式前先 `python scripts/pr_creator.py --help`，再执行查重或创建。

## 正文模板

- **Aidlc 流水线 G7b**：**G7a PASS 后**由 Leader 执行 PR 创建；**必须**按下方流程复制 `assets/pr_template.md` → `pr-body.md`，填全 `/kind`、元信息、验证与自检清单，并在模板中写入 **`doc/<module>/review.md` 审查摘要**（见 `dev-leader` / 各平台 `dev-leader` skill）。**G7a**（建分支、commit、push）见 `dev-leader` workflow「G7a Git 收口」。
- **独立 PR / 社区规范**：同样**优先**从 `assets/pr_template.md` 复制 `pr-body.md`；无 Aidlc 审查产物时，「审查摘要」节可删或写「不涉及」。

### 临时正文（用完即删，硬约束）

| 规则 | 说明 |
|------|------|
| **路径** | `pr-body.md` **仅**在 **`skills/gitcode-repo/`**（本 skill 根目录）生成；**禁止**在业务仓 `repo-root`（含 `doc/` 所在项目）落盘 |
| **文件名** | 统一 **`pr-body.md`**；**禁止** `pr_body.md` 等变体 |
| **生命周期** | 仅供 `pr_creator.py --body-file`。**删除**：`--create` 成功，或 `--dry-run` 正常结束。**保留**：`--create` 失败/非零退出时 **保留** `pr-body.md` 以便改错重试；修复后重跑成功或用户确认放弃后再删除 |
| **Git** | **禁止**将 `pr-body.md` / `pr_body.md` 纳入业务仓 commit 或 PR diff；若在 `repo-root` 执行 `git add`/commit，须先 `git status` 确认无上述文件。本 skill 目录下 `.gitignore` 已忽略上述文件名（机械兜底） |

| 步骤 | 说明 |
|------|------|
| 1. 前置检查 | G7a：自 `<fork.remote_name>/<branch_base>` 建分支（通常为 `origin/<branch_base>`；见 `dev-leader` §G7a）。G7b：`integration_guard` pass + 显式 `--base`/`--branch-base`；PR `--base` 是合入目标 `integration_base`，非 bench 基线 |
| 2. 查重 | `pr_creator.py --list --head <branch>`（跨仓 MR 时 `--list` 与 `--create` 的 `--target-project` 保持一致） |
| 3. 复制模板 | 在 **skill 根目录** `cp assets/pr_template.md pr-body.md`（**Aidlc G7b 必做**；独立 PR 同样推荐） |
| 4. 填标题 | PR **title**：`<module>: <动词>简要描述` 或 `fix(module): …`；**说明性文字须简体中文**（见「正文语言」） |
| 5. 填正文 | 替换 `【待填写】`；叙述段 **须简体中文**；**`/kind`** 保留一行且仅选一种；**元信息** 的 `module` 与 `doc/<module>/` 一致 |
| 6. 关联 Issue | 正文写 `Closes #N`；也可用 `--issue N`；Aidlc G7b 须在模板「审查摘要」填入 `doc/<module>/review.md` 要点 |
| 7. 发布前清理（正文） | 删除 HTML 注释块、各节 `> 填写指引`（勿进入 GitCode 正文） |
| 8. 创建 PR | `--body-file pr-body.md`；fork 内 MR 默认不加 `--target-project`；跨仓 upstream MR 加 `--target-project upstream` |
| 9. 删除临时文件 | `--create` 成功或 `--dry-run` 正常结束后 **删除** `pr-body.md`；`--create` 失败时 **保留** 至重试成功或用户放弃 |

**填写原则**：验证结果写已执行命令与结论；自检清单仅勾选已完成项；社区 `/kind` 与 openJiuwen 规范一致，描述不合规时可能需 `/check-pr` 重检（见模板内说明）。

### 正文语言（硬约束）

**非特定内容必须使用简体中文**（Issue/PR 标题中的说明性文字、正文各节叙述、评论、审查发帖）。不得因模板示例或习惯而用英文写变更摘要、验证说明、审查摘要等叙述段。

| 类别 | 要求 |
|------|------|
| **必须用中文** | 标题说明（`module` 后的描述）、正文各节、PR/Issue 评论、`pr-summary.md` / `pr-comment.md` 长评、行评中除标签外的句子 |
| **允许英文（特定内容）** | 源码与标识符、路径/类名/函数名、分支名、`module` 目录名、日志/堆栈/终端原文、第三方 API/错误码原文、`[Must Fix][Code]` 等维度标签、Conventional Commits 前缀（如 `fix(web-config):`）、无稳定中文译名的专有名词（宜首次中英并列） |
| **全文英文例外** | 仅当关联 PR/Issue/讨论**已全文英文且无中文**，且任务或仓库规范**明确要求**英文协作时，方可整篇使用英文 |

代码审查评论语言细则见 `skills/dev-reviewer/SKILL.md`「评论语言」。

## API 与推荐顺序

**优先使用 `pr_creator.py`**（`https://api.gitcode.com/api/v5`，路径 `/repos/{owner}/{repo}/pulls`）。  
**禁止**自行请求 `https://gitcode.com/api/v5/projects/.../merge_requests`（GitLab 风格，会 404）。

1. **连通性**：`pr_creator.py --list --per-page 1` 确认 token 与 fork 仓库无误。
2. **查重**：`pr_creator.py --list --head <branch>` 查看是否已有 open PR。
3. **创建**：确认分支已推送到 fork，再 `--create`（默认在 fork）。向 upstream 跨仓 MR 时加 `--target-project upstream`（upstream 已归档时会失败，改 fork 内 MR 或网页链接）。

## 获取单个 PR

```bash
# fork 内 PR（默认）
python scripts/pr_creator.py --number 42 --config gitcode-repo.json --workspace <name>
# 主仓 PR
python scripts/pr_creator.py --number 42 --target-project upstream --config gitcode-repo.json
```

返回 JSON 含评论与标签。

## 列出与搜索 PR

```bash
python scripts/pr_creator.py --list --config gitcode-repo.json --workspace <name>
python scripts/pr_creator.py --list --head feature/my-branch --config gitcode-repo.json
python scripts/pr_creator.py --list --search "web-config" --state open --config gitcode-repo.json
```

`--search` 先试 API `q`；失败或为空时再本地按标题/正文过滤。`--dry-run` 不写 API，`--number` 仍会读。

## 评论与标签

长评论用 skill 根目录 `pr-comment.md`（或 `pr-summary.md`）+ `--comment-file`；成功后删除，失败保留。已列入 `.gitignore`。

### `--comment` 与 Shell（代码审查常见坑）

勿把**完整 Markdown**（多行、反引号代码块、`$`、`"`、`|` 等）直接塞进 `--comment`。PowerShell / bash 会截断或错误拆分参数，后续 `--config` / `--workspace` 可能到不了脚本。

| 现象 | 实际原因 |
|------|----------|
| 已传 `--workspace` 仍报「配置文件含 N 个工作区，请使用 --workspace」 | `--comment` 过长或含特殊字符，shell 吃掉后面的参数 |
| 短 `--comment "ok"` + `--dry-run` 成功，加长 Markdown 失败 | 同上，优先改用 `--comment-file` |

**推荐**：讨论区长文 → `--comment-file`；**代码审查行评**若来自 `dev-reviewer render-comments` → 每条 finding 使用独立 `--comment-file` + `--path` + `--position`；简单短评仍可用 `--comment`。勿把多行 Markdown、代码块、反引号直接塞进 `--comment`。排障时先 `--dry-run` + 短评论确认 config/workspace。

**语言**（代码审查）：评论叙述 **必须使用简体中文**（与 PR 正文语言一致）；不得将说明性句子写成英文。仅当 PR/Issue/讨论**已全文英文且无中文**、且规范要求英文协作时，评论叙述方可英文。详见 `skills/dev-reviewer/SKILL.md`「评论语言」与上文「正文语言（硬约束）」。

### 行评 `--position`

GitCode 将 `--position` 映射为 **`diff_position.start_new_line`（合并后新文件行号）**，与 GitHub 的 diff 内序号不同。

- **正确**：与 `dev-reviewer` `result.json` 的 `location` 一致，例如 `full_compact_processor.py:965` → `--position 965`。
- **错误**：使用 `pr.diff` 文件的文本行号（例如 diff 第 194 行），会在 UI 上挂到源文件第 194 行，与评论内容无关。
- **推荐**：Leader 在 G7 发帖前于业务仓执行 `python skills/dev-reviewer/scripts/code_review_runner.py resolve-positions --module <module> --repo-root <repo-root>`，再 `pr_commenter.py --path ... --position ...` 发帖。

代码审查流程详见 `skills/dev-reviewer/SKILL.md`「PR 行评与 GitCode」。

```bash
# 讨论区评论（短文可直接 --comment）
python scripts/pr_commenter.py --number 42 \
  --comment "处理进展说明" \
  --config gitcode-repo.json \
  --workspace <name>

python scripts/pr_commenter.py --number 42 \
  --comment-file pr-comment.md \
  --config gitcode-repo.json \
  --workspace <name>

# 代码行评：简单短评可用 --comment + path/position
python scripts/pr_commenter.py --number 42 \
  --comment "[Must Fix][Code] 请将 HTTP 调用下沉到 Repository 层，避免 Service 直连客户端。" \
  --path src/foo.py --position 12 \
  --config gitcode-repo.json \
  --workspace <name>

# dev-reviewer 长 Markdown 行评：每条 finding 一个 comment-file
python scripts/pr_commenter.py --number 42 \
  --comment-file ../dev-reviewer-comments/CR-001.md \
  --path src/foo.py --position 12 --need-to-resolve \
  --config gitcode-repo.json \
  --workspace <name>

# 打标签（标签须已在仓库存在）
python scripts/pr_commenter.py --number 42 \
  --add-labels "needs-review" \
  --target-project upstream \
  --config gitcode-repo.json
```

API 参考：[获取单个 Pull Request](https://docs.gitcode.com/docs/apis/get-api-v-5-repos-owner-repo-pulls-number)、[提交 pull request 评论](https://docs.gitcode.com/docs/apis/post-api-v-5-repos-owner-repo-pulls-number-comments)、[创建 Pull Request 标签](https://docs.gitcode.com/docs/apis/post-api-v-5-repos-owner-repo-pulls-number-labels)。

## 创建 PR

### Aidlc 流水线 G7b（默认）

1. 复制模板：`cp assets/pr_template.md pr-body.md`
2. 填写 `pr-body.md`：元信息、`/kind`、变更摘要、验证结果、自检清单；**必须**填写「审查摘要」节（摘自 `doc/<module>/review.md`）及「文档与计划」表中的审查结论。
3. 删除 HTML 注释与各节 `> 填写指引` 后，用 `--body-file pr-body.md` 创建 PR。
4. **`--create` 成功或 `--dry-run` 正常结束后** 删除 `pr-body.md`；**`--create` 失败时保留**以便改错重试（勿留在业务仓 Git 中）。

**fork 内 MR**（`feature` → `develop`，`--head` 只需分支名）：

```bash
cp assets/pr_template.md pr-body.md
# 编辑 pr-body.md（含 doc/<module>/review.md 审查摘要）后：
python scripts/pr_creator.py --create \
  --title "web-config: fix empty Agent/Team save" \
  --head feature/my-branch \
  --base develop \
  --body-file pr-body.md \
  --config gitcode-repo.json \
  --workspace <name>
```

**向 upstream 跨仓 MR**（`--head` 可只写分支名，脚本自动补全为 `fork_owner:branch`）：

```bash
cp assets/pr_template.md pr-body.md
python scripts/pr_creator.py --create \
  --title "web-config: fix empty Agent/Team save" \
  --head feature/my-branch \
  --base develop \
  --body-file pr-body.md \
  --target-project upstream \
  --config gitcode-repo.json
```

**关联 Issue**（可选；正文仍建议保留 `Closes #N`）：

```bash
python scripts/pr_creator.py --create \
  --title "web-config: fix empty Agent/Team save" \
  --head feature/my-branch \
  --base develop \
  --body-file pr-body.md \
  --issue 42 \
  --target-project upstream \
  --config gitcode-repo.json
```

### 独立 PR / 社区规范

从 `assets/pr_template.md` 生成 `pr-body.md` 后再创建。

**fork 内 MR**：

```bash
cp assets/pr_template.md pr-body.md
# 编辑 pr-body.md 后：
python scripts/pr_creator.py --create \
  --title "web-config: fix empty Agent/Team save" \
  --head feature/my-branch \
  --base develop \
  --body-file pr-body.md \
  --config gitcode-repo.json \
  --workspace <name>
```

**向 upstream 跨仓 MR**：

```bash
cp assets/pr_template.md pr-body.md
# 编辑 pr-body.md 后：
python scripts/pr_creator.py --create \
  --title "web-config: fix empty Agent/Team save" \
  --head feature/my-branch \
  --base develop \
  --body-file pr-body.md \
  --target-project upstream \
  --config gitcode-repo.json
```

可选：`--no-duplicate-check` 跳过创建前 open PR 查重；`--dry-run` 仅打印不写 API；`--skip-integration-check` 跳过合入校验（非默认）。

## 合入校验（integration_guard）

G7b **`--create` 默认执行**（`pr_creator.py` 内调 `integration_guard.py`）。独立复核：

```powershell
& $PYTHON scripts/integration_guard.py `
  --repo-root <local_repo.path> `
  --head <特性分支> `
  --base <integration_base> `
  --branch-base <branch_base> `
  --module <module>
```

`integration_base` 是 PR/MR 合入目标；`branch_base` 是 G7a 分叉点。bench 场景常见 `branch_base=bench-issue-N`、`integration_base=develop`，不要把二者混用。

| 检查 | FAIL 含义 |
|------|-----------|
| first-parent 上有 merge commit | 曾 `merge` 其它分支（夹带外来 commit） |
| `branch-base..head` 非 first-parent commit | 同上（rebase/merge 副作用） |
| `base...head` 文件 ⊄ review scope + `doc/<module>/` | PR 改动超出本次审查范围 |

exit 0 才允许 `--create`（或 G7b PASS）。

## 清理

- **`--create` 成功或 `--dry-run` 正常结束**：删除临时 **`pr-body.md`**（及误生成的 `pr_body.md`）；正文已进 GitCode 或 dry-run 已验完，本地草稿无保留价值。
- **`--create` 失败/非零退出**：**保留** `pr-body.md` 以便改错重试；修复后重跑成功，或用户确认放弃任务后再删除。
- 若在业务仓 `repo-root` 误生成上述文件：删除文件；若已 `git add`，从索引移除（`git restore --staged`）且 **不得** 随 feature 分支推送。
- 本 skill 目录 `.gitignore` 已忽略 `pr-body.md` / `pr_body.md` / `issue-body.md` / `comment.md`（兜底；仍须主动删除）。
- 勿提交 token；`gitcode-repo.json` 含密钥，按仓库 `.gitignore` 处理。

## 常见错误

| 现象 | 原因 |
|------|------|
| `创建 PR 时必须指定 --head` | `--create` 未带源分支；Aidlc 须先完成 **G7a**（建分支、commit、push），再 **G7b** `--head <特性分支>` |
| PR diff 为空 | 在 G7a 前提前 push 了空特性分支，或 Leader 未完成 G7a commit/push；按 `dev-leader` workflow §G7a 重做 |
| 合入校验 FAIL / `integration_check` | first-parent 存在 merge commit，或 `branch-base..head` 夹带非本次 commit，或相对 `--base` 的文件超出 `review.md` scope；重建线性分支或修正 `--base`/`--branch-base`；紧急 `--skip-integration-check`（须登记 Gate Evidence） |
| `unrecognized arguments: --issue`（issue_fetcher） | 应使用 `--number` 或 `--issue`（后者为别名）；旧版仅支持 `--number` |
| 业务仓 `.venv` 不存在 / python 报错 | 在 **`skills/gitcode-repo` 根目录**用系统 `python` 跑脚本，勿用 `<repo-root>/.venv` |
| 404，`/projects/.../merge_requests` | 用了错误 API（应用 `pr_creator.py`） |
| archived | upstream 已归档，勿 `--target-project upstream` |
| 400 BAD_REQUEST | 跨仓 MR 缺 `fork_path` 或 `head` 未用 `username:branch` |
| 409 / 重复 | 已有同 head 的 open PR，合并或关闭后再建 |
| PR 里出现 `pr-body.md` / `pr_body.md` | 在 `repo-root` 落盘或未按生命周期删除；应在 `skills/gitcode-repo/` 生成；成功/dry-run 后删除，失败时保留至重试 |
| `pr_commenter` 报「请使用 --workspace」但已传 `--workspace` | `--comment` 含 Markdown/反引号/换行等被 shell 截断；改用 `--comment-file`；`dev-reviewer` 长行评必须每条 finding 一个 comment-file |
| 行评落在错误行或 API 400 | `--position` 误用 `pr.diff` 文本行号；应使用合并后新文件行号，并跑 `dev-reviewer` 的 `code_review_runner.py resolve-positions` |

编程调用可使用 `gitcode_client.list_pull_requests` / `create_pull_request`；查重与创建默认落在 fork，勿用底层 `_request()` 绕过封装。
