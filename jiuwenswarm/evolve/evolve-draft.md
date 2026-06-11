# 自演进架构设计文档

## 1. 摘要

本文档定义一套面向 Agent Harness 的双轨自演进架构，用于在 630 前完成一个轻量化、可运行、可验证、可演示的自演进框架。

该架构采用“数据与诊断信号共享，决策与执行工作流分离”的设计原则，将自演进能力拆分为两条路线：

第一条是组件自演进工作流，面向 Skill、Memory、Tool 等外部行为组件，通过 `Trace → Proposal → Decision → Apply` 的轻量闭环，实现快速、低成本、可灰度、可回滚的组件优化。

第二条是模型自演进工作流，面向模型训练与能力内化，不直接纳入组件自演进主流程，而是通过组件自演进工作流沉淀 Training Candidate。模型自演进后续定期从 Training Candidate 中独立完成训练候选筛选、数据集构建、外部训练任务、模型评估、模型版本管理与发布治理任务。

本文档重点描述自演进框架必须实现的组件自演进主链路，以及为模型训练预留的 Training Candidate 数据连接点。

------

## 2. 背景与目标

### 2.1 背景

Agent 在执行任务过程中会产生 trace。trace 采用 OTEL 标准表达 Agent 运行过程中的 span、事件、属性、工具调用、模型调用、Memory 访问、输出结果、用户反馈和评估结果。

本架构不再将 trace 预先转换为 trajectory 作为统一数据输入，而是直接以 trace 作为自演进系统的事实来源。这样可以避免引入额外的中间抽象，降低数据转换成本，同时保留对 OTEL 生态、离线分析算法、可观测工具和后续诊断算法的兼容性。

需要注意的是，直接使用 trace 并不意味着算法必须直接处理原始 span 明细。系统可以在读取阶段构造临时的 Trace View，例如按 task、session、agent_run 或 benchmark_case 聚合后的视图。但 Trace View 只是读取与分析阶段的临时视图，不作为新的持久化核心数据结构。

当前阶段的核心目标不是一次性完成完整自演进平台，而是完成一个可运行、可验证、可演示的轻量级框架：

1. 能从真实或半真实 trace 中发现 Skill / Memory / Tool 问题；
2. 能生成结构化演进提议（Proposal）；
3. 能通过决策策略（DecisionPolicy）判断 Proposal 是否进入候选、激活或拒绝状态；
4. 能将通过决策的 Proposal 写入对应的 Skill / Memory / Training Candidate 存储；
5. 能在下一轮 Agent 执行中检索和使用写回结果；
6. 能证明优化策略具有一定效果；
7. 能沉淀模型训练候选数据集（Training Candidate），为后续模型自演进工作流提供数据输入。

### 2.2 设计目标

本架构需要满足以下目标：

● 以 Trace 作为统一事实输入；

● 以 Proposal 作为自演进核心中间对象；

● 以 DecisionPolicy 作为可插拔决策机制；

● 以 Apply 作为唯一写回阶段，写入目标由 Proposal 类型和 target_store 决定；

● 组件自演进与模型自演进保持分离；

● 两条工作流共享 trace 数据和诊断信号；

● 当前阶段优先实现 Skill / Memory 优化闭环；

● 模型训练阶段先沉淀 Training Candidate，不直接在主流程中完成训练。

------

## 3. 核心设计原则

### 3.1 关联在数据和诊断信号上

组件自演进和模型自演进不是两套完全孤立系统。它们共享同一套事实数据来源，即 trace。

组件自演进在分析 trace 时，会产生组件级诊断信号，例如：

● Skill 缺少执行约束；

● Memory 检索 query 过泛；

● Memory 检索到了但未使用；

● Tool 参数缺失；

● 某些 trace 具备训练价值。

这些信号可以作为模型自演进筛选训练候选数据的重要依据。

因此，组件流程可以输出 Training Candidate，但不负责最终决定数据是否进入正式训练集。

### 3.2 分离在决策和执行工作流上

虽然两条路线共享数据和诊断信号，但它们的执行逻辑不同。

组件自演进是低成本、高频、可回滚的外部行为优化，适合在线或半在线执行。它的 Apply 结果可以在下一轮 Agent 执行中立即被检索使用。

模型自演进是高成本、低频、批量化、强验证的能力内化过程。它涉及训练数据组织、训练任务、模型评估、模型版本管理和发布治理，不应塞入组件自演进的轻量流程。

因此，模型自演进应作为独立工作流，定期从 Training Candidate 中选择数据进行分析归类后独立训练演进。

### 3.3 Proposal 是核心中间对象

系统不直接根据 trace 修改 Skill 或 Memory，而是先生成 Proposal。

Proposal 用于表达：

● 发现了什么问题；

● 基于哪些 trace 证据；

● 认为根因是什么；

● 准备如何修复；

● 预期影响是什么；

● 风险是什么；

● 作用于哪个目标组件。

Proposal 使演进过程可审计、可验证、可回放。

### 3.4 DecisionPolicy 是可替换决策策略

系统不默认接受所有 Proposal。每个 Proposal 需要经过一个或多个 DecisionPolicy 判断，最终决定该 Proposal 是进入候选状态、激活状态，还是被拒绝。

DecisionPolicy 可以包括：

● Rule-based Decision；

● Evaluation-based Decision；

● LLM Judge；

● Replay / Debugger；

● Human Review。

这里不再使用 Acceptance 作为阶段名称，因为该阶段的职责不是单纯“接收”，而是基于证据、规则、评估结果和风险判断，对 Proposal 做状态决策。

### 3.5 Apply 是唯一写回阶段

Apply 阶段负责将已通过决策的 Proposal 写入具体目标存储。Apply 之后不再追加单独的 Memory 阶段。

这是因为 Apply 本身已经包含写回语义。对于 Skill Proposal，Apply 写入 Skill Experience Store；对于 Memory Proposal，Apply 写入 Memory Policy Store；对于 Training Candidate Proposal，Apply 写入 Training Candidate Store。

因此，Memory 不应被设计成组件自演进主链路中的固定最后一步。Memory 只是 Apply 的一种目标存储类型，而不是所有演进结果的统一流程阶段。

------

## 4. 组件自演进工作流

组件自演进工作流是初始架构的主线能力。

### 4.1 输入

输入来自 Trace Store 中的一批 trace。

这些 trace 可以由以下方式产生：

● Agent Runtime 真实执行；

● Benchmark 批量运行；

● 受控缺陷注入实验；

● 手动指定 trace batch。

组件自演进 Pipeline 不要求输入必须是预转换后的 trajectory。Pipeline 可以直接消费 trace_id 列表，也可以消费由采样器生成的 trace batch 描述。

### 4.2 主流程

组件自演进主流程为：

```text
Trace
→ Proposal
→ Decision
→ Apply
```

为降低初始设计阶段复杂度，Diagnosis / Evidence 不作为强制独立实体，而是可以内嵌在 Proposal 生成过程之中。

其中：

● Trace 是事实输入；

● Proposal 是候选改动；

● Decision 是验证、评分、风险判断和状态决策；

● Apply 是写回到目标存储。

Apply 完成后，流程即结束。后续 Agent 是否使用写回结果，属于下一轮运行时的检索与加载逻辑，不应作为本轮自演进流程的一个固定阶段。

### 4.3 Proposal 生成

Proposal 生成器直接消费 trace，可以内部执行轻量任务理解、组件评估和问题归因。

当前建议支持三类 Proposal：

1. Skill Proposal

例如新增 Skill Experience，补充任务步骤、工具调用约束、输出格式约束等。

2. Memory Proposal

例如新增 Memory Retrieval Hint、Memory Usage Policy、Memory Search Policy 等。

3. Training Candidate Proposal

例如将 trace 标记为疑似训练候选样本，但不直接进入正式训练集。

### 4.4 Decision 决策

DecisionPolicy 对 Proposal 进行验证、评分和状态决策，输出 DecisionResult。

初始阶段建议至少支持：

● Rule-based Decision；

● Evaluation-based Decision。

Rule-based Decision 负责检查：

● Proposal 字段是否完整；

● 是否存在 failure evidence；

● targeted_fix 是否为空；

● proposal_type 是否支持；

● 是否重复；

● 是否存在明显风险。

Evaluation-based Decision 负责判断：

● Proposal 是否基于有效证据；

● root_cause 是否合理；

● targeted_fix 是否可执行；

● predicted_impact 是否清晰；

● risk 是否可接受。

DecisionResult 不直接写回目标存储。它只是对 Proposal 的判断结果。是否写回由后续 Apply 阶段根据 Proposal 状态和 DecisionResult 决定。

### 4.5 Apply 写回

Apply 阶段只对通过决策的 Proposal 执行写回。

当前支持三类写回：

● Skill Experience Store；

● Memory Policy Store；

● Training Candidate Store。

Rejected Proposal 不参与写回，但需要保留 Proposal 和 DecisionResult 以便审计。

ApplyRecord 是证明闭环生效的关键对象。它说明 Proposal 是否真的写回，以及写到了哪个 store。

### 4.6 下一轮执行

下一轮 Agent 执行时，可以从对应存储中检索 candidate 或 active 状态的 Skill Experience / Memory Policy，形成优化后的执行上下文。

架构设计上，建议通过显式开关来确定是否加载演进结果：

```text
baseline mode：不加载演进结果；
evolution mode：加载 candidate / active experience 或 memory policy。
```

这样便于做 before-after 对照实验。

------

## 5. 模型自演进工作流

模型自演进不直接纳入组件自演进主流程，而是作为独立工作流存在。

### 5.1 输入

模型自演进的输入来自 Training Candidate Store。

这些数据由组件自演进流程推荐，但最终是否纳入训练集由模型自演进工作流独立判断。

### 5.2 工作流

模型自演进工作流包括：

```text
Training Candidate Selection
→ Dataset Build
→ External Training API / Training Job
→ Model Evaluation
→ Model Registry / Release
→ Updated Model Serving
```

### 5.3 职责边界

组件自演进负责：

```text
发现 trace 可能具有训练价值；
生成 Training Candidate Proposal；
通过 Decision 后写入 Training Candidate Store。
```

模型自演进负责：

```text
筛选候选数据；
组织训练数据集；
提交训练任务；
评估新模型；
判断是否进入 registry / release；
处理回滚和版本治理。
```

### 5.4 是否先修组件再训练

默认策略是：

```text
能用 Skill / Memory 修复的问题，优先走组件自演进；
只有重复出现、跨任务泛化、组件修复不足的问题，才提升为模型训练候选。
```

因此，Training Candidate Store 中除了记录当前 trace_id 之外，还应记录组件诊断结果，例如：

● 问题类型；

● 是否已有组件修复 proposal；

● 组件修复是否成功；

● 是否仍在后续任务中重复出现；

● 是否具备训练价值。

这避免模型训练变成失败样本垃圾桶。

------

## 6. 核心数据结构

当前架构中，主链路只保留三个核心跨模块对象：

```text
Proposal
DecisionResult
ApplyRecord
```

Trace 采用 OTEL 标准定义，不在本文档重复展开。本文档只要求通过 trace_id、span_id、field_path 等引用字段定位证据。

如果算法需要面向任务级别的聚合输入，可以在读取阶段构造临时 Trace View，但不建议新增持久化 trajectory 数据结构。

### 6.1 通用基础类型

```python
from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, Field, ConfigDict


JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ProposalTargetType(StrEnum):
    SKILL = "skill"
    MEMORY = "memory"
    TRAINING = "training"


class ProposalState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str | None = None
    field_path: str | None = None

    description: str
```

### 6.2 Proposal

Proposal 是演进的核心中间对象，表示一个候选改动。

```python
class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str # Proposal 的唯一标识

    target_type: ProposalTargetType # 这个提案要作用于哪类演进目标。
    target_id: str | None = None  # 具体要影响的对象 ID

    proposal_type: str # 更细粒度地描述 proposal 类型。例如 add_skill_experience、add_memory_retrieval_hint、add_memory_usage_policy

    failure_evidence: list[EvidenceRef] # 失败证据列表，精确指向 trace 中的具体位置。
    root_cause: str # 系统认为问题发生的根因。
    targeted_fix: dict[str, JsonValue] # 具体要写回的改动内容，不同 proposal_type 的 targeted_fix 结构不同。
    predicted_impact: str # 预期这个改动会改善什么，后续的 Decision 步骤会验证它是否有效。
    risk: str | None = None # 潜在风险或副作用。

    state: ProposalState = ProposalState.CANDIDATE # Proposal 当前状态。
    proposer_name: str # 哪个 proposer 生成了这个 proposal，方便排查不同策略质量。
    created_at: str # 创建时间。
    schema_version: str = "proposal.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict) # 有限扩展字段，不能承载核心流程依赖字段。
```

Proposal 至少需要回答：

```text
基于什么证据？
根因是什么？
准备怎么修？
预期有什么效果？
有什么风险？
```

其中 `failure_evidence / root_cause / targeted_fix / predicted_impact` 是 Proposal 的核心字段，不建议删除。

### 6.3 DecisionResult

DecisionResult 表示某个 DecisionPolicy 对 Proposal 的判断结果。

```python
class DecisionSuggestion(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str # 本次决策结果的唯一 ID。
    proposal_id: str # 被决策的 Proposal ID。

    # 策略信息
    policy_name: str # 决策策略名称。
    policy_version: str # 决策策略版本。

    # 决策结果
    score: float = Field(ge=0.0, le=1.0) # 策略给出的评分，范围 0 到 1。
    reason: str # 通过、保留为候选或拒绝的理由。
    suggestion: DecisionSuggestion # 决策策略建议的状态。
    blocking: bool = False # 是否阻断后续验证或写回。

    failed_checks: list[str] = Field(default_factory=list) # 失败的检查项。

    created_at: str # 创建时间。
    schema_version: str = "decision_result.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict) # 有限扩展字段，不能承载核心流程依赖字段。
```

DecisionResult 不直接修改 Proposal，也不直接写回目标存储。它只是决策策略的判断结果。

### 6.4 ApplyRecord

ApplyRecord 表示某个 Proposal 的写回动作和写回结果。

```python
class ApplyStatus(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


class TargetStore(StrEnum):
    SKILL_EXPERIENCE_STORE = "skill_experience_store"
    MEMORY_POLICY_STORE = "memory_policy_store"
    TRAINING_CANDIDATE_STORE = "training_candidate_store"


class ApplyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply_id: str # 本次写回动作 ID。
    proposal_id: str # 来源 Proposal。

    target_type: ProposalTargetType # 写回目标大类。
    target_store: TargetStore # 具体写入的目标 store。
    target_id: str | None = None # 目标对象 ID。skill_id、memory_id 或训练候选目标 ID。

    status: ApplyStatus # applied / skipped / failed。
    stored_object_id: str | None = None # 写入后生成的新对象 ID。

    reason: str # 写回成功、跳过或者失败的说明。

    applier_name: str # 哪个 applier 执行的写回。

    created_at: str # 创建时间。
    schema_version: str = "apply_record.v1"
    metadata: dict[str, JsonValue] = Field(default_factory=dict) # 有限扩展字段，不能承载核心流程依赖字段。
```

ApplyRecord 是证明闭环生效的关键对象。它说明 Proposal 是否真的写回，以及写到了哪个 store。

------

## 7. 数据连续性设计

系统需要保证以下引用链路成立：

```text
Trace
→ Proposal.failure_evidence[].trace_id
→ DecisionResult.proposal_id
→ ApplyRecord.proposal_id
→ stored_object_id
→ Next-run Retrieval
```

也就是说，给定任意一个 trace，应该能够追踪：

```text
它触发了哪些 Proposal；
这些 Proposal 经历了哪些 DecisionPolicy；
最终哪些 Proposal 被写回；
写回到了哪个目标 Store；
下一轮执行是否检索并使用了这些写回结果。
```

### 7.1 通过 ID 串联

三类核心对象使用 `proposal_id` 串联：

```text
Proposal.proposal_id
DecisionResult.proposal_id
ApplyRecord.proposal_id
```

Proposal 使用 `failure_evidence` 关联 trace：

```text
Proposal.failure_evidence[].trace_id
Proposal.failure_evidence[].span_id
```

ApplyRecord 使用 `stored_object_id` 关联写回对象：

```text
Skill Experience
Memory Policy
Training Candidate
```

### 7.2 通过 schema_version 支持演进

每个核心对象包含 `schema_version`，用于后续数据结构升级和历史数据迁移。

Demo 阶段统一为：

```text
proposal.v1
decision_result.v1
apply_record.v1
```

### 7.3 通过 metadata 支持有限扩展

每个对象保留 `metadata` 字段，但约束如下：

```text
核心流程依赖字段不得放入 metadata；
metadata 只存放非关键调试、实验、策略附加信息；
metadata 不应成为绕过 schema 的任意数据垃圾桶。
```

------

## 8. 触发与采样策略

自演进不应对所有 trace 实时触发，而应由外部触发器和采样器决定何时启动。

建议增加轻量模块：

```text
Evolution Trigger & Trace Sampler
```

该模块负责：

```text
什么时候演进；
选择哪些 trace；
生成 trace batch。
```

初始阶段建议支持：

1. 手动触发；
2. Benchmark 完成后触发；
3. 按 task_type + 低分筛选；
4. 按 memory_usage_score / skill_compliance_score 筛选。

组件自演进 Pipeline 不负责采样，只消费已经选好的 trace batch。

这里需要避免引入新的 trajectory 存储。Trace Sampler 可以输出 trace_id 列表、时间窗口、benchmark_run_id 或 session_id 等筛选条件，由后续 Proposal Generator 自行读取对应 trace。

------

## 9. Proposal 数量控制策略

为避免 proposal 泛滥、组件互相影响、效果无法归因，需要控制每批 trace 产生的 Proposal 数量。

初始阶段建议：

```text
每批 3-5 条 trace；
每批最多 3 个 Proposal；
其中会影响下一轮执行的 Behavior Proposal 最多 1-2 个；
Training Candidate Proposal 可以多个，但不直接影响执行。
```

建议区分：

```text
Behavior Proposal：
  Skill / Memory / Tool，会影响下一轮 Agent 执行。

Data Proposal：
  Training Candidate，只沉淀数据，不立即影响执行。

Model Proposal：
  Dataset / Training Job / Model Update，属于独立模型自演进工作流。
```

同一批 trace 应优先选择一个 primary target，例如 skill 或 memory，不建议同时大改多个组件。

------

## 10. 约束项与效果证明

1. 效果上优先以公开数据集运行产生的 trace 为例开始验证。
2. 不一定要找 OfficeClaw 上执行，其框架没有那么稳定。
3. 自演进一定要用自己的 trace，Agent 也要用 agent-swarm。
4. 短期验证应优先证明 trace 能够支撑 Proposal 生成、Decision 判断、Apply 写回和下一轮检索生效，而不是优先证明复杂算法性能上限。