# Symphony Skill 等价群归一设计

## 1. 背景

Skill 集合中存在大量功能相同或高度可替代的实现，例如多个 PPT 生成、网页内容抓取或中文润色 Skill。
如果离线构建阶段不处理这些重复能力，最终能力树会把同一功能拆成多个终端节点，带来三个直接问题：

- 检索候选重复，后续仍需在一组同质 Skill 中反复选择；
- 上层分类和底层等价关系混在一次 LLM 分组中，树的粒度不稳定；
- 新增或删除一个同类 Skill 时，容易引起无关分支漂移，也无法解释两个 Skill 为什么被合并。

本方案在原有 taxonomy 构建完成后，增加一次**终端分支内的等价群归一**：保留上层分类，只把最后一层统一整理为 equivalence group，原始 Skill 作为 group 下的叶子继续保留。

## 2. 目标与边界

### 2.1 目标

1. 每个 equivalence group 只表达一个明确、可独立调用的功能。
2. 多成员 group 内任意两个 Skill 都能完成同一个代表性请求，即满足两两可替代。
3. 所有原始 Skill 必须且只能进入一个 group；没有等价对象时形成 singleton group。
4. 等价归一只在同一原始 taxonomy 终端分支内进行，不跨上层分类合并。
5. 构建过程可审计，可以回溯候选 pair、pairwise 判断、拆分原因和最终成员。
6. 支持 branch-local add/delete/update，单个 Skill 变化不触发全量重建。

### 2.2 非目标

- 不重新设计上层 taxonomy，也不在本阶段修正历史分类错误。
- 不做跨分支全局去重；真实等价但位于不同分支的 Skill 当前会保守地保持分离。
- 不根据下载量、stars、provider 或成本选择“最佳 Skill”；group 只描述可替代关系。
- 不把局部相似、父子功能、上下游步骤或不同最终产物归为等价。

## 3. 等价定义

两个 Skill 只有同时满足以下条件时才判定为 `equivalent`：

- **主任务一致**：解决同一类用户意图，而不只是属于同一领域；
- **主产物一致**：最终产物类型和使用方式一致；
- **交互预期一致**：关键输入、调用方式和完成路径没有本质差异；
- **可双向替代**：存在一个具体请求，两个 Skill 都能独立、完整地完成。

允许存在 provider、实现后端、质量、速度、价格、模板和运行入口等差异，只要这些差异不改变用户可见的主功能和主产物。

以下情况必须拆分：

| 关系 | 示例 | 原因 |
|---|---|---|
| 产物不同 | 图片 prompt 生成 vs. 图片生成 | 一个输出 prompt，一个输出图片 |
| 专用能力与通用能力 | 学术海报 PPT vs. 通用 PPT 生成 | 专用布局和输入约束不可互换 |
| 上下游关系 | 网页搜索 vs. 网页正文抽取 | 分属不同工作阶段 |
| 指导与执行 | PPT 制作指南 vs. PPT 生成 | 一个给方法，一个产出文件 |
| 部分重叠 | 文档编辑 vs. 格式转换 | 只共享部分处理对象 |
| 宽泛能力集合 | 行情查询 vs. 选股筛选 | 主任务和决策结果不同 |

本方案宁可把边界不清的 Skill 保持为 singleton，也不以压缩率换取错误合并。

## 4. 总体方案

整体采用“**树优先、分支内归一、pairwise 判定、clique 收口**”的两阶段方案。

```mermaid
flowchart LR
    A["Skill catalog"] --> B["阶段 A：构建 taxonomy"]
    B --> C["按 terminal scope 收集 Skill"]
    C --> D["召回候选 pair"]
    D --> E["Pairwise 三态判断"]
    E --> F["Complete-link clique 划分"]
    F --> G["单一功能审计"]
    G --> H["重写终端树并写出审计产物"]
```

### 4.1 阶段 A：构建上层 taxonomy

阶段 A 只回答“Skill 属于哪类能力、应路由到哪条分支”，不做 equivalence 合并。一个 terminal leaf 可以承载一个或多个 individual Skill，阶段 B 再按 scope 收集这些 Skill。

只有在 `equivalence_enabled=true` 时，taxonomy prompt 才切换为纯分类语义；开关关闭时继续使用原 PR 的单阶段 near-substitute prompt，保持原有行为。

### 4.2 阶段 B：终端 equivalence 归一

阶段 B 只回答“同一终端分支内哪些 Skill 可以互相替代”。它不改变上层分类，只把原终端层改写为：

```text
taxonomy scope
├── equivalence group A
│   ├── skill-1
│   └── skill-2
└── equivalence group B
    └── skill-3
```

`scope` 是一次等价判断的边界：原 taxonomy 中直接拥有 terminal Skill leaf 的分类父节点，以及这些 sibling leaf 下全部 Skill 的并集。synthetic root 永远不能成为 scope，两个不同 scope 之间也不会生成候选 pair。包含 `uncategorized` 的分支保守地下钻处理，不把它和 sibling leaf 强行合成一个 scope。

### 4.3 关键设计决策与取舍

| 初版思路 | 当前设计 | 调整原因 |
|---|---|---|
| LLM 先生成候选 partition | LLM 只负责候选 pair 召回 | partition 容易把过宽分组提前固化 |
| 二值等价判断 | `equivalent / not_equivalent / insufficient_evidence` 三态 | 描述不足不能被误判为明确不等价或等价 |
| 缺失成员做确定性修复 | 协议 correction 一次，仍非法则失败 | 伪造 singleton 或补分组会掩盖模型/协议问题 |
| 未定义增量行为 | 只重算旧、新受影响 scope | 避免单个 Skill 变化触发全树重建 |
| 模型回显原始 Skill ID | 每次请求使用 `s000001` 短引用 | 避免长 ID 消耗 context 和 output token |

## 5. 算法设计

### 5.1 全量构建流程

**Algorithm 1：终端 Skill 等价群归一**

```text
Input:
  已构建完成的 taxonomy tree；
  canonical Skill catalog。

Output:
  scope -> equivalence group -> Skill 的构建树；
  equivalence report；
  完整审计事件。

1. 从 taxonomy tree 中收集所有 terminal scope。
2. 对每个 scope：
   2.1 为 Skill 建立本次请求内的稳定短引用 s000001...。
   2.2 召回可能等价的 candidate pairs。
   2.3 对每个 candidate pair 做严格三态判断。
   2.4 仅以 equivalent pair 建立无向图中的正边。
   2.5 使用 deterministic complete-link 生成 clique groups。
   2.6 对每个多成员 group 做单一功能审计。
   2.7 如果审计发现冲突 pair，删除对应正边并重新聚类；最多两轮。
3. 校验 Skill 覆盖、唯一性、clique 和审计不变量。
4. 保留上层 taxonomy，把每个 scope 的终端层替换为 equivalence groups。
5. 原子写出 tree、catalog、report 和 audit。
```

### 5.2 候选 pair 生成

候选阶段只负责召回，不直接决定合并：

- scope 大小不超过 `equivalence_all_pairs_scope_limit`（默认 12）时，枚举全部无序 pair；
- 更大的 scope 由 LLM 为每个 Skill 提议最多 `equivalence_candidate_neighbors`（默认 8）个邻居；
- 名称规范化后完全相同的 Skill 额外补充为候选；
- 所有候选去重并稳定排序；全局 pair 数超过硬上限时构建失败，不静默截断。

小 scope 的 pairwise 成本为：

```text
n × (n - 1) / 2
```

大 scope 通过候选邻居把待判断 pair 控制在接近 `O(n × k)`，其中 `k` 为邻居上限。该优化降低成本，但可能漏召回，因此内部实验必须单独评估 candidate recall。

### 5.3 Pairwise 三态判断

每个候选 pair 必须返回：

- `equivalent`：证据充分，两个 Skill 可双向替代；
- `not_equivalent`：存在明确的主任务、主产物、输入或能力边界差异；
- `insufficient_evidence`：当前名称、描述或 SKILL.md 摘要不足以证明可替代。

只有 `equivalent` 产生正边。正向判断还必须给出一个两个 Skill 都能完成的具体共同请求；负向判断必须给出区分请求或明确边界。

### 5.4 Complete-link 与单一功能审计

Pairwise 的 `equivalent` 关系不直接按传递闭包合并。最终多成员 group 必须是 clique：群内任意两个成员之间都有已验证的正边。

实现从 singleton cluster 开始，按稳定顺序反复合并两个 cluster；只有当两个 cluster 之间的全部 cross-pair 都有正边时才允许合并。

每个多成员 clique 还要通过一次单一功能审计：

- `pass`：生成 provider-neutral 的 group 名称、描述和路由边界；
- `conflict`：返回至少一个冲突 member pair。实现删除冲突边、重新聚类并再次审计，不允许模型直接另造 partition。

singleton 是正常算法结果，不需要 LLM 审计。

### 5.5 构建不变量与失败策略

完成构建必须同时满足：

1. 输入 Skill 和最终 group 成员集合完全相等；
2. 每个 Skill 恰好出现一次，不存在 unknown、duplicate 或 missing Skill；
3. 每个多成员 group 都满足 clique；
4. 每个多成员 group 都有通过的单一功能审计；
5. tree leaf、catalog CID 和 report 中的 scope/group 映射一致。

候选、pairwise 或审计输出为空、截断、字段类型错误或违反覆盖协议时，代码会把具体错误反馈给模型并执行一次 correction retry。第二次仍非法则整个等价阶段失败。

这里需要区分两类行为：

- 原 taxonomy 阶段已有的最大分组和 `uncategorized` 恢复逻辑保持不变；
- 新 equivalence 阶段不使用 fallback，不能把协议失败伪装成 singleton 或“成功构建”。

## 6. Branch-local 增量构建

增量设计的核心约束是：一个 Skill 的变化最多影响其旧 scope 和新 scope，无关分支不能被改写。

`equivalence_report.json` 持久化 Skill semantic hash、scope/group 映射、pairwise decision、审计通过结果和协议签名，用于判断哪些结果可以安全复用。

| 操作 | 处理方式 | 预期 LLM 成本 |
|---|---|---|
| Add | 路由到新 scope，只补新 Skill 相关 candidate/pair，并重算该 scope | 与目标 scope 大小相关 |
| Delete | 从原 group 和 pair cache 中移除 Skill，保留旧 group 的非空子集 | 通常为 0 |
| Update（非语义字段） | 只更新 catalog 元数据 | 0 |
| Update（语义字段） | 按 delete old + add new 处理；必要时同时更新旧、新 scope | 两个受影响 scope |

只有以下全局变化才触发 full rebuild：

- taxonomy 根分类、层级或全局分配协议变化；
- 模型、prompt、等价定义、schema 或 canonicalization 变化；
- 候选和 pairwise 关键阈值变化；
- 持久状态缺失、损坏、版本不兼容或覆盖校验失败；
- 用户显式请求 full rebuild。

增量失败时不覆盖上一版完整索引，也不发布半更新的 tree/report。失败 staging 和诊断信息会保留，便于定位协议错误。

## 7. 构建产物与配置

### 7.1 构建产物

| 文件 | 说明 |
|---|---|
| `tree_index.yaml` | 完整能力树，终端结构为 scope → equivalence group → Skill |
| `catalog.jsonl` | 原始 Skill catalog，CID 与最终树一致 |
| `equivalence_report.json` | scope/group/Skill 映射、统计、不变量和增量 cache |
| `equivalence_audit.jsonl` | prompt、raw response、pairwise、clique、审计和 correction 事件 |

Group ID 由稳定 scope 路径和排序后的成员 Skill ID 计算。成员集合不变时 ID 保持稳定；成员变化时生成新 ID。

审计文件可能包含内部 Skill 描述和模型原始响应，必须按内部数据处理，不记录 API key 或 Authorization header。

### 7.2 配置

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `equivalence_enabled` | `false` | 是否启用终端等价归一 |
| `equivalence_all_pairs_scope_limit` | `12` | 小 scope 全量枚举 pair 的最大 Skill 数 |
| `equivalence_candidate_neighbors` | `8` | 大 scope 中每个 Skill 的候选邻居上限 |
| `equivalence_max_pairwise_pairs` | `10000` | 单次构建允许的 pair 总上限 |

功能默认关闭，避免未显式启用时改变原 PR 的树结构、构建成本和增量行为。

## 8. 实验设计与当前结果

### 8.1 本地 GLM-5.2 discovery

本机没有内部约 1000 个 Skill 的完整数据。本地只使用用户提供的 18 个样例 Skill，输入仅包含 `skillName/skillDesc`，不包含 SKILL.md；实验定位为 discovery，不做 train/test 划分，也不用于证明生产质量收益。

共同配置：关闭 postprocess，固定相同模型与 seed，`branching_factor=4`、`max_depth=4`、`max_skills_per_node=3`、`model_discovery_max_depth=3`、`workers=2`。

| 方案 | 构建耗时 | 结果 |
|---|---:|---|
| A：taxonomy-only 中间实验版本 | 72.544s | 18/18 覆盖，9 个 terminal bucket |
| B：原一次性 terminal partition | 55.619s | 18/18 覆盖，11 个 bucket；出现若干偏宽合并 |
| C：严格 equivalence | 233.972s | 5 scopes、38 pairs、15 groups |

C 方案的 equivalence 阶段耗时约 172.446s：

- 3 个 `equivalent` pair，35 个 `not_equivalent` pair；
- 3 个多成员 group、12 个 singleton group；
- 3 次 group audit，0 conflict，0 correction retry；
- 覆盖、唯一性、clique 和审计不变量全部通过。

观察到的多成员结果包括 Tavily 搜索、浏览器自动化和 Humanizer 三组相近 Skill。它们说明协议和树改写链路能够工作，但没有人工 gold label，不能据此计算 precision/recall。

另外，即使设置相同 seed，当前 OpenAI-compatible endpoint 的 taxonomy 仍有随机漂移；A 也是开发过程中的中间实验版本，不能简单等同于当前代码的 `equivalence_enabled=false`。因此 A/B/C 不是严格 paired 实验，当前只能得出以下结论：

- 新方案的结构协议和失败语义已验证可运行；
- 新方案明显增加构建耗时；
- 是否降低误合并、是否值得该成本，仍需内部标注数据验证。

### 8.2 内部约 1000 Skill 验证方案

内部实验必须冻结同一份 Skill 数据和 taxonomy artifact，再对 A/B/C 各独立运行至少 3 次，避免把 taxonomy 漂移误判为 equivalence 效果。

标注按 scope、领域和已知等价 family 分层拆分：

- **discovery**：归纳失败类别和完善诊断；
- **development**：选择候选邻居、pair 上限等成本参数；
- **held-out**：方案冻结后只做最终审计，不根据结果继续修改生产逻辑。

评估指标分为四组：

| 类型 | 指标 |
|---|---|
| 结构正确性 | 100% 覆盖；unknown/duplicate/missing 为 0；clique violation 为 0；未审计多成员 group 为 0 |
| 候选与判断 | candidate recall；pairwise precision/recall/F1；`insufficient_evidence` 比例 |
| 聚类质量 | B³ precision/recall/F1；over-merge；fragmentation；人工单功能通过率 |
| 成本与稳定性 | LLM calls/tokens；wall-clock；scope p50/p95；重复运行成员一致率；增量无关分支 diff |

如果后续具备真实 query，再增加 terminal group Top-K recall 和候选冗余率。聚类指标改善不能直接表述为真实端到端 dispatch 收益。

## 9. 风险与后续

| 风险 | 当前处理 | 后续方向 |
|---|---|---|
| taxonomy 分类错误导致真实等价 Skill 位于不同 scope | 不跨分支合并，接受保守漏召回 | 对高置信跨分支候选做独立二阶段评估 |
| 大 scope 候选漏召回 | LLM 邻居召回 + 同名补充 | 引入 embedding/lexical 多路候选 union |
| LLM pairwise 漂移 | 严格 schema、一次 correction、保留原始证据 | 对高风险正向 pair 增加反例二审 |
| Pairwise 和 audit 成本高 | scope-local、pair hard cap、增量 cache | 在稳定输出顺序下并行 scope/batch |
| 超大 prompt 或 group audit 超 context | 明确失败并保留 diagnostics | 分批成员审计和冲突 pair 回查 |
| complete-link 不是最小 clique cover | 优先保证确定性和等价 precision | 在不降低 precision 的前提下评估其他图分解 |

后续优先完成三件事：在内部标注集上冻结质量与成本阈值；为高风险正向 pair 增加反例二审；为大 scope 引入 embedding/lexical 多路召回并并行安全的 scope/batch。

本阶段的优先级是：**等价 precision > 覆盖可审计 > 构建成本 > 压缩率**。任何扩大召回或提高压缩率的优化，都不能破坏 scope 边界、clique 和单一功能三个核心约束。

## 附录 A：模块职责

| 模块 | 职责 |
|---|---|
| `indexing/tree/builder.py` | 编排 taxonomy、可选 postprocess 和可选 equivalence 阶段 |
| `indexing/tree/equivalence.py` | scope 收集、短引用、候选、pairwise、clique、审计和 report |
| `indexing/tree/prompts.py` | taxonomy 与 equivalence 的独立 prompt 协议 |
| `indexing/workflows/tree_ops.py` | scope 子树替换及 branch-local 增量状态更新 |
| `indexing/workflows/index_builder.py` | 全量/增量 workflow、catalog 对齐和产物写出 |
| `skill_retrieval/index_service.py` | 构建状态、原子发布、失败诊断和恢复校验 |
