# 产品路由与消歧

> 编排层补充：`openjiuwen-qa-guideline` 在识别 **组件答疑（B 类）** 时使用。社区查询的 `--repo` 映射以 `openjiuwen-community-stats/SKILL.md` 为准（二者仓库名一致）。

## 产品线 → 子 skill → 仓库

| 产品线 | 子 skill 目录 | GitCode `openJiuwen/` 仓库 | `record-bugs --module` | 最新 reference | 快照 tag 习惯 |
|--------|---------------|---------------------------|------------------------|----------------|---------------|
| Agent Core (Python) | `openjiuwen-agent-core/` | `agent-core` | `agent-core` | v0.1.14 | `vX.Y.Z` |
| Agent Core (Java) | `openjiuwen-agent-core-java/` | `agent-core-java` | `agent-core-java` | v0.1.7 | `vX.Y.Z` |
| Agent Studio | `openjiuwen-agent-studio/` | `agent-studio` | `agent-studio` | v0.1.8 | `vX.Y.Z` |
| Agent Runtime | `openjiuwen-agent-runtime/` | `agent-runtime` | `agent-runtime` | v0.1.0 | `vX.Y.Z` |
| DeepSearch | `openjiuwen-deepsearch/` | `deepsearch` | `deepsearch` | v0.1.7 | `vX.Y.Z`（可含 `.postN`） |
| JiuwenSwarm | `openjiuwen-jiuwenswarm/` | `jiuwenswarm` | `jiuwenswarm` | 0.2.2 | `JiuwenSwarmX.Y.Z` 或 `X.Y.Z` |

## 关键词 → 首选 skill

| 关键词（任一命中） | 首选 skill | 若仍歧义 |
|-------------------|------------|----------|
| `openjiuwen` 包、`pip install openjiuwen`、workflow、agent、harness、MCP、session | agent-core | 问 Python 还是 Java |
| `maven`、`gradle`、`agent-core-java`、Java Agent | agent-core-java | — |
| 画布、工作流编排 UI、FlowGram、Helm 装 Studio | agent-studio | 是否问 Runtime 部署 |
| `DeploymentManager`、subprocess/docker/k8s 部署 Agent、runtime-server | agent-runtime | 是否问 Studio 里点部署 |
| 深度检索、研究报告、溯源、citation、`deepsearch_agent` | deepsearch | 是否问 Core 通用 RAG |
| jiuwenclaw、飞书/钉钉机器人、Gateway、jiuwenbox、Swarm Team | jiuwenswarm | — |

## 常见混淆与处理

### 1. 「Core」未说明语言

- **做法**：问「您用的是 Python SDK（openjiuwen）还是 Java SDK（agent-core-java）？」
- **默认**：仅在用户明确 Python / pip / pyproject 时用 agent-core；明确 Java 时用 agent-core-java。

### 2. Studio 与 Core

- Studio **使用** Core 能力，但问答应读 **agent-studio** 快照（前后端、Docker、Helm）。
- 用户问「Studio 里某节点怎么配」→ studio；问「openjiuwen.Agent 类怎么写」→ core。

### 3. Runtime 与 Studio / Core

- 「把 Studio 发布的 Agent 部署到生产」→ 常涉及 **runtime**（及 studio 集成文档）；先确认环节是 Studio 导出还是 Runtime API。
- 「本地写 Agent 代码跑不起来」→ 多半是 **core** 或 **jiuwenswarm**，不是 runtime。

### 4. DeepSearch 与 agent-core

- DeepSearch 依赖 Core 生态，但独立仓库；DeepSearch API、模板、Server → **deepsearch**。
- 通用 Agent/workflow/MCP → **agent-core**。

### 5. 社区查询 vs 产品答疑

| 用户问 | 走 community-stats | 走产品线 skill |
|--------|-------------------|----------------|
| agent-core 有多少 star | ✓ `--repo agent-core` | |
| agent-core 的 Session API 怎么用 | | ✓ agent-core |
| 组织一共多少 open issue | ✓ 不加 `--repo` | |
| studio v0.1.7 怎么升级 | | ✓ agent-studio |

### 6. 多产品线一句话

例：「Swarm 和 Studio 的 star 谁多，Studio 怎么装？」

1. 社区部分：`get_stats.py` 或分两次 `--repo` 对比（勿混为一个模糊回答）。
2. 安装部分：加载 **agent-studio**，按版本索引答。

## 社区 `--repo` 速查（与 community-stats 一致）

| 用户说法 | `--repo` |
|----------|----------|
| swarm、jiuwenclaw | `jiuwenswarm` |
| studio | `agent-studio` |
| core（Python 语境） | `agent-core` |
| java core | `agent-core-java` |
| runtime | `agent-runtime` |
| deepsearch | `deepsearch`（community-stats 表内若无单独行，组织级查询或确认脚本是否支持该 repo 名） |
| openjiuwen 整体 | 不加 `--repo` |
