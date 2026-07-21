---
name: openjiuwen-jiuwenswarm
description: **Sub-skill** of `openjiuwen-qa-guideline` — component Q&A for **JiuwenSwarm** (`jiuwenswarm` / `jiuwenclaw` on GitCode openJiuwen/jiuwenswarm). **Read `openjiuwen-qa-guideline/SKILL.md` first** for intent routing unless the user already @ the guideline or routing is already clear. Then use when the user mentions JiuwenSwarm, jiuwenclaw, jiuwenbox, IM integrations, `jiuwenswarm-init`/`jiuwenswarm-start`, Agent Team, Swarm 安装/配置/排障, or **企业版 / 企业 claw / 企业 swarm / enterprise_kub / K8s 企业部署**. Resolves a **version or special index** per question (`0.2.3` latest open-source, or `enterprise_kub` for enterprise), then follows **`references/<version>.md`** into **`assets/<version>/`**. When snapshots are missing or the user asks to pull/sync/update, run **`scripts/fetch.sh`** or **`scripts/fetch.ps1`**. Do not confuse with **openjiuwen-agent-core** or **openjiuwen-agent-studio** unless explicitly linked.
---

# JiuwenSwarm 问答助手

## 在 QA 体系中的位置

本技能是 **openJiuwen QA Skills 集合的子 skill**（产品线组件答疑），由总入口 **`openjiuwen-qa-guideline`** 编排路由。

**必须先读 guideline**：执行本技能前，须先阅读 **`openjiuwen-qa-guideline/SKILL.md`**，完成意图分类与产品线消歧——除非用户已 `@openjiuwen-qa-guideline`，或同轮对话中刚完成 guideline 路由且任务明确只属于 **JiuwenSwarm**。

| 属性 | 说明 |
|------|------|
| **层级** | 子 skill（非 QA 总入口） |
| **能力域** | 组件答疑（guideline **B 类**） |
| **上级编排** | `openjiuwen-qa-guideline` |
| **GitCode 仓库** | `openJiuwen/jiuwenswarm` |
| **`record-bugs --module`** | `jiuwenswarm` |
| **易混 skill** | `openjiuwen-agent-core`（SDK）、`openjiuwen-agent-studio` |
| **版本 tag 习惯** | 开源多为 `X.Y.Z`（**无 `v` 前缀**）；企业版专用索引 **`enterprise_kub`**（分支 `dev/enterprise_kub`），以 `references/` 为准 |

本技能**不负责**：社区数据查询（→ `openjiuwen-community-stats`）、跨产品线选路（→ guideline）、Bug 登记（→ `record-bugs`）。

---

## 角色

本技能将 Agent 定位为 **JiuwenSwarm（jiuwenswarm / jiuwenclaw）的问答助手**：基于技能包内打包的文档与源码快照作答，而不是泛化的 LLM 应用开发闲聊。

- **本文件（`SKILL.md`）**：通用问答流程，与具体版本号无关。
- **`references/[0-9]*.md`**：某一开源版本的文档索引 + 代码索引（任务 → 最小阅读集合；文件名即索引名，如 `0.2.3.md`；实际 Git 源见文件顶部 `<!-- git-ref: ... -->`）。
- **`references/enterprise_kub.md`**：企业版 / K8s 云化专用索引（分支 `dev/enterprise_kub`）。
- **`references/jiuwenswarm-sdk-notes.md`**：Gateway/AgentServer 分层、CLI 入口、与 SDK/Studio 边界（第三步取证时按需阅读）。
- **`assets/<version>/`**：该版本对应的 `docs/`、主包源码、`tests/`、`docker/`/`deploy/`、`scripts/` 等完整快照。
- **`scripts/fetch.sh` / `scripts/fetch.ps1`**：从 GitCode 按 reference 的 git-ref 拉取快照到 `assets/<name>/`（见下文「快照拉取」）。

---

## 快照拉取（`scripts/`）

当用户要求**拉取、同步、更新** JiuwenSwarm 源码快照，或问答所需版本的 **`assets/<version>/` 不存在**时，Agent **应主动执行**本技能包内的拉取脚本（需本机已安装 `git` 且可访问 `gitcode.com`），拉取完成后再继续索引与取证。

| 脚本 | 环境 |
|------|------|
| `scripts/fetch.sh` | Linux / macOS / Git Bash |
| `scripts/fetch.ps1` | Windows PowerShell |

**固定仓库**：`https://gitcode.com/openJiuwen/jiuwenswarm.git`  
**输出目录**：`assets/<name>/`（相对技能包根目录 `openjiuwen-jiuwenswarm/`，与 `references/<name>.md` 同名）

**Git 源**：每个 `references/<name>.md` 顶部可写 `<!-- git-ref: <branch-or-tag> -->`（如 `0.2.3` → `dev_release_0.2.3`，`enterprise_kub` → `dev/enterprise_kub`）。拉取脚本会优先使用该注释；无注释时用 `<name>` 本身作为 branch/tag。

**Tag / 索引命名**：开源索引多为 **无 `v` 前缀** 的 `X.Y.Z`。用户写 `v0.2.3` 时规范为 `0.2.3`。企业问题使用索引名 **`enterprise_kub`**（不是语义化版本号）。

### 用法（Agent 可直接执行）

```bash
bash openjiuwen-jiuwenswarm/scripts/fetch.sh auto
bash openjiuwen-jiuwenswarm/scripts/fetch.sh 0.2.3
bash openjiuwen-jiuwenswarm/scripts/fetch.sh enterprise_kub
```

```powershell
powershell -ExecutionPolicy Bypass -File openjiuwen-jiuwenswarm\scripts\fetch.ps1 -Tag auto
powershell -ExecutionPolicy Bypass -File openjiuwen-jiuwenswarm\scripts\fetch.ps1 -Tag 0.2.3
powershell -ExecutionPolicy Bypass -File openjiuwen-jiuwenswarm\scripts\fetch.ps1 -Tag enterprise_kub
```

| 模式 | 命令 | 行为 |
|------|------|------|
| **auto**（推荐批量） | `fetch.sh auto` / `-Tag auto` | 扫描 `references/[0-9]*.md`，若存在则含 `enterprise_kub.md`；`assets/<name>` **已存在则跳过**，不存在则浅克隆 |
| **单索引** | `fetch.sh 0.2.3` / `fetch.sh enterprise_kub` | 仅拉取指定索引；目录已存在且非空时报错（可用 `FORCE=1` 或 `-Force`） |

**触发示例**：「拉取/更新全部快照」「补齐 assets」→ `auto`；「拉取 0.2.3 / 企业版快照」→ 单索引。

执行后说明已拉取 / 已跳过 / 失败的 name；拉取失败时勿假装 `assets/` 内已有内容。

---

## 第一步：确定版本 / 索引

在查找任何文档或代码之前，先确定本次回答使用的 **索引名** `<version>`（开源形如 `X.Y.Z`，或企业专用 `enterprise_kub`）。

| 情况 | 做法 |
|------|------|
| 用户问 **企业版 / 企业 claw / 企业 swarm / enterprise / K8s 企业部署 / Manager / RuntimeManagement / 多租户云化** | **强制**使用 **`enterprise_kub`**（`references/enterprise_kub.md` → `assets/enterprise_kub/`），**不要**用开源最新 `0.2.x` 硬答 |
| 用户明确给出版本 | 采用用户指定值；若带 `v` 前缀则去掉（`v0.2.3` → `0.2.3`） |
| 用户未指定版本（且非企业问题） | 使用 **`references/` 中的最新开源版本**：列出 `references/[0-9]*.md`，按语义化版本取最大者；当前为 **`0.2.3`** |
| 用户指定版本但无对应资源 | 若缺 `references/<version>.md`：列出 `references/` 可用索引；若仅有索引、缺 `assets/<version>/`：**先执行「快照拉取」**（单 name 或 `auto`）；未授权则说明缺失并询问是否执行 `scripts/fetch.*` |

**硬性约束**：选定 `<version>` 后，后续所有路径 **仅** 使用：

- 索引：`references/<version>.md`（例如 `references/0.2.3.md` 或 `references/enterprise_kub.md`）
- 内容根：`assets/<version>/`（例如 `assets/0.2.3/` 或 `assets/enterprise_kub/`）

不得跨版本混读文档或源码（尤其禁止把 `enterprise_kub` 的 `jiuwenclaw/` 路径套到 `0.2.3` 的 `jiuwenswarm/` 上）。回答开头应简要声明本次依据的 `<version>`。

---

## 第二步：读 Reference 索引（必先于此）

1. 打开 **`references/<version>.md`**。
2. 根据用户问题，在索引中定位 **最小相关** 条目：
   - 优先用 **「按意图快速定位」** 表（任务 → 文档 / 代码 / 测试）；
   - 再按需查阅 **文档索引**、**代码索引**、**测试索引**。
3. 从索引中得到 **相对于 `assets/<version>/` 的路径**，作为下一步阅读清单；**不要**跳过索引直接通读 `SUMMARY.md` 或整包源码。

若索引中无直接匹配，再用索引文末的 **关键词检索提示** 缩小范围，或读该版本的 `docs/zh/SUMMARY.md` / `docs/en/SUMMARY.md` 做二次定位——仍保持在同一 `<version>` 下。

涉及 **Gateway / AgentServer / E2A / A2A / Team / jiuwenbox** 等分层或近义概念时，先查索引 **「概念对照」**，避免搜错模块（详见 `references/jiuwenswarm-sdk-notes.md`）。

---

## 第三步：在 assets 中取证（文档优先）

在 **`assets/<version>/`** 内按索引给出的路径阅读，默认顺序如下。

### 1. 文档

- 中文：`assets/<version>/docs/zh/...`（`SUMMARY.md` 为总目录）
- 英文：`assets/<version>/docs/en/...`（用户未指定语言时，中文优先，必要时对照英文）
- 产品说明：`README.md`、`README_CN.md`

### 2. 配置与运维（安装、部署、升级时）

- `pyproject.toml`（包版本、`project.scripts` CLI 入口）
- `docker/`、`scripts/`、`TESTING.md`

### 3. 源码（文档不足或需确认行为/签名时）

- 主运行时：开源 `0.2.x` 为 `assets/<version>/jiuwenswarm/...`；企业 `enterprise_kub` 为 `assets/enterprise_kub/jiuwenclaw/...`
- 附属 Box 产品：`assets/<version>/jiuwenbox/...`（若问题相关）
- TUI / 企业扩展包：`assets/<version>/packages/`
- 企业部署工具：仅 `enterprise_kub` → `assets/enterprise_kub/deploy/`

### 4. 测试（需确认边界或回归预期时）

- `assets/<version>/tests/unit_tests/...`
- 必要时 `tests/system_tests/`、`tests/integration/`、`tests/ui_e2e/`

**原则**：文档与代码冲突时，以 **`assets/<version>/` 内代码实际行为** 为准，并说明与文档的差异。问题含糊时，可先澄清一句再查索引，避免扩大阅读范围。

---

## 第四步：组织回答

| 项 | 要求 |
|----|------|
| **版本** | 写明本次使用的 `<version>`（无 `v` 前缀） |
| **依据** | 列出实际阅读的 `references/<version>.md` 中的定位依据，以及 `docs/zh/...` 或 `docs/en/...` 路径 |
| **证据链** | 若涉及实现，注明 `jiuwenclaw/...`、`jiuwenbox/...`、`tests/...` 等具体路径（均带 `assets/<version>/` 前缀） |
| **范围** | 明确 **JiuwenSwarm / jiuwenclaw**；不将当前版本结论默认推广到未打包的其它版本 |

除非用户明确要求「只给结论 / 直接读代码」，否则应体现 **先索引、后 assets、文档优先** 的调查过程。

---

## 触发与边界

**适合使用本技能**：JiuwenSwarm 的安装初始化、Gateway/AgentServer/Web/TUI/桌面端、频道接入、Agent Team、技能/记忆/自演进、定时任务与心跳、E2A/ACP/A2A、配置与权限、打包 exe、**企业版 K8s / Manager / 多租户**、分布式部署与排障等，且答案应来自技能包内 `assets/<version>/` 快照。用户要求**拉取/更新/同步**快照或补齐 `assets/` 时，按「快照拉取」执行 `scripts/fetch.sh` 或 `scripts/fetch.ps1`。

**应转其它 skill**（勿用本 skill 硬答）：

| 场景 | 转 |
|------|-----|
| 通用 `openjiuwen` SDK、Workflow/Session/Runner API（非 Swarm 平台层） | `openjiuwen-agent-core` |
| Studio 画布、Helm 装 Studio | `openjiuwen-agent-studio` |
| Runtime Server、`DeploymentManager` | `openjiuwen-agent-runtime` |
| DeepSearch 报告、溯源 | `openjiuwen-deepsearch` |
| Java SDK | `openjiuwen-agent-core-java` |

**可不使用或需说明**：与 Swarm 无关的通用编程；用户机器上未打包的 fork/分支；需要实时联网文档而非本包快照时——应说明限制并建议用户提供版本或上下文。

---

## 目录约定（速查）

```
openjiuwen-jiuwenswarm/
├── SKILL.md                 # 本文件：通用 QA 流程
├── scripts/
│   ├── fetch.sh             # 按索引拉取快照（Linux / Git Bash）
│   └── fetch.ps1            # 按索引拉取快照（Windows）
├── references/
│   ├── 0.2.3.md             # 开源最新索引（git-ref: dev_release_0.2.3）
│   ├── enterprise_kub.md    # 企业版索引（git-ref: dev/enterprise_kub）
│   └── jiuwenswarm-sdk-notes.md  # Gateway/AgentServer 分层、CLI、产品线边界
└── assets/
    ├── 0.2.3/               # 与 references/0.2.3.md 对应
    └── enterprise_kub/      # 与 references/enterprise_kub.md 对应
```

新增开源版本时：先新增 `references/<version>.md`（顶部写 `<!-- git-ref: <branch-or-tag> -->`），再执行 `fetch.sh auto` 或单 name 拉取生成 `assets/<version>/`。企业问答固定使用 `enterprise_kub`，**无需**为每个版本复制本 `SKILL.md` 的流程说明。

---

## 参考文件

| 文件 | 何时阅读 |
|------|----------|
| `references/<version>.md` | 每次问答（定版本/索引后必读） |
| `references/enterprise_kub.md` | 企业版 / 企业 claw / 企业 swarm / K8s 部署 |
| `references/jiuwenswarm-sdk-notes.md` | Gateway vs AgentServer 混淆、CLI 入口、与 SDK/Studio 边界 |
| `openjiuwen-qa-guideline/references/product-routing.md` | 跨产品线消歧、用户话术含糊 |

---

## Bug 发现与记录

确认是 Bug 后，经 **guideline** 串联加载 **`record-bugs`** 并执行脚本（`--module jiuwenswarm`，`--file` 为快照内路径如 `jiuwenclaw/...`）。必须真正执行命令，勿仅口头说「已记录」。
