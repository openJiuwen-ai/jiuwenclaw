# DeepResearch 友好进度展示设计

## 目标

把 DeepResearch 大纲、章节调研计划和检索来源的结构化 JSON 转换为可读 Markdown，再通过现有 `chat.reasoning.content` 返回前端。研究执行、恢复协议和原始数据不变。

## 当前问题

DeepSearch 会把 `Outline`、`Plan` 和检索来源序列化为 JSON。Jiuwen 的 DeepResearch 路由器为了完整保留过程，将字典、数组和 JSON 字符串直接写入 `chat.reasoning.content`。OfficeClaw 通用任务气泡把它当文本或 Markdown 渲染，因此暴露了内部字段。

## 设计

在 `jiuwenclaw/agentserver/tools/deepresearch/stream_router.py` 增加纯展示适配函数：

- `outline`：显示报告标题、章节列表、章节描述和核心章节标记，不显示内部 ID、语言、依赖数组等实现字段。
- `plan_reasoning`：显示计划标题、调研思路、调研状态和步骤列表。
- `collector_info_retrieval`：将 `{title, url, query}` 显示为来源和对应检索词；仅 `http/https` 地址生成可点击链接。
- 其他节点：保持原有文本；未知结构或 JSON 解析失败时原样回退。
- `sub_reporter`：继续只显示推理内容，不流式暴露完整章节正文。

格式化只作用于路由生成的展示内容。原始 chunk 仍存在于 `run_deepsearch.py` 的 NDJSON/进度数据中，路由状态与中断提示仍使用原始内容。

## 兼容性

- 不新增事件类型或字段。
- `task_id`、`task_content`、`task_index`、`total_tasks`、`stream_source_id` 不变。
- 已经是 Markdown 或普通文本的内容保持逐字不变。
- JSON 标量、无法识别的对象和解析失败文本保持原样。
- URL 使用 Markdown 链接展示时转义链接标题中的方括号，避免破坏渲染；非 `http/https` 地址只显示为普通文本。

## 测试

在现有路由器单测中覆盖：

1. 大纲 JSON 转为标题和章节清单。
2. 章节调研计划 JSON 转为目标和步骤。
3. 检索来源 JSON 转为链接与检索词。
4. 普通文本和未知 JSON 不发生语义改变。
5. `sub_reporter` 仍不输出章节正文。
