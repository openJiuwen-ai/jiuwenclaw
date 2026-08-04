# Symphony Skill 等价群归一设计

## 1. 文档状态

本文描述 Symphony 离线能力树构建中的 Skill 等价群归一方案。设计结论如下：

- 上层继续构建普通 taxonomy，只表达能力分类和路由层级。
- 仅在直接拥有终端 Skill 叶子的原始分类节点内构建 equivalence group。
- equivalence group 表达“完成同一单一功能、可互相替代”的 Skill 集合，不承担上层分类职责。
- 原有 taxonomy 阶段的恢复行为保持兼容；新增等价阶段采用严格协议，协议连续失败时终止构建，不生成伪造分组。
- 功能通过显式配置开启，默认关闭。
- 增量 add/delete/update 只重算受影响的终端分支；只有全局协议或状态失效时才转为 full rebuild。

本文同时给出本地 GLM-5.2 discovery 实验方案和内部约 1000 个 Skill 的验证方案。实验数据、模型连接配置和临时脚本不进入正式 PR。

## 2. 背景与问题

Skill 集合中常存在多个功能相同或高度可替代的实现，例如多个演示文稿生成、网页正文抽取或中文润色 Skill。如果所有 Skill 都直接作为 taxonomy 的独立终端能力，会产生以下问题：

- 同一能力在树中重复出现，终端候选冗余。
- 上层分类和底层等价关系混在一次 LLM 分组中，分类粒度不稳定。
- 新增或删除一个重复 Skill 时，树形结构容易发生无关漂移。
- 无法回答“为什么这些 Skill 被认为可替代”，也难以审计错误合并。

已有的一次性 sibling 分组只能得到 LLM 给出的 partition，缺少 individual Skill 语义、两两验证、clique 约束和单功能审计。它既可能把弱相关能力合并，也可能因模型结构化输出不完整而静默保留错误结果。

## 3. 目标与非目标

### 3.1 目标

1. 每个多成员 equivalence group 只表达一个明确、可独立调用的功能。
2. 群内任意两个 Skill 都经过等价判断，并能完成同一个代表性请求。
3. 所有进入等价阶段的 Skill 恰好出现一次；没有等价对象的 Skill 形成单成员群。
4. 等价判断只发生在同一个原始 taxonomy 终端分支内，不跨上层分类合并。
5. 保留原始上层 taxonomy，只把终端 Skill 层改写为 `scope -> equivalence group -> Skill`。
6. 构建过程可审计，能够追溯候选来源、pairwise 判断、审计拆分和最终成员。
7. 对大分支设置明确成本上限，并通过局部候选召回避免全局 `O(N²)` 比较。
8. 支持 branch-local 增量 add/delete/update，避免单个 Skill 变化触发全量 LLM 构建。

### 3.2 非目标

- 不在本阶段重新设计上层 taxonomy 或修正所有历史分类偏差。
- 不做跨分支全局去重；跨分支但真实等价的 Skill 可能暂时漏合并，这是当前保守边界。
- 不根据 provider、热度、stars、下载量等质量信号选出“最佳 Skill”。等价群只描述可替代关系，排序是后续职责。
- 不把局部相似、上下游关系、父子能力或产物不同的 Skill 合并为等价群。
- 不在生产代码中加入 CSV 样例、内部 Skill 数据、特定 Skill 名称或 benchmark case 特判。
- 不把离线聚类指标直接表述为真实端到端检索收益。

## 4. 核心语义

### 4.1 等价定义

两个 Skill 只有同时满足以下条件时才判定为 `equivalent`：

- **主任务一致**：解决同一类用户意图，而不只是属于同一领域。
- **主产物一致**：最终产物类型和使用方式一致。
- **交互预期一致**：调用路径、关键输入以及完成方式没有本质差异。
- **能力边界兼容**：存在一个具体代表性请求，二者都能独立完整完成。

以下差异通常不破坏等价性：provider、实现后端、速度、价格、模板、质量等级或运行入口不同。

以下情况必须拆分：

- 只有部分能力重叠；
- 父功能与子功能；
- 上下游能力；
- 指导类能力与执行类能力；
- 最终产物不同；
- 必须使用多个并列功能词才能描述共同点的宽泛集合。

例如，“图片 prompt 生成”和“图片生成”、“文档编辑”和“格式转换”、“行情查询”和“选股筛选”都不是等价能力。

### 4.2 Scope 定义

`scope` 是一次等价归一允许考虑的候选边界，具体定义为：

> 原始 taxonomy 中直接拥有一组 terminal Skill leaf 的分类父节点，以及这些 sibling leaf 中全部
> individual Skill 的并集。

这个定义不能退化成“每个 Skill leaf 各自一个 scope”。尤其当
`max_skills_per_node=1` 时，同一 taxonomy 父节点下通常会出现多个一 Skill leaf；它们仍必须先合并为
同一个 scope，才能发现 sibling Skill 之间的等价关系。若一个 terminal taxonomy 节点直接持有多个
Skill、没有可合并的 sibling Skill leaf，则该节点自身作为 scope。只有一个 terminal leaf 时可以在该
leaf 的 taxonomy parent 内形成 singleton group；parent 仍是 scope，原 terminal leaf 层被 group 层
替换。synthetic `root` 只是序列化容器，永远不能成为 scope，否则会跨顶层 taxonomy 分类合并。

例如原树为：

```text
root
└── ContentCreation
    └── PresentationGeneration  <- scope
        ├── provider-a-leaf
        │   └── skill-a
        ├── provider-b-leaf
        │   └── skill-b
        └── provider-c-leaf
            └── skill-c
```

归一后保持 `PresentationGeneration` 不变：

```text
root
└── ContentCreation
    └── PresentationGeneration  <- 原 scope 仍是 taxonomy 节点
        ├── eq-...
        │   ├── skill-a
        │   └── skill-b
        └── eq-...
            └── skill-c
```

不同 scope 之间不生成候选 pair，也不合并 equivalence group。归一会替换 scope 下原有的 terminal
Skill leaf 层，但保留 scope 及其祖先；不会跨两个独立 taxonomy 父节点扩大候选范围。

## 5. 分层架构

构建流程分为两个语义独立的阶段：

### 5.1 阶段 A：上层 taxonomy 构建

本阶段回答“Skill 属于哪类能力、应该路由到哪条分支”，使用普通分类语义。Skill 在本阶段保持为独立叶子，不要求同层分类互相可替代。

通用 taxonomy prompt 不应包含“每个分组必须是最终等价能力”之类约束，否则会过早压平分类树，并让 `branching_factor`、`max_depth` 等参数失去原有语义。
该 prompt 切换只在 `equivalence_enabled=true` 时生效；开关关闭时继续使用原 PR 的单阶段 near-substitute
prompt，避免默认关闭却改变既有树结构、构建成本或增量行为。

### 5.2 阶段 B：终端 equivalence 归一

本阶段只处理阶段 A 产出的 scope，回答“同一终端分支内哪些 Skill 可以互相替代”。它不改变 scope 及其祖先，只在 scope 下插入 equivalence group 层。

两个阶段使用独立 prompt、输出 schema 和失败策略，避免 taxonomy 分类与等价判断互相污染。

## 6. 等价归一算法

### 6.1 输入

- 已完成 taxonomy 构建和原有覆盖审计的树；
- canonical Skill catalog；
- 每个 Skill 的名称、routing description、`select_when`、`dont_select_when`，以及长度受控的 `SKILL.md` 语义摘要；
- 模型、prompt、协议和阈值配置。

本地内部数据验证可由适配器把 `skillDesc` 映射为 routing description。该 CSV/Excel 适配只属于本地实验，不改变生产环境以 `SKILL.md` 为主的数据路径。

### 6.2 短引用协议

原始 `skillId` 可能很长、包含特殊字符，或在 JSON key 回显时占用大量 output token。等价阶段不要求 LLM 原样返回 canonical ID，而是在每次请求内建立临时短引用：

```text
s000001 -> canonical-skill-id-a
s000002 -> canonical-skill-id-b
```

短引用按 canonical ID 的稳定顺序生成，只存在于模型协议中。模型输入和输出只使用 `sNNNNNN`，解析完成后再映射回 canonical ID；树、catalog 和审计索引仍使用原始 canonical ID。

解析器必须验证：

- 引用属于本次请求；
- 无未知引用；
- 需要 partition 的输出中每个引用恰好出现一次；
- pairwise 输出恰好覆盖所请求的 pair；
- 不接受重复、缺失或额外成员。

短引用解决的是协议可靠性和 token 成本问题，不改变 Skill 身份，也不是数据 fallback。

### 6.3 候选生成

候选生成目标是尽量召回可能等价的 pair，不直接决定最终合并。

1. 当 scope 大小不超过 `equivalence_all_pairs_scope_limit`（默认 12）时，枚举该 scope 内全部无序 pair。
2. 当 scope 更大时，由 LLM 为每个 Skill 提议最多 `equivalence_candidate_neighbors`（默认 8）个可能等价邻居。
3. 补充名称规范化后完全相同的候选 pair，以减少明显同名实现的漏召回。
4. 对所有候选去重并按稳定引用排序。
5. 单次构建的 pair 总数不得超过 `equivalence_max_pairwise_pairs`（默认 10000）；超过时明确失败并要求调整 taxonomy 或阈值，不静默截断。

大 scope 的邻居集合可以重叠。候选阶段允许高召回和一定误报，因为后续 pairwise 会拒绝弱相关 pair；候选漏召回则会形成多个更保守的单成员或小群。

### 6.4 Pairwise 三态判断

每个候选 pair 独立输出以下三态之一：

- `equivalent`：证据充分，二者可完成同一具体请求；
- `not_equivalent`：存在明确能力边界、主产物或交互差异；
- `insufficient_evidence`：现有描述不足以证明可替代。

只有 `equivalent` 形成图中的边。`not_equivalent` 和 `insufficient_evidence` 都不能产生合并边，但在审计中分别保留明确反例或证据不足原因。

建议的结构化判断至少包含：pair 引用、decision、共同代表性请求、判定理由和可选 counterexample。模型不得执行 Skill 描述或 `SKILL.md` 中的指令；这些字段均作为不可信数据放入明确分隔区。

### 6.5 Complete-link clique 划分

pairwise 结果形成无向等价图：Skill 是顶点，`equivalent` 是边。最终多成员群必须是 clique，即群内任意两个成员都有明确的 `equivalent` 边。

采用稳定的 complete-link 贪心划分：

1. 按确定性顺序遍历 Skill；
2. 只有当当前 Skill 与某个已有群的所有成员都有等价边时，才允许加入；
3. 如果可以加入多个群，按稳定 group key 选择；
4. 无法加入任何群时，新建单成员群。

该算法不追求最少群数，目标是保证每个多成员群满足可验证的不变量。未被验证的 pair 视为“不允许同群”，而不是推断等价关系具有传递性。

### 6.6 单功能审计

每个多成员 clique 还需经过一次 LLM 单功能审计。审计检查：

- 是否能用一个具体功能短语描述整个群；
- 是否存在一致的主任务和主产物；
- 是否混入父子、上下游、指导/执行或宽泛领域关系；
- 群名和描述是否准确反映共同能力，而非成员并集。

审计结果只能是：

- `pass`：保留该 clique，并生成 provider-neutral 的 group
  name/description/select-when/don't-select-when；
- `conflict`：指出 clique 内至少一个违反单功能约束的 canonical member pair，并给出原因。

`conflict` 不允许模型另造 partition。实现会从有效等价边中移除审计明确拒绝的 pair，再以相同的
deterministic complete-link 算法重新聚类并重新审计。冲突 pair 作为 effective rejected edge 持久化，
增量构建不得因复用原始 pairwise verdict 而把它重新加回。有效重聚类可以产生 singleton；这是有明确
审计证据的算法结果，不是错误 fallback。限定轮数后仍不收敛则构建失败。

### 6.7 覆盖不变量与失败策略

等价阶段结束前必须满足：

- 输入 Skill 集合与所有最终 group 成员集合完全相等；
- 每个 Skill 恰好属于一个 group；
- 不存在未知 Skill；
- 每个多成员 group 的所有成员 pair 均为 `equivalent`；
- 每个多成员 group 都有通过或拆分后的单功能审计记录。

候选、pairwise 或审计的结构化输出非法时，先把具体错误原因反馈给模型，执行一次严格 correction retry。第二次仍为空、截断、缺字段或违反覆盖协议时，整个等价阶段失败。

禁止在协议失败后用确定性规则伪造 equivalence group，也禁止为了“构建成功”而把失败 batch 静默变成 singleton。正常算法因没有等价边得到 singleton 是有效结果，和协议失败 fallback 有明确区别。

### 6.8 当前执行与成本边界

当前等价阶段按 scope、candidate batch、pairwise batch 和 group audit 串行执行，`max_workers` 暂不并行这些请求。pair 数硬上限可以约束 pairwise 数量，但大 scope 的候选 prompt 仍会重复携带 scope profile；超大 clique 的一次性 group audit 也可能先触达模型 context 上限。此时必须保留异常并失败，不能走 fallback。

因此内部 1000 Skill 验证必须同时记录最大 scope、prompt/token、请求数和 p95 latency。后续优化方向是稳定排序后的 scope/batch 并行，以及把超大 group audit 改成“先确定 canonical capability，再分批验证成员、仅对冲突 pair 回查”的分层协议；这些优化需保持相同的 clique、覆盖和严格失败不变量。

## 7. 与原有 taxonomy 恢复行为的兼容边界

本方案不改变阶段 A 已有的恢复策略。现有 taxonomy 分类流程中的缺失分配 retry、把残余 Skill 放入最大分组/最大子节点，以及最终覆盖审计补入 `uncategorized` 的行为继续保留。

这是向后兼容边界，而不是等价阶段的新设计。阶段 B 接收阶段 A 恢复后的完整叶子集合，并在自己的 scope 内执行严格覆盖校验；阶段 B 不复用 largest-group 或 `uncategorized` 语义来掩盖协议错误。

后续如果要收紧 taxonomy 恢复行为，应作为独立变更评估，不能与本次等价群质量优化混在同一个结论中。

## 8. 树改写与稳定标识

### 8.1 树改写

对每个 scope：

1. 保留 scope 节点及其所有祖先；
2. 移除 scope 直接拥有的 terminal Skill leaf，或清空 scope 直接持有的 Skill；
3. 为每个最终 equivalence group 创建一个 category 节点；
4. 把该组 Skill 作为直接 leaf 挂在 group 下；
5. singleton 也创建 group 节点，以保证最后一层结构一致。

最终树的 group 层是额外的终端语义层，不计入上层 taxonomy 的模型发现深度。

### 8.2 稳定 Group ID

Group ID 由以下 canonical 内容计算：

- scope 的稳定路径或 CID；
- 按 canonical 顺序排列的成员 Skill ID。

显示名称、LLM 返回顺序和协议版本不参与 ID 计算。相同 scope 和成员集合在重复构建中得到相同 ID；
成员集合变化时 ID 相应变化。实现使用 `equiv-` 加 SHA-256 的前 16 个十六进制字符，并在 report 中
保留 scope path 和完整成员集合。

## 9. 审计产物

在现有 `full/tree_index.yaml` 和 `full/catalog.jsonl` 之外，等价阶段写出：

### 9.1 `equivalence_audit.jsonl`

按事件记录：

- protocol hash、scope 和阶段；model 与全局配置保存在配套 report 中；
- scope ID/path 和临时短引用映射；
- 候选生成请求、实际 prompt、结构化结果和原始响应；
- pairwise decision、理由、共同请求、counterexample；
- clique 初分；
- 单功能审计、冲突 pair 和重聚类；
- 最终 group ID 与成员；
- correction retry 的错误原因和次数。
- 最后写入一个与完整 report scope hash 绑定的 `build_complete` 事件，用于检测截断或错配的审计文件。

### 9.2 `equivalence_report.json`

保存机器可读汇总和增量状态：

- 输入/输出 Skill 数、scope 数、group 数；
- multi-member/singleton 数量；
- candidate/pairwise 三态数量；
- 覆盖和 clique 不变量结果；
- 每个 Skill 的 semantic hash、scope 和 group 映射；
- 可复用 pair decision 及其协议 hash；
- LLM 调用、耗时和可从 runtime 取得的 token/correction retry 统计；原始 exchange 事件始终保留 attempt 和校验错误，可用于补算。

审计产物可能包含内部 Skill 描述和模型原始响应，应按内部数据处理，不提交 repo，不记录 API key、Authorization header 或其他密钥。

构建失败时不覆盖上一版完整 index；失败 staging 目录保留 report/audit，并通过 build status 的 `build_diagnostics_dir` 暴露，便于定位严格协议错误。旧 index 会被标记 stale，不继续作为当前检索结果，但其 cache 可供修复后的下一次增量重试复用。

## 10. 配置

核心配置建议如下：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `equivalence_enabled` | `false` | 是否启用终端等价归一 |
| `equivalence_all_pairs_scope_limit` | `12` | 小 scope 全量枚举 pair 的最大 Skill 数 |
| `equivalence_candidate_neighbors` | `8` | 大 scope 中每个 Skill 最多提议的候选邻居数 |
| `equivalence_max_pairwise_pairs` | `10000` | 单次构建允许的 pair 总上限 |

默认关闭是为了保持现有用户的树结构、构建成本和增量行为不变。只有显式开启后才生成 group 层和等价审计产物。

prompt schema 和等价协议正文进入 protocol hash；模型、canonicalization 实现、taxonomy 语义和以上阈值进入更外层的 incremental signature。只有 protocol hash、incremental signature 和两个 Skill 的 semantic hash 都兼容时，历史 pair decision 才可复用。

## 11. Branch-local 增量构建

增量设计的基本不变量是：一个 Skill 的变化最多影响其旧 scope 和新 scope，不得改写无关分支。

### 11.1 持久状态

`equivalence_report.json` 至少保存：

- `skill_id -> semantic_hash/scope_id/group_id`；
- `scope_id -> stable path/member ids/group ids`；
- canonical pair key 对应的三态 decision；
- 单功能审计通过结果和 audit-rejected pair；
- taxonomy、模型、prompt、协议和配置 hash。

pair decision 只有在两个 Skill 的 semantic hash 和 protocol hash 都未变化时才可复用。

发布前和增量复用前都校验 report 的 protocol/incremental signature、scope/group 唯一性、Skill 覆盖、clique 与 audit 不变量，并用 JSONL 尾部 `build_complete` 事件核对 report scope hash。每个已发布 index 另写小型 build metadata；崩溃恢复只有在 build fingerprint、inventory fingerprint、数量和 manifest 都匹配时才允许提升 backup，不能仅凭路径相同恢复旧内容。

### 11.2 Add

1. 通过现有 taxonomy 增量路由找到新 Skill 的原始终端 scope；路由时忽略 equivalence group 层。
2. 只在该 scope 内为新 Skill 生成候选。
3. 复用旧成员之间仍有效的 pair decision，只调用新 Skill 与候选旧成员的 pairwise 判断。
4. 基于更新后的局部图执行 complete-link，并审计新增或成员发生变化的多成员群。
5. 只替换该 scope 子树；树中其他 scope 的序列化结果必须不变。

### 11.3 Delete

1. 通过持久映射定位旧 scope 和 group。
2. 删除 Skill 顶点及其 incident pair，并从 group 中移除。
3. 对 cached final groups 与保留成员求非空交集；删除不能跨两个旧 group 创造一个新 group。已通过
   审计的 group 子集复用原审计结论。即使删除后 scope 大小跨过 all-pairs 阈值，也不得为旧成员
   凭空补做新的 pairwise 或 group-audit 调用。
4. 删除空 group，保留有效 singleton/multi-member group；通常不需要新的 LLM 调用。
5. 只替换该 scope 子树并更新审计状态。

### 11.4 Update

语义字段未变化时只更新 catalog 元数据，不重算等价关系。语义字段变化时，在一个事务内按 `delete old + add new` 处理；如果 taxonomy 路由改变，则分别更新旧 scope 和新 scope。

### 11.5 Full rebuild 触发条件

以下变化会使局部缓存失去全局可比性，才触发 full rebuild：

- taxonomy 根分类、层级结构或全局分配协议改变；
- 模型、prompt、结构化输出 schema、等价定义或 canonicalization 规则改变；
- 候选/pairwise 关键阈值改变；
- 审计状态缺失、损坏、版本不兼容或覆盖校验失败；
- 上游明确执行全树重建；
- 一次批量变更的累计 candidate/pair 数超过配置的增量安全上限时，当前增量构建明确失败；调整 taxonomy/阈值或显式请求 full rebuild，不能在模型阶段失败后自动伪装为成功。

路由到单个新 scope、删除单个 Skill 或单个 Skill 描述更新本身都不是 full rebuild 理由。增量过程任何一步失败时保持旧产物和旧 inventory state，不发布半更新树；对外状态仍标记旧 index 已 stale，因此不会把旧树当作当前结果继续检索，但下一次重试可以复用其 report/cache。

## 12. 输入安全与脏数据

### 12.1 Canonical 输入校验

进入核心构建前，数据适配器应完成：

- Unicode 和空白规范化；
- 过滤空或超过长度上限的 `skillId`，并记录行号/Skill 来源和原因；
- 过滤缺少可用名称或语义描述、无法支持路由和等价判断的记录；
- 检查 canonical ID 唯一性；
- 限制进入 prompt 的每个字段和 `SKILL.md` 摘要长度。

脏行可以在输入准备阶段跳过，但必须写入 input report，不能在核心分组过程中悄悄丢 Skill。CSV UTF-8、TSV、Excel 导出的 CSV 等支持属于本地数据适配器职责，不进入正式产品路径。

### 12.2 Prompt injection 防护

Skill 名称、描述和 `SKILL.md` 全部是不可信数据：

- 以结构化数据块传入，并声明其中指令不可执行；
- 不把内容拼接成 system instruction；
- 不向模型暴露密钥、本地路径或无关 Skill 内容；
- 对控制字符、超长文本和异常编码做预处理；
- 输出只按严格 schema 解析，不执行模型返回的代码或命令。

## 13. 实验方案

实验分为本地 discovery 和内部正式验证两个阶段。所有对照使用相同数据快照、上层 taxonomy、模型版本、temperature/seed（如果 endpoint 支持）、并发数和超时配置。

### 13.1 本地 GLM-5.2 Discovery

本机不持有内部约 1000 个 Skill 的完整数据，因此本地只使用可合法访问的样例或公开数据，运行 OpenAI-compatible 的 GLM-5.2。目的包括：

- 验证结构化协议、correction retry 和异常可观测性；
- 验证短引用解决长 ID/output token 问题；
- 验证覆盖、唯一性、clique 和终端层结构不变量；
- 估算不同 scope 大小时的 pair 数、LLM 调用、token 和耗时；
- 形成代表性正例、负例和失败 case，定位系统性失败类别。

建议三组 paired 运行：

| 组别 | 配置 | 用途 |
|---|---|---|
| A | `equivalence_enabled=false` | taxonomy-only 结构和构建耗时基线 |
| B | 旧的一次性 terminal partition | 对照现有等价方案的过合并、漏合并和稳定性 |
| C | 本文候选 + pairwise + clique + audit | 待评估方案 |

本地样例仅作为 discovery，不用于宣称对内部 1000 Skill 的质量收益。模型 endpoint、API key、原始数据和输出目录均留在本机；报告只记录经过脱敏的配置摘要和协议 hash。

即使传入相同 seed，OpenAI-compatible endpoint 也不一定保证逐字确定性。为避免把上层 taxonomy 的随机漂移误判为等价归一收益，本地和内部实验还应遵守：

- 每组至少独立重复 3 次，同时报告均值、离散程度和成员一致率；
- A/B/C 使用同一份冻结的 taxonomy artifact 进入 terminal normalization，额外保留端到端重建作为系统稳定性测试；
- 若当前 runner 暂不支持复用 taxonomy artifact，则把结果明确标为非严格 paired discovery，不据此选择生产阈值；
- 比较 A/B/C 时使用成员 pair 和 canonical Skill ID，不依赖可能随模型变化的分类名称或节点 ID。

### 13.2 内部约 1000 Skill 验证

内部机器上对同一冻结数据快照和 taxonomy artifact 运行 A/B/C paired 实验，并对每组至少重复 3 次。质量标注按 scope、领域和已知等价 family 分层抽样，避免同一等价 family 同时进入调参集和最终审计集：

- **discovery**：用于归纳失败类别、完善 prompt/schema 和诊断工具；
- **development**：用于选择候选阈值和成本参数；
- **held-out**：方案冻结后只运行最终审计，不根据其结果继续修改生产逻辑。

构建可以使用完整 1000 Skill 上下文，评测标签按 scope/family 切分。这样既不破坏真实候选空间，也能避免根据 held-out 标签反向调参。

每个可复现 case 至少记录：split、scope/task ID、必要输入、修复前分组、失败类别、通用不变量、修复后输出和受影响指标。case 证据保存在本地 `case_evidence.json`，不把内部 Skill 名称或 benchmark 特判写入生产代码。

### 13.3 指标

**强制结构指标**：

- Skill 覆盖率为 100%；
- unknown/duplicate/missing Skill 均为 0；
- multi-member group 的 clique violation 为 0；
- 未审计 multi-member group 为 0；
- scope 外成员和无关分支 diff 为 0。

**候选与 pairwise 指标**：

- gold equivalent pair 的 candidate recall；
- pairwise precision、recall、F1；
- `insufficient_evidence` 比例及人工复核结果；
- counterexample 有效性抽检通过率。

**聚类指标**：

- pairwise clustering precision/recall/F1；
- B³ precision/recall/F1；
- 含人工 negative pair 的 over-merged group 比例；
- gold family 被拆散的 fragmentation；
- multi-member group 人工单功能通过率。

**成本与稳定性指标**：

- LLM call、input/output token、wall-clock time；
- scope latency 的 p50/p95；
- candidate pair 数与缓存复用率；
- 同配置重复构建的 group ID 和成员一致率；
- add/delete/update 相对 full rebuild 的耗时和调用节省；
- 增量后无关 scope 的 byte-level diff。

**可选下游指标**：在具备真实或标注 query 时，评估 terminal group Top-1/Top-K recall、候选冗余率和后续 Skill 排序质量。该结果需要与真实 dispatch 链路分开报告；仅有聚类改善不能推导端到端收益。

### 13.4 建议验收门槛

结构不变量必须全部通过。质量门槛在 development 上冻结后再用于 held-out，初始建议为：

- candidate recall 不低于 95%；
- pairwise precision 不低于 95%，recall 不低于 80%；
- over-merged group 比例不高于 5%；
- 相对 B 组的 B³ F1 不退化；
- 有下游 query 时，关键 retrieval 指标无显著回归；
- 增量 add/delete/update 不修改无关 scope。

这些数值是待内部数据校准的工程门槛，不是当前已验证结论。报告必须同时给出负例、回归、置信区间或样本量限制。

## 14. 模块职责

| 模块 | 职责 |
|---|---|
| `TreeBuilder` | 编排 taxonomy 构建、可选 postprocess、可选 terminal equivalence 和产物写出 |
| equivalence normalizer | scope 收集、短引用、候选、pairwise、clique、单功能审计和树改写 |
| prompt/schema | taxonomy 与 equivalence 独立协议、correction retry 和严格解析 |
| workflow/config | 配置透传、默认关闭、协议 hash 和构建上下文 |
| incremental workflow | 持久状态校验、branch-local add/delete/update 和原子发布 |
| audit writer | JSONL 事件与汇总报告，保证不泄露密钥 |

## 15. PR 与本地实验资产边界

正式 PR 应包含：

- 通用等价归一引擎、prompt/schema 和 TreeBuilder 集成；
- 配置链路及默认关闭行为；
- branch-local 增量逻辑和状态兼容检查；
- 必要的审计产物写出；
- 聚焦生产不变量、协议失败和增量边界的单元/集成测试；
- 本设计文档和必要用户配置说明。

以下内容保留在本地实验 workspace，不进入正式 PR：

- 内部约 1000 Skill 数据、CSV/Excel 读取脚本和数据清洗报告；
- `skillDesc` 专用构建入口；
- 小艺 root category 文件；
- HTML/树状可视化和一次性运行脚本；
- GLM-5.2 endpoint、API key 或其他环境配置；
- benchmark runner、人工标注、原始模型响应和实验结果目录。

该边界保证 PR 只携带可复用的产品能力，内部数据验证仍可在隔离机器上使用本地脚本完成。

## 16. 风险与后续演进

| 风险 | 当前处理 | 后续方向 |
|---|---|---|
| taxonomy 分支错误导致真实等价 Skill 不在同一 scope | 接受保守漏合并，不跨分支扩大候选 | 对高置信跨分支候选做独立、可审计的二阶段评估 |
| 大 scope 候选漏召回 | 邻居提议 + 同名补充，度量 candidate recall | 引入 embedding/lexical 多路召回并做 union |
| LLM pairwise 随机漂移 | temperature/seed 固定、严格 schema、一次 correction retry、保留证据 | 对高风险正向 pair 增加独立反例二审 |
| complete-link 贪心不是最小 clique cover | 保证确定性和安全性，不追求最大压缩 | 在不降低 precision 的前提下评估更稳定图分解 |
| 审计和 prompt 成本增加 | scope-local、pair cap、缓存和增量复用 | 按风险分层审计，低风险决策复用 |
| 审计产物包含内部语义数据 | 本地受控保存、禁止密钥、禁止提交 | 增加字段级脱敏和保留周期策略 |
| 等价阶段当前串行，1000 Skill 下延迟可能偏高 | 记录 scope/token/call/p50/p95，使用 pair hard cap | 在保持稳定输出顺序的前提下并行 scope/batch |
| 大 scope 候选重复携带 profile、超大 clique audit 可能超 context | 严格失败并保留 diagnostics，不生成 fallback | 分块候选召回与分层成员审计 |

本阶段优先保证等价 precision、覆盖性和可审计性。任何扩大召回或压缩率的优化，都必须在不破坏 clique、单功能和 scope 边界三个不变量的前提下进行。
