# DeepResearch 大纲自动恢复阶段去重设计

## 问题

DeepResearch 首次运行到 `outline` 节点时会推进到 Stage 2。随后
`outline_interaction` 被工具静默接受，工具递归执行一次 `resume`。递归调用重新创建
`RouterState`，并把 Stage 2 作为恢复起点再次发送，导致任务输出中的
`[DeepResearch 阶段切换] 开始 Stage 2：大纲生成与确认` 重复。

## 目标

- 一次 DeepResearch 工具调用链中，Stage 2 切换消息只发送一次。
- `task.update`、`chat.reasoning` 和 `chat.delta` 继续由同一次
  `advance_stage()` 调用生成，三种界面状态保持一致。
- 不改变 `deepresearch_stream` 的公开工具参数和返回协议。
- 不修改前端、公共 rail 或其他 Skill。

## 方案比较

### 方案 A：内部自动恢复复用 `RouterState`（采用）

为 `_call_deepresearch_stream_impl()` 增加仅供内部递归调用使用的可选状态参数。首次调用
仍创建新状态；`outline_interaction` 自动恢复时传入当前状态。恢复流收到
`resuming` 标记后再次尝试推进 Stage 2，现有 `advance_stage()` 会因
`stage <= current_stage` 返回空列表。

优点是复用现有去重语义、改动局限在 DeepResearch 工具实现中，并保留自动恢复前已经
收集的大纲和章节状态。代价是内部函数多一个私有参数。

### 方案 B：按 `conversation_id` 持久化当前阶段

把当前 Stage 写入进程缓存或外部存储，所有 start/resume 都读取并去重。它可以覆盖跨工具
调用的阶段连续性，但需要处理并发、过期和异常清理，超出本次 Stage 2 自动恢复问题范围。

### 方案 C：自动恢复时不发送初始 Stage 2

在 `resume + outline_interaction` 分支直接跳过 `advance_stage()`。改动更少，但把去重规则
硬编码到一个恢复节点；如果前半段没有成功发送 Stage 2，会造成阶段缺失。

## 数据流

1. `start` 创建 `RouterState`。
2. `outline` chunk 调用 `advance_stage(state, 2)`，同时生成任务列表、思考消息和前台消息。
3. `outline_interaction` 中断被静默接受。
4. 内部递归 `resume` 复用步骤 1 的 `RouterState`。
5. `resuming` 标记再次请求 Stage 2 时被现有单调推进规则过滤。
6. 后续节点继续在同一状态上推进 Stage 3 至 Stage 6。

外部由模型发起的独立 `resume` 仍按现有逻辑创建新状态，本次不引入跨调用持久化。

## 测试

在 `test_deepresearch_stream_tool.py` 的自动接受大纲场景中记录推送事件，断言完整内部递归
调用链只产生一次 Stage 2 `chat.delta`，并同时断言 Stage 2 `task.update` 和
`chat.reasoning` 也各只有一次。测试先在当前实现上失败，再以最小代码修改使其通过。

随后运行：

- DeepResearch stream tool 聚焦测试；
- DeepResearch stream router 全量单元测试；
- 相关 DeepResearch 单元测试集合；
- Python 语法/格式检查（按仓库现有命令）。

## 非目标

- 不处理跨模型工具调用的 Stage 1 恢复去重。
- 不改变六阶段名称、状态计算或完成后保留任务列表的行为。
- 不改变 `outline_interaction` 静默自动接受策略。
