# DeepResearch 改写快速通道设计

## 背景

DeepResearch 报告的润色、扩写和缩写目前作为普通 `agent.plan` 请求进入通用
Agent。一次成功改写通常需要多轮模型决策来完成技能发现、工具搜索、
`deepresearch_prepare_rewrite`、正文生成、`deepresearch_commit_rewrite`、任务收尾和
最终答复。prepare/commit 自身耗时很短，主要时间和 token 消耗来自重复模型调用及
重复上下文。

本设计为严格格式的 DeepResearch 改写请求增加进程内快速通道，将固定工作流收敛为：

`parse -> prepare -> one model call -> validate -> commit -> final`

## 目标

- 对合法 `<deepresearch_rewrite_request>` 请求恰好调用一次模型。
- `deepresearch_prepare_rewrite` 和 `deepresearch_commit_rewrite` 各调用一次。
- 不调用通用 `Runner.run_agent_streaming`，因此跳过 QA、memory、技能发现、todo 和
  ReAct 工具循环。
- 保持现有 Protocol v2、Markdown 结构、引用校验、不可变 revision 和文件交付语义。
- 保持普通消息及非严格改写请求的现有行为。
- 为快速通道记录可比较的总耗时、prepare、model、commit 和状态日志。

## 非目标

- 不修改前端 `<deepresearch_rewrite_request>` 协议。
- 不修改 prepare/commit 核心算法、错误码、上下文 token 生命周期或文件发布规则。
- 不新增 OfficeClaw 与 JiuwenClaw 之间的 RPC。
- 不扩展到报告 HTML 生成、首次研究报告生成或任意自然语言改写请求。
- 不自动回退到通用 Agent 重试已经被识别为快速通道的失败请求。

## 入口和识别规则

快速通道位于 `JiuWenClawDeepAdapter.process_message_stream_impl` 中：

1. 保留现有模型配置检查、request/session trace、runtime route、permission context 和
   `_update_runtime_config`。
2. 在 `Runner.run_agent_streaming` 之前检查 query。
3. 只有 query 去除首尾空白后完全由一对
   `<deepresearch_rewrite_request>...</deepresearch_rewrite_request>` 标签组成时才识别。
4. 标签内容必须是 JSON object，且顶层字段精确为：
   `report_path`、`action`、`selection`、`instruction`。
5. `action` 仅允许 `polish`、`expand`、`shorten`；selection 和其余字段继续交给现有
   prepare tool 的 Protocol v2 校验。
6. 不匹配标签的普通请求返回“未识别”，继续原 Runner；匹配标签但 JSON 或契约错误的
   请求属于快速通道错误，不进入 Runner。

严格区分“未识别”和“已识别但失败”可防止恶意或损坏请求通过通用 Agent 获得第二条、
语义不同的执行路径。

## 组件

### `deepresearch_rewrite_fast_path.py`

新增一个聚焦模块，负责：

- 严格解析 envelope，并返回“未识别”或经过基础校验的请求。
- 由 prepare 结果构造最小 system/user messages。
- 从唯一一次模型响应中提取 JSON object。
- 校验模型输出只有 `units` 和 `facts_added`，并将最终契约校验交给现有 commit tool。
- 编排 prepare、模型调用和 commit，返回领域结果和分段耗时。

该模块通过注入的 `model_invoke`、`prepare_invoke` 和 `commit_invoke` 调用点进行单元
测试。生产环境的 prepare/commit 注入使用现有工具函数的 `_func`，保证工具层的输入
校验、安全错误和文件交付仍然生效。

### `interface_deep.py`

适配器只负责：

- 在 runtime context 已绑定后调用快速通道。
- 把快速通道的成功或错误转换为既有 `AgentResponseChunk`。
- commit 完成后，将本轮按合法的 user → assistant tool call → tool result →
  assistant 拓扑写入 ContextEngine 并落到 checkpointer，使后续“生成 HTML”仍可从可信的
  `deepresearch_commit_rewrite` 工具结果读取目标 revision。
- 成功时发送 `chat.final`，内容保持现有改写 Skill 的固定邀请语。
- 将 dict 或 Pydantic `UsageMetadata` 统一计入既有 usage summary。
- 错误时发送 `chat.final`，展示安全错误码和可行动提示。
- 发送唯一终止事件，并执行现有 `finally` 清理逻辑。
- 未识别时无副作用地继续调用 Runner。

业务规则、prompt 和 JSON 解析不直接塞入已经很大的 `interface_deep.py`。

## 模型输入与输出

system message 固定要求：

- 仅改写已提供的 slots，不调用工具、不解释过程。
- 按 action 执行润色、扩写或缩写。
- 保持 unit 顺序、unit_id、slot 顺序和 slot_id。
- 不修改引用、链接、代码、公式或其它受保护结构。
- 不添加事实；只可使用原 slot、只读相邻上下文和 citation evidence 中的信息。
- 只返回一个 JSON object，不使用 Markdown code fence。

user message 仅包含 prepare 的以下结果：

- `action`
- `instruction`
- `units`
- `readonly_context`
- `allowed_source_ids`
- `citation_evidence`

模型输出契约为：

```json
{
  "units": [
    {
      "unit_id": "u1",
      "slots": [
        {"slot_id": "s1", "text": "改写后的文本"}
      ]
    }
  ],
  "facts_added": false
}
```

快速通道可以去除响应首尾空白；除此之外只接受单个 JSON object。最终 unit/slot
拓扑、空文本、事实标志和结构完整性由现有 commit 校验，不在快速通道复制第二套规则。

## 数据流

1. 适配器建立 request/session/output-dir/deepresearch route context。
2. parser 严格识别并解析 envelope。
3. 调用 `deepresearch_prepare_rewrite._func(...)`。
4. 若 prepare 返回 `status != prepared`，立即结束，不调用模型。
5. 以 prepare 的结构化结果构造最小 messages。
6. 调用当前请求已经解析好的 `self._model.invoke(messages)` 一次。
7. 解析唯一模型响应的 content 为 structured result。
8. 调用 `deepresearch_commit_rewrite._func(...)`；context token 保持在同一进程和任务中。
9. commit 继续负责 child revision、provenance、引用/拓扑校验和 `chat.file` 推送。
10. 保留完整 commit 结果，并将配对的 `deepresearch_commit_rewrite` tool call/tool result
    写入当前 session 的对话上下文和 checkpointer。
11. 适配器发送固定 `chat.final` 并完成请求。

## 错误处理

- envelope JSON/顶层契约错误：`BAD_REQUEST`，零模型调用、零 commit。
- prepare 业务错误：保留现有安全 `error_code`，零模型调用、零 commit。
- 模型调用异常：`MODEL_CALL_FAILED`，不 commit，不暴露凭证或内部路径。
- 模型 content 缺失或不是单个合法 JSON object：`MODEL_OUTPUT_INVALID`，不 commit。
- commit 业务错误：保留现有安全 `error_code`。
- commit 已完成但文件推送失败：保留现有 `completed` 和
  `REPORT_DELIVERY_FAILED` 语义，明确提示“版本已保留但交付失败”，不发送标准成功邀请，
  也不将已创建 revision 表述为回滚。
- commit 已完成但可信工具结果无法持久化：保留已创建 revision，返回
  `CONTEXT_PERSIST_FAILED`，不得承诺后续 HTML 请求能够自动恢复目标版本。
- 所有已识别的快速通道错误都不回退 Runner，避免重复调用、重复提交或改变错误语义。

## 并发与安全

- 不增加全局可变配置；继续使用现有 ContextVar 隔离 route、session 和 output dir。
- prepare 返回的一次性 context token 仅在同一个 async task 内传给 commit。
- 不记录 selection 正文、模型完整输入、模型完整输出或凭证。
- report path 的 workspace 约束继续由 prepare 核心验证。
- session 队列仍在外层生效，同 session 请求顺序不变。

## 可观测性与性能验收

每个已识别请求写一条结构化完成日志，包含：

- request_id、session_id、action
- status 和安全 error_code
- `prepare_ms`、`model_ms`、`commit_ms`、`total_ms`
- `model_calls`，只允许 0 或 1

不把 selection 文本或生成正文写入日志。

P0 验收标准：

- 自动化测试证明成功路径：prepare 1 次、model 1 次、commit 1 次、Runner 0 次。
- prepare 失败：model 0 次、commit 0 次、Runner 0 次。
- 模型输出失败：model 1 次、commit 0 次、Runner 0 次。
- 普通请求：快速通道不调用 prepare/model/commit，Runner 仍为 1 次。
- 在相同本地模型和相同规模选区下，分别实测 polish、expand、shorten；报告端到端耗时及
  日志中的分段耗时。P0 不承诺固定秒数，因为单次模型生成时间受服务负载和输出长度影响，
  但模型调用次数必须从当前约 10–12 次降为 1 次。

## 测试策略

### 解析器单元测试

- 精确 envelope 可识别。
- 前后存在普通文本时不识别。
- 匹配标签但非法 JSON、额外顶层字段、非法 action 时返回 `BAD_REQUEST`。

### 编排单元测试

- 成功时调用顺序为 prepare、model、commit，且 model 恰好一次。
- 成功结果保留完整 commit tool result；结构化 `UsageMetadata` 归一化为字典。
- prepare 错误短路模型和 commit。
- 模型异常、空 content、code fence 或非法 JSON 短路 commit。
- commit 错误原样映射安全错误码。
- prompt 只包含 prepare 返回的允许字段。

### 适配器回归测试

- 已识别请求不调用 `Runner.run_agent_streaming`。
- 未识别请求继续调用 Runner。
- 成功和失败流均只产生一个正确终止态。
- commit 成功会持久化成对的 assistant tool call 与 `ToolMessage`；后者包含
  `status=completed`、`report_path` 和 `revision_id`。
- `report_delivered=false` 明确显示交付失败，且不显示标准“生成 HTML”邀请。
- 原有 rewrite tool 和 document rewrite 测试继续通过。

## 交付范围

首轮只修改 JiuwenClaw：

- 新增快速通道模块及单元测试。
- 在 deep adapter 增加最小路由接入及回归测试。
- 不修改 OfficeClaw 前端或 API。

实现通过后，用独立 worktree 启动受控 JiuwenClaw 实例，并让现有 OfficeClaw 本地运行环境
指向该实例进行三种动作实测。任何运行时切换只使用进程级参数，不修改 `.env` 或持久配置。
