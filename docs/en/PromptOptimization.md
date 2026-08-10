# Prompt Optimization (RLAF-P)

A runtime prompt optimizer for JiuwenSwarm. It improves a **system prompt** for a
repeatable task through an RL-style feedback loop — **no model weights are trained**.
A Policy (LLM) proposes candidate prompts, an Environment executes them, a Reward
model scores the results, a Drift judge keeps the objective fixed, and a compressed
optimization history steers the next round. It lives beside Symphony's
`experience` subsystem because it is the same feedback-loop shape applied to prompts
instead of skill selections.

- Package: [`jiuwenswarm/symphony/optimization/`](../../jiuwenswarm/symphony/optimization/)
- Extension: [`jiuwenswarm/extensions/optimization/`](../../jiuwenswarm/extensions/optimization/)
- Tool: `optimize_prompt` · Rail: `PromptOptimizerPromptRail`
- Config: `symphony.optimization` in `config.yaml`

---

## Architecture

```
Task ─▶ PromptOptimizer.optimize()
          │
          ├─ PromptPolicy ........... generate N candidate system prompts (LLM)
          │     └─ OptimizationHistory + HistoryCompressor  (textual "policy gradient")
          ├─ PromptEnvironment ...... execute each candidate (LLM / workflow / agent / callable)
          ├─ RewardModel ............ CompositeReward = Σ wᵢ·componentᵢ → scalar + breakdown
          │     ├─ Correctness (LLM judge)  ├─ Latency / TokenUsage / Cost
          │     └─ Completeness / StructuredValidation / Custom
          ├─ DriftJudge ............. deviation(objective, candidate) → reward penalty
          ├─ ConvergenceDetector .... moving average / variance / no-improve-K
          └─ PromptMemory ........... store & retrieve prior optimizations
          ▼
     OptimizationResult (best prompt + full trace) ─▶ PromptMemory
```

Each conceptual component is an **ABC with a swappable default**, built by
`OptimizerRuntimeFactory` from config (the same pattern as
`symphony.build.ScoreBuildRuntimeFactory`). The loop mirrors
`SymphonyScoreBuilder.build` and emits a JSONL run log like Symphony's build log.

| Component | Interface | Default | Reuses |
|---|---|---|---|
| Policy | `PromptPolicy` | `LLMPromptPolicy` | `symphony.llm` client |
| Environment | `PromptEnvironment` | `LLMEnvironment` (+ `WorkflowEnvironment`, `CallableEnvironment`) | `symphony.llm`, token tracker |
| Reward | `RewardModel` / `RewardComponent` | `CompositeReward` + built-ins | `TraceEvaluator` judge idiom |
| Drift | `DriftJudge` | `LLMDriftJudge` | LLM-as-judge |
| Memory | `PromptMemory` | `JsonlPromptMemory` / `ExperienceBankPromptMemory` | `ExperienceBank` + `EmbeddingClient` |
| History | `HistoryCompressor` | LLM buckets | `TraceDistiller` idiom |

---

## Quick start (Python)

```python
import asyncio
from jiuwenswarm.symphony.optimization import optimize_prompt, TaskSpec, TaskCase

task = TaskSpec(
    objective="Summarize a customer support ticket into at most 3 action items.",
    constraints=["Output a markdown bullet list", "At most 3 bullets"],
    cases=[
        TaskCase(input="My invoice is wrong and the app keeps crashing on login.",
                 expected="- Fix invoice\n- Investigate login crash"),
        TaskCase(input="Password reset email never arrives; also dark mode is broken.",
                 expected="- Fix password reset email\n- Fix dark mode", hidden=True),
    ],
)

result = asyncio.run(optimize_prompt(task))
print(result.best_score, result.best_prompt)
```

`optimize_prompt` resolves `symphony.optimization` config, builds defaults, and runs
the loop. Inject any collaborator to override it:

```python
result = await optimize_prompt(task, environment=my_workflow_env, reward_model=my_reward)
```

## Inside a workflow (tool + rail)

When `symphony.optimization.enabled: true`, the team **leader** gets:
- the `optimize_prompt` **tool** (`PromptOptimizerToolkit`), and
- the `PromptOptimizerPromptRail`, which injects guidance on when to call it.

The agent calls `optimize_prompt(objective=..., cases=[...])`; the tool dispatches to
the `optimizer.optimize` extension RPC and returns the best prompt + reward. This is
the requested flow — *Task → optimizer → candidates → parallel execution → reward →
prompt update → best prompt → continue workflow* — as native JiuwenSwarm pieces.

RPC methods: `optimizer.optimize`, `optimizer.status`, `optimizer.best_prompt`.

---

## Configuration (`symphony.optimization`)

```yaml
symphony:
  optimization:
    enabled: false
    candidate_prompts: 5          # candidates generated per iteration
    max_iterations: 6
    parallel_execution: true      # execute candidates concurrently
    convergence_threshold: 0.01   # min reward gain counted as improvement
    convergence_window: 3         # stop after K iterations with no improvement
    drift_penalty: 0.5            # weight on semantic deviation from the objective
    min_correctness: 0.5          # hard gate against reward hacking
    memory_enabled: true
    memory_dir: ""                # default <workspace>/symphony/optimization/prompt_kb
    policy_temperature: 0.9
    reward_weights:               # component weights (already-bounded metrics)
      correctness: 1.0
      completeness: 0.3
      latency: 0.1
      token_usage: 0.1
      cost: 0.0
      structured_validation: 0.0
    models:
      policy_model: ""            # "" => JiuwenSwarm default model
      environment_model: ""
      judge_model: ""             # correctness + drift judges
    embedding:                    # enables FAISS prompt memory when set
      base_url: ""
      api_key: ""
      model: ""
      model_name: ""
      dimension:
```

A component with weight `0` is skipped entirely. If `embedding` is unset, memory
falls back to a dependency-light JSONL store with lexical retrieval.

---

## Extension points

Swap any implementation without touching the loop — pass it to `optimize_prompt`
or override a method on `OptimizerRuntimeFactory`.

**Custom reward metric:**

```python
from jiuwenswarm.symphony.optimization.reward import RewardComponent, CompositeReward, CorrectnessReward

class KeywordReward(RewardComponent):
    name = "keyword"
    def __init__(self, keyword): self._kw = keyword
    async def score(self, execution, task):
        outs = execution.visible_results
        return sum(self._kw in r.output for r in outs) / max(1, len(outs))

reward = CompositeReward(
    [CorrectnessReward(judge_client), KeywordReward("action")],
    {"correctness": 1.0, "keyword": 0.5},
    min_correctness=0.5, drift_penalty=0.5,
)
result = await optimize_prompt(task, reward_model=reward)
```

**Custom environment** — implement `PromptEnvironment.execute(candidate, task)`, or
wrap any coroutine with `WorkflowEnvironment` / `CallableEnvironment` to score
candidates against a real JiuwenSwarm workflow, agent, plugin, or benchmark.

**Custom policy / drift judge / memory** — subclass `PromptPolicy`, `DriftJudge`, or
`PromptMemory` respectively.

---

## Anti-reward-hacking

The optimizer will not maximize one metric while destroying quality:

- **Min-correctness gate** — reward is capped at correctness when correctness is below
  `min_correctness`, so latency/token gains can't buy a bad answer a high score.
- **Hidden validation cases** (`TaskCase(hidden=True)`) — visible-vs-hidden correctness
  gaps are detected and penalized as overfitting.
- **Drift penalty** — an LLM judge scores semantic deviation from the objective; large
  deviations subtract from the reward.
- **Bounded, composite metrics** — built-in components are already in `[0, 1]`, so no
  single metric runs away; enable `normalize=True` only for custom unbounded metrics.

---

## Best practices

- Provide 3–6 evaluation cases; mark 1–2 as `hidden`.
- Keep `drift_penalty ≥ 0.3` and always keep a correctness weight.
- Start with `candidate_prompts=5`, `max_iterations=6`; raise only if reward is still
  climbing at the last iteration.
- Review the JSONL run log (`optimizer.status`) before promoting a prompt — it records
  candidate prompts, outputs, reward breakdowns, drift, and convergence metrics.

## Example

A runnable, network-free walkthrough that shows reward climbing across iterations is
in [`examples/optimize_summarizer.py`](examples/optimize_summarizer.py).
