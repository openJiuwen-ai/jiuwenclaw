# Skill Turbo 与内置 PPT 流水线模块设计说明书

> 本分册来自 149 个 `server/runtime/**/*.py` 文件的逐文件源码取证。全部类、函数、方法、字段与准确签名见[Runtime Core Python API](../interfaces/03-runtime-core-api.md)和[Skill Runtime Python API](../interfaces/04-skill-runtime-api.md)。

## 6. Skill Turbo

### 6.1 Planner、plan code 与校验

- `skill_acceleration_exec(query)` 是 DeepAgent 工具入口；每次创建新的 `SkillTurbo`/environment/executor，输入从 adapter ContextVar、parent session、request metadata、effective workspace 组装。自定义模板和已有 PPT 选区编辑目前显式旁路，返回 `success=false` 指引标准 `skill_tool`（[`skill_turbo_tools.py:310`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L310)）。
- environment 扫描 enabled skills，计算 skill 根 checksum，定位 builtin 或 workspace `skill_codes`，按工具组动态装载 filesystem/bash/web/vision/audio/video/image/ask/deepresearch/send-file 等。校验失败的 skill code 不注册；send-file 路由使用请求 ContextVar，避免全局 tool singleton 串会话（[`environment.py:40`](../../../../../jiuwenswarm/server/runtime/skill_turbo/environment.py#L40)、[`tools_loader.py:35`](../../../../../jiuwenswarm/server/runtime/skill_turbo/tools_loader.py#L35)）。
- planner 用 LLM 在已注册 skill 中单选，confidence 必须 >=0.6；路由调用/JSON/未知 skill 任一失败均返回 None。匹配后按 `{name}_gen_root.py`、`*_gen_root.py`、`{name}_root.py`、`*_root.py`、`plan_code.py` 搜入口，最后才用显式 `skill.plan_code`（[`planner.py:27`](../../../../../jiuwenswarm/server/runtime/skill_turbo/planner.py#L27)）。
- executor 在动态 import 前用 `PlanCodeValidator` AST 白名单校验 import、call、delete 和 broad-except 必须重抛 AbortError 等规则；builtin/generated/plan code 有不同 policy。import namespace 受 skill code package/prefix 限制，缺 root 或加载异常抛 `PlanCodeLoadError`（[`validator.py:21`](../../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L21)、[`executor.py:3282`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L3282)）。

### 6.2 Executor、权限桥和 rails

- executor 将 config 合入 inputs，按请求注册工具，创建独立 openjiuwen session，并把 agent_id 改为 `{card.id}__skill_turbo`，使其 checkpointer 与外层 DeepAgent 隔离；所有 session/request/channel、send-file、interactive ask、task state 都用 ContextVar 绑定并在 finally token-reset（[`executor.py:601`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L601)）。
- 每个 `PlanNode` 的 `call_tool/call_llm/stream_llm/execute_subplan` 由 executor 注入回调；子计划 before/after 回调生成稳定 task_id、task.start/update/complete。并发 LLM 受 semaphore 限制，每个并发 source 分配 `skill_turbo:<node>:<hex>`，reasoning/output/usage 按 source 归桶（[`plan_node.py:42`](../../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L42)、[`executor.py:1727`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1727)）。
- 工具调用生成确定性 tool_call_id（同 plan、同重放顺序和参数得到同 id）。`build_tool_ctx()` 把它包装成 rail 所需 duck-typed ToolCall；before_tool_call 依次通过 permission、ask-user、artifact/stream rails。普通 rail 异常 warning 后继续，但 `AbortError` 必须上抛，不能吞掉 HITL（[`permission_bridge.py:43`](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L43)、[`executor.py:902`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L902)）。
- `SkillTurboPermissionRail` 仅定制审批消息；`SkillTurboAskUserRail` 将 structured answers/outline preview 适配到 interrupt；非 interactive 模式可按规则跳过 ask。`SkillTurboArtifactRail` 在 tool result 后识别文件路径，写节点 artifact holder 并发 artifact.generated（`rails/*.py`）。
- 流输出先发 plan.started，预建 pending task 快照，再执行 root。chat.delta/reasoning 按 source+event 缓冲但首 chunk 立即发；非缓冲事件会先 flush。上层工具丢弃前端不识别的 plan/node 事件；外层已有 todo 时再丢内部 task 事件，并把 `chat.*` 反映射为原始 `OutputSchema.type` 写回 parent session（[`executor.py:664`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L664)、[`skill_turbo_tools.py:552`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L552)）。

### 6.3 Artifact、fallback 与恢复

- 节点 artifact 仅持久化摘要、小结构和文件路径到 `__skill_turbo_node_artifacts__`，通过 `pre_run→update_state→post_run` 复用 checkpointer；新任务开头使用独立 session 清旧记录，HITL resume 不清（[`node_artifact_store.py:1`](../../../../../jiuwenswarm/server/runtime/skill_turbo/node_artifact_store.py#L1)、[`executor.py:1167`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1167)）。
- PlanNode 业务异常可调 `DeepAgentFallbackHandler`；它把失败节点/指令/inputs/error 编为委托 prompt，解析严格 JSON contract，成功标记 `status=degraded`。fallback 次数受配置上限；契约失败或超限时 executor 特意不发 `chat.error`，而是 plan.failed+complete 后抛到工具层，让外层 LLM 仍有机会改走标准 skill（[`fallback_handler.py:24`](../../../../../jiuwenswarm/server/runtime/skill_turbo/fallback_handler.py#L24)、[`executor.py:2135`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L2135)）。
- HITL 中断时 executor 先保存当前 node artifacts 和 `plan_code/inputs/pending_tool_call_id/task_states` 到 `__skill_turbo_resume_ctx__`；工具层把 `ToolInterruptException` 放 ContextVar，使外层 after_tool_call 产生原生 HITL 三件套（[`permission_bridge.py:231`](../../../../../jiuwenswarm/server/runtime/skill_turbo/permission_bridge.py#L231)、[`skill_turbo_tools.py:650`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L650)）。
- 恢复请求先用相同 external session_id 和 turbo agent id 读 checkpoint；命中后跳过 planner，`set_pending_resume()` 注入用户输入并从根重放。completed 二层 stage 短路复用原 task_id/产物，in_progress stage 从头重入；重放到相同 tool_call_id 时 permission rail 消费 answer（[`agent.py:99`](../../../../../jiuwenswarm/server/runtime/skill_turbo/agent.py#L99)、[`executor.py:1392`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L1392)）。
- checkpoint 只在成功消费 resume 或明确 `SkillTurboNotHandled` 时清；瞬时异常保留最近可用断点，且不 `post_run()` 以免旧内存覆盖磁盘。二次 HITL 继续覆盖新断点（[`skill_turbo_tools.py:516`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_turbo_tools.py#L516)）。

### 6.4 主时序文字稿

```text
DeepAgent 决定调用 skill_acceleration_exec(query)
  -> 工具旁路检查（自定义模板/已有 PPT 编辑）
  -> 从 adapter + ContextVar 收集 request/session/workspace/metadata
  -> 若存在 resume answers：用 session_id + card.id__skill_turbo 读取 resume_ctx
  -> SkillTurboEnvironment 扫描 skill、校验 checksum、装载工具
  -> Planner LLM 路由 skill（>=0.6）并找到 builtin plan root
  -> Executor AST 校验 plan code，受限 import，取得 root PlanNode
  -> 创建隔离 turbo session，绑定 rails/ContextVar/task state
  -> plan.started + pending task snapshot
  -> root.run_stream(inputs)
       -> before_subplan -> task.start/update
       -> node 调 LLM/工具 -> permission/ask/artifact/stream rails
       -> 普通成功：收集 artifact，task.complete
       -> 节点业务失败：fallback DeepAgent；契约满足则 degraded 继续
       -> permission/ask 中断：保存 artifacts + resume_ctx -> AbortError 上抛
  -> executor flush stream，plan.finished + complete，持久化 artifacts，reset ContextVar
  -> 工具把事件写回 parent session，只返回精简 success/result + artifact summary
  -> 中断时外层 after_tool_call 把缓存 TIC 转原生 interaction/paused
  -> 用户回答后再次调用工具，跳 planner，从根重放并跳过 completed stages
```

## 7. 内置 PPT 流水线

[`ppt_gen_root.py`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py) 在 import 时构造单例 `root = PPTGenRootNode()`。实际顺序为：

1. `PipelineInitNode`：P01 检查 Node/pptx 依赖（需要时 npm/playwright 安装），P02 创建独立时间戳输出 workspace。
2. `IntentClassifyNode`：识别附件、文档路径、编辑/notes 意图和已有槽位；路径同时从 files/attachments/LLM 提取并规范化。
3. `DocumentParseNode`：有文档时读取文本/PDF/图片并合并 `doc_raw`，必要时 LLM 推断主题；无文档则 root 显式 skip，仍把该 stage 标 completed。文档声明存在但解析失败会立即停止整个 PPT，避免无依据生成。
4. `RequirementCollectNode`：P21 抽 topic/page_count/style/audience/purpose 等槽位，P22/P23 通过 ask-user 补批量字段/风格，P24 派生参数；non-interactive 路径用 LLM default/fallback 补齐但仍校验必填。
5. `TemplateContextNode`：仅特定 style_mode 读取模板叙事上下文；工具入口当前又对自定义模板请求整体旁路，因此该能力存在但主入口受限。
6. `ContentPlanNode`：P41 规范输入，P42 决定是否快速检索并并行 web search，P43 生成大纲，P44 做结构/占位/研究字段校验和修复，写 outline。
7. `OutlineReviewNode`：interactive 模式展示 preview，用户可接受、编辑或用自然语言要求 LLM 修订；由 ask rail 形成可恢复 HITL。
8. `DeepResearchNode`：按页面并行 worker 搜索、fetch、评分、补搜、引用验证，逐页写研究材料；证据不足时生成明确 no-data/fallback section，不伪造来源。
9. `StylePrepareNode`：加载 preset 或 LLM 生成 custom style，写 style 文件。
10. `ImagePrepareNode`：从研究/本地/AI 图源准备图片、OCR/VQA/实体匹配，验证后写 image map，并清理临时项。
11. `PPTPageGenNode`：按页生成 HTML，支持结构模板/内容模板填充；有大量 DOM、尺寸、overflow、字号、chart mount/label collision、page number、check-layout gates 和 rewrite 修复。page worker 并行，随后 QA fix；模板包路径另有 manifest/select/fill 分支。
12. `PPTExportNode`：执行模板 finalizer/convert，校验 pptx；失败会尝试替代转换路径并保留诊断。
13. `SpeakerNotesNode`：仅 `need_speaker_notes=True`，逐页生成、验证、注入；标注 best-effort，不阻塞最终交付。
14. `DeliveryNode`：检查页文件、构建 summary、调用 send_file；识别 send failure 并返回最终交付状态。

root 的流式 resume 对已 completed stage 走“静默 skip 但仍执行 before/after 回调链”，保证共享 inputs 合并和任务状态一致；真实 in-progress stage 重跑。P3 被固定放在任务列表，故无附件时 UI 仍得到明确完成/跳过状态（[`skill_codes/ppt/ppt_gen_root.py:96`](../../../../../jiuwenswarm/server/runtime/skill_turbo/skill_codes/ppt/ppt_gen_root.py#L96)）。


## 运行时一致性与已知边界

执行器的取消、权限、fallback、恢复和 artifact 一致性约束见上文；跨 Runtime 的统一规则见[失败、并发与一致性总览](03-runtime-session-agent.md#8-失败并发与一致性总览)。

- 当前工具主入口显式旁路自定义模板路径和已有 PPT 选区编辑；底层模板能力不代表入口已经开放。
- 恢复依赖持久化 plan、node 状态与确认 ledger；恢复时仍会重新执行权限检查。
