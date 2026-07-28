# DeepResearch 缺失阶段事件补齐设计

## 问题

DeepResearch 的六阶段任务快照和阶段消息都由
`jiuwenclaw/agentserver/tools/deepresearch/stream_router.py` 中的
`advance_stage()` 生成。当前实现允许阶段跨级推进，但一次调用只为目标阶段生成
`task.update`、`chat.reasoning` 和 `chat.delta`。

当 workflow 流中最后一个可见节点属于 Stage 3，随后直接返回成功
`__deepsearch_status__: completed` 时，工具会调用 `advance_stage(state, 6)`：

- Stage 6 的任务快照把 Stage 4、5 直接标记为 `completed`；
- 思考过程和前台输出只出现 Stage 6 的切换消息；
- Stage 4、5 在前端任务段中存在，但没有对应阶段消息。

这造成任务列表、思考过程和前台输出消息的阶段序列不一致。

## 已验证的运行时事实

- Skill 把 Stage 4 映射到 `reporter` 和可选的 `vlm_chart_generator`。
- Skill 把 Stage 5 映射到 `source_tracer` 和 `source_tracer_infer`。
- DeepSearch 成功路径中，`ReporterNode` 成功后进入图表处理，再进入
  `SourceTracerNode` 和 `SourceTracerInferNode`；节点内部可以跳过具体溯源算法，但节点
  本身仍会经过。
- 失败路径通过 error outcome 结束，不应补齐未观察到的后续阶段。
- 真实任务记录显示 Stage 4、5 的任务段为空，而 Stage 6 的最终快照直接将全部六阶段
  标记为完成，证明缺少的是阶段观测事件。

因此，成功终态可以作为 workflow 已经过 Stage 4、5 的兜底证据；错误或取消终态不能。

## 目标

- 阶段从当前值推进到更大的目标值时，依次生成所有缺失的中间阶段。
- 从 Stage 3 推进 Stage 6 时，输出顺序固定为 Stage 4、Stage 5、Stage 6。
- 每个补齐阶段同时生成同一份状态对应的 `task.update`、`chat.reasoning` 和
  `chat.delta`。
- 已收到真实节点事件时继续按真实事件推进，不产生重复消息。
- error/cancelled 不主动推进阶段，也不补齐未观察到的后续阶段。
- Stage 6 文件发送成功后的完成快照继续保留六个 Stage，且全部为 `completed`。
- 修改范围限制在 DeepResearch 路由及其测试，不修改公共 rail、前端或其他 Skill。

## 方案比较

### 方案 A：`advance_stage()` 单调补齐所有中间阶段（采用）

普通推进时遍历 `state.current_stage + 1` 到目标阶段，为每个阶段依次更新状态并生成三种
事件。真实节点仍调用同一入口；后续迟到的旧阶段节点会被单调状态规则过滤。

优点：

- 任务列表、思考过程和前台消息由同一个函数、同一次阶段状态生成；
- 不依赖某个特定完成分支，任何丢失的中间阶段事件都能按统一规则恢复；
- 修改只位于 DeepResearch 路由。

代价：

- 当多个节点事件缺失时，补齐消息会连续出现，而不是反映各阶段的真实开始时间。

### 方案 B：仅在成功 completed 分支补发 Stage 4、5

在 `deepresearch_stream` 收到成功终态后硬编码调用 Stage 4、5、6。

优点是只覆盖当前症状；缺点是把阶段规则绑定到完成分支，其他跨级场景仍会不一致，也会
让工具主循环重复维护路由语义。

### 方案 C：要求 DeepSearch SDK 强制返回全部节点事件

修改或包装 SDK 流协议，确保 `reporter`、`source_tracer` 等节点总有可见 chunk。

它能保留最准确的时间边界，但跨越外部 SDK 边界，兼容和发布风险明显高于本次需求。

## 数据流

以当前阶段为 3、目标阶段为 6 为例：

1. `advance_stage(state, 6)` 计算待推进阶段 `[4, 5, 6]`。
2. 推进 Stage 4，生成 Stage 4 的完整 `task.update`，随后生成对应
   `chat.reasoning` 和 `chat.delta`。
3. 同样推进并生成 Stage 5 的三种事件。
4. 同样推进并生成 Stage 6 的三种事件。
5. 文件成功发送后，`advance_stage(state, 6, complete=True)` 生成全六阶段完成快照及
   Stage 6 完成消息。
6. 若之后收到迟到的 Stage 4 或 Stage 5 节点，目标阶段小于等于当前阶段，返回空事件。

失败和取消分支不调用成功阶段推进，因此不会触发补齐。

## 事件顺序

每一个阶段必须保持：

1. `task.update`
2. `chat.reasoning`
3. `chat.delta`

跨级推进时，上一阶段的三种事件必须全部完成后，才能开始下一阶段的三种事件。最终可见
序列为：

```text
Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5 → Stage 6 → Stage 6 完成
```

## 测试

采用测试驱动实现：

1. 新增路由单元测试：从 Stage 3 推进 Stage 6，断言依次得到 Stage 4、5、6 的三组
   事件和三次任务快照。
2. 修改工具级成功路径测试：即使输入不含 `reporter`、`source_tracer` chunk，成功终态
   仍得到连续六阶段序列。
3. 保留并加强去重测试：真实 Stage 4/5 节点已出现或迟到时不重复输出。
4. 保留错误路径测试：错误终态不补齐 Stage 4、5、6。
5. 运行 DeepResearch stream router 与 stream tool 的完整聚焦测试。

## 非目标

- 不修改 DeepSearch SDK 的节点流协议。
- 不为补齐消息伪造节点级过程正文。
- 不改变阶段名称、最终文件协议或工具公开参数。
- 不改变跨独立工具调用的持久化策略。
- 不修改前端任务聚合、公共 task 组件或其他 Skill。
