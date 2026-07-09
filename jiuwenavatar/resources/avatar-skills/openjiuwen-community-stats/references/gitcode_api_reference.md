# GitCode API v5 只读接口参考

本 Skill 使用的 GitCode API 端点参考。
Base URL: `https://api.gitcode.com/api/v5`
认证: `access_token` query param（公开接口可省略）

> **安全说明**：本 Skill 仅使用 GET 请求（只读操作），不调用任何写入接口。

## 组织级查询

### 获取组织仓库列表

```
GET /orgs/:org/repos
```

参数:
- `page`, `per_page`: 分页（默认 per_page=20，最大 100）

响应关键字段:
- `name`: 仓库名
- `stargazers_count`: Star 数
- `forks_count`: Fork 数
- `open_issues_count`: Open Issue 数
- `watchers_count`: Watch 数

## 仓库级查询

### 获取仓库详情

```
GET /repos/:owner/:repo
```

### 获取贡献者列表

```
GET /repos/:owner/:repo/contributors
```

参数: `page`, `per_page`

### 获取下载统计

```
GET /repos/:owner/:repo/download_statistics
```

响应关键字段:
- `download_statistics_history_total`: 历史总下载量
- `download_statistics_detail[].today_dl_cnt`: 每日下载量
- `download_statistics_detail[].pdate`: 日期

## Issue 查询（只读）

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
- `created_at`, `updated_at`, `closed_at`
- `html_url`

### 获取 Issue 评论

```
GET /repos/:owner/:repo/issues/:number/comments
```

参数: `page`, `per_page`

响应关键字段:
- `id`, `body`, `user.login`, `created_at`

## PR 查询（只读）

### 获取 PR 列表

```
GET /repos/:owner/:repo/pulls
```

参数: `state`, `head`, `base`, `page`, `per_page`

### 获取 PR 详情

```
GET /repos/:owner/:repo/pulls/:number
```

## Tag/Release 查询（只读）

### 获取 Tag 列表

```
GET /repos/:owner/:repo/tags
```

参数: `page`, `per_page`

响应关键字段:
- `name`: Tag 名称
- `message`: Tag 信息
- `commit.sha`: Commit SHA
- `commit.date`: Commit 时间

### 获取 Release 详情

```
GET /repos/:owner/:repo/releases/tags/:tag
```

响应关键字段:
- `tag_name`, `name`, `body`
- `prerelease`: 是否预发布
- `created_at`
- `assets[].name`: 附件名
- `assets[].browser_download_url`: 下载地址

## 贡献者统计（只读）

### 获取单个贡献者统计

```
GET /repos/:owner/:repo/contributors/statistic
```

参数:
- `author`: 贡献者用户名（必填）
- `since`: 起始日期
- `until`: 结束日期
- `ref_name`: 分支/Tag/Commit

响应关键字段:
- `name`, `email`
- `overview.additions`, `overview.deletions`
- `overview.commit_count`
- `contributions[]`: 每日贡献明细

## 限流

- 50 次/分钟，4000 次/小时
- 超限返回 429，需等待后重试
- `gitcode_client.py` 已内置自动重试逻辑

## 本 Skill 不使用的接口

以下接口涉及写操作，本 Skill **不调用**：

- `POST /repos/:owner/:repo/issues` - 创建 Issue
- `PATCH /repos/:owner/:repo/issues/:number` - 更新 Issue
- `POST /repos/:owner/:repo/issues/:number/comments` - 创建评论
- `POST /repos/:owner/:repo/issues/:number/labels` - 添加标签
- `DELETE /repos/:owner/:repo/issues/:number/labels/:name` - 删除标签
- `POST /repos/:owner/:repo/pulls` - 创建 PR
