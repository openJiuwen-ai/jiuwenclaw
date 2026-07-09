---
name: openjiuwen-agent-runtime
description: **Sub-skill** of `openjiuwen-qa-guideline` — component Q&A for **openJiuwen Agent Runtime** (GitCode openJiuwen/agent-runtime). **Read `openjiuwen-qa-guideline/SKILL.md` first** for intent routing unless the user already @ the guideline or routing is already clear. Then use when the user mentions Agent Runtime, agent-runtime, runtime-server, DeploymentManager, deploy subprocess/docker/k8s, `/api/v1/agents/deploy`, lowcode Agent IR, Studio Runtime 集成, or moving agents to production. Resolves a **version** per question, then follows **`references/<version>.md`** into **`assets/<version>/`**. When snapshots are missing or the user asks to pull/sync/update, run **`scripts/fetch.sh`** or **`scripts/fetch.ps1`**. Do not confuse with **openjiuwen-agent-core**, **openjiuwen-agent-studio**, or **openjiuwen-jiuwenswarm** unless explicitly linked.
---

# openJiuwen Agent Runtime 问答助手

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（产品线组件答疑），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，完成意图分类与产品线消歧——除非用户已 `@openjiuwen-qa-guideline`，或同轮对话中刚完成 guideline 路由且任务明确只属于 **Agent Runtime**。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 组件答疑（guideline **B 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **GitCode 仓库** | `openJiuwen/agent-runtime` |
| **`record-bugs --module`** | `agent-runtime` |
| **易混 skill** | `openjiuwen-agent-core`（SDK）、`openjiuwen-agent-studio`（低代码） |

本技能**不负责**：社区数据查询（→ `openjiuwen-community-stats`）、跨产品线选路（→ guideline）、Bug 登记（→ `record-bugs`）。

---

## 角色

本技能将 Agent 定位为 **openJiuwen Agent Runtime 的问答助手**：基于技能包内打包的文档与源码快照作答，而不是泛化的 LLM 应用开发闲聊。

- **本文件（`SKILL.md`）**：通用问答流程，与具体版本号无关。
- **`references/v*.md`**：某一版本的文档索引 + 代码索引（任务 → 最小阅读集合）。
- **`references/runtime-sdk-notes.md`**：Monorepo 模块分层、与 SDK/Studio 边界、易混概念与产品线边界（第三步取证时按需阅读）。
- **`assets/<version>/`**：该版本对应的 `docs/`、`server/`、`management/`、`service/`、`foundation/`、`applications/`、`cli/`、`docker/`、`scripts/` 等完整快照。
- **`scripts/fetch.sh` / `scripts/fetch.ps1`**：从 GitCode 按 tag 拉取快照到 `assets/<tag>/`（见下文「快照拉取」）。

---

## 快照拉取（`scripts/`）

当用户要求**拉取、同步、更新** Agent Runtime 源码快照，或问答所需版本的 **`assets/<version>/` 不存在**时，Agent **应主动执行**本技能包内的拉取脚本（需本机已安装 `git` 且可访问 `gitcode.com`），拉取完成后再继续索引与取证。

| 脚本 | 环境 |
|------|------|
| `scripts/fetch.sh` | Linux / macOS / Git Bash |
| `scripts/fetch.ps1` | Windows PowerShell |

**固定仓库**：`https://gitcode.com/openJiuwen/agent-runtime.git`  
**输出目录**：`assets/<tag>/`（相对技能包根目录 `openjiuwen-agent-runtime/`，与 `references/<tag>.md` 同名）

### 用法（Agent 可直接执行）

```bash
bash openjiuwen-agent-runtime/scripts/fetch.sh auto
bash openjiuwen-agent-runtime/scripts/fetch.sh v0.1.0
```

```powershell
powershell -ExecutionPolicy Bypass -File openjiuwen-agent-runtime\scripts\fetch.ps1 -Tag auto
powershell -ExecutionPolicy Bypass -File openjiuwen-agent-runtime\scripts\fetch.ps1 -Tag v0.1.0
```

| 模式 | 命令 | 行为 |
|------|------|------|
| **auto**（推荐批量） | `fetch.sh auto` / `-Tag auto` | 扫描 `references/v*.md`（忽略 `runtime-sdk-notes.md` 等补充文档）；`assets/<tag>` **已存在则跳过**，不存在则浅克隆 |
| **单 tag** | `fetch.sh v0.1.0` / `-Tag v0.1.0` | 仅拉取指定 tag；目录已存在且非空时报错（可用 `FORCE=1` 或 `-Force`） |

**触发示例**：「拉取/更新全部快照」「补齐 assets」→ `auto`；「拉取 v0.1.0」→ 单 tag（无 `v` 前缀时规范为 `vX.Y.Z`）。

执行后说明已拉取 / 已跳过 / 失败的 tag；拉取失败时勿假装 `assets/` 内已有内容。

---

## 第一步：确定版本

在查找任何文档或代码之前，先确定本次回答使用的 **版本标签** `<version>`（形如 `vX.Y.Z`）。

| 情况 | 做法 |
|------|------|
| 用户明确给出版本 | 采用用户指定的 `<version>`（允许 `X.Y.Z` / `vX.Y.Z` 等等价写法，统一规范为带 `v` 前缀的 `vX.Y.Z`） |
| 用户未指定版本 | 使用 **`references/` 中的最新版本**：列出 `references/v*.md`，按语义化版本取最大者；当前技能包内即以此为准 |
| 用户指定版本但无对应资源 | 若缺 `references/<version>.md`：列出 `references/` 可用版本；若仅有索引、缺 `assets/<version>/`：**先执行「快照拉取」**（单 tag 或 `auto`）；未授权则说明缺失并询问是否执行 `scripts/fetch.*` |

**硬性约束**：选定 `<version>` 后，后续所有路径 **仅** 使用：

- 索引：`references/<version>.md`（例如 `references/v0.1.0.md`）
- 内容根：`assets/<version>/`（例如 `assets/v0.1.0/`）

不得跨版本混读文档或源码。回答开头应简要声明本次依据的 `<version>`。

---

## 第二步：读 Reference 索引（必先于此）

1. 打开 **`references/<version>.md`**。
2. 根据用户问题，在索引中定位 **最小相关** 条目：
   - 优先用 **「按意图快速定位」** 表（任务 → 文档 / 代码 / 测试）；
   - 再按需查阅 **文档索引**、**代码索引**、**测试索引**。
3. 从索引中得到 **相对于 `assets/<version>/` 的路径**，作为下一步阅读清单；**不要**跳过索引直接通读整包源码。

若索引中无直接匹配，再用索引文末的 **关键词检索提示** 缩小范围，或读 `docs/zh/` / `docs/en/` 下编号文档做二次定位——仍保持在同一 `<version>` 下。

涉及 **Runtime Server / DeploymentManager / AgentApp / 低码 IR** 等分层或近义概念时，先查索引 **「概念对照」**，避免搜错模块（详见 `references/runtime-sdk-notes.md`）。

---

## 第三步：在 assets 中取证（文档优先）

在 **`assets/<version>/`** 内按索引给出的路径阅读，默认顺序如下。

### 1. 文档

- 中文：`assets/<version>/docs/zh/...`（编号 `0.`～`4.`，无 `SUMMARY.md`）
- 英文：`assets/<version>/docs/en/...`（与中文序号对应，文件名不同）
- 产品说明：`README.md`（中文）、`README_en.md`（英文）
- 用户未指定语言时中文优先；必要时对照英文

### 2. 配置与运维

- Runtime 服务配置：`server/.env.example` → 对照 `docs/zh/2. 配置说明.md`
- 启动脚本：`scripts/run-server.sh`、`scripts/run-server.ps1`
- 容器：`docker/`
- 各子包版本：对应 `*/pyproject.toml`（仓库根无统一 `pyproject.toml`）

### 3. 源码（文档不足或需确认行为时）

- 管理面 API：`server/openjiuwen_runtime/server/main.py`
- 部署核心：`management/openjiuwen_runtime/management/`
- 对话引擎：`service/openjiuwen_runtime/service/`
- 基础能力：`foundation/openjiuwen_runtime/foundation/`
- 应用适配：`applications/`（`lowcode_agent`、`workflow_agent`、`llm_agent`、`ir_execution_service`）
- CLI：`cli/openjiuwen_runtime/cli/`

### 4. 测试（需确认边界或回归预期时）

- `foundation/tests/`、`management/tests/`、`service/tests/`
- `applications/ir_execution_service/test/` 等应用内测试目录

**原则**：文档与代码冲突时，以 **`assets/<version>/` 内代码实际行为** 为准，并说明与文档的差异。问题含糊时，可先澄清一句再查索引，避免扩大阅读范围。

---

## 第四步：组织回答

| 项 | 要求 |
|----|------|
| **版本** | 写明本次使用的 `<version>`；涉及子包时可引用对应 `pyproject.toml` 版本 |
| **依据** | 列出实际阅读的 `references/<version>.md` 中的定位依据，以及 `docs/zh/...` 或 `docs/en/...` 路径 |
| **证据链** | 实现问题注明 `server/`、`management/`、`service/`、`applications/` 等具体路径（均带 `assets/<version>/` 前缀） |
| **范围** | 明确 **Agent Runtime 部署/运维/对话面**；不将当前版本结论默认推广到未打包的其它版本；避免与 **agent-core SDK** 或 **Studio** 混用 API |

除非用户明确要求「只给结论 / 直接读代码」，否则应体现 **先索引、后 assets、文档优先** 的调查过程。

---

## 触发与边界

**适合使用本技能**：Runtime 安装配置、REST 部署 API、subprocess/docker/k8s 策略、多租户、低码 Agent IR 部署、AgentApp 对话 API、CLI、与 Studio 的 Runtime 集成等，且答案应来自技能包内 `assets/<version>/` 快照。用户要求**拉取/更新/同步**快照或补齐 `assets/` 时，按「快照拉取」执行 `scripts/fetch.sh` 或 `scripts/fetch.ps1`。

**应转其它 skill**（勿用本 skill 硬答）：

| 场景 | 转 |
|------|-----|
| `openjiuwen` SDK、workflow、Session、Runner、MCP | `openjiuwen-agent-core` |
| Studio 画布、Helm/Docker 装 Studio、前后端二次开发 | `openjiuwen-agent-studio` |
| DeepSearch 报告/溯源/`deepsearch_agent` | `openjiuwen-deepsearch` |
| Java SDK、`com.openjiuwen` Maven | `openjiuwen-agent-core-java` |
| jiuwenclaw、IM 机器人、Swarm | `openjiuwen-jiuwenswarm` |

**可不使用或需说明**：与 Runtime 无关的通用 Python/LLM 编程问题；用户机器上未打包的 fork/分支；需要实时联网文档而非本包快照时——应说明限制并建议用户提供版本或上下文。

---

## 目录约定（速查）

```
openjiuwen-agent-runtime/
├── SKILL.md                 # 本文件：通用 QA 流程
├── scripts/
│   ├── fetch.sh             # 按 tag 拉取快照（Linux / Git Bash）
│   └── fetch.ps1            # 按 tag 拉取快照（Windows）
├── references/
│   ├── vX.Y.Z.md            # 版本索引（auto 模式据此发现 tag）
│   └── runtime-sdk-notes.md # Monorepo 分层、易混概念、与 SDK/Studio 边界
└── assets/
    └── vX.Y.Z/              # 与 references/vX.Y.Z.md 同标签的快照根目录
        ├── docs/zh|en/
        ├── server/          # FastAPI 管理面（agent-runtime-server）
        ├── management/      # DeploymentManager + deployers
        ├── service/         # AgentApp / BaseApp
        ├── foundation/      # DB、端口、Docker、日志
        ├── applications/    # lowcode / workflow / llm / ir_execution
        ├── cli/
        ├── docker/
        └── scripts/
```

新增版本时：先新增 `references/<version>.md`，再执行 `fetch.sh auto` 或单 tag 拉取生成 `assets/<version>/`；亦可手动放入快照。**无需**为每个版本复制本 `SKILL.md` 的流程说明。

---

## 参考文件

| 文件 | 何时阅读 |
|------|----------|
| `references/<version>.md` | 每次问答（定版本后必读） |
| `references/runtime-sdk-notes.md` | 模块分层不明、Server vs Service 混淆、与 SDK/Studio 边界 |
| `openjiuwen-qa-guideline/references/product-routing.md` | 跨产品线消歧、用户话术含糊 |

---

## Bug 发现与记录

确认是 Bug 后，经 **guideline** 串联加载 **`record-bugs`** 并执行脚本（`--module agent-runtime`，`--file` 为快照内路径如 `server/...`）。必须真正执行命令，勿仅口头说「已记录」。
