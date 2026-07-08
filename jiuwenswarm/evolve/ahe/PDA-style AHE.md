# PDA-style One-shot AHE：裁剪版工程化算法
![self-evolve-pda](./self-evolve-pda.png)
## 设计定位
AHE 原始算法面向固定 benchmark 和 sandbox 环境，通过多轮 rollout、AgentDebugger、workspace edit、manifest attribution、rollback 和 commit 实现 Harness 自动演进。该方式在论文实验环境中成立，但完整迁移到现有 PDA 框架会引入较高工程复杂度，包括 Agent 执行环境接入、真实用户 trace 不可重放、SKILL.md 修改风险、经验治理复杂度和多轮验证成本。

因此，第一阶段采用 PDA-style One-shot AHE 的裁剪版实现。该版本不追求完整复刻 AHE，而是保留其核心思想：从 trace 中提取证据，生成结构化 Proposal，通过 Decision 进行验收，最后将被接受的改动以 candidate experience 的方式写入现有演进机制。

第一阶段目标是形成一个低风险、可审计、可回滚的最小闭环：

Trace
→ Clean
→ Task Evaluation
→ Diagnosis
→ Proposal
→ Decision
→ Candidate Apply
→ Effect Observation

第一阶段只开启 Skill Experience Proposal，Memory Policy 和 Training Candidate 暂作为后续扩展接口保留。

## AHE迁移
AHE 原始算法通过多轮 rollout、trace clean、AgentDebugger、manifest attribution、rollback 和 commit，在固定 benchmark 上持续演进 harness。该流程适合 benchmark / sandbox 场景，但在真实用户 trace 场景下，历史任务不一定可重放，重新执行可能产生副作用。因此，在 PDA 框架中不直接照搬 AHE 的 rollout-based outer loop，而是将其改造为 production-aware 的单轮演进流程。

## 核心目标

### 第一阶段明确不做的能力

为降低工程风险，第一阶段不实现以下能力：

1. 不启动完整 Agent 作为 Propose / Decision 执行器；
2. 不重新执行真实用户 trace；
3. 不直接修改 SKILL.md；
4. 不自动 solidify；
5. 不做多轮 AHE outer loop；
6. 不做完整 workspace commit / rollback；
7. 不开启 Memory Policy 自动写回；
8. 不自动生成训练数据集或触发模型训练；
9. 不做后台无限扫描；
10. 不做高风险现网自动 Apply。

### 第一阶段成功标准

第一阶段只验证最小闭环是否成立，成功标准包括：

1. 能从 traces.db 中读取并清洗 trace；
2. 能通过 TaskCompletion evaluator 筛选 fail / uncertain trace；
3. 能对失败 trace 生成根因诊断；
4. 能生成带 evidence 的 Skill Experience Proposal；
5. Decision 能拒绝无证据、重复、超限或高风险 Proposal；
6. Apply 能将被接受的 Proposal 写入 candidate experience；
7. 写入结果可追溯到 trace、proposal 和 decision；
8. candidate experience 不会直接污染 SKILL.md；
9. 经验数量受控，不会无限增长；
10. 在 mini benchmark 或受控样例中观察到局部正向效果。

### 第一阶段经验治理规则

为避免经验管理失控，第一版引入最小治理机制：

1. 新增 experience 默认 state = candidate；
2. 每批最多生成 3 个 Proposal；
3. 每个 skill 每次最多新增 1 条 experience；
4. 每条 experience 必须包含 evidence_refs；
5. 写入前进行重复检测，重复经验优先 merge evidence；
6. 记录 created_at、last_used_at、hit_count、success_after_hit_count；
7. 长期未命中的 candidate 可以 deprecated；
8. 被验证有害的 experience 标记为 rejected；
9. candidate 晋升 active 需要通过 mini benchmark、人工确认或后验效果观测；
10. 不允许直接写入 SKILL.md。

### 决策原因
AHE 提供了很好的方法论启发，但其完整形态依赖可重放 benchmark、Agent 执行环境、workspace 修改、git commit/rollback 和多轮验证。直接完整迁移会显著增加工程复杂度和上线风险。因此，第一阶段不追求完整复刻 AHE，而是实现 PDA-style AHE-Lite：保留 trace evidence、proposal、decision、apply 的核心闭环，只在低风险的 Skill Experience 层做候选演进。待最小闭环验证有效后，再逐步扩展到 AgentDebugger、SafeValidate、Memory Policy、Training Candidate 和多轮演进。

### 分阶段演进指导思路
先闭环，再优化；
先可控，再自动；
先候选态，再正式态；
先单组件，再多组件；


## 整体算法思路如下

Algorithm: PDA-style One-shot AHE（六步流程）

Require:
  seed harness H0,
  trace set D,
  validation policy Ω,
  experience governance policy Γ
```shell
1:  Tbase ← LOAD_TRACES(D)
    # 从 traces.db 中读取历史执行轨迹。
    # 实现参考 jiuwenswarm/evolve/models.py 的 TraceBatch 类
    # 以及 jiuwenswarm/evolve/cli.py 中 trace 的载入逻辑。
    #
    # ⚠️ 分批处理策略（代码实现）：
    # - 每批最多 10 traces（避免一次性处理太多数据）
    # - 多批并发处理（asyncio.gather）

2:  Tclean ← CLEAN(Tbase)
    # 对 trace 进行标准化、脱敏、去噪和规范化，形成统一表示。
    # 实现参考 jiuwenswarm/evolve/ahe/otel_adapter.py 的 OtelTraceAdapter。
    # 输出：NormalizedTrace（包含 messages、tool_calls、input/output）
    #
    # ⚠️ 清洗策略（代码实现）：
    # - 从 OTEL span 提取关键信息
    # - 过滤无法清洗的 trace（记录警告）

3:  TaskEval ← TASK_EVALUATE(Tclean)
    # 只基于 root span 中的用户输入和 Agent 最终输出，
    # 判断任务完成度，输出 pass / fail / uncertain。
    # 实现参考 jiuwenswarm/evolve/ahe/evaluator.py 的 TraceOutcomeEvaluator。
    #
    # ⚠️ 评估方法（代码实现）：
    # - judgment_method: "span_error" | "heuristic" | "llm_evaluator"
    # - span_error: 直接从 span 发现 error event
    # - heuristic: 基于启发式规则（如 output 为空）
    # - llm_evaluator: 使用 LLM 进行语义分析
    #
    # ⚠️ 筛选策略（代码实现）：
    # - 只对 fail / uncertain 的 trace 进行后续诊断
    # - pass 的 trace 直接跳过（避免过度优化）

4:  Diagnosis ← DIAGNOSE(Tclean, TaskEval)
    # 对 fail / uncertain trace 进行根因诊断。
    # 实现参考 jiuwenswarm/evolve/ahe/diagnosis/agent.py 的 DiagnosisAgent。
    # 诊断目标是判断问题更可能来自哪个 Harness 组件。
    #
    # ⚠️ 诊断策略（代码实现）：
    # - 支持多轮迭代诊断（max 20 iterations）
    # - 使用 LLM 工具链分析根因
    # - 输出 diagnosis_result：issues、root_cause、suggested_fix
    # - 每个 issue 包含：trace_id、span_index、issue_type、summary、evidence

5:  Governance ← GOVERNANCE_CHECK(Diagnosis)
    # ⚠️ 新增步骤（文档未提及，但代码已实现）
    # 实现参考 jiuwenswarm/evolve/ahe/experience_governor.py 的 ExperienceGovernor。
    #
    # 双重安全检查：
    # 1. 是否为 builtin/system skill？（protected，不允许修改）
    # 2. 是否存在于用户 workspace？（不存在则不允许创建新 skill）
    #
    # Experience Classification：
    # - existing: 已存在的 experience
    # - similar: 相似的 experience（可用于 MERGE）
    # - replaceable: 可替换的 experience（times_used=0 或 score<0.6）
    # - protected: 受保护的 experience（times_used>0 且 score>=0.7）
    #
    # Allowed Operations：
    # - ADD / MERGE / REPLACE / UPDATE / DEPRECATE / NOOP
    # - 根据 current_count、usage_stats、score 计算 allowed_operations 白名单
    #
    # 安全机制：
    # - 只允许修改已存在的用户 skill
    # - 禁止修改 builtin skill（防止破坏核心功能）
    # - 禁止创建新 skill（防止 LLM 幻觉）

6:  P ← PROPOSE(Tclean, TaskEval, Diagnosis, Governance)
    # 对应 PDA 的 Propose 阶段。
    # 实现参考 jiuwenswarm/evolve/ahe/proposer.py 的 AheProposer。
    # 生成 Proposal，数据结构定义在 jiuwenswarm/evolve/models.py。
    #
    # Proposal 至少包含（五个必答问题）：
    # - failure_evidence（什么证据？）
    # - root_cause（什么根因？）
    # - targeted_fix（什么修复？）
    # - predicted_impact（什么影响？）
    # - risk（什么风险？）
    #
    # ⚠️ Proposal 数量控制（代码实现）：
    # - 每批最多 3 个 Proposal（含 Behavior + Data）
    # - Skill Experience Proposal 最多 2 条
    # - 每个 skill 每批最多新增 1 条 experience
    # - 每条 experience 必须包含可追溯 evidence
    #
    # ⚠️ ExperienceOperation（新增数据结构）：
    # - op: ADD / MERGE / REPLACE / UPDATE / DEPRECATE / NOOP
    # - target_experience_id: 目标 experience ID（MERGE/REPLACE 需要）
    # - new_content: 新内容（ADD/REPLACE/UPDATE 需要）
    # - reason: 为什么选择此操作
    # - evidence_refs: 支持此操作的证据

7:  A ← DECISION(P, H0, Tclean, TaskEval, Diagnosis, Governance, Ω, Γ)
    # 对应 PDA 的 Decision 阶段。
    # 实现参考 jiuwenswarm/evolve/ahe/decision_policy.py 的 AheDecisionPolicy。
    #
    # Decision 采用 RuleGate + GovernanceCheck + LLMDecision 三阶段：
    #
    # RuleGate（硬约束检查）：
    # - Proposal 字段是否完整
    # - 是否包含 failure_evidence
    # - target_type 是否在允许范围内
    # - targeted_fix 是否为空
    # - 是否与已有 experience 重复
    # - 是否超过每批 proposal 数量上限
    # - 是否超过每个 skill 的 experience 数量上限
    # - 是否存在高风险或越权修改
    #
    # GovernanceCheck（治理检查）：
    # - 验证 ExperienceOperation 是否在 allowed_operations 内
    # - ADD: 检查 can_add 是否为 true
    # - REPLACE/DEPRECATE: 检查 target 是否在 replaceable 内
    # - 检查是否违反 protected experience 保护规则
    #
    # LLMDecision（语义判断）：
    # - Proposal 是否与 Diagnosis 一致
    # - root_cause 是否合理
    # - targeted_fix 是否能解决对应问题
    # - predicted_impact 是否可信
    # - risk 是否可接受
    #
    # Decision 输出 DecisionResult，数据结构定义在 jiuwenswarm/evolve/models.py。

8:  Hfinal ← APPLY_ACCEPTED(P, A)
    # 对应 PDA 的 Apply 阶段。
    # 实现参考 jiuwenswarm/evolve/apply_writers/skill_writer.py。
    # 将 accepted / candidate 的 Proposal 写入 evolutions.json。
    #
    # ⚠️ 写入格式（代码实现）：
    # - 使用 EXISTING format（兼容 openjiuwen EvolutionStore）
    # - change: nested structure（section, action, content, target）
    # - usage_stats: nested structure（times_presented, times_used, etc）
    # - applied: boolean flag
    # - score: quality score
    #
    # Apply 阶段生成 ApplyRecord，保证审计链完整。

9:  Training ← APPLY_TRAINING_PROPOSALS(P, A)
    # 模型演进候选沉淀由 TrainingCandidateWriter 执行。
    # 实现参考 jiuwenswarm/evolve/apply_writers/training_writer.py。
    #
    # 双轨演进：
    # - 组件演进轨道（Skill/Memory）：写入 evolutions.json
    # - 模型演进轨道（Training Candidate）：写入 training_candidates 表
    #
    # 沉淀机制：
    # - 仅处理 target_type=training 且 Decision 后 state=active 的 Proposal
    # - 从该 Proposal.failure_evidence 中提取 trace_id
    # - 写入 training_candidates 表（sqlite）
    # - 为后续模型自演进工作流提供数据输入
    #
    # 不直接在主流程中完成训练，而是沉淀数据供后续独立处理。
```

**关键修正说明**：
1. ✅ 修正步骤编号（1-9 步，不再重复）
2. ✅ 补充 GOV 步骤（Governance Check）
3. ✅ 补充分批处理策略（max 10 traces/batch）
4. ✅ 补充安全机制（双重安全检查）
5. ✅ 补充 Training Candidate 沉淀步骤
6. ✅ 补充 judgment_method 说明
7. ✅ 补充 ExperienceOperation 数据结构
8. ✅ 补充 EXISTING format 说明
9. ✅ 标注代码实现文件路径

## 详细实现指导

###  1 Tbase ← LOAD_TRACES(D)
这部分的实现参考D:\github\jiuwenswarm\jiuwenswarm\evolve\models.py下面487行的TraceBatch类以及D:\github\jiuwenswarm\jiuwenswarm\evolve\cli.py下面第111和112行载入trace的方案执行。


### 2 Tclean ← CLEAN(Tbase)
具体实现参考D:\github\jiuwenswarm\jiuwenswarm\evolve\clean_trace.md 文档中的描述

### 3 Ebase ← EVALUATE(Tclean)

agentic-harness-engineering 方案（简称 AHE）中有一个算法，即需要在 harbor 的执行结果文件中统计任务的执行结果，使用 compute_statistics 评估分数的实现。

但是我们的 trace 来自用户实际使用，不是 benchmark 的受控执行。这意味着：我们需要一种方法来从 trace 内容推断任务是否成功。

我们要设计一个轻量的 **TaskCompletion Evaluator**，它阅读 trace 后判断任务是否真正成功。这个评估器输出的数据结构定义如下：

```python
class TraceOutcome(BaseModel):
    """一个 trace 的任务结果判定（AHE 算法使用）。

    实现参考：jiuwenswarm/evolve/ahe/models.py
    """

    trace_id: str
    """OTEL trace ID — 唯一标识"""

    task_name: str | None = None
    """从 trace 内容推断的任务描述（TaskNameInferrer 实现）"""

    outcome: str
    """任务完成度判定，必须是 "pass" / "fail" / "uncertain"

    - pass: 任务成功完成，主要要求被满足
    - fail: 任务明确失败，核心目标未完成
    - uncertain: 仅凭用户输入和输出无法可靠判断
    """

    score: float = Field(ge=0.0, le=1.0)
    """任务完成度分数

    - pass: 通常 0.8-1.0
    - fail: 通常 0.0-0.3
    - uncertain: 通常 0.4-0.6
    """

    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    """outcome 判定的置信度 0.0-1.0"""

    judgment_method: str = ""
    """判断方法（代码实现新增字段）

    - "span_error": 直接从 span 中发现 error event
    - "heuristic": 基于启发式规则（如 output 为空、tool call 失败）
    - "llm_evaluator": 使用 LLM 进行语义分析
    """

    reason: str = ""
    """一句话概括判定原因"""

    key_evidence: str = ""
    """支持判定的关键证据（引用或概括最关键的用户要求与输出证据）"""

    missing_requirements: list[str] = Field(default_factory=list)
    """如果 fail，列出关键缺失；如果 pass 或 uncertain，可为空数组"""

    needs_external_verification: bool = False
    """是否需要外部事实、工具结果、附件或系统状态才能判断"""
```

**TaskCompletion Evaluator 实现约束**：

1. 从 root span 中的用户输入和 Agent 最终输出，判断 Agent 是否完成了用户任务。
2. 该评估器只判断任务结果，不负责根因诊断，不检查中间步骤，不强行验证外部事实。
3. ⚠️ judgment_method 说明（代码实现）：
   - **span_error**（最高可信度）：直接从 OTEL span 中发现 `event` with `exception` type
   - **heuristic**（中等可信度）：基于启发式规则判断
     - output 为空或只有礼貌性回复
     - tool call 返回错误状态码
     - 缺少关键交付物（如文件未生成）
   - **llm_evaluator**（最低可信度）：使用 LLM 进行语义分析
     - 当 span_error 和 heuristic 都无法判断时使用
     - 需要额外调用 LLM（成本较高）

**TraceOutcomeEvaluator 实现参考**：`jiuwenswarm/evolve/ahe/evaluator.py`

```python
class TraceOutcomeEvaluator:
    """任务完成度评估器（AHE 算法 Step 3）"""

    async def evaluate_batch(
        self,
        normalized_traces: list[dict]
    ) -> list[TraceOutcome]:
        """批量评估 trace（并发执行）

        Args:
            normalized_traces: NormalizedTrace 列表（来自 CLEAN 步骤）

        Returns:
            TraceOutcome 列表，每个 trace 对应一个评估结果
        """
        # 实现细节见 evaluator.py
```

有了上述评估器，用户 trace 到 AHE 等价的 compute_stats 输出方案设计如下：

新增模块：`jiuwenswarm/evolve/ahe/stats.py`（可选，用于统计汇总）

```python
class TraceBatchStats:
    """从 TraceBatch 计算等价于 AHE compute_stats 的统计数据。

    输入: TraceBatch → OtelTraceAdapter → TraceOutcomeEvaluator
    输出: 与 AHE compute_stats() 同结构的 dict
    """

    def compute_stats(
        self,
        batch: TraceBatch,
        *,
        trace_reader: SqliteStore,
        prev_results: dict[str, str] | None = None,  # 上一轮的 task_results
        task_history: dict | None = None,              # 跨迭代历史
    ) -> dict:
        """针对每个任务，产出评估结果。"""

        # Step 1: 归一化 trace（CLEAN 步骤）
        adapter = OtelTraceAdapter(db_path=self._traces_db_path)
        normalized_traces = []
        for trace_id in batch.trace_ids:
            trace_dict = adapter.convert_trace(trace_id)
            trace_dict["trace_id"] = trace_id
            normalized_traces.append(trace_dict)

        # Step 2: 判定每个 trace 的 outcome（EVAL 步骤）
        evaluator = TraceOutcomeEvaluator(model=self._model)
        outcomes = await evaluator.evaluate_batch(normalized_traces)

        # Step 3: 统计汇总
        task_results: dict[str, str] = {}
        for nt, oc in zip(normalized_traces, outcomes):
            task_results[nt["trace_id"]] = oc.outcome

        return {
            "task_results": task_results,
            "total_traces": len(normalized_traces),
            "pass_count": sum(1 for oc in outcomes if oc.outcome == "pass"),
            "fail_count": sum(1 for oc in outcomes if oc.outcome == "fail"),
            "uncertain_count": sum(1 for oc in outcomes if oc.outcome == "uncertain"),
        }
```

---

## 代码实现与文档对应关系（新增章节）

### 核心文件对应关系

| 文档步骤 | 代码实现文件 | 核心类/函数 | 说明 |
|---------|-------------|-----------|------|
| Step 1: LOAD | `evolve/cli.py` + `evolve/pipeline.py` | `TraceBatch` + `run()` | 从 traces.db 加载 trace_ids |
| Step 2: CLEAN | `ahe/otel_adapter.py` | `OtelTraceAdapter.convert_trace()` | OTEL trace → NormalizedTrace |
| Step 3: EVAL | `ahe/evaluator.py` | `TraceOutcomeEvaluator.evaluate_batch()` | 任务完成度评估 |
| Step 4: DIAG | `ahe/diagnosis/agent.py` | `DiagnosisAgent.run()` | 根因诊断（LLM 工具链） |
| Step 5: GOV | `ahe/experience_governor.py` | `ExperienceGovernor.get_context()` | 治理检查（双重安全） |
| Step 6: PROPOSE | `ahe/proposer.py` | `AheProposer.generate()` | Proposal 生成（六步流程） |
| Step 7: DECISION | `ahe/decision_policy.py` | `AheDecisionPolicy.evaluate()` | 三阶段决策（RuleGate + GovCheck + LLM） |
| Step 8: APPLY | `apply_writers/skill_writer.py` | `SkillExperienceWriter.apply()` | 写入 evolutions.json（EXISTING format） |
| Step 9: Training | `apply_writers/training_writer.py` | `TrainingCandidateWriter.apply()` | 沉淀已通过 Decision 的 Training Candidate |

### 数据模型对应关系

| 数据模型 | 定义文件 | 使用步骤 | 说明 |
|---------|---------|---------|------|
| `TraceBatch` | `evolve/models.py` | Step 1 (LOAD) | Trace ID 列表描述符 |
| `NormalizedTrace` | `ahe/otel_adapter.py` | Step 2 (CLEAN) | 标准化 trace 数据 |
| `TraceOutcome` | `ahe/models.py` | Step 3 (EVAL) | 任务评估结果 |
| `DiagnosisResult` | `ahe/diagnosis/models.py` | Step 4 (DIAG) | 根因诊断结果 |
| `GovernanceContext` | `ahe/models.py` | Step 5 (GOV) | 治理上下文 |
| `Proposal` | `evolve/models.py` | Step 6 (PROPOSE) | 演进提议（核心契约） |
| `ExperienceOperation` | `evolve/models.py` | Step 6 (PROPOSE) | Experience 操作定义 |
| `DecisionResult` | `evolve/models.py` | Step 7 (DECISION) | 决策结果 |
| `ApplyRecord` | `evolve/models.py` | Step 8 (APPLY) | 写回记录 |

### 关键策略与参数

**分批处理策略**（Step 1 + Step 6）：
```python
# ahe/proposer.py: generate() 方法
batch_size = 10  # 每批最多 10 traces
for i in range(0, len(trace_ids), batch_size):
    batch_trace_ids = trace_ids[i:i+batch_size]
    proposals = await self._process_batch_traces(batch_trace_ids, batch_id)
```

**Proposal 数量限制**（Step 6 + Step 7）：
```python
# evolve/config.yaml
evolve:
  ahe:
    proposer:
      max_proposals_per_batch: 3    # 每批最多 3 Proposal
      max_skill_proposals: 2        # 每批最多 2 Skill Proposal
      max_per_skill_per_batch: 1    # 每个 skill 每批最多 1 experience

    governor:
      max_per_skill: 10             # 每个 skill 最多 10 experiences
      deprecate_threshold_days: 30  # 30 天未使用则可废弃
```

**双重安全检查**（Step 5）：
```python
# ahe/experience_governor.py: get_context() 方法
# Safety Check 1: Is this a builtin/system skill?
if self._is_builtin_skill(skill_name):
    return GovernanceContext(
        can_add=False,
        allowed_operations=[ExperienceOperationType.NOOP],
    )

# Safety Check 2: Does this skill exist in user workspace?
skill_dir = self._skills_dir / skill_name
if not skill_dir.exists() or not skill_dir.is_dir():
    return GovernanceContext(
        can_add=False,
        allowed_operations=[ExperienceOperationType.NOOP],
    )
```

**Experience Classification**（Step 5）：
```python
# ahe/experience_governor.py: _find_replaceable() / _find_protected()

# Replaceable experience（可替换）
replaceable_criteria:
  - usage_stats.times_used = 0    # 从未使用
  - score < 0.6                   # 低质量
  - applied = false               # 未应用

# Protected experience（受保护）
protected_criteria:
  - usage_stats.times_used > 0   # 已被使用
  - score >= 0.7                  # 高质量
  - applied = true                # 已应用
```

**EXISTING Format 写入**（Step 8）：
```python
# apply_writers/skill_writer.py: _build_record() 方法
record = {
    "id": f"exp-{proposal.proposal_id[:8]}-{timestamp}",
    "source": "ahe_evolution",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "context": proposal.root_cause.strip(),
    "change": {                      # ⚠️ nested structure
        "section": "body",
        "action": "append",
        "content": content,
        "target": "body",
    },
    "applied": False,                # ⚠️ boolean flag
    "score": float(proposal.metadata.get("max_score", 0.6)),
    "usage_stats": {                 # ⚠️ nested structure
        "times_presented": 0,
        "times_used": 0,
        "times_positive": 0,
        "times_negative": 0,
    },
    "summary": content[:300],
    "proposal_id": proposal.proposal_id,
    "evidence": [...],
}
```

### 安全机制总结

**三层安全防护**：
1. **ExperienceGovernor 双重检查**（Step 5）：
   - Builtin skill 保护
   - Skill 存在性验证

2. **AheDecisionPolicy GovernanceCheck**（Step 7）：
   - Allowed operations 白名单验证
   - Protected experience 保护

3. **Proposal 数量限制**（Step 6 + Step 7）：
   - 每批最多 3 Proposal
   - 每个 skill 最多 10 experiences

**禁止操作**：
- ❌ 修改 builtin/system skill
- ❌ 创建新 skill（只允许修改已存在的 skill）
- ❌ 替换 protected experience
- ❌ 超过 max_per_skill 限制
- ❌ 超过 proposal 数量上限

### 双轨演进实现

**组件演进轨道**（Step 8）：
- Skill Experience: 写入 `workspace/skills/<name>/evolutions.json`
- Memory Policy: 写入 `workspace/memory/policies/`（后续扩展）

**模型演进轨道**（Step 9）：
- Training Candidate: 写入 `evolution.db` 的 `training_candidates` 表
- 为后续模型自演进工作流提供数据输入
- 不直接在主流程中完成训练

### 并发执行优化

**Pipeline 并发设计**：
```python
# evolve/pipeline.py: run() 方法

# Step 1: 并发执行多个 Generator
tasks = [gen.generate(batch) for gen in self._generators]
results = await asyncio.gather(*tasks)

# Step 2: 并发执行多个 Policy 评估每个 Proposal
async def evaluate_one(prop: Proposal):
    tasks = [policy.evaluate(prop) for policy in self._policies]
    results = await asyncio.gather(*tasks)
    return valid_results

tasks = [evaluate_one(p) for p in proposals]
results = await asyncio.gather(*tasks)

# Step 3: 并发执行多个 Writer
tasks = [writer.apply(prop) for prop in proposals if prop.state == ProposalState.ACTIVE]
results = await asyncio.gather(*tasks)
```

### 审计链完整性

**完整审计链**：
```text
trace_id
  └── Proposal.failure_evidence[].trace_id
      └── DecisionResult.proposal_id
          └── ApplyRecord.proposal_id
              └── stored_object_id (evolutions.json path / training_candidates row)
                  └── Next-run Retrieval
```

**可追溯性保证**：
- 每个 Proposal 包含 failure_evidence（trace_id + span_id）
- 每个 DecisionResult 记录 proposal_id
- 每个 ApplyRecord 记录 proposal_id + stored_object_id
- 所有记录写入 SQLite（`evolution.db`）或文件系统

### Benchmark 验证结果

**最新测试结果**（2026-06-26）：
- 总分：6.00 / 11.00
- ✅ 基本修复能力：unit-converter 成功（1.00分）
- ✅ 过度优化抑制：hash-calculator 正确抑制（1.00分）
- ✅ 经验污染治理：currency-converter 成功治理（1.00分）
- ✅ 边界判断能力：system-permission-check 正确判断（1.00分）
- ⏳ 其他案例：待进一步调试和优化

详见 `benchmark/report/report_20260626_092051.md`

---

## 总结

本文档定义的 PDA-style One-shot AHE 算法已完成轻量级实现，核心特征：

**已实现能力**：
1. ✅ 六步流程完整实现（LOAD→CLEAN→EVAL→DIAG→GOV→PROPOSE）
2. ✅ 双重安全检查（Builtin + Skill Existence）
3. ✅ ExperienceGovernor 治理机制
4. ✅ Proposal 数量限制
5. ✅ EXISTING format 兼容写入
6. ✅ Training Candidate 沉淀（双轨演进）
7. ✅ 并发执行优化
8. ✅ 审计链完整

**第一阶段不做的能力**（依然遵守）：
1. ❌ 不启动完整 Agent 作为 Propose / Decision 执行器
2. ❌ 不重新执行真实用户 trace
3. ❌ 不直接修改 SKILL.md
4. ❌ 不自动 solidify
5. ❌ 不做多轮 AHE outer loop
6. ❌ 不做完整 workspace commit / rollback
7. ❌ 不开启 Memory Policy 自动写回（后续扩展）
8. ❌ 不自动生成训练数据集或触发模型训练
9. ❌ 不做后台无限扫描
10. ❌ 不做高风险现网自动 Apply

**下一步工作**：
- Agent 集成：验证下一轮执行是否检索演进结果
- Benchmark 验证：Before/After 对照实验
- 失败案例分析：csv-row-counter 等未生成 Proposal 的原因排查
- 算法优化：提高 Proposal 生成准确率
- Memory Policy 扩展：实现 Memory 演进轨道



