# 首 Token 性能优化设计

## 背景与基线

最后一次 `officeclaw` 会话的 `chat.send` 于 15:20:36.250 进入
AgentServer，A2UI decision 为 15:20:40.226，模型调用边界 `pre_llm` 为
15:20:40.450，首 token 为 15:20:46.747。因此本地调用前耗时为 4.200 秒，
模型 TTFT 为 6.297 秒；模型使用统计上报的 TTFT 为 6.406 秒。该请求输入
33,233 tokens，缓存命中 13,312 tokens（40.1%）。A2UI 在该 channel 未启用，
不是时延主因。

## 目标

本次只实现阶段 A/B，且不改变用户可见工具能力或鉴权语义。

| 场景 | `chat.send -> pre_llm` | `pre_llm -> first_token` |
| --- | ---: | ---: |
| 当前样本 | 4.200s | 6.297s |
| 预热命中目标 | <= 1.0s | <= 3.0s（依赖模型服务） |
| 冷启动目标 | <= 2.5s | <= 4.0s（依赖模型服务） |

阶段 A 的目标是把首 token 前时间精确归因；阶段 B 的目标是移除确定无收益的
本地工作，并让模型请求的可变 prompt 更小、更容易命中前缀缓存。

## 架构

请求路径划分为 `recv -> init -> mcp -> prompt/rails -> pre_llm -> first_token`。
每个请求以 `request_id` 关联阶段事件。阶段 A 不引入新的外部服务或持久化数据：
仅使用结构化日志记录安全的计数、耗时和匿名标识。

阶段 B 由三个低风险改动组成：

1. 缺少 embedding 三元组（`api_key`、`base_url`、`model`）时直接跳过
   `MemoryRail`，不再创建必然失败的 Pydantic 配置。
2. `ProgressiveToolRail` 的导航提示只构建活动语言；描述采用固定短摘要，保留
   工具名称、分类和按需检索协议，不改变工具可见性。
3. 增加只观测的 prompt budget 快照：每个系统 prompt section 记录字符数；模型
   可用时记录最终消息与工具数。它不截断任何内容，后续依据数据决定是否实施
   硬性 token budget。

KV cache affinity 保持默认关闭。现有 provider 保护逻辑只允许 `AscendAffinity`
提供方启用；阶段 B 增加清晰的有效状态日志，不绕过该安全保护。

## 可观测性

每次模型调用记录：`request_id`、模型名、rail 名称、耗时、prompt section 数、
section 字符数、工具数、缓存亲和请求值和有效值。不得写入用户 query、prompt
正文、Authorization、MCP 环境变量或 token。

阶段日志：

- `latency.rail`: 一个 rail 的 `before_invoke` 或 `before_model_call` 耗时。
- `latency.prompt`: prompt section 数、总字符数和每 section 字符数。
- `latency.kv_cache_affinity`: 请求配置、有效配置与 provider。

## 兼容性与失败处理

- 任何 prompt 统计异常都必须被吞掉并写 debug 日志，不能中断模型调用。
- 无 embedding 配置是合法部署状态，跳过 MemoryRail 是预期行为。
- 多语言导航在无法确定语言时保持既有中英文双内容行为，避免改变 agent-core
  的语言解析结果。
- 所有新增日志默认不含敏感正文。

## 测试

单元测试覆盖：缺 embedding 时不构造 `MemoryRail`；有效 embedding 仍创建它；
导航只创建活动语言；prompt snapshot 不读取正文；缓存亲和日志反映 fail-closed
后的有效值。性能 wiring 测试验证阶段日志调用点存在。

## 阶段 C：共享无项目 Root 与请求级 Session Overlay

### 修订结论

当前 root agent 缓存键包含 `project_dir`，因此 OfficeClaw 每个新工作目录都会
miss。仅缓存 config 模板不能减少冷启动：传给 root 的动态 config 基本只有
`project_dir`，真正耗时发生在 session child 的 `create_instance()`。

阶段 C 改为复用无项目 root router，绝不复用已创建的 session child / DeepAgent。
child 创建时深拷贝 root config，并合并当前请求的 `project_dir`。MCP、checkpoint、
附件、workspace、interaction 和用户 metadata 始终只属于 child。

### 范围与开关

仅当 `channel_id=officeclaw`、agent 模式且环境变量
`JIUWENSWARM_SHARED_OFFICECLAW_ROOT=1` 时启用。默认关闭；其他 channel 和关闭
开关时保持现有 `(mode, sub_mode, project_dir)` 缓存键与行为。

共享 root key 为 `(channel_id, mode, sub_mode)`，由已有 `AgentManager` 的每-key
创建锁保证单飞。root 创建时不传 `project_dir`。配置 reload 仍走既有 agent 回收与
重建路径，不引入新的全局缓存或 TTL。

### 数据流与隔离

```text
chat.send(project_dir=A, session=S1)
  -> shared OfficeClaw root
  -> child S1 = deepcopy(root config) + {project_dir: A}
  -> child S1 独立 DeepAgent / MCP / checkpoint

chat.send(project_dir=B, session=S2)
  -> 同一 shared root
  -> child S2 = deepcopy(root config) + {project_dir: B}
```

同一 session 已有 child 时，带不同的非空 `project_dir` 的请求会被拒绝，不会覆盖
已绑定目录。开关、cache hit/miss 和拒绝重绑仅记录无敏感日志。

### 验收

- 两个不同项目目录的 OfficeClaw 请求只创建一个 root、创建两个 child。
- child 的 config、`_project_dir`、MCP invocation 与 checkpoint 互不共享。
- 并发 root miss 只创建一次；非 OfficeClaw 与开关关闭路径不变。
- 固定 benchmark 分别报告 root miss/hit 的 `recv -> init` 和 `recv -> pre_llm`；
  不以模型 TTFT 波动判断阶段 C。

## 后续阶段（不在阶段 C 实现）

- 阶段 D：MCP 工具分级暴露与 `McpToolCatalog`，首轮仅注入最小工具集。
- 阶段 E：`PromptProfile` 分层、固定前缀预算与可验证的 KV cache affinity。
