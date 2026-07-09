# 工作流示例

典型用户话术 → 意图 → 子 skill 执行顺序。Agent 应加载本表对应 skill 的 `SKILL.md` 后按该 skill 细则操作，而非仅复述下表。

---

## A. 社区信息（仅 community-stats）

| 用户话术 | 子 skill | 要点 |
|----------|----------|------|
| agent-core 现在多少 star？ | community-stats | `get_stats.py`，结果中只呈现 agent-core；或带 `--repo agent-core` 的专项脚本 |
| 有哪些超过 30 天没关的 studio issue？ | community-stats | `get_issues.py --days 30 --repo agent-studio` |
| 整个 openJiuwen 最近涨了多少 fork？ | community-stats | `query_trends.py`（需本地库则先 sync） |
| agent-core v0.1.13 的 release 下载地址 | community-stats | `get_release.py agent-core v0.1.13` |
| iamcandiceguo 在 core 上贡献了多少 commit？ | community-stats | `get_contributor.py agent-core iamcandiceguo` |

**反例**：用户问「swarm 的 issue」→ 禁止返回全组织 395 条；必须 `--repo jiuwenswarm`。

---

## B. 组件分析答疑（产品线 skill）

| 用户话术 | 子 skill | 要点 |
|----------|----------|------|
| openjiuwen 工作流怎么打断恢复？ | agent-core | 定版本 → references → assets 取证分析 → 答复 |
| Java Agent 怎么注册 tool？ | agent-core-java | 勿用 Python 快照 |
| Studio 0.1.8 docker compose 怎么配？ | agent-studio | 版本 `v0.1.8`；读 ops 索引 |
| Runtime k8s 部署多租户 API | agent-runtime | 区分 subprocess/docker/k8s |
| DeepSearch 报告模板怎么改？ | deepsearch | 非通用 RAG 教程 |
| jiuwenswarm 飞书机器人怎么接？ | jiuwenswarm | tag 可能无 `v` 前缀 |
| 帮我拉一下 agent-core 全部快照 | agent-core | `scripts/fetch.sh auto` 或 `.ps1 -Tag auto` |

**缺 assets**：先 fetch，再索引；失败则如实说明，勿假装已有快照。

---

## C. 疑似问题记录（record-bugs）

| 用户话术 | 前置 | 子 skill |
|----------|------|----------|
| 把这个 bug 记一下（已给出文件行号） | 确认 module | record-bugs，必须跑脚本 |
| 读代码发现 Session 泄漏，登记 | 完成 agent-core 取证 | `--module agent-core`，`--file` 为快照内路径 |
| 先别记，我只是问问 | — | 不加载 record-bugs |

**severity 参考**：崩溃/数据丢失/安全 → 高；功能错有绕行 → 中；文案边界 → 低。

---

## 组合流程

### B + C（分析答疑与登记同轮，不互斥）

1. 加载产品线 skill，在快照上分析并给出结论与证据路径。
2. 先完成答复（排障/用法/根因）；若用户要求记录或证据已确认 Bug → 加载 record-bugs，执行 `record_bug.py`。
3. 回复：**分析结论 + 答疑要点** +（若已登记）「已记录至 PENDING_BUGS.md，待责任人确认」。

**反例**：用户问「为什么报错并记 bug」→ 只跑 record-bugs 不分析；或只答疑不登记且未说明「待确认是否记录」。

### A + B

1. 社区：`get_issues.py --repo agent-studio --days 30`（汇总 + 分批明细）。
2. 产品：加载 agent-studio 答「某 Issue 描述的配置问题是否在文档有说明」。

### 意图不明

用户：「帮我看看 openjiuwen」

编排层回复示例：

> 可以帮您三类事情：① GitCode 社区数据（Star/Issue/下载等）；② 具体产品线用法与排障（Core/Studio/Runtime/DeepSearch/Swarm）；③ 登记已确认的疑似 Bug。您更关心哪一类，涉及哪个产品？

---

## 不应走本集合的场景

| 场景 | 说明 |
|------|------|
| 在 GitCode 上创建 Issue/PR | 超出只读社区 skill；record-bugs 仅写本地 PENDING |
| 修改用户业务代码 | 非 QA skill 职责 |
| 与 openJiuwen 无关的 LeetCode / 通用 Python | 不必加载 openjiuwen-* |
