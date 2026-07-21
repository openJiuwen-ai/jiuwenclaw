---
name: openjiuwen-qa-guideline
description: openJiuwen 产品线 QA 总入口与流程编排。凡用户提到 openJiuwen、九问、agent-core/studio/runtime/deepsearch/jiuwenswarm、社区 Star/Issue/下载量、组件 API/配置/排障、疑似 Bug 登记、PENDING_BUGS，或不确定该用哪个 openjiuwen-* skill 时，必须先加载本技能再路由到子技能。本技能不替代子技能取证，只负责意图识别、产品消歧、流程串联与边界说明。涵盖三大能力（可并存）：社区信息（openjiuwen-community-stats）、组件取证分析与答疑（各 openjiuwen-*）、疑似问题记录（record-bugs，常在分析后追加，与答疑不互斥）。
---

# openJiuwen QA 总入口

## 角色

本技能是 **openJiuwen QA Skills 集合的总编排层**：识别用户意图（可组合）→ 产品消歧 → 选择并加载子 skill → 组件类先**取证分析**再**答疑** → 有证据或用户要求时**追加** Bug 记录。

**本技能负责**：意图分类、产品/仓库消歧、流程指引、多 skill 串联、边界与缺失说明。  
**本技能不负责**：直接读 `assets/` 快照、执行社区统计脚本、写入 `PENDING_BUGS.md`——这些由子 skill 完成。

技能包根目录（下文称 **QA 根**）即 `my-openjiuwn-qa/`，与各子 skill 目录同级。

---

## 主编排原则

| 原则 | 说明 |
|------|------|
| **意图可并存** | A（社区）、B（组件）、C（登记）不是三选一；同一轮可先 B 再 C，或 A+B 分段执行。 |
| **B 的主线是分析** | 组件类问题先路由到产品线 skill，在快照上**取证分析**；面向用户的**答疑/排障结论**是分析产出，不是跳过分析的独立终点。 |
| **C 不替代 B** | 确认 Bug 或用户要求记录时，在已有分析结论与证据路径基础上追加 `record-bugs`；可与答疑**同轮交付**（先答后记，或答复中说明将登记）。 |
| **仅 C 的捷径** | 用户**只**要登记且证据、`--module` 已齐全 → 可直接 `record-bugs`，无需再走 B。 |

---

## 第一步：意图分类

收到 openJiuwen 相关问题时，先识别涉及的能力域（可多选）：

| 意图 | 典型用户说法 | 路由子 skill |
|------|--------------|--------------|
| **A. 社区信息** | Star/Fork/下载量、Issue/PR 数量、超期 Issue、趋势、Tag/Release、贡献者统计、组织汇总 | `openjiuwen-community-stats` |
| **B. 组件分析答疑** | API 用法、架构、配置、安装部署、排障、示例、版本差异、源码行为 | 对应产品线 `openjiuwen-*`（见下表） |
| **C. 疑似问题记录** | 「记一下这个 bug」「登记待确认问题」 | `record-bugs` |

**B 与 C 的关系（重要）**：

- **默认路径**：有组件问题 → 走 B（分析）→ 输出答疑结论；若分析中**有证据确认缺陷**或用户**明确要求记录** → **继续** C，与答疑**不互斥**。
- **不要**因用户同时问「为什么报错」和「记一下 bug」就只走 C 或只口头登记；须先完成 B 的取证分析（或本轮已具备同等证据），再执行 `record_bug.py`。
- **A + B**：例如「studio 有多少 open issue，以及 v0.1.7 怎么配模型」→ 先 A 再 B，各自按子 skill 执行。
- **仅 C**：见上表「仅 C 的捷径」。

意图不清时：**先问一句**（社区数据 vs 哪个产品线排障/用法 vs 是否仅登记），不要默认查全组织或通读源码。

---

## 第二步：产品消歧（组件答疑 B 类必做）

用户未明确产品线时，根据关键词路由；**有歧义则先澄清**（例如「core」可能指 Python 或 Java）。

| 用户可能说法 | 子 skill | GitCode 仓库 | 最新 reference 版本 | record-bugs `--module` |
|--------------|----------|--------------|---------------------|------------------------|
| core、openjiuwen、python sdk、工作流、DeepAgent、harness | `openjiuwen-agent-core` | `agent-core` | v0.1.14 | `agent-core` |
| java、agent-core-java、Java SDK | `openjiuwen-agent-core-java` | `agent-core-java` | v0.1.7 | `agent-core-java` |
| studio、Agent Studio、低代码、画布、FlowGram | `openjiuwen-agent-studio` | `agent-studio` | v0.1.8 | `agent-studio` |
| runtime、运行时、部署、DeploymentManager、k8s 部署 | `openjiuwen-agent-runtime` | `agent-runtime` | v0.1.0 | `agent-runtime` |
| deepsearch、深度检索/研究、溯源、报告模板 | `openjiuwen-deepsearch` | `deepsearch` | v0.1.7 | `deepsearch` |
| swarm、jiuwenswarm、jiuwenclaw、jiuwenbox、IM 接入、企业版/企业 claw/企业 swarm | `openjiuwen-jiuwenswarm` | `jiuwenswarm` | **0.2.3**（企业 → `enterprise_kub`） | `jiuwenswarm` |

**易混边界**（加载子 skill 前心里过一遍）：

- **agent-core** vs **agent-core-java**：语言/包名不同，勿混读快照。
- **agent-studio** vs **agent-core**：Studio 是低代码平台；Core 是 SDK；除非用户明确桥接，勿用 Core 答 Studio UI。
- **agent-runtime** vs **agent-core**：Runtime 管部署与生产运行；Core 是开发 SDK。
- **deepsearch** vs **agent-core**：DeepSearch 基于 Core 但独立仓库与 skill。
- **jiuwenswarm** vs **studio/core**：Swarm 是个人 AI 管家产品，独立仓库。

完整消歧表与示例话术见 **`references/product-routing.md`**（产品边界复杂或一次涉及多仓时阅读）。

---

## 第三步：按意图执行子 skill

### A. 社区信息 → `openjiuwen-community-stats`

1. **立即阅读** `openjiuwen-community-stats/SKILL.md` 并按其流程执行。
2. **硬性约束**（与子 skill 一致）：用户问单个产品 → 必须带 `--repo`；未指定产品 → 先问用户，勿默认查全组织。
3. 脚本在 `openjiuwen-community-stats/scripts/`；只读 GET，勿臆造数据。
4. 长列表遵守子 skill 字数与分批规则。

### B. 组件分析答疑 → 对应 `openjiuwen-*`

编排层只做串联；**分析取证**由子 skill 完成。标准链路：

```
消歧（第二步）→ 加载子 skill → 定版本 → 索引 → assets 取证分析 → 形成答复
                                                      │
                                                      └─（可选）满足 C 条件 → record-bugs
```

1. **立即阅读** 目标目录下的 `SKILL.md`（如 `openjiuwen-agent-core/SKILL.md`）。
2. **分析**：定版本 → 读 `references/<version>.md` → 在 `assets/<version>/` 读文档/源码并归纳根因或用法（文档优先）；缺快照则先 `fetch.sh` / `fetch.ps1`（Windows 用 `.ps1`）。
3. **答疑**：基于分析结果作答——声明版本、索引依据、`assets/` 内证据路径；区分「已证实行为」与「推测/需复现」。
4. **版本约定**：`agent-core` / `studio` / `runtime` / `deepsearch` / `java` 的 tag 多为 `vX.Y.Z`，最新版本见上表「最新 reference 版本」列；**jiuwenswarm** 开源多为 `X.Y.Z`（无 `v`），最新 reference 为 **0.2.3**（分支 `dev_release_0.2.3`）；问企业版 / 企业 claw / 企业 swarm / K8s 企业部署时用索引 **`enterprise_kub`**（分支 `dev/enterprise_kub`）。如 `assets/` 目录中已存在对应版本的快照，优先使用已有快照，无需重新拉取。
5. **是否进入 C**：分析已确认 Bug（有快照路径+行号）或用户要求记录 → 进入下方 C，**不**因已给出答疑而跳过登记。

### C. 疑似问题记录 → `record-bugs`

**触发**（满足其一即可，且通常已有 B 的分析结论）：

- 用户明确要求记录；
- B 分析中已有充分证据认定缺陷（非猜测）。

**执行**：

1. **立即阅读** `record-bugs/SKILL.md`。
2. **必须执行**（勿口头说「已记录」）：

```bash
python record-bugs/scripts/record_bug.py \
  --title "简短标题" \
  --file "快照内相对路径:行号" \
  --module "<上表 module>" \
  --severity "高|中|低" \
  --desc "一句话" \
  --analysis "根因与证据（可与 B 分析结论一致）"
```

3. 从 **QA 根**执行；确认 JSON `"success": true` 后告知用户：已记录，后续由责任人确认并开 Issue。

**仅 C**：无组件分析需求、证据与 `--module` 已齐全时，可跳过 B 直接执行上式。

组件 skill 文末的 Bug 提示与 **`record-bugs`** 一致；写入以本脚本为准。

---

## 第四步：组织回答（编排层补充）

在子 skill 产出内容之上，编排层回答宜包含：

| 项 | 说明 |
|----|------|
| **本次路径** | 意图组合（如 B+C）、产品线、是否已跑 `record_bug.py` |
| **分析结论** | 根因/行为依据与证据路径（B 的主交付） |
| **答疑要点** | 对用户问题的直接回答、配置步骤或排障建议（由分析导出） |
| **登记状态** | 若走 C：说明已写入 `PENDING_BUGS.md`；未走 C 但像 Bug：说明待确认或请用户确认是否登记 |
| **后续建议** | 缺快照、缺版本、需拉取、需开 Issue 等可行动项 |
| **边界** | 非 openJiuwen、无快照版本、需实时外网文档而包内无资料时说明限制 |

勿重复子 skill 已写明的长篇流程；勿在未加载子 skill 时代答具体 API 细节。

---

## 子 skill 一览

| 目录 | 用途 | 最新版本 |
|------|------|----------|
| `openjiuwen-community-stats/` | GitCode 社区指标与 Issue 查询（只读） | — |
| `openjiuwen-agent-core/` | Python SDK（openjiuwen） | v0.1.14 |
| `openjiuwen-agent-core-java/` | Java SDK | v0.1.7 |
| `openjiuwen-agent-studio/` | Agent Studio 低代码平台 | v0.1.8 |
| `openjiuwen-agent-runtime/` | Agent 运行时与部署 | v0.1.0 |
| `openjiuwen-deepsearch/` | DeepSearch 深度检索 | v0.1.7 |
| `openjiuwen-jiuwenswarm/` | JiuwenSwarm 个人 AI 管家（企业版见同包 `enterprise_kub`） | 0.2.3 |
| `record-bugs/` | 疑似 Bug 写入 `PENDING_BUGS.md` | — |

---

## 快速决策（流程图）

```
用户 openJiuwen 相关问题
        │
        ▼
   意图分类（可多选：A / B / C）
        │
        ├─ 含 A ─────────────────────────► openjiuwen-community-stats
        │                                      （与 B 可先后串联）
        │
        ├─ 仅 C 且证据+module 齐全 ────────► record-bugs
        │
        └─ 含 B（组件问题，常与 C 同轮）
                │
                ▼
         第二步：产品消歧
                │
                ▼
         加载对应 openjiuwen-* 
                │
                ▼
         定版本 → 索引 → assets 取证分析
                │
                ├─► 形成答复（答疑）
                │
                └─ 含 C 或分析确认 Bug / 用户要求记录？
                        │
                       是 ──► record-bugs（与答疑同轮，不互斥）
                        │
                       否 ──► 结束（或提示用户可登记）
```

**误用**：把「答疑」和「记 Bug」画成两条互斥支路；或未经分析直接 `record-bugs`（仅 C 捷径除外）。

更多路由示例见 **`references/workflow-examples.md`**。

---

## 触发与边界

**应使用本技能**：任何 openJiuwen 产品线 QA；不确定用哪个子 skill；混合社区+产品问题；QA 流程入口、值班答疑、社区周报+产品问题串联。

**可不单独强调本技能**：用户已 `@` 明确单一子 skill 且意图无歧义（仍可按同一子 skill 执行，本技能作背景编排亦可）。

**超出范围**：与 openJiuwen 无关的通用编程；修改 GitCode 远程（Issue/PR 创建）——社区 skill 只读；未授权时勿拉取私有 fork。

---

## 参考文件

| 文件 | 何时阅读 |
|------|----------|
| `references/product-routing.md` | 产品名混淆、跨产品线、Studio/Core/Runtime 边界 |
| `references/workflow-examples.md` | 典型用户话术 → 意图与子 skill 映射 |
