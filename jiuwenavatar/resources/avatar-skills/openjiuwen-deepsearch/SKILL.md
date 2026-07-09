---
name: openjiuwen-deepsearch
description: **Sub-skill** of `openjiuwen-qa-guideline` — component Q&A for **openJiuwen DeepSearch** (GitCode openJiuwen/deepsearch). **Read `openjiuwen-qa-guideline/SKILL.md` first** for intent routing unless the user already @ the guideline or routing is already clear. Then use when the user mentions DeepSearch, 深度检索/深度研究, report template, 溯源/citation, `deepsearch_agent`, REST API, or Studio DeepSearch integration. Resolves a **version** per question, then follows **`references/<version>.md`** into **`assets/<version>/`**. When snapshots are missing or the user asks to pull/sync/update, run **`scripts/fetch.sh`** or **`scripts/fetch.ps1`**. Do not confuse with **openjiuwen-agent-core**, **openjiuwen-agent-studio**, or **openjiuwen-agent-runtime** unless explicitly linked.
---

# openJiuwen DeepSearch 问答助手

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（产品线组件答疑），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，完成意图分类与产品线消歧——除非用户已 `@openjiuwen-qa-guideline`，或同轮对话中刚完成 guideline 路由且任务明确只属于 **DeepSearch**。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 组件答疑（guideline **B 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **GitCode 仓库** | `openJiuwen/deepsearch` |
| **`record-bugs --module`** | `deepsearch` |
| **易混 skill** | `openjiuwen-agent-core`（通用 SDK）、`openjiuwen-agent-studio` |

本技能**不负责**：社区数据查询（→ `openjiuwen-community-stats`）、跨产品线选路（→ guideline）、Bug 登记（→ `record-bugs`）。

---

## 角色

本技能将 Agent 定位为 **openJiuwen DeepSearch 的问答助手**：基于技能包内打包的文档与源码快照作答，而不是泛化的 RAG/搜索闲聊。

- **本文件（`SKILL.md`）**：通用问答流程，与具体版本号无关。
- **`references/v*.md`**：某一版本的文档索引 + 代码索引（任务 → 最小阅读集合）。
- **`references/deepsearch-sdk-notes.md`**：SDK vs 完整版后端、algorithm/framework 分层、与 agent-core/Studio 边界（第三步取证时按需阅读）。
- **`assets/<version>/`**：该版本对应的 `docs/`、`openjiuwen_deepsearch/`、`server/`、`tests/`、`docker/` 等完整快照。
- **`scripts/fetch.sh` / `scripts/fetch.ps1`**：从 GitCode 按 tag 拉取快照到 `assets/<tag>/`（见下文「快照拉取」）。

---

## 快照拉取（`scripts/`）

当用户要求**拉取、同步、更新** DeepSearch 源码快照，或问答所需版本的 **`assets/<version>/` 不存在**时，Agent **应主动执行**本技能包内的拉取脚本（需本机已安装 `git` 且可访问 `gitcode.com`），拉取完成后再继续索引与取证。

| 脚本 | 环境 |
|------|------|
| `scripts/fetch.sh` | Linux / macOS / Git Bash |
| `scripts/fetch.ps1` | Windows PowerShell |

**固定仓库**：`https://gitcode.com/openJiuwen/deepsearch.git`  
**输出目录**：`assets/<tag>/`（相对技能包根目录 `openjiuwen-deepsearch/`，与 `references/<tag>.md` 同名）

### 用法（Agent 可直接执行）

```bash
bash openjiuwen-deepsearch/scripts/fetch.sh auto
bash openjiuwen-deepsearch/scripts/fetch.sh v0.1.6
```

```powershell
powershell -ExecutionPolicy Bypass -File openjiuwen-deepsearch\scripts\fetch.ps1 -Tag auto
powershell -ExecutionPolicy Bypass -File openjiuwen-deepsearch\scripts\fetch.ps1 -Tag v0.1.6
```

| 模式 | 命令 | 行为 |
|------|------|------|
| **auto**（推荐批量） | `fetch.sh auto` / `-Tag auto` | 扫描 `references/v*.md`（忽略 `deepsearch-sdk-notes.md` 等补充文档）；`assets/<tag>` **已存在则跳过**，不存在则浅克隆 |
| **单 tag** | `fetch.sh v0.1.6` / `-Tag v0.1.6` | 仅拉取指定 tag；目录已存在且非空时报错（可用 `FORCE=1` 或 `-Force`） |

**触发示例**：「拉取/更新全部快照」「补齐 assets」→ `auto`；「拉取 v0.1.6」→ 单 tag（无 `v` 前缀时规范为 `vX.Y.Z`，post 版本保留后缀如 `v0.1.6.post1`）。

执行后说明已拉取 / 已跳过 / 失败的 tag；拉取失败时勿假装 `assets/` 内已有内容。

---

## 第一步：确定版本

在查找任何文档或代码之前，先确定本次回答使用的 **版本标签** `<version>`（形如 `vX.Y.Z` 或 `vX.Y.Z.postN`）。

| 情况 | 做法 |
|------|------|
| 用户明确给出版本 | 采用用户指定的 `<version>`（允许 `X.Y.Z` / `vX.Y.Z` 等等价写法，统一规范为带 `v` 前缀，post 版本保留后缀） |
| 用户未指定版本 | 使用 **`references/` 中的最新版本**：列出 `references/v*.md`，按语义化版本取最大者；当前技能包内即以此为准 |
| 用户指定版本但无对应资源 | 若缺 `references/<version>.md`：列出 `references/` 可用版本；若仅有索引、缺 `assets/<version>/`：**先执行「快照拉取」**（单 tag 或 `auto`）；未授权则说明缺失并询问是否执行 `scripts/fetch.*` |

**硬性约束**：选定 `<version>` 后，后续所有路径 **仅** 使用：

- 索引：`references/<version>.md`（例如 `references/v0.1.6.md`）
- 内容根：`assets/<version>/`（例如 `assets/v0.1.6/`）

不得跨版本混读文档或源码。回答开头应简要声明本次依据的 `<version>`。

---

## 第二步：读 Reference 索引（必先于此）

1. 打开 **`references/<version>.md`**。
2. 根据用户问题，在索引中定位 **最小相关** 条目：
   - 优先用 **「按意图快速定位」** 表（任务 → 文档 / 代码 / 测试）；
   - 再按需查阅 **文档索引**、**代码索引**、**测试索引**。
3. 从索引中得到 **相对于 `assets/<version>/` 的路径**，作为下一步阅读清单；**不要**跳过索引直接通读 `SUMMARY.md` 或整包源码。

若索引中无直接匹配，再用索引文末的 **关键词检索提示** 缩小范围，或读 `docs/zh/SUMMARY.md` / `docs/en/SUMMARY.md` 做二次定位——仍保持在同一 `<version>` 下。

涉及 **DeepSearchAgent / algorithm vs framework / SDK vs server / 溯源** 等分层或近义概念时，先查索引与 **`directory_structure.md`**，避免搜错模块（详见 `references/deepsearch-sdk-notes.md`）。

---

## 第三步：在 assets 中取证（文档优先）

在 **`assets/<version>/`** 内按索引给出的路径阅读，默认顺序如下。

### 1. 文档

- 中文：`assets/<version>/docs/zh/...`（`SUMMARY.md` 为总目录）
- 英文：`assets/<version>/docs/en/...`（用户未指定语言时，中文优先，必要时对照英文）
- 模块结构：`assets/<version>/docs/zh/4.开发指南/directory_structure.md`
- 产品说明：`README.md`、`README-en.md`

### 2. SDK 核心（算法与工作流）

- `assets/<version>/openjiuwen_deepsearch/algorithm/` — 查询理解、收集、报告、溯源、反馈
- `assets/<version>/openjiuwen_deepsearch/framework/openjiuwen/` — 工作流节点、Agent 工厂、搜索工具
- `assets/<version>/openjiuwen_deepsearch/config/`、`common/`、`utils/`

### 3. 后端服务（完整版 / REST）

- `assets/<version>/server/` — FastAPI、`routers/`、`deepsearch/core/`
- `assets/<version>/start_backend.py`、`main.py`
- 环境模板：`.env.example`

### 4. 测试（边界与回归）

- `assets/<version>/tests/` — 按模块镜像（`algorithm/`、`search_agent/`、`server/`、`workflow/` 等）

### 5. 运维

- `assets/<version>/docker/`

**原则**：文档与代码冲突时，以 **`assets/<version>/` 内代码实际行为** 为准。依赖版本以该快照根目录 `pyproject.toml` 为准（含 `openjiuwen` 锁定版本）。

---

## 第四步：组织回答

| 项 | 要求 |
|----|------|
| **版本** | 写明本次使用的 `<version>`；涉及依赖时可引用 `pyproject.toml` |
| **依据** | 列出实际阅读的 `references/<version>.md` 中的定位依据，以及 `docs/zh/...` 或 API 文档路径 |
| **证据链** | 实现问题注明 `openjiuwen_deepsearch/...`、`server/...`、`tests/...`（均带 `assets/<version>/` 前缀） |
| **范围** | 明确 **DeepSearch 产品线**；不将当前版本结论默认推广到未打包的其它版本；避免与通用 **agent-core SDK** 混用 API |

除非用户明确要求「只给结论 / 直接读代码」，否则应体现 **先索引、后 assets、文档优先** 的调查过程。

---

## 触发与边界

**适合使用本技能**：DeepSearch 安装（完整版/SDK/Docker）、快速上手、工作流与节点开发、报告/模板/溯源、知识库与搜索引擎配置、用户反馈改写、REST API、后端 `server` 等，且答案应来自技能包内 `assets/<version>/` 快照。用户要求**拉取/更新/同步**快照或补齐 `assets/` 时，按「快照拉取」执行 `scripts/fetch.sh` 或 `scripts/fetch.ps1`。

**应转其它 skill**（勿用本 skill 硬答）：

| 场景 | 转 |
|------|-----|
| 通用 `openjiuwen` SDK、Runner、Session、MCP（非 DeepSearch 包） | `openjiuwen-agent-core` |
| Studio 画布、Helm 装 Studio、Studio 内嵌 DeepSearch UI | `openjiuwen-agent-studio` |
| Runtime Server、`DeploymentManager` | `openjiuwen-agent-runtime` |
| Java SDK | `openjiuwen-agent-core-java` |
| jiuwenclaw、Swarm | `openjiuwen-jiuwenswarm` |

**可不使用或需说明**：与 DeepSearch 无关的通用 RAG/搜索问题；用户机器上未打包的 fork/分支；需要实时联网文档而非本包快照时——应说明限制并建议用户提供版本或上下文。

---

## 目录约定（速查）

```
openjiuwen-deepsearch/
├── SKILL.md                 # 本文件：通用 QA 流程
├── scripts/
│   ├── fetch.sh             # 按 tag 拉取快照（Linux / Git Bash）
│   └── fetch.ps1            # 按 tag 拉取快照（Windows）
├── references/
│   ├── vX.Y.Z.md            # 版本索引（auto 模式据此发现 tag）
│   └── deepsearch-sdk-notes.md  # SDK/后端分层、易混概念、产品线边界
└── assets/
    └── vX.Y.Z/
        ├── docs/zh|en/
        ├── openjiuwen_deepsearch/
        │   ├── algorithm/
        │   └── framework/openjiuwen/
        ├── server/
        ├── tests/
        ├── docker/
        └── pyproject.toml
```

新增版本时：先新增 `references/<version>.md`，再执行 `fetch.sh auto` 或单 tag 拉取生成 `assets/<version>/`；亦可手动放入快照。**无需**为每个版本复制本 `SKILL.md` 的流程说明。

---

## 参考文件

| 文件 | 何时阅读 |
|------|----------|
| `references/<version>.md` | 每次问答（定版本后必读） |
| `references/deepsearch-sdk-notes.md` | algorithm vs framework 不明、SDK vs server 混淆、与 agent-core/Studio 边界 |
| `openjiuwen-qa-guideline/references/product-routing.md` | 跨产品线消歧、用户话术含糊 |

---

## Bug 发现与记录

确认是 Bug 后，经 **guideline** 串联加载 **`record-bugs`** 并执行脚本（`--module deepsearch`，`--file` 为快照内路径如 `openjiuwen_deepsearch/...`）。必须真正执行命令，勿仅口头说「已记录」。
