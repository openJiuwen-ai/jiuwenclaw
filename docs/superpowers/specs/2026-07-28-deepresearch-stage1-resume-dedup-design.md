# DeepResearch Stage 1 恢复去重设计

## 目标

修复同一个 DeepResearch 任务在研究主题澄清后恢复执行时，思考过程和前台正文重复显示：

```text
[DeepResearch 阶段切换] 开始 Stage 1：研究主题澄清
```

首次启动仍应发送一次 Stage 1。用户回答澄清问题并恢复后，不再重复发送 Stage 1；实际观察到 `outline` 节点时再发送 Stage 2。

## 已确认的根因

真实任务记录中，同一个助手消息先后执行：

1. `deepresearch_stream(action="start")`
2. `ask_user_question`
3. `deepresearch_stream(action="resume", node="feedback_handler")`

两次 `deepresearch_stream` 调用各自发送了一条 Stage 1 `chat.reasoning` 和一条 Stage 1 `chat.delta`。持久化的 `thinkingChunks` 和正式正文中都存在两条 Stage 1 消息，因此不是前端重复渲染。

`deepresearch_stream` 的恢复调用没有继承首次调用的 `RouterState`，会创建 `current_stage=0` 的新状态。收到 `resuming` 状态标记后，现有逻辑调用 `advance_stage(state, 1)`，把已经进入过的 Stage 1 再次当作新阶段发送。

## 方案比较

### 方案 1：恢复状态使用中断阶段作为基线

当 `action="resume"` 且 `node="feedback_handler"`，新建路由状态时直接把 `current_stage` 初始化为 1。随后处理 `resuming` 标记时，`advance_stage(state, 1)` 按现有单调推进规则返回空事件。实际收到 `outline` 节点后，现有路由映射自然推进到 Stage 2。

优点：

- 修复状态源头，而不是按文本去重。
- 仅影响 DeepResearch 的主题澄清恢复路径。
- 复用 `advance_stage` 已有的单调推进和跨阶段补齐语义。
- 不修改公共事件协议、API 转换或前端聚合。

这是采用的方案。

### 方案 2：`stream_router` 按消息内容去重

可以阻止相同 Stage 文本重复输出，但无法修复恢复状态从 0 开始的问题，也可能误删未来合法的同文本事件。影响面比方案 1 更大。

### 方案 3：前端展示层去重

前端可以隐藏重复文本，但后端和持久化数据仍然重复，并且会修改所有任务共用的聚合逻辑。不采用。

## 代码范围

只修改：

- `jiuwenclaw/agentserver/tools/deepresearch/tools.py`
- `tests/unit/agentserver/test_deepresearch_stream_tool.py`

不修改：

- `stream_router.py`
- OfficeClaw API 事件转换
- OfficeClaw 前端任务与思考过程聚合
- DeepResearch Skill 文档和六阶段定义
- 其他中断节点的现有恢复行为

## 数据流

修复后的主题澄清流程：

1. 首次 `start` 创建 `RouterState(current_stage=0)`。
2. `started` 标记推进到 Stage 1，并依次发送 `task.update`、`chat.reasoning`、`chat.delta`。
3. `feedback_handler` 中断，主 Agent 展示澄清问题。
4. 用户回答后发起独立的 `resume` 调用。
5. `resume` 为 `feedback_handler` 创建 `RouterState(current_stage=1)`。
6. `resuming` 标记尝试推进 Stage 1，但因阶段未前进而不发送事件。
7. 实际 `outline` chunk 到达后推进 Stage 2，并发送一次对应的三个阶段事件。

## 异常与兼容性

- 无效的 `resume` 参数校验保持不变。
- 恢复子进程未输出 `outline` 时，不提前宣告 Stage 2。
- 恢复后直接失败时，Stage 1 不重复，且不会虚构 Stage 2。
- 首次启动、内部 `outline_interaction` 自动恢复、`user_feedback_processor` 恢复均保持现有行为。
- 事件字段、任务 ID 和六阶段标题保持不变。

## 测试设计

新增一个回归测试，模拟真实的两次工具调用：

1. 首次调用输出 `started`，随后在 `feedback_handler` 中断。
2. 第二次调用输出 `resuming`，随后输出 `outline` chunk。
3. 两次调用共享同一个推送 mock，以还原同一助手消息中的累计事件。

断言：

- Stage 1 `task.update` 只出现一次。
- Stage 1 `chat.reasoning` 只出现一次。
- Stage 1 `chat.delta` 只出现一次。
- Stage 2 在 `outline` chunk 到达后出现一次。
- 阶段快照顺序为 Stage 1、Stage 2。

完成实现后运行 DeepResearch router/tool 聚焦测试，并执行语法检查与差异检查。
