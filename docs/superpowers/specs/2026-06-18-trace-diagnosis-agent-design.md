# Trace Diagnosis Agent — 设计文档

> **日期**: 2026-06-18
> **分支**: dolores-trace
> **状态**: Draft

## 1. 摘要

在 `jiuwenswarm/evolve` 模块内新增一个 **Trace Diagnosis Agent**（以下简称 `DiagnosisAgent`），具备自主读取 trace 数据和查询演进记录的能力，用于对 OTEL trace 进行诊断分析。

该 Agent 需支持两种调用模式：
- **Pipeline 模式**：作为 `ProposalGenerator` 注册到 evolve pipeline，在 Proposal 生成阶段被调用，输出结构化 Proposal
- **独立模式**：通过 CLI 或代码直接调用，输出诊断报告，不参与 Proposal 流程

## 2. 设计参考

本设计参考了 `agentic-harness-engineering` 项目中的 `agent-debugger-cli` 实现，借鉴其核心模式：

| 原始设计 | 本设计适配 |
|---------|----------|
| NexAU Agent（`nexau.Agent`）驱动的 ReAct loop | 自实现轻量 ReAct loop，不依赖 NexAU |
| NexAU 工具绑定系统（YAML `.tool.yaml`） | Python 函数式 Tool 定义，直接注册 |
| `complete_task` 停止工具 | `submit_result` 停止工具 |
| `ask` / `check` 双模式 | `diagnose` / `propose` 双模式 |
| Langfuse / OpenAI messages / InMemoryTracer 三种 trace 格式 | OTEL spans（从 `traces.db` 读取）+ evolution.db 查询 |
| NexAU ModelConfig + OpenAI SDK | openjiuwen `Model`（复用 jiuwenswarm 现有 LLM 基础设施） |
| LongToolOutputMiddleware / ContextCompactionMiddleware | 内置输出截断 + 无中间件（轻量设计） |

### 2.1 保留的核心模式

从 `agent-debugger-cli` 保留以下关键设计：

1. **五阶段工作流**：Skim → Locate → Read → Cross-trace diff → Finalize
2. **迭代预算硬限制**：最多 20 次工具调用
3. **结构化 JSON 输出契约**：Agent 必须调用停止工具提交 JSON 结果
4. **工具集最小化**：只读工具 + 停止工具，无写入能力
5. **输出验证 + 重试**：Runner 验证 JSON schema，失败时追加修正提示重试一次
6. **分页读取**：大 trace 支持 offset/limit 分页，避免一次性全读

### 2.2 关键差异

| 方面 | agent-debugger-cli | DiagnosisAgent |
|------|--------------------|----------------|
| 运行环境 | NexAU 框架 + 独立 venv | jiuwenswarm 进程内，evolve 模块自治 |
| 数据来源 | 文件系统 JSON | SQLite (traces.db + evolution.db) |
| LLM 后端 | OpenAI SDK (via NexAU) | openjiuwen `Model` |
| 额外查询能力 | 无 | 可查询已有的 Proposal/Decision/Apply 记录 |
| 输出格式 | ask → text / check → QC issues | diagnose → 诊断报告 / propose → Proposal[] |

## 3. 架构

### 3.1 模块结构

```
jiuwenswarm/evolve/
  diagnosis/
    __init__.py          # 导出 DiagnosisAgent, DiagnosisResult, run_diagnosis
    agent.py             # DiagnosisAgent — ReAct loop + Runner
    tools.py             # 只读工具集定义
    prompts.py           # system prompt + 输出 schema 定义
    models.py            # DiagnosisResult, DiagnosisIssue 数据模型
```

### 3.2 核心组件

#### DiagnosisAgent（`agent.py`）

```python
class DiagnosisAgent:
    """轻量 ReAct Agent，用于 trace 诊断分析。

    不依赖 DeepAgent 或 NexAU，使用 openjiuwen Model 调用 LLM，
    通过内置 Tool 集完成 trace 数据读取和查询。
    """

    def __init__(
        self,
        store: EvolutionStore,       # 数据源（traces.db + evolution.db）
        model: Model | None = None,  # openjiuwen Model（None 时从 config 创建）
        max_iterations: int = 20,    # 工具调用硬预算
        temperature: float = 0.4,    # LLM temperature
    ) -> None:
        ...

    async def run(
        self,
        trace_ids: list[str],
        mode: str = "diagnose",       # "diagnose" | "propose"
        question: str | None = None,  # diagnose 模式的自定义问题
    ) -> DiagnosisResult:
        """执行 ReAct 循环，返回诊断结果。"""
        ...

    # ProposalGenerator 接口适配
    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        """ProposalGenerator 接口 — propose 模式输出 Proposal 列表。"""
        ...
```

#### Runner 模式（`agent.py` 内嵌）

`DiagnosisAgent.run()` 内部实现 ReAct 循环：

```
1. 构建 system prompt（注入 trace_ids、mode、工具说明）
2. 构建初始 user message（列出 trace_ids + 问题/任务）
3. ReAct 循环（最多 max_iterations 次）：
   a. 发送 messages 给 LLM
   b. 解析 LLM 响应：工具调用 or 文本
   c. 如果是 submit_result 工具 → 停止循环，提取 JSON payload
   d. 如果是其他工具 → 执行工具，追加结果到 messages
   e. 如果是纯文本 → 追加到 messages，继续
4. 预算耗尽 → 返回 DiagnosisResult(budget_exceeded=True)
5. 验证 JSON payload schema → 失败时追加修正提示重试一次
6. 返回 DiagnosisResult
```

#### 只读工具集（`tools.py`）

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `read_spans` | 读取指定 trace_id 的 OTEL spans（支持分页） | `trace_id`, `offset`, `limit`, `name_filter` |
| `search_spans` | 在 spans 中按 regex 搜索关键词 | `trace_id`, `pattern`, `max_results` |
| `list_traces` | 列出最近 N 条 trace_id | `limit`, `since` |
| `query_evolve_records` | 查询指定 trace_id 的 Proposal/Decision/Apply 链 | `trace_id` |
| `query_proposals` | 查询指定 batch_id 的所有 Proposal | `batch_id` |
| `read_file` | 读取本地文件（trace JSON、evolve 输出文件等） | `path`, `offset`, `limit` |
| `submit_result` | 停止工具 — 提交最终 JSON 结果 | `result` (JSON string) |

**设计说明**：

- `read_spans` 和 `search_spans` 对应原 `agent-debugger-cli` 的 `read_file` + `search_file_content`，但直接从 SQLite 读取而非文件系统。这解决了"trace 很大，一次性读不完"的问题——Agent 可以先 `list_traces` 获取概览，再 `read_spans(trace_id, offset=0, limit=20)` 分页读取，或 `search_spans(trace_id, pattern="error")` 精准定位。
- `read_file` 保留，用于读取被导出到文件系统的 trace JSON 或其他辅助文件。
- `submit_result` 对应原 `complete_task`，作为停止信号。

#### 数据模型（`models.py`）

```python
@dataclass
class DiagnosisIssue:
    """单个诊断发现。"""
    issue_type: str          # "工具错误" | "幻觉" | "循环" | "不合规" | "截断"
    summary: str             # 一行摘要
    evidence: str            # 引用原文
    trace_id: str            # 所属 trace
    span_index: int          # 0-based span 序号（对应 message_index）
    root_cause: str | None   # 根因分析（propose 模式必填）
    suggested_fix: str | None # 建议修复（propose 模式必填）

@dataclass
class DiagnosisResult:
    """Agent 诊断结果。"""
    mode: str                # "diagnose" | "propose"
    issues: list[DiagnosisIssue]
    response: str            # 整体诊断摘要
    iterations: int          # 实际使用的迭代次数
    budget_exceeded: bool    # 是否预算耗尽
    proposals: list[Proposal] | None  # propose 模式下的 Proposal 列表
```

#### System Prompt（`prompts.py`）

核心结构参考 `agent-debugger-cli` 的 `system_prompt.md`，适配 jiuwenswarm 数据源：

```
你是 trace_diagnosis_agent，专门分析 OTEL trace 数据和演进记录的诊断专家。

## 数据源
你有两个数据库：
- traces.db: OTEL spans（trace_id → spans 列表）
- evolution.db: Proposal/Decision/Apply 记录

## 工具
read_spans, search_spans, list_traces, query_evolve_records,
query_proposals, read_file, submit_result

## 迭代预算（硬限制）
最多 20 次工具调用。第 20 次必须是 submit_result。

## 工作流
1. Skim (≈1-3): list_traces 概览 → read_spans(trace_id, limit=10) 粗看结构
2. Locate (≈4-10): search_spans 搜索错误关键词、工具名、异常事件
3. Read (≈11-15): read_spans(trace_id, offset=X, limit=Y) 精读关键 span 上下文
4. Cross-trace (≈16-18): 多 trace 比对
5. Finalize (≤20): submit_result 提交结果

## 输出契约

diagnose 模式:
{"mode": "diagnose", "issues": [...], "response": "..."}

propose 模式:
{"mode": "propose", "proposals": [...], "response": "..."}

每个 issue:
{"issue_type": "工具错误|幻觉|循环|不合规|截断",
 "summary": "...",
 "evidence": "...",
 "trace_id": "...",
 "span_index": <int>,
 "root_cause": "...",
 "suggested_fix": "..."}
```

### 3.3 ReAct Loop 实现

```python
async def _react_loop(self, messages: list) -> DiagnosisResult:
    """核心 ReAct 循环。"""
    for iteration in range(self._max_iterations):
        # 1. 调用 LLM
        response = await self._model.invoke(messages=messages)
        content = response.content

        # 2. 解析工具调用
        tool_calls = self._parse_tool_calls(content)

        if not tool_calls:
            # 纯文本响应 → 追加到 messages，继续
            messages.append({"role": "assistant", "content": content})
            continue

        # 3. 执行工具
        tool_results = []
        for tc in tool_calls:
            if tc.name == "submit_result":
                # 停止信号 → 解析结果
                return self._finalize(tc.arguments["result"], iteration + 1)

            result = self._execute_tool(tc.name, tc.arguments)
            tool_results.append({"tool_name": tc.name, "result": result})

        # 4. 追加工具调用和结果到 messages
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "tool", "results": tool_results})

    # 预算耗尽
    return DiagnosisResult(mode=..., budget_exceeded=True, iterations=self._max_iterations)
```

**LLM 调用方式**：使用 openjiuwen `Model.invoke()`，复用 `LLMProposer._call_llm()` 中已有的 LLM 初始化模式。不引入 NexAU 或 OpenAI SDK。

消息格式使用 openjiuwen 的 `SystemMessage` / `UserMessage` 对象（与 `LLMProposer` 一致）。工具调用结果以 `UserMessage` 形式追加到 messages 列表中。

**工具调用解析**：LLM 响应中的工具调用以 JSON 格式表达（`{"tool_name": "...", "arguments": {...}}`），通过正则或 JSON 解析提取。这与 openjiuwen `Model` 的原生 tool_call 格式不同，但保持简洁——diagnosis Agent 不需要 openjiuwen 的完整 function-calling 协议。

解析策略：
1. 尝试整个响应做 `json.loads()` — 如果是完整 JSON 对象，直接提取
2. 正则搜索 `{"tool_name":\s*"(\w+)",\s*"arguments":\s*\{[^}]*\}}` — 提取所有工具调用
3. 如果包含 `submit_result` → 立即停止循环
4. 如果包含其他工具调用 → 执行并追加结果
5. 如果无工具调用 → 视为纯文本推理步骤，继续循环

### 3.3b 上下文管理（双保险策略）

ReAct loop 的上下文会随着迭代累积增长。20 次迭代中，如果一半调用 `read_spans` 等返回大量数据的工具，工具结果可能累积 50k-100k chars，加上 assistant 消息和 system prompt，总量可能超出 LLM 的 context window（如 GPT-4 是 128k tokens）。

采用双保险策略：

#### 第一层：工具输出截断（基础保障）

每次工具返回结果后，如果结果超过 `max_tool_output_chars`（默认 10000 chars），自动截断：

```
原始 tool result (800 lines / 50000 chars):
  前 50 行（开头，包含 span 概要 / 结构信息）
  [...truncated: 700 lines omitted, total_spans=120.
   Use read_spans(trace_id, offset=50, limit=30) to read more...]
  后 30 行（结尾，包含 status/error 信息）
```

截断参数：`max_tool_output_chars = 10000`，`head_lines = 50`，`tail_lines = 30`。

**关键设计**：截断信息中提示 Agent 可以用分页参数重新读取被截断的部分。截断不是丢弃信息，而是引导 Agent 用更精准的 `offset/limit` 或 `search_spans` 重新读取需要的部分。这与 Agent 的五阶段工作流（Skim → Locate → Read）天然配合——先粗看，再精读。

#### 第二层：上下文压缩（阈值触发）

在每次 LLM 调用前，估算当前 messages 的总 token 数。当达到 `max_context_tokens`（默认 200k）的 75%（150k tokens）时，触发一次压缩：

1. **保留区域**：system prompt + 最近 `keep_iterations`（默认 3）次完整迭代
2. **压缩区域**：更早的迭代 → 合并为一段摘要
3. **摘要生成**：调用一次额外的 LLM，prompt 为 "以下是一个 trace 诊断 Agent 的前 N 次迭代记录。请生成一段简明摘要（<2000 chars），保留关键发现（issue_type、trace_id、span_index、evidence 引用）但省略详细的 tool result 内容。"
4. **替换**：用摘要 UserMessage 替换被压缩的迭代历史

```
压缩前 messages (180k tokens):
  [system_prompt: 3k]                        → 保留
  [iter 1-5: 30k tokens, tool calls/results] → 压缩为摘要
  [iter 6-10: 35k tokens, tool calls/results] → 压缩为摘要
  [iter 11-15: 40k tokens]                   → 压缩为摘要
  [iter 16-18: 30k tokens]                   → 保留（最近 3 次迭代）
  [iter 19: 待调用 LLM]                       → 保留

压缩后 messages (60k tokens):
  [system_prompt: 3k]                          → 保留
  [摘要 UserMessage: "迭代1-15发现3个工具错误(span#7,#42,#89)
    和1个循环(span#110)。已定位到trace_id=abc123中的bash
    命令失败和LLM幻觉...": 2k]                 → 替换原 iter 1-15
  [iter 16-18: 30k tokens]                     → 保留
  [iter 19: 待调用 LLM]                         → 保留
```

压缩成本：一次额外 LLM 调用（约 2000-3000 tokens），换来从 180k → 60k 的上下文缩减。

#### Token 估算方法

使用字符数粗估：`estimated_tokens = total_chars / 4`（英文约 4 chars/token，中文约 2 chars/token，取中间值）。不引入 tiktoken 等外部库。

对于 `max_context_tokens` 的配置值，从 `evolve.llm` 或 Agent 初始化参数中读取，默认 200000。

#### 与 agent-debugger-cli 的对应

| agent-debugger-cli | DiagnosisAgent |
|--------------------|----------------|
| `LongToolOutputMiddleware` (max_output_chars: 10000) | `_truncate_tool_output()` 内置函数 |
| `ContextCompactionMiddleware` (threshold: 0.75, strategy: tool_result_compaction) | `_compact_context()` 内置函数 |
| NexAU Middleware 框架（import path + params） | 直接在 ReAct loop 中调用，无 Middleware 抽象 |

### 3.4 两种调用模式

#### Pipeline 模式（作为 ProposalGenerator）

```python
# 注册到 registry
@proposal_generators.register("diagnosis_agent")
class DiagnosisProposer(ProposalGenerator):
    """Pipeline ProposalGenerator — 使用 DiagnosisAgent 生成 Proposals。"""

    def __init__(self, trace_reader=None, store=None):
        super().__init__(name="diagnosis_agent", trace_reader=trace_reader)
        self._agent = DiagnosisAgent(store=store)

    async def generate(self, batch: TraceBatch) -> list[Proposal]:
        result = await self._agent.run(
            trace_ids=batch.trace_ids,
            mode="propose",
        )
        return result.proposals or []
```

在 `config.yaml` 中启用：
```yaml
evolve:
  pipeline:
    proposal_generators:
      - diagnosis_agent    # 新增
      - llm_proposer       # 保留，作为备选
```

#### 独立模式（CLI / 代码调用）

```python
# 代码调用
result = await DiagnosisAgent(store=store).run(
    trace_ids=["abc123", "def456"],
    mode="diagnose",
    question="为什么这些 trace 中工具调用失败率这么高？",
)

# CLI 调用（扩展 evolve CLI）
jiuwenswarm-evolve diagnose --traces abc123,def456 --question "..."
jiuwenswarm-evolve diagnose --latest 10
jiuwenswarm-evolve diagnose --since "2026-06-10T00:00:00"
```

### 3.5 数据流

```
Pipeline 模式:
  TraceBatch.trace_ids
    → DiagnosisAgent.run(trace_ids, mode="propose")
      → ReAct loop (LLM + Tools)
        → Tools 查询 traces.db + evolution.db
        → LLM 分析 → submit_result
      → DiagnosisResult.proposals
    → Proposal[] → Decision → Apply

独立模式:
  CLI args / API args
    → DiagnosisAgent.run(trace_ids, mode="diagnose", question=...)
      → ReAct loop (LLM + Tools)
      → DiagnosisResult (issues + response)
    → 输出到终端 / 文件
```

## 4. 关键设计决策

### 4.1 为什么自实现 ReAct loop 而不是用 NexAU / DeepAgent

| 因素 | 自实现 | NexAU | DeepAgent |
|------|--------|-------|-----------|
| 依赖 | 仅 openjiuwen Model | nexau 库 | openjiuwen harness 全套 |
| 初始化 | 3 行代码 | AgentConfig YAML + env 变量注入 | AgentCard + Workspace + Rails + SessionManager |
| 进程内运行 | ✅ | 需 venv + sys.path hack | 需 AgentServer 上下文 |
| 工具定义 | Python 函数 | YAML binding + import path | AgentCard + AbilityManager |
| 代码量 | ~200 行 | ~0 行（框架提供） | ~0 行（框架提供） |
| evolve 模块自治 | ✅ | ❌（需要 AHE 工具包） | ❌（需要主 Agent 上下文） |

结论：diagnosis Agent 是一个轻量诊断工具，不应引入重基础设施依赖。自实现 ReAct loop 约 200 行代码，换来完全自治和灵活的工具定义。

### 4.2 为什么用 openjiuwen Model 而不是 OpenAI SDK

- openjiuwen Model 已在 `LLMProposer._call_llm()` 中使用，复用现有模式
- `get_default_models()` 自动读取 `.env` 和 `config.yaml` 的 LLM 配置
- 不需要额外配置 LLM API key / base URL
- 诊断 Agent 的 LLM 配置可通过 `evolve.llm` 独立覆盖（config.yaml 中 `evolve.llm.model_name`）

### 4.3 为什么保留 `read_file` 工具

用户明确要求文件读取能力，原因：trace 数据可能很大，Agent 需要自主决定"读哪些 trace 的哪些部分"。除了 SQLite 查询，`read_file` 允许 Agent 读取：
- 被导出到文件系统的 trace JSON 文件
- evolve 的 JSON 输出文件（`evolutions.json`、`policies/` 目录等）
- 其他诊断辅助文件

### 4.4 工具调用格式选择

LLM 的工具调用有两种表达方式：

1. **OpenAI function-calling**（原生 tool_call）— 需要 `Model` 支持 function-calling 协议
2. **JSON 内嵌**（LLM 在文本中输出 `{"tool_name": "...", "arguments": {...}}`）— 任何 LLM 都支持

**选择方案 2（JSON 内嵌）**：
- openjiuwen `Model.invoke()` 目前不支持 function-calling 协议（`LLMProposer` 也是纯文本调用）
- JSON 内嵌格式更简单，不依赖特定 LLM provider
- 用正则 `{"tool_name":\s*"(\w+)",\s*"arguments":\s*\{...\}}` 提取即可
- 缺点是 LLM 可能不严格遵守格式，但 prompt 中明确约束 + 解析 fallback 可应对

**未来升级路径**：当 openjiuwen `Model` 支持 function-calling 时，可无缝切换到原生协议。

## 5. 输出格式

### 5.1 diagnose 模式

```json
{
  "mode": "diagnose",
  "issues": [
    {
      "issue_type": "工具错误",
      "summary": "bash 工具执行命令失败：command not found",
      "evidence": "span #7: name='gen_ai.tool.execute: bash', attributes.error='command not found'",
      "trace_id": "abc123",
      "span_index": 7,
      "root_cause": "Skill 中未指定完整路径，导致 shell 环境找不到命令",
      "suggested_fix": "在 Skill experience 中补充命令完整路径和前置依赖说明"
    }
  ],
  "response": "3 条 trace 中共发现 2 个工具错误和 1 个循环问题。主要根因是 Skill 缺少执行约束。"
}
```

### 5.2 propose 模式

```json
{
  "mode": "propose",
  "proposals": [
    {
      "target_id": "skill-bash-tool",
      "target_type": "skill",
      "proposal_type": "add_skill_experience",
      "failure_evidence": [
        {"trace_id": "abc123", "span_id": "span-07", "description": "bash: command not found"}
      ],
      "root_cause": "Agent 未知道命令的完整路径",
      "targeted_fix": {
        "action": "add_knowledge",
        "suggestion": "bash 工具执行前应先检查命令可用性；需要完整路径 /usr/bin/python3"
      },
      "predicted_impact": "减少工具调用失败率，提升任务完成率",
      "risk": "如果路径在不同环境不同，可能仍需调整"
    }
  ],
  "response": "从 3 条 trace 中生成 2 个 Skill Proposal 和 1 个 Training Candidate。"
}
```

propose 模式的 `proposals` 数组中的每个元素直接对应 `jiuwenswarm.evolve.models.Proposal` 的字段，由 `DiagnosisProposer` 转换为 `Proposal` 对象。

### 5.3 budget_exceeded 降级

当 Agent 耗尽迭代预算时，返回最后一次 LLM 文本作为 `response`，`issues` 为空，`budget_exceeded=True`。

## 6. 工具详细设计

### 6.1 read_spans

```python
def read_spans(
    trace_id: str,
    offset: int = 0,        # 跳过前 N 个 span
    limit: int = 50,        # 最多返回 N 个 span
    name_filter: str = "",  # 按 span name 过滤（regex）
) -> dict:
    """从 traces.db 读取 OTEL spans。

    返回:
    {
        "trace_id": "...",
        "total_spans": 120,
        "offset": 0,
        "limit": 50,
        "returned": 50,
        "spans": [{...}, ...]  # 每个 span 包含 name, span_id, attributes, events, status
    }
    """
```

关键设计：返回 `total_spans` 让 Agent 知道总大小，从而决定分页策略。

### 6.2 search_spans

```python
def search_spans(
    trace_id: str,
    pattern: str,            # regex 搜索关键词
    max_results: int = 20,   # 最多返回 N 个匹配 span
) -> dict:
    """在指定 trace 的 spans 中搜索匹配 pattern 的 span。

    搜索范围：span name, attributes JSON, events JSON, status description。
    返回:
    {
        "trace_id": "...",
        "pattern": "error",
        "matches": [
            {"span_index": 7, "name": "...", "matched_text": "..."},
            ...
        ],
        "total_matches": 5
    }
    """
```

### 6.3 list_traces

```python
def list_traces(
    limit: int = 20,     # 最多返回 N 条
    since: str = "",     # ISO timestamp，只返回此时间之后的
) -> dict:
    """列出最近的 trace_id 及概要信息。

    返回:
    {
        "traces": [
            {"trace_id": "abc123", "span_count": 120, "first_span_name": "...", "start_time": "..."},
            ...
        ]
    }
    """
```

### 6.4 query_evolve_records

```python
def query_evolve_records(trace_id: str) -> dict:
    """查询指定 trace_id 关联的所有演进记录。

    返回:
    {
        "trace_id": "...",
        "proposals": [...],      # Proposal 列表
        "decisions": [...],      # DecisionResult 列表
        "apply_records": [...],  # ApplyRecord 列表
    }
    """
```

### 6.5 query_proposals

```python
def query_proposals(batch_id: str) -> dict:
    """查询指定 batch 的所有 Proposal 及决策结果。

    返回:
    {
        "batch_id": "...",
        "proposals": [...],
    }
    """
```

### 6.6 read_file

```python
def read_file(
    path: str,           # 文件路径
    offset: int = 0,     # 0-based 行号偏移
    limit: int = 100,    # 最多返回 N 行
) -> dict:
    """读取本地文件内容（分页支持）。

    返回:
    {
        "path": "...",
        "total_lines": 500,
        "offset": 0,
        "limit": 100,
        "content": "..."  # 行内容拼接
    }
    """
```

安全约束：只允许读取以下目录下的文件：
- `<workspace>/evolution/` — evolve 输出目录
- `<data_dir>/traces.db` 附近 — trace 导出文件
- `<workspace>/.jiuwenswarm/` — skill 配置文件

### 6.7 submit_result

```python
def submit_result(result: str) -> str:
    """停止工具 — 提交最终 JSON 结果并终止 ReAct 循环。

    result 必须是符合输出契约的 JSON 字符串。
    返回: "TASK_COMPLETED"
    """
```

## 7. 错误处理

| 场景 | 处理方式 |
|------|---------|
| LLM 调用失败 | 重试 3 次，指数退避（2^i 秒） |
| 工具执行失败 | 返回错误信息给 Agent（`{"error": "..."}`），Agent 可换策略 |
| 输出 JSON schema 验证失败 | 追加修正提示，重试 1 次 |
| 预算耗尽 | 返回 `budget_exceeded=True`，`response` 为最后 LLM 文本 |
| traces.db 不存在 | `list_traces` / `read_spans` 返回空，Agent 自行决定下一步 |

## 8. 测试策略

| 测试类型 | 内容 |
|---------|------|
| 单元测试 | 每个工具的输入输出、分页、过滤、搜索逻辑 |
| 单元测试 | `_parse_tool_calls()` 的 JSON 解析和 fallback |
| 单元测试 | `_validate_payload()` 的 schema 验证 |
| 单元测试 | `DiagnosisProposer.generate()` 的 Proposal 转换 |
| 集成测试 | Mock LLM → 完整 ReAct loop（2-3 轮工具调用） |
| 集成测试 | Mock Store → diagnose + propose 两种模式 |

## 9. 配置

在 `evolve/config.yaml` 中扩展：

```yaml
evolve:
  pipeline:
    proposal_generators:
      - diagnosis_agent     # 新增
      - llm_proposer        # 保留

  diagnosis:
    max_iterations: 20      # 工具调用硬预算
    temperature: 0.4        # LLM temperature
    max_tokens: 20000       # LLM 最大输出 token
    max_context_tokens: 200000  # LLM 最大上下文 token 数
    context:
      compact_threshold: 0.75   # 上下文达到此比例时触发压缩
      keep_iterations: 3        # 压缩时保留最近 N 次完整迭代
    tool_output:
      max_chars: 10000          # 工具输出最大字符数
      head_lines: 50            # 截断保留头部行数
      tail_lines: 30            # 截断保留尾部行数
    allowed_file_dirs:      # read_file 允许的目录
      - evolution
      - .jiuwenswarm
```

## 10. CLI 扩展

在 `jiuwenswarm/evolve/cli.py` 中新增 `diagnose` 子命令：

```
jiuwenswarm-evolve diagnose --traces abc123,def456 [--question "..."] [--format text|json]
jiuwenswarm-evolve diagnose --latest 10 [--question "..."]
jiuwenswarm-evolve diagnose --since "2026-06-10T00:00:00" [--question "..."]
jiuwenswarm-evolve diagnose --batch <batch-id>  # 使用已有 batch 的 trace_ids
```

## 11. 与现有模块的集成点

| 集成点 | 文件 | 改动 |
|--------|------|------|
| Registry | `evolve/registry.py` | 新增 `diagnosis_agents` registry |
| CLI | `evolve/cli.py` | 新增 `diagnose` 子命令 |
| Config | `evolve/config.yaml` | 新增 `diagnosis:` 配置段 |
| Pipeline | `evolve/pipeline.py` | 无改动（通过 ProposalGenerator 接口接入） |
| Storage | `evolve/storage/` | 无改动（DiagnosisAgent 通过 store 参数读取） |
| __init__ | `evolve/__init__.py` | 导出 `DiagnosisAgent` |
