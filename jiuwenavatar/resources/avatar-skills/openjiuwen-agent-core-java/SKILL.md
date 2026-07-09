---
name: openjiuwen-agent-core-java
description: **Sub-skill** of `openjiuwen-qa-guideline` — component Q&A for **openJiuwen agent-core-java** (`com.openjiuwen:agent-core-java`, Java 21, Maven). **Read `openjiuwen-qa-guideline/SKILL.md` first** for intent routing and product disambiguation unless the user already @ the guideline or routing is already clear. Then use when the user asks about agent-core-java, openJiuwen Java SDK, workflows, agents, graph/session/streaming/interrupt, memory, retrieval, multi-agent, skills, or APIs/behavior of this codebase. Resolves a **version** per question, then follows **`references/<version>.md`** into **`assets/<version>/`**. When snapshots are missing or the user asks to pull/sync/update, run **`scripts/fetch.sh`** or **`scripts/fetch.ps1`**. Do not confuse with Python **openjiuwen-agent-core**.
---

# openJiuwen agent-core-java 问答助手

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（产品线组件答疑），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，完成意图分类与产品线消歧——除非用户已 `@openjiuwen-qa-guideline`，或同轮对话中刚完成 guideline 路由且任务明确只属于 **agent-core-java（Java SDK）**。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 组件答疑（guideline **B 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **GitCode 仓库** | `openJiuwen/agent-core-java` |
| **`record-bugs --module`** | `agent-core-java` |
| **易混 skill** | `openjiuwen-agent-core`（Python） |

本技能**不负责**：社区数据查询（→ `openjiuwen-community-stats`）、跨产品线选路（→ guideline）、Bug 登记（→ `record-bugs`）。

---

## 角色

本技能将 Agent 定位为 **openjiuwen-agent-core-java 的问答助手**：基于技能包内打包的文档与源码快照作答，而不是泛化的 Java/LLM 应用开发闲聊。

- **本文件（`SKILL.md`）**：通用问答流程，与具体版本号无关。
- **`references/v*.md`**：某一版本的文档索引 + 代码索引（任务 → 最小阅读集合）。
- **`references/java-sdk-notes.md`**：Java 包结构、与 Python SDK 边界、易混概念与产品线边界（第三步取证时按需阅读）。
- **`assets/<version>/`**：该版本对应的 `documents/`、`src/`、`examples/` 等完整 Maven 仓库快照。
- **`scripts/fetch.sh` / `scripts/fetch.ps1`**：从 GitCode 按 tag 拉取快照到 `assets/<tag>/`（见下文「快照拉取」）。

---

## 快照拉取（`scripts/`）

当用户要求**拉取、同步、更新** agent-core-java 源码快照，或问答所需版本的 **`assets/<version>/` 不存在**时，Agent **应主动执行**本技能包内的拉取脚本（需本机已安装 `git` 且可访问 `gitcode.com`），拉取完成后再继续索引与取证。

| 脚本 | 环境 |
|------|------|
| `scripts/fetch.sh` | Linux / macOS / Git Bash |
| `scripts/fetch.ps1` | Windows PowerShell |

**固定仓库**：`https://gitcode.com/openJiuwen/agent-core-java.git`  
**输出目录**：`assets/<tag>/`（相对技能包根目录 `openjiuwen-agent-core-java/`，与 `references/<tag>.md` 同名）

### 用法（Agent 可直接执行）

```bash
bash openjiuwen-agent-core-java/scripts/fetch.sh auto
bash openjiuwen-agent-core-java/scripts/fetch.sh v0.1.7
```

```powershell
powershell -ExecutionPolicy Bypass -File openjiuwen-agent-core-java\scripts\fetch.ps1 -Tag auto
powershell -ExecutionPolicy Bypass -File openjiuwen-agent-core-java\scripts\fetch.ps1 -Tag v0.1.7
```

| 模式 | 命令 | 行为 |
|------|------|------|
| **auto**（推荐批量） | `fetch.sh auto` / `-Tag auto` | 扫描 `references/v*.md`；`assets/<tag>` **已存在则跳过**，不存在则浅克隆 |
| **单 tag** | `fetch.sh v0.1.7` / `-Tag v0.1.7` | 仅拉取指定 tag；目录已存在且非空时报错（可用 `FORCE=1` 或 `-Force`） |

**触发示例**：「拉取/更新全部快照」「补齐 assets」→ `auto`；「拉取 v0.1.7」→ 单 tag（无 `v` 前缀时规范为 `vX.Y.Z`）。

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

- 索引：`references/<version>.md`（例如 `references/vX.Y.Z.md`）
- 内容根：`assets/<version>/`（例如 `assets/vX.Y.Z/`）

不得跨版本混读文档或源码。回答开头应简要声明本次依据的 `<version>`。

---

## 第二步：读 Reference 索引（必先于此）

1. 打开 **`references/<version>.md`**。
2. 根据用户问题，在索引中定位 **最小相关** 条目：
   - 优先用 **「按意图快速定位」** 表（任务 → 文档 / 代码 / 示例 / 测试）；
   - 再按需查阅 **文档索引**、**代码索引**、**示例索引**、**测试索引**。
3. 从索引中得到 **相对于 `assets/<version>/` 的路径**（如 `documents/zh/...`、`src/main/java/...`），作为下一步阅读清单；**不要**跳过索引直接通读 `SUMMARY.md` 或整包源码。

若索引中无直接匹配，再用索引文末的 **关键词检索提示** 缩小范围，或读该版本的 `documents/zh/SUMMARY.md` 做二次定位——仍保持在同一 `<version>` 下。

涉及 `ReActAgent` / `LlmAgent` / `WorkflowAgent` / `legacy` 等同名或近义概念时，先查索引 **「类型与概念对照」**，避免搜错包（详见 `references/java-sdk-notes.md`）。

---

## 第三步：在 assets 中取证（文档优先）

在 **`assets/<version>/`** 内按索引给出的路径阅读，默认顺序如下。

### 1. 文档

- 中文 API / 模块导航（主文档树）：`assets/<version>/documents/zh/...`
- 产品与快速上手：`assets/<version>/README.md`（英文）、`assets/<version>/README.zh.md`（中文）
- 本版本通常**没有**与 Python 仓对等的 `documents/en/` 全量树；深度 API 叙述以 **`documents/zh/`** 为准。用户未指定语言时，中文 API 文档优先；产品层术语可对照 `README.md` / `README.zh.md`。
- 仅读与问题相关的章节与 API 子树，避免通读。

### 2. 示例（需要用法或范式时）

- `assets/<version>/examples/<topic>/`（各子目录常有 `README.md`）

### 3. 源码（文档不足或需确认行为/签名时）

- `assets/<version>/src/main/java/com/openjiuwen/...`
- 构建与坐标以 `assets/<version>/pom.xml` 为准（`groupId` `com.openjiuwen`，`artifactId` `agent-core-java`）

### 4. 测试（需确认边界或回归预期时）

- `assets/<version>/src/test/java/com/openjiuwen/...`（路径通常与源码包结构对应）
- 必要时 `src/test/resources/`

**原则**：文档与代码冲突时，以 **`assets/<version>/` 内代码实际行为** 为准，并说明与文档的差异。问题含糊时，可先澄清一句再查索引，避免扩大阅读范围。

---

## 第四步：组织回答

回答或方案应包含：

| 项 | 要求 |
|----|------|
| **版本** | 写明本次使用的 `<version>`；Maven 坐标可引用 `pom.xml` 中的 `<version>` |
| **依据** | 列出实际阅读的 `references/<version>.md` 中的定位依据，以及 `assets/<version>/` 下的文档路径（`documents/zh/...` 或 `README*.md`） |
| **证据链** | 若涉及实现，注明 `src/main/java/...`、`examples/...` 或 `src/test/java/...` 的具体路径（均带 `assets/<version>/` 前缀） |
| **范围** | 不将当前版本结论默认推广到未打包的其它版本；明确 **Java SDK**，避免与 Python **openjiuwen-agent-core** 混用 API 名称 |

除非用户明确要求「只给结论 / 直接读代码」，否则应体现 **先索引、后 assets、文档优先** 的调查过程。

---

## 触发与边界

**适合使用本技能**：openJiuwen / agent-core-java 的 API、概念、配置、架构、示例用法、排障、设计与实现方式等，且答案应来自技能包内 `assets/<version>/` 快照。用户要求**拉取/更新/同步**快照或补齐 `assets/` 时，按「快照拉取」执行 `scripts/fetch.sh` 或 `scripts/fetch.ps1`。

**可不使用或需说明**：与 agent-core-java 无关的通用 Java 编程问题；用户机器上未打包的 fork/分支；需要实时联网文档而非本包快照时——应说明限制并建议用户提供版本或上下文。

---

## 目录约定（速查）

```
openjiuwen-agent-core-java/
├── SKILL.md                 # 本文件：通用 QA 流程
├── scripts/
│   ├── fetch.sh             # 按 tag 拉取快照（Linux / Git Bash）
│   └── fetch.ps1            # 按 tag 拉取快照（Windows）
├── references/
│   ├── vX.Y.Z.md            # 版本索引（auto 模式据此发现 tag）
│   └── java-sdk-notes.md    # 包结构、易混概念、与 Python / 其它产品线边界
└── assets/
    └── vX.Y.Z/              # 与 references/vX.Y.Z.md 同标签的快照根目录（由 fetch 拉取或手动放入）
        ├── documents/
        ├── src/
        ├── examples/
        ├── pom.xml
        ├── README.md
        └── README.zh.md
```

新增版本时：先新增 `references/<version>.md`，再执行 `fetch.sh auto` 或单 tag 拉取生成 `assets/<version>/`；亦可手动放入快照。**无需**为每个版本复制本 `SKILL.md` 的流程说明。

---

## 参考文件

| 文件 | 何时阅读 |
|------|----------|
| `references/<version>.md` | 每次问答（定版本后必读） |
| `references/java-sdk-notes.md` | 包结构不明、易混类名、与 Python SDK 或其它产品线边界 |
| `openjiuwen-qa-guideline/references/product-routing.md` | 跨产品线消歧、用户话术含糊 |

---

## Bug 发现与记录

确认是 Bug 后，经 **guideline** 串联加载 **`record-bugs`** 并执行脚本（`--module agent-core-java`，`--file` 为快照内路径如 `src/main/java/...`）。必须真正执行命令，勿仅口头说「已记录」。
