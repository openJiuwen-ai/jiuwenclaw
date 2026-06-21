# PDA-style One-shot AHE：裁剪版工程化算法

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
Algorithm: PDA-style One-shot AHE

Require:
  seed harness H0,
  trace set D,
  validation policy Ω,
  experience governance policy Γ
```shell
1:  Tbase ← LOAD_TRACES(D)
    # 从 traces.db中读取历史执行轨迹。
    # 实现参考 jiuwenswarm\jiuwenswarm\evolve\models.py的TraceBatch类
    # 以及 jiuwenswarm\jiuwenswarm\evolve\cli.py中trace的载入逻辑。

2:  Tclean ← CLEAN(Tbase)
    # 对 trace 进行标准化、脱敏、去噪和规范化，形成统一表示。
    # 该步骤由 TraceNormalizer 完成。
    # 实现可参考 jiuwenswarm/evolve/clean_trace.md 中的描述。

3:  TaskEval ← TASK_EVALUATE(Tclean) 
    # 只基于 root span 中的用户输入和 Agent 最终输出， 
    # 判断任务完成度，输出 pass / fail / uncertain。 
    # 该评估器只用于筛选值得进一步分析的 trace，
    # 不负责根因诊断。

4. Diagnosis ← DIAGNOSE(Tclean, TaskEval) 
    # 对 fail / uncertain / 高价值 trace 进行根因诊断。 
    # 第一版可使用 llm_diagnosis 实现。 
    # 诊断目标是判断问题更可能来自哪个 Harness 组件， 
    # 例如 Skill、Memory、Tool、Prompt、Model 或 Environment。 
    # 当前阶段建议优先支持 Skill 相关诊断。
    # 虽然步骤4独立，但是要放入PDA框架仍然需要把步骤2、3、4放入到步骤5中作为步骤5的预先执行步骤的输入部分

5:  P ← PROPOSE(Tclean, TaskEval, Diagnosis)
    # 对应 PDA 的 Propose 阶段。 
    # 第一版可使用 llm_proposer 实现， 
    # 生成Proposal，数据结构已经定义在 jiuwenswarm/evolve/proposal_generators/llm_proposer.py。 
    # 
    # Proposal 至少包含： 
        # - target_type 
        # - failure_evidence 
        # - root_cause 
        # - targeted_fix 
        # - predicted_impact 
        # - risk 
    # 
    # 第一版只允许生成 Skill Experience Proposal。 
    # 数量控制： 
        # - 每批最多生成 3 个 Proposal； 
        # - Skill Experience Proposal 最多 2 条； 
        # - 每个 skill 每次最多新增 1 条 experience； 
        # - 每条 experience 必须包含可追溯 evidence。

5:  A ← DECISION(P, H0, Tclean, TaskEval, Diagnosis, Ω, Γ)
    # 对应 PDA 的 Decision 阶段，生成 DecisionResult，
    # 基于llm_decision形式实现，根据当前提案以及对当前组件实施的经验判定是否采纳
    # Decision 不只依赖 LLM，而是采用 RuleGate + LLMDecision 两阶段： 
    # 
    # RuleGate 负责硬约束检查： 
        # - Proposal 字段是否完整； 
        # - 是否包含 failure_evidence； 
        # - target_type 是否在允许范围内； 
        # - targeted_fix 是否为空； 
        # - 是否与已有 experience 重复； 
        # - 是否超过每批 proposal 数量上限； 
        # - 是否超过每个 skill 的 experience 数量上限； 
        # - 是否存在高风险或越权修改； 
        # - 是否违反 candidate-only 策略。 
    # 
    # LLMDecision 负责语义判断： 
        # - Proposal 是否与 Diagnosis 一致； 
        # - root_cause 是否合理； 
        # - targeted_fix 是否能解决对应问题； 
        # - predicted_impact 是否可信； 
        # - risk 是否可接受。 
    # 
    # Decision 输出 DecisionResult，数据结构已经定义在 D:\github\jiuwenswarm\jiuwenswarm\evolve\models.py 

6:  Hfinal ← APPLY_ACCEPTED(P, A)
    # 对应 PDA 的 Apply 阶段。
    # 第一版只将 accepted / candidate 的 Skill Experience Proposal
    # 写入现有 evolutions.json 或 experience store。
    #
    # 写入要求：
    # - 不直接修改 SKILL.md；
    # - 不自动 solidify；
    # - 默认 state = candidate；
    # - 记录 proposal_id、decision_id、trajectory_id、evidence_refs；
    # - 记录 TTL、hit_count、last_used_at 等治理字段。
    #
    # Apply 阶段生成 ApplyRecord，
    # 保证从 trace evidence 到最终写回结果可审计、可回放。
```

## 详细实现指导

###  1 Tbase ← LOAD_TRACES(D)
这部分的实现参考D:\github\jiuwenswarm\jiuwenswarm\evolve\models.py下面487行的TraceBatch类以及D:\github\jiuwenswarm\jiuwenswarm\evolve\cli.py下面第111和112行载入trace的方案执行。


### 2 Tclean ← CLEAN(Tbase)
具体实现参考D:\github\jiuwenswarm\jiuwenswarm\evolve\clean_trace.md 文档中的描述

### 3 Ebase ← EVALUATE(Tclean)
agentic-harness-engineering方案（简称AHE）中有一个算法，即需要在harbor的执行结果文件中统计任务的执行结果，使用compute_statics评估分数的实现：
但是我们的 trace 来自用户实际使用，不是 benchmark 的受控执行。这意味着：我们需要一种方法来从 trace 内容推断任务是否成功。
我们要设计一个轻量的 TaskCompletion Evaluator ，它阅读 trace 后判断任务是否真正成功。这个评估器输出的数据结构定义如下,其中task_name
```python
  class TraceOutcome(BaseModel):
      """一个 trace 的任务结果判定。"""
      trace_id: str
      task_name: str | None = None        # 从 trace 内容推断的任务描述
      outcome: "pass | fail | uncertain",
      score: float | 0.0,
      confidence: float | 0.0,
      reason: str | "一句话概括判定原因",
      key_evidence: str | "引用或概括最关键的用户要求与输出证据",
      missing_requirements: str | ["未满足的关键要求；如果没有则为空数组"],
      needs_external_verification: bool | "是否需要额外验证"
```
  
TaskCompletion Evaluator实现约束如下：
1. 从root span 中的用户输入和 Agent 最终输出，判断 Agent 是否完成了用户任务。
2. 该评估器只判断任务结果，不负责根因诊断，不检查中间步骤，不强行验证外部事实。

    
```python
system_prompt = """
# 角色

你是一名智能体任务完成度评估专家，负责判断 Agent 的最终输出是否完成了用户任务。

# 评估对象

你只允许基于以下两项信息进行判断：

1. 用户输入
2. Agent 最终输出

你不负责分析中间执行步骤，也不负责定位失败根因。如果仅凭用户输入和最终输出无法判断，应返回 uncertain。

# 评估目标

通过比较[用户输入]和[Agent最终输出]，判断用户的核心任务目标是否被满足。

# 评估步骤

1. 识别用户输入中的核心目标、明确约束和必须交付的产物。
2. 判断 Agent 最终输出是否覆盖这些核心目标和明确约束。
3. 判断输出是否存在导致任务失败的严重问题，例如偏题、关键内容缺失、拒答不当、无意义礼貌回复、明显自相矛盾。
4. 如果任务是否完成依赖外部事实、附件、工具执行结果、真实系统状态或中间步骤，而这些信息未提供，则返回 uncertain。
5. 不要因为轻微措辞问题、格式小瑕疵或非关键遗漏直接判 fail；只有核心目标未完成时才判 fail。

# 判定标准

- pass：
  用户核心目标已经完成；主要要求被满足；输出可作为最终结果使用。允许存在轻微表达瑕疵或非关键遗漏。

- fail：
  用户核心目标未完成；输出明显偏题；遗漏关键交付物；与用户明确要求冲突；拒答不当；只提供空泛说明或礼貌性回复；输出明显不可用。

- uncertain：
  仅凭用户输入和最终输出无法可靠判断。常见情况包括：
  - 任务依赖外部事实或实时信息，但没有参考依据；
  - 任务依赖附件、文件、工具执行结果或系统状态；
  - 用户目标模糊，无法确定成功标准；
  - 输出是过程说明或中间态，无法确认最终产物是否完成；
  - 需要根因诊断或步骤证据才能判断。

# 输出格式

请严格输出 JSON，不要输出 JSON 以外的任何内容：

{
  "outcome": "pass | fail | uncertain",
  "score": 0.0,
  "confidence": 0.0,
  "reason": "一句话概括判定原因",
  "key_evidence": "引用或概括最关键的用户要求与输出证据",
  "missing_requirements": ["未满足的关键要求；如果没有则为空数组"],
  "needs_external_verification": false
}

# 字段说明

- outcome：最终判定，只能是 pass、fail、uncertain。
- score：任务完成度分数。pass 通常为 0.8-1.0；fail 通常为 0.0-0.3；uncertain 通常为 0.4-0.6。
- confidence：你对 outcome 判定的置信度，范围 0.0-1.0。
- reason：高度概括的判定原因。
- key_evidence：支持判定的关键证据。
- missing_requirements：如果 fail，列出关键缺失；如果 pass 或 uncertain，可为空数组。
- needs_external_verification：如果需要外部事实、工具结果、附件或系统状态才能判断，则为 true。

# 输入

- [用户输入]&#58; {{input}}

- [Agent最终输出]&#58; {{actual_output}}

# 输出语言

必须使用中文输出。
"""
```

有了上述评估器，用户 trace 到 AHE 等价的 compute_stats 输出方案设计如下：

新增模块：jiuwenswarm/evolve/adb/stats.py
```python
  class TraceBatchStats:
      """从 TraceBatch 计算等价于 AHE compute_stats 的统计数据。

      输入: TraceBatch → TraceNormalizer → TraceOutcomeEvaluator
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

          # Step 1: 归一化 trace
          normalizer = TraceNormalizer(trace_reader)
          normalized_traces = normalizer.normalize_batch(batch)

          # Step 2: 判定每个 trace 的 outcome
          evaluator = TraceOutcomeEvaluator()
          task_results: dict[str, str] = {}
          for nt in normalized_traces:
              # 从标准化处理的trace红读取input和output进行评估
              outcome = evaluator.evaluate(nt.input, nt.output)
              task_results[nt.trace_id] = outcome
```



