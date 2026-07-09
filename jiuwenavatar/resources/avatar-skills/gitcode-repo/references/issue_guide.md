# Issue 操作指引

> 正文模板文件：`assets/issue_template.md`  
> 执行命令前将 shell 工作目录切换到 `skills/gitcode-repo`（本 skill 根目录）。  
> **Main**：首次调用或切换子命令前，先 `python scripts/issue_fetcher.py --help`（评论/标签用 `issue_commenter.py --help`），再拼正式命令。

## 正文模板

创建或代用户填写 Issue 时，**优先**从 `assets/issue_template.md` 复制出临时文件（如 `issue-body.md`），再按类型填全必填节；多 Agent 编排时 **仅 Leader** 执行创建，填好后将摘要交给 `dev-analyzer`。

### 临时正文（用完即删，硬约束）

| 规则 | 说明 |
|------|------|
| **路径** | `issue-body.md` **仅**在 **`skills/gitcode-repo/`** 生成；**禁止**在业务仓 `repo-root` 落盘 |
| **文件名** | 统一 **`issue-body.md`**；评论草稿用 **`comment.md`**（同目录） |
| **生命周期** | 仅供 `--body-file` / `--comment-file`。**删除**：创建成功，或 `--dry-run` 正常结束。**保留**：创建失败/非零退出时 **保留** 草稿以便改错重试；修复后重跑成功或用户确认放弃后再删除 |
| **Git** | **禁止**纳入业务仓 commit；`git add`/commit 前用 `git status` 确认无 `issue-body.md` / `comment.md`。本 skill 目录 `.gitignore` 已忽略上述文件名（机械兜底） |

| 步骤 | 说明 |
|------|------|
| 1. 复制模板 | 在 **skill 根目录** `cp assets/issue_template.md issue-body.md`（Windows 可用等价复制命令） |
| 2. 填标题 | Issue **title**（非正文）：`[Bug\|Feature\|Refactor\|Docs] <module>：一句话摘要`；**摘要须简体中文** |
| 3. 填正文 | 替换各节 `【待填写】`；叙述段 **须简体中文**；**元信息** 表的 `module` 须与后续 `doc/<module>/` 一致 |
| 4. 按类型裁剪 | Bug 保留「复现步骤 / 实际结果 / 期望结果」，删「用户故事」；Feature 相反；不适用章节删除或标「不涉及」 |
| 5. 发布前清理（正文） | 删除模板顶部 HTML 注释块、各节 `> 填写指引` 引用块（勿进入 GitCode 正文） |
| 6. 创建与打标 | `--body-file issue-body.md` 创建；标签用 `issue_commenter.py --add-labels` 单独添加（须已在仓库存在） |
| 7. 删除临时文件 | 创建/`--comment` 成功或 `--dry-run` 正常结束后 **删除** `issue-body.md`（及 `comment.md`）；失败时 **保留** 至重试成功或用户放弃 |

**填写原则**（与 `dev-analyzer` 输入对齐）：只写可核实事实与已读代码证据；无证据写「待确认」；Issue 中不写未验证根因或具体修复方案（实现方案属设计阶段）。

### 正文语言（硬约束）

**非特定内容必须使用简体中文**：Issue 标题中的说明性文字、正文各节（背景、复现、期望、验收标准等）、评论与 `comment.md` 草稿中的叙述。**禁止**用英文写问题描述、影响说明、验收标准等说明段。

**允许保留英文的特定内容**：源码与标识符、路径/类名/函数名、`module` 目录名、日志/堆栈/终端原文、第三方 API/错误码原文、类型前缀 `[Bug]` 等标签、无稳定中文译名的专有名词（宜首次中英并列）。

**全文英文例外**：仅当该 Issue 所属讨论链或任务/仓库规范**已全文英文且无中文**，且**明确要求**英文协作时，方可整篇使用英文。

## 获取与搜索

> **参数**：获取单个 Issue 用 `--number <n>`（`--issue <n>` 为等价别名）。勿与 `pr_creator.py --issue`（创建 PR 时**关联** Issue）混淆。

### Issue 来源（`--source`）

| 值 | 含义 | 何时使用 |
|----|------|----------|
| `fork`（**默认**） | 读/写 `fork.owner` / `fork.repo`（**须**配置 `fork.owner`） | bench 造题、个人 fork 内创建/读写 Issue |
| `upstream` | 读/写 `upstream.owner` / `upstream.repo` | **主仓** Issue 拉取、轮询、社区任务（Aidlc 拉题推荐） |

- `issue_fetcher.py` 的 `--number` / `--list` / `--create` 与 `issue_commenter.py` 均支持 `--source`。
- 返回 JSON 的 `html_url` 可用来确认实际命中的仓库（fork 与 upstream 的 owner 不同）。

获取单个 Issue（含评论，分页拉全）：

```bash
# 主仓 Issue（Aidlc / 轮询跟进，推荐）
python scripts/issue_fetcher.py --number 42 --source upstream --config gitcode-repo.json --workspace <name>
# fork / bench Issue（须已配置 fork.owner）
python scripts/issue_fetcher.py --number 42 --source fork --config gitcode-repo.json --workspace <name>
```

列出或搜索 Issue：

```bash
python scripts/issue_fetcher.py --list --state open --config gitcode-repo.json
python scripts/issue_fetcher.py --list --labels bug --config gitcode-repo.json
python scripts/issue_fetcher.py --list --assignee <user> --config gitcode-repo.json
python scripts/issue_fetcher.py --list --search "error" --config gitcode-repo.json
# 主仓 open issues
python scripts/issue_fetcher.py --list --state open --source upstream --config gitcode-repo.json
```

## 创建 Issue

推荐拆成多个子步骤：先确认 API 连通性，再创建，最后补充标签。这样即使标签权限或正文格式有问题，也能保留已创建成功的 Issue。

1. **连通性**（只读验证配置、token、仓库；默认探测 fork）：

```bash
python scripts/issue_fetcher.py --list --state open --per-page 1 --config gitcode-repo.json
# 若目标为主仓，可额外探测 upstream：
python scripts/issue_fetcher.py --list --state open --per-page 1 --source upstream --config gitcode-repo.json
```

2. **从模板生成正文**（避免命令行转义、反引号或换行导致内容变形）：

```bash
cp assets/issue_template.md issue-body.md
# 编辑 issue-body.md 后创建（title 建议含类型与 module）
python scripts/issue_fetcher.py --create \
  --title "[Bug] web-config: 简要描述" \
  --body-file issue-body.md \
  --config gitcode-repo.json
```

3. **补充标签**（可选，标签须已存在于仓库）：

```bash
python scripts/issue_commenter.py --number 42 \
  --add-labels "bug" \
  --config gitcode-repo.json

# 主仓 Issue 评论/打标
python scripts/issue_commenter.py --number 42 \
  --comment "处理进展说明" \
  --source upstream \
  --config gitcode-repo.json
```

`issue_fetcher.py --create --labels ...` 也会分步执行：先创建 Issue，再调标签 API；标签失败时 JSON 的 `warnings` 会说明，已创建的 Issue 不会丢失。

简单正文也可直接创建：

```bash
python scripts/issue_fetcher.py --create \
  --title "Bug: 简要描述" \
  --body "详细描述" \
  --config gitcode-repo.json
```

## 评论与标签

```bash
python scripts/issue_commenter.py --number 42 \
  --comment "处理进展说明" \
  --config gitcode-repo.json

python scripts/issue_commenter.py --number 42 \
  --comment-file comment.md \
  --config gitcode-repo.json

python scripts/issue_commenter.py --number 42 \
  --add-labels "in-progress" \
  --config gitcode-repo.json
```

**注意**：创建 Issue 或添加标签前，标签名应已存在于仓库；不存在的标签可能导致 GitCode API 返回错误。

## 清理

- **`--create` / 评论提交成功或 `--dry-run` 正常结束**：删除临时 **`issue-body.md`**、**`comment.md`**。
- **API 失败/非零退出**：**保留** 草稿以便改错重试；修复后重跑成功，或用户确认放弃后再删除。
- 若在业务仓 `repo-root` 误生成：删除文件；若已 `git add`，从索引移除且 **不得** 随 feature 分支推送。
- 本 skill 目录 `.gitignore` 已忽略上述临时文件名（兜底；仍须主动删除）。
