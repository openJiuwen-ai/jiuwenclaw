# GitCode API v5 速查

Issue Resolver 使用的 GitCode API 端点参考。
Base URL: `https://api.gitcode.com/api/v5`
认证: `access_token` query param

## 配置文件 gitcode-repo.json

运行时配置（通常不入库），样例见 `assets/gitcode-repo.example.json`。

### 多工作区格式（推荐）

```json
{
  "gitcode_token": "",
  "workspaces": [
    {
      "name": "jiuwenclaw_wyk",
      "upstream": { "owner": "...", "repo": "...", "base_branch": "develop" },
      "fork": { "owner": "...", "repo": "...", "remote_name": "origin" },
      "local_repo": { "path": "D:/path/to/repo", "auto_clone": false, "auto_update": true },
      "poller": {}
    }
  ]
}
```

- 顶层 `gitcode_token` 所有工作区共享；也可改用环境变量 `GITCODE_TOKEN`。
- `workspaces[].name` 唯一，供 `--workspace` 选择。
- 操作前应用 `git -C <local_repo.path> remote -v` 等核对 remote/分支，并回写 JSON。

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--config <path>` | 配置文件路径，默认同目录或技能根目录下的 `gitcode-repo.json` |
| `--workspace <name>` | 选定 `workspaces[]` 条目；仅一条时可省略，多条时必填 |

解析逻辑见 `scripts/config_loader.py`；`GitCodeClient.from_config(path, workspace_name=...)` 同样支持。

### 旧版扁平格式（兼容）

无 `workspaces` 时，顶层直接写 `upstream` / `fork` / `local_repo` / `poller` 仍可被脚本读取。

## Issue

CLI `issue_fetcher.py` / `issue_commenter.py` 通过 `--source fork|upstream` 选择 `:owner/:repo`（**默认 fork**，且须配置 `fork.owner`）。**主仓 Issue 拉取** 用 `--source upstream`。底层客户端 Issue 方法均支持 `target_project`；`issue_poller.py` 监控 **upstream**，`--auto-trigger` 带 `upstream` 参数。

### 获取 Issue 列表

```
GET /repos/:owner/:repo/issues
```

参数:
- `state`: open / closed / all（默认 open）
- `labels`: 逗号分隔的标签名
- `assignee`: 指派人用户名
- `page`, `per_page`: 分页

### 获取 Issue 详情

```
GET /repos/:owner/:repo/issues/:number
```

响应关键字段:
- `number`, `title`, `body`, `state`
- `labels[].name`
- `assignee.login`
- `html_url`, `created_at`, `updated_at`

### 创建 Issue

```
POST /repos/:owner/:repo/issues
```

Body:
- `repo`: 仓库名
- `title`: Issue 标题
- `body`: Issue 描述（可选）

为提高成功率，创建 Issue 时不要直接携带 `labels`。先创建 Issue，
再调用标签接口添加标签。

### 更新 Issue

```
PATCH /repos/:owner/issues/:number
```

Body:
- `repo`: 仓库名（必填）
- `title`, `body`, `state`, `assignee`: 可选

### 获取 Issue 评论

```
GET /repos/:owner/:repo/issues/:number/comments
```

参数: `page`, `per_page`

响应关键字段:
- `id`, `body`, `user.login`, `created_at`

### 创建 Issue 评论

```
POST /repos/:owner/:repo/issues/:number/comments
```

Body: `{"body": "评论内容"}`

### Issue 标签

```
POST /repos/:owner/:repo/issues/:number/labels
```

Body: `["label1", "label2"]`（JSON 数组）

```
DELETE /repos/:owner/:repo/issues/:number/labels/:name
```

## Pull Request / MR

### 创建 PR

```
POST /repos/:owner/:repo/pulls
```

`:owner/:repo` 默认使用 fork；需要向 upstream 创建时显式指定 upstream 路径。

Body:
- `title`: PR 标题（必填）
- `head`: 源分支（必填；跨仓 PR 须 `username:branch`）
- `base`: 目标分支（必填）
- `body`: PR 描述（可选）
- `issue`: 关联 Issue 编号（可选，**string**；可自动填充 PR 标题与正文）
- `fork_path`: Fork 项目路径 `owner/repo`（**跨仓 PR 必填**）

跨仓 PR（`target_project='upstream'`）时，`:owner/:repo` 为 upstream 主仓；`head` 为 `fork_owner:branch`；`fork_path` 为 fork 的 `fork_owner/fork_repo`（`head` 已含 owner 时从 head 解析 owner，repo 取配置 fork.repo）。同仓 PR（默认 fork）不需要 `fork_path`，`head` 只需分支名。

加固建议：与创建 Issue 一样，使用 `access_token` query 认证；长 `body` 避免经 shell 拼接；创建前先在同一 `:owner/:repo` 上 `GET /pulls?state=open&head=...` 查重。客户端 `create_pull_request` 默认在 fork 仓库创建，并会在创建前做一次 open PR 预检查（可通过参数关闭）；可用 `target_project='upstream'` 改到 upstream，并自动附带 `fork_path`。

CLI：`scripts/pr_creator.py`（`--list` / `--create`）。**勿**使用 `gitcode.com` 上的 `/projects/.../merge_requests`（GitLab 风格，会 404）；基址必须是 `https://api.gitcode.com/api/v5`。

### 获取 PR 列表

```
GET /repos/:owner/:repo/pulls
```

参数: `state`, `head`, `base`, `page`, `per_page`, `q`；CLI 用 `--search`

### 获取单个 PR

```
GET /repos/:owner/:repo/pulls/:number
```

响应关键字段: `number`, `title`, `body`, `state`, `head`, `base`, `labels`, `html_url`, `merged`

CLI: `pr_creator.py --number <n> [--target-project fork|upstream]`

### 获取 PR 评论

```
GET /repos/:owner/:repo/pulls/:number/comments
```

参数: `page`, `per_page`

### 提交 PR 评论

```
POST /repos/:owner/:repo/pulls/:number/comments
```

Body:
- `body`（必填）
- `path`（可选，行评文件路径）
- `position`（可选，**合并后新文件行号**，对应 `diff_position.start_new_line`；不是 `pr.diff` 文本行号）
- `position_type`（可选：`text` 默认行评；`binary` 为文件级，忽略 `position`）

CLI: `pr_commenter.py --number <n> --comment ...` 或 `--comment-file pr-comment.md`

### PR 标签

```
GET /repos/:owner/:repo/pulls/:number/labels
```

```
POST /repos/:owner/:repo/pulls/:number/labels
```

Body: `["label1", "label2"]`（JSON 数组）

CLI: `pr_commenter.py --number <n> --add-labels "a,b"`

### 获取 PR 文件变更

```
GET /repos/:owner/:repo/pulls/:number/files
```

## 限流

- 50 次/分钟，4000 次/小时
- 超限返回 429，需等待后重试
- `gitcode_client.py` 已内置自动重试逻辑
