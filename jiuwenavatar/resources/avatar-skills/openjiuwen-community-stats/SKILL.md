---
name: openjiuwen-community-stats
description: **Sub-skill** of `openjiuwen-qa-guideline` — GitCode **community metrics** for openJiuwen (read-only GET). **Read `openjiuwen-qa-guideline/SKILL.md` first** for intent routing unless the user already @ the guideline or the request is clearly community-stats only. Then use for stars/forks/downloads/PRs/issues, overdue issues, trends, tags/releases, contributor stats. Never POST/PATCH/DELETE. Do not use for component API Q&A (→ product `openjiuwen-*` skills) or bug filing (→ `record-bugs`).
---

# openJiuwen 社区数据查询指引

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（社区信息查询），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，确认用户意图属于 **社区信息（A 类）** 而非组件答疑或 Bug 登记——除非用户已 `@openjiuwen-qa-guideline`，或问题明确仅为 Star/Issue/下载量/趋势等 GitCode 指标。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 社区信息查询（guideline **A 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **数据范围** | GitCode `openJiuwen` 组织（单仓查询须 `--repo`） |
| **操作性质** | **只读**（仅 GET，无写操作） |

本技能**不负责**：产品线 API/配置/排障（→ 各 `openjiuwen-*` 组件 skill）、`assets/` 快照取证、写入 `PENDING_BUGS.md`（→ `record-bugs`）。

> **安全声明**：本 Skill 仅使用 **GET 请求**（只读操作），不会对 GitCode 进行任何写操作（无 POST/PATCH/DELETE）。可安全用于自动化场景。

---

## ⚠️ 主体约束（最高优先级 - 执行任何查询前必读）

**用户问某个产品，就只查那个产品的仓库，不要查整个组织！**

### 用户问法 → 仓库名映射（必须严格遵守）

| 用户可能的问法 | --repo 参数 |
|---------------|-------------|
| swarm、jiuwenswarm、jiuwen-swarm、claw、jiuwenclaw | `jiuwenswarm` |
| studio、jiuwenstudio、jiuwen-studio、agent-studio | `agent-studio` |
| core、jiuwencore、jiuwen-core、agent-core | `agent-core` |
| core-java、agent-core-java、java版 | `agent-core-java` |
| runtime、agent-runtime | `agent-runtime` |
| docs、文档 | `docs` |
| openjiuwen、整体、全部、所有仓库、总共 | **不加 --repo**（才查整个组织） |

### 执行规则

1. **用户说 "swarm 的 issue"** → `get_issues.py --days 30 --repo jiuwenswarm`
2. **用户说 "studio 的 star"** → 只展示 agent-studio 的数据
3. **用户没说查哪个** → **先问用户**："您想查哪个仓库？可选：jiuwenswarm、agent-studio、agent-core，或整个 openJiuwen 组织？"

### 绝对禁止

- ❌ 用户问 swarm，却返回整个组织 395 个 issue
- ❌ 用户问 studio，却返回 jiuwenswarm 的数据
- ❌ 用户没指定，就自作主张查整个组织

**检查点**：执行脚本前问自己："用户问的是哪个产品？我加了 --repo 参数吗？"

---

## ⚠️ 输出字数限制（硬性约束）

**每次回复必须小于 10000 字，绝对不能超过！**

- 超过会被截断，用户看不到完整内容
- **禁止生成文件**（用户拿不到）
- 长列表必须分批输出：
  1. 先输出汇总（总数、按仓库分布）
  2. 每批 15-20 条明细（确保 < 10000 字）
  3. 输出后问："是否继续查看下一批？"

---

## 查询类型选择

| 查询类型 | 使用脚本 | 说明 |
|---------|---------|------|
| 实时数据（Star/Fork/下载量） | `get_stats.py` | 直接调 API，数据最新，耗时 2-3 分钟 |
| **超期 Issue（实时）** | `get_issues.py` | **直接调 API，数据最准确，推荐使用** |
| 超期 Issue（本地库） | `query_overdue.py` | 查本地库，秒级响应，需先运行 sync_data.py |
| 趋势分析（涨了多少 Star） | `query_trends.py` | 需要历史数据，查本地库 |
| 单仓 Tags / Release | `get_tags.py` / `get_release.py` | 直接调 API |

> **重要**：查询 Issue 时**优先使用 `get_issues.py`**（实时 API），除非需要频繁查询（此时用本地库更快）。

---

## 数据同步（定期执行）

本地数据库需要定期同步才能支持趋势分析和快速查询。**建议每天执行一次**：

```bash
python3 {SKILL_DIR}/scripts/sync_data.py
```

可选参数：
- `--repos-only`：仅同步仓库统计（Star/Fork 等）
- `--issues-only`：仅同步 Issue 数据

数据存储在 `{SKILL_DIR}/data/community.db`（SQLite）。

---

## 查询超期 Issue（推荐 get_issues.py）

当用户询问「有多少超期 issue」「哪些 issue 超过30天没解决」时，**优先使用实时 API 查询**：

```bash
python3 {SKILL_DIR}/scripts/get_issues.py --days 30
```

### 常用示例（get_issues.py - 实时 API）

```bash
# 超过 30 天未关闭的 open issue（全组织）
python3 {SKILL_DIR}/scripts/get_issues.py --days 30

# 指定仓库
python3 {SKILL_DIR}/scripts/get_issues.py --days 30 --repo agent-core

# 超过 60 天未关闭的 issue
python3 {SKILL_DIR}/scripts/get_issues.py --days 60

# 查看已关闭的 issue
python3 {SKILL_DIR}/scripts/get_issues.py --state closed --days 30

# 限制返回数量（默认不限制）
python3 {SKILL_DIR}/scripts/get_issues.py --days 30 --limit 50
```

### 备选：本地库查询（query_overdue.py）

如果需要**频繁查询**或**更多过滤条件**，可使用本地库（需先运行 `sync_data.py` 同步数据）：

```bash
# 超过 30 天未关闭的 issue（从本地库查询）
python3 {SKILL_DIR}/scripts/query_overdue.py --days 30

# 指定仓库
python3 {SKILL_DIR}/scripts/query_overdue.py --days 30 --repo agent-core

# 14 天没有任何更新的 issue（真正的 stale）
python3 {SKILL_DIR}/scripts/query_overdue.py --days 30 --no-update 14

# 按标签过滤
python3 {SKILL_DIR}/scripts/query_overdue.py --label bug

# 按作者过滤
python3 {SKILL_DIR}/scripts/query_overdue.py --author zhangsan

# 按仓库汇总统计
python3 {SKILL_DIR}/scripts/query_overdue.py --summary

# 按标签汇总统计
python3 {SKILL_DIR}/scripts/query_overdue.py --by-label
```

### 输出字段（get_issues.py）

| 字段 | 说明 |
|------|------|
| `total_fetched_from_api` | 从 API 获取的 issue 原始总数 |
| `total_matched` | 符合过滤条件（如 --days）的 issue 总数 |
| `returned_count` | 本次实际返回的 issue 数 |
| `is_truncated` | 是否截断（`true` 表示还有更多结果） |
| `issues[].repo` | 所属仓库 |
| `issues[].number` | Issue 编号 |
| `issues[].title` | Issue 标题 |
| `issues[].author` | 创建者 |
| `issues[].age_days` | 创建至今天数 |
| `issues[].url` | Issue 链接 |

### 输出字段（query_overdue.py - 本地库）

| 字段 | 说明 |
|------|------|
| `total_count` | 符合条件的 issue 总数（不受 limit 影响） |
| `returned_count` | 本次实际返回的 issue 数 |
| `is_truncated` | 是否截断（`true` 表示还有更多结果） |
| `truncated_hint` | 截断提示（仅当截断时显示） |
| `issues[].repo` | 所属仓库 |
| `issues[].number` | Issue 编号 |
| `issues[].title` | Issue 标题 |
| `issues[].author` | 创建者 |
| `issues[].age_days` | 创建至今天数 |
| `issues[].stale_days` | 最后更新至今天数 |
| `issues[].labels` | 标签列表 |
| `issues[].url` | Issue 链接 |

> **注意**：`--limit` 默认为 0（不限制），会返回全部结果。如果结果较多，可用 `--limit 50` 限制返回数量。

---

## 查询趋势数据

当用户询问「最近涨了多少 Star」「这周 Fork 增长多少」时，执行：

```bash
python3 {SKILL_DIR}/scripts/query_trends.py
```

### 常用示例

```bash
# 组织整体 7 天 / 30 天趋势
python3 {SKILL_DIR}/scripts/query_trends.py

# 指定仓库趋势
python3 {SKILL_DIR}/scripts/query_trends.py --repo agent-core

# 自定义时间窗口（90 天）
python3 {SKILL_DIR}/scripts/query_trends.py --days 90

# Star 增长 Top 10 仓库
python3 {SKILL_DIR}/scripts/query_trends.py --metric stars --top 10

# Star 每日明细
python3 {SKILL_DIR}/scripts/query_trends.py --metric stars --daily --days 30
```

### 输出字段

| 字段 | 说明 |
|------|------|
| `trends.stars.current` | 当前 Star 数 |
| `trends.stars.previous` | N 天前 Star 数 |
| `trends.stars.change` | Star 增量 |
| `trends.forks` | Fork 增量（同上结构） |
| `trends.downloads_total` | 下载量增量 |
| `top_repos_by_growth[]` | 增长最快的仓库列表 |

---

## 实时查询（默认工作流）

当用户需要最新数据且不涉及历史对比时，执行：

```bash
python3 {SKILL_DIR}/scripts/get_stats.py
```

耗时约 2–3 分钟，返回全组织实时数据。

---

## 查询单个仓库的全部 Tags

当用户询问某个 openJiuwen 仓库的版本、tag、release 列表时，执行：

```bash
python3 {SKILL_DIR}/scripts/get_tags.py <repo>
```

示例：

```bash
python3 {SKILL_DIR}/scripts/get_tags.py agent-core
```

脚本调用 GitCode API v5：`GET /repos/openjiuwen/{repo}/tags`（公开接口，无需 token），自动翻页（`per_page=100`），返回该仓库全部 tag。

### 输出字段

| 字段 | 说明 |
|------|------|
| `tag_count` | tag 总数 |
| `tags[].name` | tag 名称 |
| `tags[].message` | tag 附注信息 |
| `tags[].commit_sha` | 对应 commit SHA |
| `tags[].commit_date` | commit 时间 |
| `tags[].tagger_name` | 打 tag 者姓名 |
| `tags[].tagger_email` | 打 tag 者邮箱 |
| `tags[].tagger_date` | tag 创建时间 |

---

## 查询单个 Release 的组件与下载地址

当用户询问某个 tag 的 release 附件、源码包、wheel 下载链接时，执行：

```bash
python3 {SKILL_DIR}/scripts/get_release.py <repo> <tag>
```

示例：

```bash
python3 {SKILL_DIR}/scripts/get_release.py agent-core v0.1.13
```

脚本调用 GitCode API v5：`GET /repos/openjiuwen/{repo}/releases/{tag}`（公开接口，无需 token）。若设置环境变量 `GITCODE_ACCESS_TOKEN`，会额外请求 `temp_download_url=true` 以获取带鉴权的临时下载链接。

### 输出字段

| 字段 | 说明 |
|------|------|
| `asset_count` | 组件（附件/源码包）数量 |
| `release.tag_name` | tag 名称 |
| `release.name` | release 标题 |
| `release.prerelease` | 是否为预发布 |
| `release.created_at` | 发布时间 |
| `release.release_status` | 发布状态（如 `latest`） |
| `release.body` | release 说明（Markdown） |
| `release.assets[].name` | 组件文件名 |
| `release.assets[].type` | 组件类型（`source` 源码包 / `attach` 附件） |
| `release.assets[].browser_download_url` | 永久下载地址 |
| `release.assets[].temp_download_url` | 临时下载地址（带鉴权，有时效） |

---

## 查询单个贡献者统计

当用户询问某人在某仓库的提交贡献、代码行数、commit 数时，执行：

```bash
python3 {SKILL_DIR}/scripts/get_contributor.py <repo> <author>
```

示例：

```bash
python3 {SKILL_DIR}/scripts/get_contributor.py agent-core iamcandiceguo
python3 {SKILL_DIR}/scripts/get_contributor.py agent-core iamcandiceguo --since 2026-01-01 --until 2026-05-20
python3 {SKILL_DIR}/scripts/get_contributor.py agent-core iamcandiceguo --ref-name main
```

脚本调用 GitCode API v5：`GET /repos/openjiuwen/{repo}/contributors/statistic?author={username}`（公开接口，无需 token）。

### 可选参数

| 参数 | 说明 |
|------|------|
| `--since` | 起始日期（`YYYY-MM-DD` 或 `YYYY-MM-DD HH:mm:ss`） |
| `--until` | 结束日期（同上） |
| `--ref-name` | 分支名、commit ID 或 tag；省略则用默认分支 |

### 输出字段

| 字段 | 说明 |
|------|------|
| `contributor.name` | 贡献者用户名 |
| `contributor.email` | 贡献者邮箱 |
| `contributor.overview.additions` | 累计新增行数 |
| `contributor.overview.deletions` | 累计删除行数 |
| `contributor.overview.total_changes` | 累计变更行数 |
| `contributor.overview.commit_count` | 累计 commit 数 |
| `contributor.contribution_days` | 有贡献记录的天数 |
| `contributor.contributions[]` | 按日的贡献明细（date、additions、deletions、total_changes、commit_count） |

---

## 数据说明

脚本从 GitCode API v5 动态拉取 **openjiuwen 组织下全部仓库**（`GET /orgs/openjiuwen/repos`，公开接口，无需 token；自动翻页）。可选环境变量 `GITCODE_ACCESS_TOKEN` 用于需要鉴权的场景。

### 每个仓库提供的字段

| 字段 | 说明 |
|------|------|
| `stars` | Star 数（GitCode） |
| `forks` | Fork 数 |
| `open_issues` | 当前 Open Issue 数 |
| `contributors` | 贡献者数（最多统计 100 人） |
| `pr_count_approx` | 历史 PR 总数（近似，取最新 PR 编号） |
| `issue_count_approx` | 历史 Issue 总数（近似，取最新 Issue 编号） |
| `downloads.total` | 历史累计下载量 |
| `downloads.recent_30d` | 近 30 天下载量 |
| `downloads.yesterday` | 昨日下载量 |

### 组织汇总字段（`org_totals`）

- `total_stars` — 全组织 Star 总数
- `total_forks` — 全组织 Fork 总数
- `total_open_issues` — 当前 Open Issue 总数
- `total_contributors_sum` — 贡献者数各仓之和
- `total_downloads` — 全组织历史下载总量
- `total_downloads_30d` — 全组织近 30 天下载总量
- `total_pr_approx` — 全组织历史 PR 近似总数
- `total_issue_approx` — 全组织历史 Issue 近似总数

### 注意事项

- PR / Issue 数量为**近似值**（用最新编号推算），可能略低于实际值（编号存在跳号）。
- 下载量来源于 GitCode 下载统计接口，仅统计从 GitCode 下载的次数。
- 贡献者数每仓最多统计 100 人，超大仓库可能不完整。
- 若某仓无下载数据，`downloads` 字段为 `null`。
