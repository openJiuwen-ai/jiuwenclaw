# PDA-style One-shot AHE — 第一阶段实现设计文档

> **日期**: 2026-06-21
> **分支**: dolores-trace
> **状态**: Draft
> **前置文档**: `evolve/PDA-style AHE.md`（算法思路）、`evolve/clean_trace.md`（OTEL 适配）、`evolve/AHE_to_PDA.md`（迁移思路）、`docs/superpowers/specs/2026-06-18-trace-diagnosis-agent-design.md`（DiagnosisAgent）

## 1. 摘要

在 `jiuwenswarm/evolve` 模块内实现 PDA-style One-shot AHE 的裁剪版（第一阶段），形成 **低风险、可审计、可回滚** 的最小闭环：

```
Trace → Clean → TaskEvaluation → Diagnosis → Proposal → Decision → CandidateApply → Record
```

第一阶段只开启 **Skill Experience Proposal**，Memory Policy 和 Training Candidate 作为后续扩展接口保留。

### 1.1 明确不做的能力

1. 不启动完整 Agent 作为 Propose/Decision 执行器
2. 不重新执行真实用户 trace
3. 不直接修改 SKILL.md
4. 不自动 solidify
5. 不做多轮 AHE outer loop
6. 不做完整 workspace commit/rollback
7. 不开启 Memory Policy 自动写回
8. 不自动生成训练数据集或触发模型训练
9. 不做后台无限扫描
10. 不做高风险现网自动 Apply

### 1.2 成功标准

1. 能从 traces.db 中读取并清洗 trace
2. 能通过 TaskCompletion evaluator 筛选 fail/uncertain trace
3. 能对失败 trace 生成根因诊断
4. 能生成带 evidence 的 Skill Experience Proposal
5. Decision 能拒绝无证据、重复、超限或高风险 Proposal
6. Apply 能将被接受的 Proposal 写入 candidate experience
7. 写入结果可追溯到 trace、proposal 和 decision
8. candidate experience 不会直接污染 SKILL.md
9. 经验数量受控，不会无限增长
10. 在 mini benchmark 或受控样例中观察到局部正向效果

## 2. 整体架构

### 2.1 模块结构

```
jiuwenswarm/evolve/
  # ── 现有模块（不改）──
  models.py                          # Proposal, DecisionResult, ApplyRecord, TraceBatch 等
  pipeline.py                        # EvolutionPipeline — generate → decide → apply
  proposal_generators/
    base.py                          # ProposalGenerator ABC
    llm_proposer.py                  # LLMProposer（保留，独立算法）
    rule_proposer.py                 # RuleProposer（保留）
  decision_policies/
    base.py                          # DecisionPolicy ABC
    rule_policy.py                   # RulePolicy（保留）
    eval_policy.py                   # EvalPolicy（保留）
  apply_writers/
    base.py                          # ApplyWriter ABC
    skill_writer.py                  # SkillExperienceWriter（需要扩展支持 operations）
    memory_writer.py                 # MemoryPolicyWriter（保留）
    training_writer.py               # TrainingCandidateWriter（保留）
  registry.py                        # Registry 实例
  storage/                           # EvolutionStore + SqliteStore + FileStore
  config.yaml                        # 配置
  cli.py                             # CLI 入口

  # ── 新增模块 ──
  otel_adapter.py                    # OtelTraceAdapter — OTEL spans → Langfuse dict
  ahe/
    __init__.py                      # 导出 AheProposer, AheDecisionPolicy, ExperienceGovernor
    models.py                        # GovernanceContext, TraceOutcome (AHE 算法内部模型)
    proposer.py                      # AheProposer — 自含 CLEAN→EVAL→DIAG→PROPOSE 流程
    decision_policy.py               # AheDecisionPolicy — RuleGate + LLMDecision
    evaluator.py                     # TraceOutcomeEvaluator
    experience_governor.py           # ExperienceGovernor — 经验治理上下文提供
    otel_adapter.py                  # OTEL spans → NormalizedTrace
    diagnosis/                       # AHE 诊断方案
      __init__.py
      agent.py                       # DiagnosisAgent — 消费 NormalizedTrace
      tools.py                       # 只读工具集 (read_trace, search_trace, list_traces)
      prompts.py                     # System prompt
      models.py                      # DiagnosisResult, DiagnosisIssue
```

### 2.2 数据流

```
AheProposer.generate(batch):
  ┌──────────────────────────────────────────────────────────┐
  │ 1. LOAD: TraceBatch.trace_ids → SqliteStore.read_spans   │
  │ 2. CLEAN: OtelTraceAdapter.convert_trace() → Langfuse dict│
  │           → extract_trace_data() → NormalizedTrace dict    │
  │ 3. EVAL: TraceOutcomeEvaluator.evaluate(input, output)    │
  │           → TraceOutcome (pass/fail/uncertain)            │
  │    → 筛选: 只保留 fail/uncertain trace 进入后续步骤       │
  │ 4. DIAG: DiagnosisAgent.run(normalized_traces, mode="diagnose") │
  │           → DiagnosisResult (issues + response)           │
  │ 5. GOV: ExperienceGovernor.get_context(skill_name)        │
  │           → GovernanceContext (已有经验、容量、可替换集)  │
  │ 6. PROPOSE: LLM(NormalizedTrace + TaskOutcome +           │
  │              DiagnosisResult + GovernanceContext)          │
  │              → Proposal[] (含 ExperienceOperation[])       │
  └──────────────────────────────────────────────────────────┘

AheDecisionPolicy.evaluate(proposal):
  ┌──────────────────────────────────────────────────────────┐
  │ 1. RuleGate: 硬约束检查                                   │
  │    - 字段完整性、evidence 非空、target_type 允许范围       │
  │    - 重复检测、数量上限、高风险判定、candidate-only 策略   │
  │    - 如果任一硬约束失败 → blocking=True, suggestion=REJECT│
  │ 2. LLMDecision: 语义判断                                  │
  │    - Proposal 是否与 Diagnosis 一致                       │
  │    - root_cause/targeted_fix 是否合理                     │
  │    - 操作类型 (ADD/MERGE/REPLACE) 是否适合当前治理上下文  │
  │    → 输出 score + suggestion (CANDIDATE/ACTIVE/REJECT)    │
  └──────────────────────────────────────────────────────────┘

SkillExperienceWriter.apply(proposal):
  ┌──────────────────────────────────────────────────────────┐
  │ 1. 解析 proposal.metadata["operations"] 为 ExperienceOp[] │
  │ 2. 加载目标 skill 的 evolutions.json                       │
  │ 3. 按 op 类型执行:                                         │
  │    - ADD: 追加新 experience (state=candidate)              │
  │    - MERGE: 合并 evidence_refs 到已有 experience           │
  │    - REPLACE: 替换指定 experience                           │
  │    - UPDATE: 更新已有 experience 内容                       │
  │    - DEPRECATE: 标记已有 experience 为 deprecated          │
  │    - NOOP: 不操作                                           │
  │ 4. 写入治理字段: proposal_id, decision_id, created_at      │
  │ 5. 写回 evolutions.json                                    │
  └──────────────────────────────────────────────────────────┘
```

### 2.3 与其他 ProposalGenerator 的解耦

AheProposer 自含步骤 2-4，不依赖外部前置步骤。它从 `TraceBatch.trace_ids` 和 `SqliteStore` 开始，内部完成所有数据处理。

其他 ProposalGenerator（如 `LLMProposer`、`RuleProposer`）保持不变，可以独立运行。Pipeline 的 `_generate()` 并发执行所有 generators，各自产出的 Proposal 合并后进入 Decision 阶段。

配置切换：

```yaml
evolve:
  pipeline:
    proposal_generators:
      # 方案 A：只用 PDA-style AHE
      - ahe_proposer
      # 方案 B：PDA + 基础 LLMProposer 并发
      - ahe_proposer
      - llm_proposer
      # 方案 C：只用原始 LLMProposer
      - llm_proposer
```

## 3. 各步骤详细设计

### 3.1 步骤 1 — LOAD_TRACES

**复用现有实现**，不改 `TraceBatch` 或 `SqliteStore`。

```python
# AheProposer.generate() 内部
batch_trace_ids = batch.trace_ids
trace_reader = self._trace_reader  # SqliteStore 实例
```

### 3.2 步骤 2 — CLEAN (TraceNormalizer)

**新增模块**: `jiuwenswarm/evolve/otel_adapter.py`

完全按照 `clean_trace.md` 的设计实现 `OtelTraceAdapter`：

- OTEL spans → Langfuse observation dict（逐字段映射）
- 调用 `_extract_trace_data_impl()` 产出 NormalizedTrace dict
- NormalizedTrace 包含: `id`, `messages`, `input`, `output`, `system_prompt`, `subagents`, `tool_definitions`, `total_tokens`

**NormalizedTrace 核心字段**（下游步骤依赖的）：

| 字段 | 类型 | 用途 |
|------|------|------|
| `id` | `str` | trace_id |
| `input` | `dict/str` | 用户输入（步骤 3 EVAL 用） |
| `output` | `dict/str` | Agent 最终输出（步骤 3 EVAL 用） |
| `messages` | `list[dict]` | 完整对话序列（步骤 4 DIAG 用） |
| `subagents` | `list[dict]` | 子 Agent 轨迹 |
| `system_prompt` | `str` | 系统提示词 |
| `total_tokens` | `int` | Token 消耗 |

### 3.3 步骤 3 — TASK_EVALUATE (TraceOutcomeEvaluator)

**新增模块**: `jiuwenswarm/evolve/ahe/evaluator.py`（包含 TraceOutcome, TaskNameInferrer, TraceOutcomeEvaluator）

```python
class TraceOutcome(BaseModel):
    """一个 trace 的任务结果判定。"""
    trace_id: str
    task_name: str | None = None
    outcome: str                    # "pass" | "fail" | "uncertain"
    score: float                    # 0.0-1.0
    confidence: float               # 0.0-1.0
    reason: str = ""
    key_evidence: str = ""
    missing_requirements: list[str] = []
    needs_external_verification: bool = False


class TaskNameInferrer:
    """从 NormalizedTrace 推断 task_name。"""
    def infer(self, trace: dict) -> str:
        # 1. 从 skill_tool span 提取 skill_name → "skill_{name}_{id[:8]}"
        # 2. 从第一条 user message 提取前 30 字 → "task_{snippet}_{id[:8]}"
        # 3. 兜底 → trace_id


class TraceOutcomeEvaluator:
    """基于 root span input/output 判断任务完成度。"""

    def __init__(self, model: Model | None = None):
        self._model = model  # openjiuwen Model

    async def evaluate(self, input_text: str, output_text: str) -> TraceOutcome:
        """LLM 评估 — 只基于用户输入和 Agent 最终输出。"""
        # 构造 system prompt (方案中已完整定义)
        # LLM 调用 (复用 openjiuwen Model)
        # 解析 JSON response → TraceOutcome
        ...

    def evaluate_fast(self, trace_dict: dict) -> TraceOutcome:
        """非 LLM 快速评估 — heuristic + span_error。"""
        # 策略 1: 检查 spans 中是否有 error status → fail
        # 策略 2: 检查 output 是否为空 → uncertain
        # 策略 3: 默认 → uncertain
        ...
```

**评估策略优先级**：
1. `span_error`: 检查 OTEL spans 是否有 `status.code = ERROR` → 快速判定 fail
2. `heuristic`: 检查 output 是否为空/截断 → uncertain
3. `llm_evaluator`: 对 heuristic/span_error 无法判定的 trace 调用 LLM

**筛选规则**: 只保留 `outcome = "fail"` 或 `outcome = "uncertain"` 的 trace 进入步骤 4-5。

### 3.4 步骤 4 — DIAGNOSE (DiagnosisAgent)

**已有设计文档**: `docs/superpowers/specs/2026-06-18-trace-diagnosis-agent-design.md`

关键集成点：
- AheProposer 在步骤 4 中调用 `DiagnosisAgent.run(normalized_traces=..., mode="diagnose")`
- DiagnosisAgent 消费 **NormalizedTrace**（即 CLEAN 步骤产出的结构化数据），而非原始 OTEL spans
- 工具集操作在 `messages` 字段上：`read_trace`、`search_trace`、`list_traces`
- 输出 `DiagnosisResult` 包含 `issues` 列表和 `response` 摘要

**注意**: DiagnosisAgent 也可独立运行（CLI 模式），此时内部自行执行 CLEAN 步骤。

### 3.5 步骤 5 — PROPOSE (AheProposer)

**新增模块**: `jiuwenswarm/evolve/ahe/proposer.py`

```python
@proposal_generators.register("ahe_proposer")
class AheProposer(ProposalGenerator):
    """PDA-style One-shot AHE — 自含 CLEAN→EVAL→DIAG→GOV→PROPOSE 流程。

    与其他 ProposalGenerator（llm_proposer, rule_proposer）完全解耦。
    内部串联所有前置步骤，对外只暴露 generate(batch) 接口。
    """

    def __init__(
        self,
        trace_reader: SqliteStore | None = None,
        store: EvolutionStore | None = None,
        model: Model | None = None,
        max_proposals: int = 3,
        max_skill_proposals: int = 2,
    ) -> None:
        super().__init__(name="ahe_proposer", trace_reader=trace_reader)
        self._store = store
        self._model = model
        self._max_proposals = max_proposals
        self._max_skill_proposals = max_skill_proposals
        self._adapter = OtelTraceAdapter(db_path=...)
        self._governor = ExperienceGovernor(store=store)

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        # ── 步骤 1: LOAD ──
        trace_ids = batch.trace_ids

        # ── 步骤 2: CLEAN ──
        normalized = []
        for tid in trace_ids:
            trace_dict = self._adapter.convert_trace(tid)
            cleaned = extract_trace_data(trace_dict)
            normalized.append({"trace_id": tid, "cleaned": cleaned})

        # ── 步骤 3: EVAL ──
        evaluator = TraceOutcomeEvaluator(model=self._model)
        outcomes = []
        for nt in normalized:
            outcome = await evaluator.evaluate(nt["cleaned"]["input"], nt["cleaned"]["output"])
            outcomes.append(outcome)

        # 筛选: 只保留 fail/uncertain
        failed_traces = [
            (nt, outcome) for nt, outcome in zip(normalized, outcomes)
            if outcome.outcome in ("fail", "uncertain")
        ]
        if not failed_traces:
            return []

        # ── 步骤 4: DIAG ──
        diagnosis_agent = DiagnosisAgent(store=self._store)
        diag_trace_ids = [nt["trace_id"] for nt, _ in failed_traces]
        diagnosis_result = await diagnosis_agent.run(
            trace_ids=diag_trace_ids, mode="diagnose"
        )

        # ── 步骤 5a: GOV (获取经验治理上下文) ──
        # 从 DiagnosisResult.issues 中提取涉及的 skill_name
        # 策略: 从 issue.evidence 中的 span name 提取 skill_tool 名称,
        # 或从 LLMProposer._is_skill_trace 的 SKILL_TRACE_MARKERS 匹配 span name
        skill_names = set()
        for issue in diagnosis_result.issues:
            for ev in issue.evidence_refs:  # EvidenceRef list
                # 如果 evidence description 包含 skill_tool 调用
                if "skill_tool" in (ev.description or ""):
                    # 从 span attributes 中提取 skill_name (需 trace_reader)
                    spans = self._trace_reader.read_spans(ev.trace_id)
                    for span in spans:
                        attrs = _parse_attrs(span.get("attributes", ""))
                        args = _parse_attrs(attrs.get("gen_ai.tool.arguments", ""))
                        if args.get("skill_name"):
                            skill_names.add(args["skill_name"])
        # fallback: 从 proposal.target_id 或 DiagnosisResult.issues 的 root_cause 推断

        governance_contexts = {}
        for skill_name in skill_names:
            governance_contexts[skill_name] = self._governor.get_context(skill_name)

        # ── 步骤 5b: PROPOSE (LLM 调用) ──
        proposals = await self._call_llm_propose(
            failed_traces=failed_traces,
            diagnosis_result=diagnosis_result,
            governance_contexts=governance_contexts,
            batch=batch,
        )

        # 数量控制
        proposals = self._enforce_limits(proposals)
        return proposals
```

#### ExperienceOperation — 治理感知的 Proposal 操作

```python
class ExperienceOperationType(StrEnum):
    """经验操作类型 — Propose 阶段明确表达治理意图。"""
    ADD = "add"           # 新增经验
    MERGE = "merge"       # 合并 evidence 到已有经验
    UPDATE = "update"     # 更新已有经验内容
    DEPRECATE = "deprecate" # 标记已有经验为 deprecated
    REPLACE = "replace"   # 替换指定已有经验
    NOOP = "noop"         # 不操作（已有经验覆盖当前问题）


class ExperienceOperation(BaseModel):
    """单个经验操作 — 包含在 Proposal.metadata["operations"] 中。"""
    op: ExperienceOperationType
    target_experience_id: str | None = None    # REPLACE/MERGE/UPDATE/DEPRECATE 时必填
    new_content: str | None = None             # ADD/REPLACE/UPDATE 时必填
    reason: str                                # 为什么选择这个操作
    evidence_refs: list[EvidenceRef]            # 操作的证据支撑
    expected_effect: str | None = None         # 预期效果
    risk: str | None = None                    # 风险评估
```

#### ExperienceGovernor — 经验治理上下文提供

```python
class GovernanceContext(BaseModel):
    """某个 skill 的当前经验治理状态 — 供 Propose 和 Decision 使用。"""
    skill_name: str
    current_count: int                   # 当前经验数量
    max_count: int                       # 允许的最大数量 (默认 10)
    can_add: bool                        # 是否还能 ADD (current_count < max_count)
    existing_experiences: list[dict]     # 已有经验摘要列表
    similar_experiences: list[dict]      # 与当前问题相似的已有经验 (可 MERGE)
    replaceable_experiences: list[dict]  # 可被替换的低价值经验 (candidate + low hit_count)
    protected_experiences: list[str]     # 受保护的经验 ID (state=active + high hit_count)
    allowed_operations: list[ExperienceOperationType]  # 当前允许的操作集合


class ExperienceGovernor:
    """经验治理上下文提供者。

    职责: 在 Propose 前提供治理上下文，在 Decision 中做校验。
    不后置改写 Proposal — 如果需要删除/合并/替换，必须在 Propose 阶段明确表达。
    """

    def __init__(self, store: EvolutionStore, max_per_skill: int = 10):
        self._store = store
        self._max_per_skill = max_per_skill

    def get_context(self, skill_name: str) -> GovernanceContext:
        """读取 skill 的 evolutions.json，构建治理上下文。"""
        # 1. 加载 evolutions.json
        # 2. 分类: existing, similar, replaceable, protected
        # 3. 判断 allowed_operations:
        #    - 如果 can_add → 允许 ADD
        #    - 如果有 replaceable → 允许 REPLACE
        #    - 如果有 similar → 允许 MERGE
        #    - 如果已有经验覆盖问题 → 允许 NOOP
        ...

    def validate_operation(self, skill_name: str, operation: ExperienceOperation) -> bool:
        """Decision 阶段校验: 操作是否在治理上下文允许范围内。"""
        ctx = self.get_context(skill_name)
        if operation.op not in ctx.allowed_operations:
            return False
        if operation.op == ExperienceOperationType.ADD and not ctx.can_add:
            return False
        if operation.op == ExperienceOperationType.REPLACE:
            if operation.target_experience_id not in [
                e["id"] for e in ctx.replaceable_experiences
            ]:
                return False
        return True
```

#### Proposer LLM Prompt 设计

```python
PDA_PROPOSER_SYSTEM_PROMPT = """
你是一名智能体演进提议专家。你将基于以下信息生成 Skill Experience Proposal：

## 输入信息

1. **任务评估结果**: 每条 trace 的 pass/fail/uncertain 判定和理由
2. **诊断结果**: 每条失败 trace 的根因诊断（issue_type, evidence, span_index）
3. **标准化 trace**: 失败 trace 的完整对话序列
4. **经验治理上下文**: 每个 skill 的已有经验、容量限制和允许操作

## 输出要求

为每个有明确 Skill 相关问题的 trace 生成一个 Proposal。每个 Proposal 包含:
- target_type: "skill"
- target_id: skill 名称
- proposal_type: "add_skill_experience" 或其他
- failure_evidence: 引用具体 trace_id + span_index
- root_cause: 根因分析
- targeted_fix: 修复建议 (action + suggestion)
- predicted_impact: 预期效果
- risk: 风险评估
- metadata.operations: 经验操作列表

## 经验治理约束

你必须严格遵守经验治理上下文中的约束:
- 如果 skill 已满 (can_add=False)，不能提出 ADD，只能 REPLACE 或 MERGE
- 如果已有相似经验，优先 MERGE 而非 ADD
- 如果已有经验已覆盖当前问题，提出 NOOP
- 每个 skill 每次最多 1 条操作
- 每批最多 2 个 Skill Proposal

## 操作类型说明

- ADD: 新增一条经验。需要 new_content。
- MERGE: 合并当前 evidence 到已有经验。需要 target_experience_id。
- UPDATE: 更新已有经验内容。需要 target_experience_id 和 new_content。
- REPLACE: 替换一条低价值经验。需要 target_experience_id 和 new_content。
- DEPRECATE: 标记已有经验为 deprecated。需要 target_experience_id。
- NOOP: 不操作（已有经验已覆盖问题）。

## 数量控制

- 每批最多 3 个 Proposal
- Skill Experience Proposal 最多 2 条
- 每个 skill 每次最多 1 条操作

输出 JSON:
{"proposals": [...]}
"""
```

### 3.6 步骤 6 — DECISION (AheDecisionPolicy)

**新增模块**: `jiuwenswarm/evolve/ahe/decision_policy.py`

```python
@decision_policies.register("ahe_decision_policy")
class AheDecisionPolicy(DecisionPolicy):
    """PDA-style Decision — RuleGate + LLMDecision 两阶段判定。

    RuleGate 负责硬约束检查，失败时 blocking=True。
    LLMDecision 负责语义判断，输出 score + suggestion。
    """

    def __init__(
        self,
        governor: ExperienceGovernor | None = None,
        model: Model | None = None,
    ) -> None:
        super().__init__(name="ahe_decision_policy")
        self._governor = governor or ExperienceGovernor(store=...)
        self._model = model

    async def evaluate(self, proposal: Proposal) -> DecisionResult:
        # ── Phase 1: RuleGate ──
        rule_result = self._rule_gate(proposal)
        if rule_result.blocking:
            return rule_result

        # ── Phase 2: LLMDecision ──
        llm_result = await self._llm_decision(proposal)
        return llm_result

    def _rule_gate(self, proposal: Proposal) -> DecisionResult:
        """硬约束检查 — 任一失败即 blocking。"""
        failed_checks = []

        # 1. 字段完整性
        if not proposal.failure_evidence:
            failed_checks.append("empty_failure_evidence")
        if not proposal.root_cause.strip():
            failed_checks.append("empty_root_cause")
        if not proposal.targeted_fix:
            failed_checks.append("empty_targeted_fix")

        # 2. target_type 范围检查 (第一阶段只允许 skill)
        if proposal.target_type != ProposalTargetType.SKILL:
            failed_checks.append("unsupported_target_type")

        # 3. 数量上限 (在 Pipeline._enforce_limit 中已处理, 这里做二次确认)

        # 4. 经验治理校验
        operations = proposal.metadata.get("operations", [])
        for op_dict in operations:
            op = ExperienceOperation(**op_dict)
            if not self._governor.validate_operation(proposal.target_id or "general", op):
                failed_checks.append(f"governance_violation_{op.op.value}")

        # 5. 重复检测
        if self._is_duplicate(proposal):
            failed_checks.append("duplicate_proposal")

        if failed_checks:
            return DecisionResult(
                proposal_id=proposal.proposal_id,
                policy_name=self.name,
                policy_version="1.0",
                score=0.0,
                reason=f"RuleGate blocked: {', '.join(failed_checks)}",
                suggestion=DecisionSuggestion.REJECTED,
                blocking=True,
                failed_checks=failed_checks,
            )

        return DecisionResult(
            proposal_id=proposal.proposal_id,
            policy_name=self.name,
            policy_version="1.0",
            score=0.5,  # RuleGate 通过但不打分，交给 LLM
            reason="RuleGate passed",
            suggestion=DecisionSuggestion.CANDIDATE,
            blocking=False,
            failed_checks=[],
        )

    async def _llm_decision(self, proposal: Proposal) -> DecisionResult:
        """LLM 语义判断 — 检查 Proposal 的合理性。"""
        # 获取治理上下文供 LLM 参考
        governance_ctx = self._governor.get_context(proposal.target_id or "general")

        # 构建 prompt
        # LLM 调用
        # 解析响应 → score + suggestion + reason
        ...
```

#### LLM Decision Prompt 设计

```
你是一名智能体演进决策专家。判断以下 Proposal 是否应该被接受。

## Proposal 内容
{proposal 的完整字段}

## 经验治理上下文
{governance_ctx 的摘要}

## 评估维度

1. **一致性**: Proposal 的 root_cause 是否与诊断结果一致？
2. **合理性**: targeted_fix 是否能有效解决 root_cause 描述的问题？
3. **可信度**: predicted_impact 是否可信？
4. **风险**: risk 是否可接受？
5. **治理合规**: 操作类型是否适合当前治理上下文？

## 输出 JSON

{
  "score": 0.0-1.0,
  "suggestion": "candidate | active | rejected",
  "reason": "判定原因"
}
```

### 3.7 步骤 7 — APPLY (SkillExperienceWriter 扩展)

**现有模块**: `jiuwenswarm/evolve/apply_writers/skill_writer.py`

需要扩展以支持 `ExperienceOperation`：

```python
# 在 SkillExperienceWriter.apply() 中增加
operations = proposal.metadata.get("operations", [])
if not operations:
    # 兼容旧 Proposal (无 operations 字段) → 默认 ADD
    return self._apply_add(proposal)

for op_dict in operations:
    op = ExperienceOperation(**op_dict)
    match op.op:
        case ExperienceOperationType.ADD:
            self._apply_add(proposal, op)
        case ExperienceOperationType.MERGE:
            self._apply_merge(proposal, op)
        case ExperienceOperationType.REPLACE:
            self._apply_replace(proposal, op)
        case ExperienceOperationType.UPDATE:
            self._apply_update(proposal, op)
        case ExperienceOperationType.DEPRECATE:
            self._apply_deprecate(proposal, op)
        case ExperienceOperationType.NOOP:
            pass  # 不操作
```

**治理字段写入**:

每条新写入/更新的 EvolutionRecord 需包含：

```python
record.metadata = {
    "proposal_id": proposal.proposal_id,
    "decision_id": "...",     # 从 DecisionResult 获取
    "trajectory_id": "...",   # 从 trace_id 获取
    "evidence_refs": [...],   # 从 failure_evidence 获取
    "state": "candidate",    # 默认 candidate，不直接 active
    "created_at": "...",
    "last_used_at": "...",
    "hit_count": 0,
    "success_after_hit_count": 0,
}
```

### 3.8 步骤 8 — RECORD

**复用现有实现**: `EvolutionPipeline._persist()` 已实现 Proposal + DecisionResult + ApplyRecord 的持久化。不需改动。

## 4. 经验治理规则

| # | 规则 | 实现位置 |
|---|------|---------|
| 1 | 新增 experience 默认 state = candidate | SkillExperienceWriter |
| 2 | 每批最多生成 3 个 Proposal | AheProposer._enforce_limits() |
| 3 | 每个 skill 每次最多新增 1 条 experience | ExperienceGovernor + AheDecisionPolicy |
| 4 | 每条 experience 必须包含 evidence_refs | AheDecisionPolicy RuleGate |
| 5 | 写入前重复检测，优先 merge evidence | ExperienceGovernor + AheProposer prompt |
| 6 | 记录 created_at, last_used_at, hit_count, success_after_hit_count | EvolutionRecord.metadata |
| 7 | 长期未命中 candidate 可 deprecated | ExperienceGovernor（后续阶段实现） |
| 8 | 被验证有害 experience 标记 rejected | ExperienceGovernor（后续阶段实现） |
| 9 | candidate 晋升 active 需通过验证 | 后续阶段实现 |
| 10 | 不允许直接写入 SKILL.md | 架构约束（SkillExperienceWriter 只写 evolutions.json） |

## 5. 配置扩展

在 `evolve/config.yaml` 中新增：

```yaml
evolve:
  pipeline:
    proposal_generators:
      - ahe_proposer          # 新增 PDA-style AHE

    decision_policies:
      - ahe_decision_policy   # 新增 RuleGate + LLMDecision
      # - rule_policy         # 可保留，但与 ahe_decision_policy 并发时需注意重复判定
      # - eval_policy         # 可保留

  pda:
    proposer:
      max_proposals_per_batch: 3
      max_skill_proposals: 2
      max_per_skill_per_batch: 1
      model_name: ""          # 空 = 使用 evolve.llm 配置

    decision:
      llm_decision_enabled: true
      rule_gate_strict: true

    governor:
      max_per_skill: 10       # 每个 skill 最多 10 条经验
      deprecate_threshold_days: 30  # 30 天未命中 → 可 deprecated

    diagnosis:
      max_iterations: 20
      temperature: 0.4
      max_tokens: 20000
      max_context_tokens: 200000
      context:
        compact_threshold: 0.75
        keep_iterations: 3
      tool_output:
        max_chars: 10000
        head_lines: 50
        tail_lines: 30

  otel_adapter:
    traces_db_path: "traces.db"
```

## 6. CLI 扩展

在 `jiuwenswarm/evolve/cli.py` 中新增：

```
jiuwenswarm-evolve run --pda               # 使用 ahe_proposer + ahe_decision_policy
jiuwenswarm-evolve run --latest 10          # 现有 CLI（可选择不同 proposer）
jiuwenswarm-evolve diagnose --latest 5     # 独立诊断模式
jiuwenswarm-evolve governor --skill bash --status  # 查看某 skill 的经验治理状态
jiuwenswarm-evolve governor --skill bash --deprecate <exp-id>  # 手动 deprecated
```

## 7. 与现有模块的集成点

| 集成点 | 文件 | 改动类型 |
|--------|------|---------|
| Registry | `evolve/registry.py` | 无改动（通过 `@proposal_generators.register` / `@decision_policies.register` 自动注册） |
| Pipeline | `evolve/pipeline.py` | 无改动（AheProposer/AheDecisionPolicy 通过标准接口接入） |
| CLI | `evolve/cli.py` | 新增 `--pda` 选项 + `governor` 子命令 |
| Config | `evolve/config.yaml` | 新增 `pda:` 配置段 |
| Skill Writer | `evolve/apply_writers/skill_writer.py` | 扩展支持 ExperienceOperation |
| Storage | `evolve/storage/` | 无改动（AheProposer 通过 SqliteStore 参数读取） |
| Models | `evolve/models.py` | 新增 ExperienceOperationType, ExperienceOperation, GovernanceContext, TraceOutcome |
| OTEL Adapter | `evolve/otel_adapter.py` | 新增（按照 clean_trace.md 实现） |
| Diagnosis | `evolve/diagnosis/` | 新增（按照 2026-06-18 设计文档实现） |
| PDA | `evolve/ahe/` | 新增 AheProposer, AheDecisionPolicy, ExperienceGovernor |

## 8. 测试策略

| 测试类型 | 覆盖点 |
|---------|--------|
| 单元测试 | OtelTraceAdapter 逐字段映射、边缘情况 |
| 单元测试 | TraceOutcomeEvaluator (span_error/heuristic/llm) |
| 单元测试 | TaskNameInferrer 各策略路径 |
| 单元测试 | ExperienceGovernor.get_context() 分类逻辑 |
| 单元测试 | ExperienceGovernor.validate_operation() 各操作校验 |
| 单元测试 | AheDecisionPolicy._rule_gate() 硬约束检查 |
| 单元测试 | AheProposer 数量控制 (_enforce_limits) |
| 单元测试 | SkillExperienceWriter ExperienceOperation 执行 (ADD/MERGE/REPLACE/NOOP) |
| 单元测试 | DiagnosisAgent 工具集、ReAct loop、上下文管理 |
| 集成测试 | AheProposer.generate() 完整流程 (Mock Store + Mock LLM) |
| 集成测试 | AheDecisionPolicy.evaluate() 完整流程 (RuleGate + Mock LLM) |
| 集成测试 | EvolutionPipeline.run() 使用 ahe_proposer + ahe_decision_policy |
| 端到端测试 | Mini benchmark: 从 traces.db → 完整闭环 → evolutions.json 写入验证 |
