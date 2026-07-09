# Bench 创建完整流程

从 **upstream 已合入 PR** 反推「修复前」基准，在 **fork** 上建 `bench-issue-N` 分支并创建可派工的 Issue。

## upstream / fork 边界

| 操作 | 仓库 | 方式 |
|------|------|------|
| 读 PR、commits、files、commit parents | **upstream** | `bench_from_pr.py`（GitCode GET） |
| `git fetch` 拉取对象 | **upstream** remote | `bench_git.py --upstream-url` |
| 建分支、`git push` | **fork** remote（通常 `origin`） | `bench_git.py --push --push-remote origin` |
| 创建 Issue | **fork** | `issue_fetcher.py --create --source fork` |

**禁止**：向 upstream push 分支；在 upstream 创建 bench Issue（勿依赖 API 返回的 upstream 样式 URL 误判成功）。

## 阶段总览

| 阶段 | 关键输出 | 验证点 |
|------|----------|--------|
| 1. 提取 PR | `fix_sha`、`parent_sha`、变更文件 | 父提交为修复**前**状态 |
| 2. 配置 Git | upstream remote、已 fetch | `git remote -v` 含 upstream + origin |
| 3. 建分支 | `bench-issue-N` @ `parent_sha` | 工作区干净；问题代码存在 |
| 4. 写 Issue | `issue-body.md` | 现象+复现+期望；**无泄题**（见 [issue_authoring.md](issue_authoring.md)） |
| 5. 创建 Issue | fork 上新 Issue URL | 正文可访问、module 合法 |

## 1. 提取 PR 信息（upstream，只读）

**API（upstream，仅 GET）**：

| 信息 | 端点 |
|------|------|
| PR 详情 | `GET /repos/{owner}/{repo}/pulls/{n}` |
| 提交 | `GET .../pulls/{n}/commits` |
| 文件 | `GET .../pulls/{n}/files` |

**推荐脚本**（本 skill）：

```bash
cd skills/bench-creator
python scripts/bench_from_pr.py --pr <N> \
  --config ../gitcode-repo/gitcode-repo.json \
  --workspace <name> --format json
```

记录：`pr_title`、`fix_sha`、`parent_sha`、`files[]`、`suggested_branch`。

多提交 PR 时默认取**最后一笔**为 fix；不确定时用 `--fix-sha` / `--parent-sha` 覆盖。详见 [commit_selection.md](commit_selection.md)。

## 2. 配置本地 Git

前提：`gitcode-repo.json` 中 `local_repo.path` 指向本地 clone，且 `upstream` / `fork` 已填。

1. 核对 JSON 与 `git -C <path> remote -v`（与 `gitcode-repo` skill 一致，先读后写）。
2. 若无 upstream：

```bash
git -C <path> remote add upstream https://gitcode.com/<upstream-owner>/<repo>.git
git -C <path> fetch upstream
```

## 3. 创建 bench-issue-N 分支（fork 推送）

**原则**：分支基点必须是 **`parent_sha`（修复前）**，不是 `fix_sha`。推送目标必须是 **fork**（`origin`），不是 `upstream`。

```bash
# 脚本方式（fetch upstream → 建分支 → push origin）
python scripts/bench_git.py \
  --repo-path <local_repo.path> \
  --parent-sha <parent_sha> \
  --branch bench-issue-1 \
  --upstream-url https://gitcode.com/<upstream-owner>/<repo>.git \
  --push-remote origin \
  --push
```

或手动（同样禁止 `git push upstream`）：

```bash
git -C <path> fetch upstream
git -C <path> branch bench-issue-1 <parent_sha>
git -C <path> checkout bench-issue-1
git -C <path> push -u origin bench-issue-1
```

**验证**：

- `git -C <path> log --oneline -1` 为 `parent_sha`
- `git -C <path> status` 干净
- 打开 PR `files` 中的路径，确认仍为**修复前**代码（可与 patch 中 `-` 行对照）

序号 `N`：若已有 `bench-issue-1`…`bench-issue-k`，下一分支为 `bench-issue-{k+1}`。

## 4. 编写 Issue 正文

**不要**在 bench-creator 内复制模板。使用 **`skills/gitcode-repo`**，并遵守 **[issue_authoring.md](issue_authoring.md)**（bench 防泄题，优先于通用 issue_guide）。

1. 复制 `gitcode-repo/assets/issue_template.md` → `issue-body.md`
2. 填写元信息（`module` 须与后续 `doc/<module>/` 一致）
3. 题面只写：**问题描述、复现、实际/期望、环境**（分支写 `bench-issue-N`，路径到文件/目录即可，**无行号**）
4. **「初步分析」写 1–3 条粗粒度**（子系统/目录/观测事件）；禁止函数名+行号+根因+upstream 修复 PR
5. **关联信息**不得含 upstream 修复 PR；`parent_sha`/`fix_sha` 只出现在 Agent 交付表，不进 Issue
6. 删除模板 HTML 注释与各节 `> 填写指引`
7. 标题：`[Bug] <module>：<用户可见摘要>`（勿用 `fix(scope):` 式修复标题）

`module` 推断：从变更路径首段或 PR scope，**勿**从修复函数名推断。

发布前 checklist 见 [issue_authoring.md](issue_authoring.md#发布前检查清单)。

## 5. 在 fork 创建 Issue

在 **`skills/gitcode-repo`** 目录执行；**必须** `--source fork`：

```bash
cd skills/gitcode-repo
python scripts/issue_fetcher.py --list --state open --per-page 1 \
  --config gitcode-repo.json --workspace <name>

python scripts/issue_fetcher.py --create \
  --title "[Bug] memory: ..." \
  --body-file issue-body.md \
  --config gitcode-repo.json --workspace <name> \
  --source fork
```

记录返回的 `number` 与 `html_url`。验收：`html_url` 须为 `https://gitcode.com/<fork.owner>/<fork.repo>/issues/<n>`，不得仅为 upstream 主仓路径。

## 6. 成果清单（交付给用户）

| 项目 | 示例 |
|------|------|
| 原始 PR | `https://gitcode.com/{upstream}/pull/{n}` |
| 基准提交 | `parent_sha` |
| 修复提交 | `fix_sha` |
| 分支 | `bench-issue-N` @ fork |
| 新 Issue | fork Issue URL |

## 清理

删除临时 `issue-body.md`、一次性 Python 片段；勿提交 token。

## 下一步

1. 在 bench 分支复现问题  
2. 编写/运行测试  
3. 修复并提 PR（通常经 `dev-leader` + `gitcode-repo`）
