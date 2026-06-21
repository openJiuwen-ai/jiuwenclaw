一、整体架构

  ┌──────────────────────────────────────────────────────────────────┐
  │  Pipeline 入口                                                    │
  │                                                                    │
  │  TraceBatch                                                        │
  │    │ trace_ids: ["abc123...", "def456..."]                         │
  │    │ source: "manual"                                              │
  │    ▼                                                               │
  │  SqliteStore.query_spans(trace_id)                                 │
  │    │ 返回 List[OTEL span row dict]                                 │
  │    ▼                                                               │
  │  OtelTraceAdapter.convert_trace(trace_id, db_path)                 │
  │    │ ① 读 spans → ② 拍平+排序 → ③ 逐 span 转为 observation        │
  │    │ ④ 组装 trace dict → ⑤ 传入 _extract_trace_data_impl          │
  │    ▼                                                               │
  │  _extract_trace_data_impl(trace_dict)                              │
  │    │ 返回 cleaned_trace (标准化后的完整 trace)                      │
  │    ▼                                                               │
  │  ProposalGenerator 使用 cleaned_trace                              │
  └──────────────────────────────────────────────────────────────────┘

  adapter 是唯一的桥梁——OTEL 侧不改、trace_converter 侧不改，所有映射逻辑集中在一处。

  二、新增文件与模块位置

  ┌──────────────────────┬────────────────────────────────────────┬──────────────┐
  │         文件         │                  位置                  │     职责     │
  ├──────────────────────┼────────────────────────────────────────┼──────────────┤
  │ otel_adapter.py      │ jiuwenswarm/evolve/otel_adapter.py     │ 核心转换逻辑 │
  ├──────────────────────┼────────────────────────────────────────┼──────────────┤
  │ test_otel_adapter.py │ jiuwenswarm/tests/test_otel_adapter.py │ 单元测试     │
  └──────────────────────┴────────────────────────────────────────┴──────────────┘

  不改 models.py 的 TraceBatch——它已经足够，trace_ids + SqliteStore 的模式不需要扩展。

  三、核心类设计

  # jiuwenswarm/evolve/otel_adapter.py

  class OtelTraceAdapter:
      """OTEL SQLite spans → Langfuse-style trace dict 的适配器。

      唯一职责：让 _extract_trace_data_impl 能消费标准 OTEL trace。
      不修改 OTEL instrumentor，不修改 trace_converter。

      Usage:
          adapter = OtelTraceAdapter(db_path="traces.db")
          trace_dict = adapter.convert_trace("abc123def456...")
          cleaned = extract_trace_data(trace_dict, ...)
      """

      def __init__(self, db_path: str):
          self.db_path = db_path

      def convert_trace(self, trace_id: str) -> dict[str, Any]:
          """将一条 OTEL trace 转为 _extract_trace_data_impl 预期的 dict。"""
          ...

      def convert_batch(self, batch: TraceBatch) -> list[dict[str, Any]]:
          """将 TraceBatch 中所有 trace_ids 批量转换。"""
          ...

      # ── 内部方法 ──

      def _read_flat_spans(self, trace_id: str) -> list[dict[str, Any]]:
          """从 SQLite 读取一条 trace 的所有 span（拍平，按 start_time 排序）。"""
          ...

      def _span_to_observation(self, span: dict) -> dict[str, Any]:
          """单个 OTEL span dict → 单个 Langfuse observation dict。"""
          ...

      def _reconstruct_llm_input(self, attrs: dict, events: list) -> dict:
          ...

      def _reconstruct_llm_output(self, attrs: dict, events: list) -> dict:
          ...

      def _reconstruct_tool_input(self, attrs: dict, events: list) -> dict:
          ...

      def _reconstruct_tool_output(self, attrs: dict, events: list) -> dict:
          ...

      def _collect_tool_definitions(self, spans: list[dict]) -> list[dict]:
          """从同一 trace 的 tool spans 中推断 tool definitions。"""
          ...

  四、逐字段映射表

  这是方案最核心的部分——每个 OTEL 字段怎么映射到 _extract_trace_data_impl 预期的字段。

  4.1 Trace 顶层 dict

  _extract_trace_data_impl 入口处的 trace dict 需要这些顶层字段：

  trace_dict = {
      # ── 直接取自 root span (无 parent_span_id 的第一个 span) ──
      "id":          trace_id,                          # trace_id hex
      "trace_id":    trace_id,                          # 同上（兼容两种 key）
      "timestamp":   _ns_to_iso(root["start_time_ns"]), # ns → ISO 8601
      "name":        root.get("name", "N/A"),           # agent span name
      "input":       _reconstruct_trace_input(root),    # 见 §4.5
      "output":      _reconstruct_trace_output(root),   # 见 §4.5
      "latency":     _ns_to_ms(root.get("duration_ns")),# ns → ms 字符串

      # ── 从 spans 聚合计算 ──
      "observations": [self._span_to_observation(s) for s in flat_spans],
  }

  4.2 Observation dict（单 span 转换）

  _extract_trace_data_impl 内部 _normalize_observations 期望的 observation dict：

  ┌───────────────────────────────────┬─────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │ _extract_trace_data_impl 预期 key │                     OTEL span 来源                      │                                                              转换规则                                                              │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ id                                │ span["span_id"]                                         │ 直取                                                                                                                               │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ name                              │ span["name"]                                            │ LLM span 特殊处理: 若 gen_ai.span.type=="model"，重写为 {gen_ai.system}.chat（如 "anthropic.chat"），确保 is_llm_span 的关键词匹配 │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ type                              │ gen_ai.span.type attribute                              │ "model" → "GENERATION"; "tool" → "SPAN"; "agent" → "SPAN"; 其他 → "SPAN"                                                           │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ span_type                         │ gen_ai.span.type attribute                              │ "model" → "LLM"; "tool" → "TOOL"; "agent" → "AGENT"; 其他 → span kind name                                                         │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ parentObservationId               │ span["parent_span_id"]                                  │ 直取（key 名映射）                                                                                                                 │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ startTime                         │ span["start_time_ns"]                                   │ _ns_to_iso() 转为 ISO 8601 字符串                                                                                                  │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ endTime                           │ span["end_time_ns"]                                     │ 同上                                                                                                                               │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ latency                           │ span["duration_ns"]                                     │ _ns_to_ms() 转为毫秒数值                                                                                                           │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ input                             │ attributes + events                                     │ 按 span_type 分别重构，见 §4.3                                                                                                     │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ output                            │ attributes + events                                     │ 按 span_type 分别重构，见 §4.4                                                                                                     │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ model                             │ gen_ai.request.model 或 gen_ai.response.model attribute │ 直取                                                                                                                               │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ totalTokens                       │ gen_ai.usage.total_tokens attribute                     │ 直取；同时写入 output.usage.total_tokens（双路径兼容 _sum_total_tokens）                                                           │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ metadata                          │ jiuwenclaw.* attributes + parent 信息                   │ 见 §4.6                                                                                                                            │
  ├───────────────────────────────────┼─────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ calculatedTotalCost               │ —                                                       │ 暂设 "N/A"（OTEL 标准不含 cost，后续可从 pricing table 计算）                                                                      │
  └───────────────────────────────────┴─────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  4.3 LLM span input 重构

  extract_tool_definitions_from_observations 和 get_system_prompt_from_observations 都从 LLM span 的 input 字段提取数据。OTEL 把消息记录在 events 中，需要逆向拼装：

  OTEL span.events:
    [
      {"name": "gen_ai.system.message",   "attributes": {"content": "You are..."}},
      {"name": "gen_ai.user.message",     "attributes": {"content": "帮我..."}},
      {"name": "gen_ai.assistant.message","attributes": {"content": "好的..."}},
      {"name": "gen_ai.tool.message",     "attributes": {"content": "result...", "tool_call_id": "call_abc"}},
    ]

  → 重构为 input dict:

    {
      "model": "claude-sonnet-4-6",       # from gen_ai.request.model
      "messages": [
        {"role": "system",   "content": "You are..."},
        {"role": "user",     "content": "帮我..."},
        {"role": "assistant","content": "好的..."},
        {"role": "tool",     "content": "result...", "tool_call_id": "call_abc"},
      ],
      "tools": [                          # from _collect_tool_definitions()
        {"type": "function", "function": {"name": "bash", ...}},
      ],
    }

  事件排序规则: 按 event timestamp 排序（events 中有 timestamp 字段），保证 messages 的顺序正确。

  Tools 字段: 由于 OTEL instrumentor 不记录 gen_ai.tool.definitions，需要从同 trace 的 tool spans 推断。见 §4.7。

  4.4 LLM span output 重构

  get_assistant_from_openai_generation_output 和 build_agent_turns_from_observations 都从 LLM span 的 output 字段提取 assistant message。

  OTEL 的 assistant output 是最后一个 gen_ai.assistant.message event：

  OTEL span.events 中 gen_ai.assistant.message events:
    [
      {"name": "gen_ai.assistant.message",
       "attributes": {"content": "I'll run bash...", "tool_calls": "ToolCall(name='bash', ...)"}},
    ]

  → 重构为 output dict:

    {
      "role": "assistant",
      "content": "I'll run bash...",
      "tool_calls": [                      # 从 tool_calls 字符串解析
        {"id": "call_abc", "type": "function",
         "function": {"name": "bash", "arguments": "{}"}},
      ],
      "usage": {
        "total_tokens": 1500,             # from gen_ai.usage.total_tokens
        "input_tokens":  800,             # from gen_ai.usage.input_tokens
        "output_tokens": 700,             # from gen_ai.usage.output_tokens
      },
    }

  tool_calls 解析难点: instrumentor 把 tool_calls 记为 str(tool_calls)[:4096]——这是 Python 对象的字符串 repr，不是 JSON。需要设计一个 tolerant parser：

  def _parse_tool_calls_repr(raw: str) -> list[dict]:
      """从 Python repr 字符串尽力解析 tool_calls list。

      格式可能是:
        "[ToolCall(id='call_abc', name='bash', arguments='{\"cmd\":\"ls\"}')]"
      或 Anthropic 格式:
        "[{'id': 'toolu_abc', 'type': 'tool_use', 'name': 'bash', 'input': {\"cmd\": \"ls\"}}]"

      策略: 先尝试 json.loads，失败则用 regex 提取 name/id/arguments。
      """
      # Strategy 1: JSON parse
      try:
          parsed = json.loads(raw)
          if isinstance(parsed, list):
              return [_normalize_tool_call(tc) for tc in parsed]
      except json.JSONDecodeError:
          pass

      # Strategy 2: regex fallback
      # Pattern: name='xxx', id='xxx', arguments='xxx' 或 input={...}
      pattern = r"(?:name|function\.name)=['\"](\w+)['\"]"
      names = re.findall(pattern, raw)
      ...  # 提取后组装为标准 OpenAI tool_calls 格式

  这是一个尽力而为的解析——repr 格式不稳定，但核心需求只是让 build_agent_turns_from_observations 知道这个 LLM span 后面有 tool call，而 tool call 的详细信息由后续的 tool span observation 提供。

  4.5 Agent span (root) 的 input/output

  Agent span (jiuwenswarm.agent.invoke) 是 trace 的 root span，_extract_trace_data_impl 从 trace 顶层 input/output 提取 user message。但 agent span 的 events 不包含完整 input/output payload。

  策略: Agent span 的 input/output 设为从 LLM 子 span 中推断——

  def _reconstruct_trace_input(self, root_span: dict, all_spans: list[dict]) -> dict:
      """Trace 顶层 input — 从第一个 user message event 或第一个 LLM span 推断。"""
      # 找同 trace 中第一个 LLM span
      llm_spans = [s for s in all_spans if (s.get("attributes") or {}).get("gen_ai.span.type") == "model"]
      if llm_spans:
          first_llm = llm_spans[0]
          events = first_llm.get("events") or []
          user_events = [e for e in events if e.get("name") == "gen_ai.user.message"]
          if user_events:
              return {"message": user_events[0].get("attributes", {}).get("content", "")}

      # fallback: 从 root span 的 attributes 提取
      return root_span.get("attributes") or {}

  def _reconstruct_trace_output(self, root_span: dict, all_spans: list[dict]) -> dict:
      """Trace 顶层 output — 从最后一个 LLM span 的 final assistant message 推断。"""
      llm_spans = [s for s in all_spans if (s.get("attributes") or {}).get("gen_ai.span.type") == "model"]
      if llm_spans:
          last_llm = llm_spans[-1]
          events = last_llm.get("events") or []
          assistant_events = [e for e in events if e.get("name") == "gen_ai.assistant.message"]
          if assistant_events:
              last = assistant_events[-1]
              return {"role": "assistant", "content": last.get("attributes", {}).get("content", "")}

      return {}

  4.6 Subagent 检测所需的 metadata

  extract_subagents_from_observations 通过 metadata.subagent_id + metadata.controller_observation_id 检测子 agent。OTEL 的嵌套 agent 通过 jiuwenclaw.agent.parent attribute 和 parent_span_id 表示层级关系。

  转换策略：

  # 在 _span_to_observation 中
  metadata = {}

  # 方案: 若 span 的 gen_ai.span.type == "agent" 且有 parent_span_id，
  # 则它是一个子 agent span → 生成 subagent_id 和 controller_observation_id
  if span_type_otel == "agent" and span.get("parent_span_id"):
      metadata["subagent_id"] = span["span_id"]
      metadata["subagent_name"] = attrs.get("jiuwenclaw.agent.name", "") or attrs.get("gen_ai.agent.name", "")
      metadata["controller_observation_id"] = span["parent_span_id"]

  # 非 agent span 若其 parent 是 agent span，也标记归属
  # （这需要 span 父子关系已建立，见 §4.8）

  obs["metadata"] = metadata

  4.7 Tool Definitions 推断（不修改 OTEL 标准）

  extract_tool_definitions_from_observations 从 LLM span 的 input.tools 取完整 tool schema。OTEL instrumentor 没有记录这个。

  不修改 OTEL 标准的前提下，唯一的信息来源是 tool spans 的 gen_ai.tool.name attribute 和 tool.arguments event。我们只能构建 简化版 tool definition：

  def _collect_tool_definitions(self, spans: list[dict]) -> list[dict]:
      """从同 trace 的 tool spans 推断简化版 tool definitions。

      产出格式兼容 extract_tool_definitions_from_observations 的预期:
      {"type": "function", "function": {"name": "bash", "parameters": {}}}

      注意: parameters 为空 dict，因为没有完整的 schema 信息。
      """
      seen_names = set()
      definitions = []
      for span in spans:
          attrs = span.get("attributes") or {}
          if attrs.get("gen_ai.span.type") != "tool":
              continue
          tool_name = attrs.get("gen_ai.tool.name", "")
          if not tool_name or tool_name in seen_names:
              continue
          seen_names.add(tool_name)
          definitions.append({
              "type": "function",
              "function": {
                  "name": tool_name,
                  "parameters": {},  # 简化版，无完整 schema
              },
          })
      return definitions

  这些简化版 definitions 会写入 每个 LLM span observation 的 input.tools 字段，使 extract_tool_definitions_from_observations 能正常返回结果（虽然 parameters 为空）。

  对下游的影响: extract_tool_definitions_from_observations 只做 dedup + collect，不检查 parameters 内容。ProposalGenerator 在分析 tool 失败时仍然可以知道有哪些 tool 可用，只是无法从 trace 中看到 tool 的完整参数 schema。

  4.8 Span 拍平与排序

  get_trace_tree 返回嵌套树结构，但 _extract_trace_data_impl 需要拍平的 observations list。同时需要保留父子关系信息：

  def _read_flat_spans(self, trace_id: str) -> list[dict[str, Any]]:
      """读取并拍平排序。用 query_spans 而非 get_trace_tree，直接拿平铺结果。"""
      from jiuwenswarm.telemetry.sqlite_exporter import query_spans
      spans = query_spans(self.db_path, trace_id=trace_id, limit=10000)
      # query_spans 已经按 start_time_ns DESC 排序
      # 但 _sort_key 需要 startTime 字段（ISO string），这里数据已是 ISO（因为 JSON 已解析）
      # 按 start_time_ns 升序重排（与 _sort_key 的行为一致）
      spans.sort(key=lambda s: s.get("start_time_ns", 0))
      return spans

  五、时间格式转换工具

  # 在 otel_adapter.py 中

  from datetime import datetime, timezone

  def _ns_to_iso(ns: int | None) -> str:
      """纳秒时间戳 → ISO 8601 字符串。"""
      if ns is None:
          return "N/A"
      seconds = ns / 1_000_000_000
      dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
      return dt.isoformat()

  def _ns_to_ms(ns: int | None) -> float | str:
      """纳秒 → 毫秒数值。"""
      if ns is None:
          return "N/A"
      return ns / 1_000_000

  六、LLM span name 适配策略详细设计

  这是让 is_llm_span 正确识别的关键。当前逻辑：

  # is_llm_span 检查:
  #   1. type/span_type 是 "SPAN"/"LLM"/"GENERATION" 或 span_type=="LLM"
  #   2. output 中有 assistant message
  #   3. name 包含关键词 "openai" | "anthropic" | "gemini" | "gpt" | "llama"

  OTEL span name 是 "gen_ai.chat"——不含任何关键词。适配策略：

  # _adapt_observation_name: 在 _span_to_observation 中应用
  _LLM_SYSTEM_TO_KEYWORD = {
      "openai":    "openai",
      "anthropic": "anthropic",
      "azure":     "openai",     # Azure OpenAI 用 openai 关键词
      "gemini":    "gemini",
      "google":    "gemini",
      "deepseek":  "llama",      # DeepSeek 用 openai-compatible API，fallback
      "unknown":   "openai",     # fallback — 保守选一个能匹配的
  }

  def _adapt_observation_name(original_name: str, attrs: dict, span_type_otel: str) -> str:
      """确保 LLM span name 包含 is_llm_span 所需的关键词。"""
      if span_type_otel != "model":
          return original_name
      # original_name 是 "gen_ai.chat"
      system = attrs.get("gen_ai.system", "unknown")
      keyword = _LLM_SYSTEM_TO_KEYWORD.get(system, "openai")
      return f"{keyword}.chat"   # e.g. "anthropic.chat", "openai.chat"

  这样 is_llm_span 的三重检查全部通过：
  - type="GENERATION" ✓
  - span_type="LLM" ✓
  - name="anthropic.chat" 包含 "anthropic" ✓
  - output 有 assistant message ✓（因为重构了 output）

  七、边缘情况处理

  ┌────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                      场景                      │                                               处理                                                │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Span 无 context (trace_id/span_id 为空)        │ 跳过，不转为 observation                                                                          │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ LLM span 无 assistant message event            │ output 设为 {"role": "assistant", "content": ""}，is_llm_span 返回 False，该 span 被当作普通 span │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Tool span 无 arguments/result event            │ input/output 设为空 dict                                                                          │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ tool_calls repr 无法解析                       │ 返回空 list；build_agent_turns_from_observations 仍能从后续 tool span observation 构建 tool_calls │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ 同 trace 有多个 root span（无 parent_span_id） │ 取 start_time_ns 最小的作为 trace root                                                            │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Trace 无任何 LLM span                          │ 所有 observations 都是普通 span；system_prompt="", agent_turns=[]，total_tokens="N/A"             │
  ├────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Span events 为 None 或空 list                  │ json.loads(span["events"]) 失败时设为 []                                                          │
  └────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────┘

  八、集成调用示例

  # 在 ProposalGenerator 或 Pipeline 中使用

  from jiuwenswarm.evolve.otel_adapter import OtelTraceAdapter
  from jiuwenswarm.evolve.models import TraceBatch
  # trace_converter 来自 agentic-harness-engineering (vendored 或 import)

  def process_batch(batch: TraceBatch, db_path: str) -> list[dict]:
      adapter = OtelTraceAdapter(db_path=db_path)
      results = []
      for trace_id in batch.trace_ids:
          trace_dict = adapter.convert_trace(trace_id)
          cleaned = extract_trace_data(
              trace_dict,
              include_system_prompt_message=True,
              include_user_message=True,
          )
          results.append(cleaned)
      return results

  九、测试策略

  ┌─────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │        测试类别         │                                                      覆盖点                                                      │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ 单 span 转换            │ 每个 span_type（model/tool/agent/unknown）的 _span_to_observation 输出完整性                                     │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ LLM input 重构          │ messages 从 events 正确提取 + 排序；tools 简化版 definitions 正确注入                                            │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ LLM output 重构         │ assistant message + usage + tool_calls 解析                                                                      │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ name 适配               │ 各 gen_ai.system 值映射到正确关键词                                                                              │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ 完整 trace              │ 一条含 agent→LLM→tool→LLM 的完整 trace 转换后，_extract_trace_data_impl 输出字段齐全                             │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ 边缘情况                │ 空 trace、无 LLM span、无 events、tool_calls repr 解析失败                                                       │
  ├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ 与 trace_converter 兼容 │ 转换产物直接传入 extract_trace_data 不报错，且关键字段（system_prompt、messages、total_tokens、subagents）值正确 │
  └─────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  十、不做的事项（明确排除）

  ┌─────────────────────────────────────┬──────────────────────────────────────────────────────────────┐
  │               排除项                │                             原因                             │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ 修改 OTEL instrumentor 加 attribute │ OTEL 标准不轻易修改                                          │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ 修改 trace_converter.py             │ 上游代码，适配层应完全消化差异                               │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ 修改 TraceBatch 结构                │ 它只做描述符，不需要扩展                                     │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ 实现完整的 tool schema 推断         │ 没有 gen_ai.tool.definitions attribute，无法恢复完整参数定义 │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────┤
  │ cost 计算                           │ OTEL 不含 cost attribute，设 "N/A"                           │
  └─────────────────────────────────────┴──────────────────────────────────────────────────────────────┘

  ---
  这就是完整细化方案。核心就一个文件 otel_adapter.py，所有映射逻辑集中在那里，OTEL 侧和 trace_converter 侧都不动。要我进入实现阶段吗？