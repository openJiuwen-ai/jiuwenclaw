# Python Agent 冷路径系统工程性能归因与无语义优化空间分析

> 文档状态：专项分析稿，不修改代码。  
> 分析日期：2026-08-28。  
> 关联方案：`2026-08-28-cold-start-first-model-call-2s-reviewed-design.md`。本文是补充分析，未修改关联方案。  
> 目标边界：服务首次启动后的第一个用户 query，从 AgentServer 接收请求到进入底层大模型 `stream/invoke`；MCP 耗时排除在 2 秒目标之外。  
> 约束：不改变主题流程、业务逻辑、最终提示词、工具集合、权限、记忆、checkpoint 可靠性或异常语义，只讨论系统工程实现的性能优化空间。

---

## 1. 结论

当前冷路径慢，主要不是“Python 天生执行慢”，而是若干本来正确但粒度偏细的系统工程动作被串行放在了首请求关键路径上，并在 root/child、invoke/model-call 等层级重复执行。Python 的动态导入、对象分配、GIL 和异步调度会放大这些问题，但不是秒级耗时的主因。

本次代码和日志给出的直接证据是：

- 最新完整请求 `47ec551a-7fd9-4eda-8003-d8a38fc440fd` 的 child `create_instance` 为 `985 ms`。其中配置阶段 `266 ms`、工具卡构造与注册 `437 ms`，两项合计 `703 ms`，占 `71.4%`。
- 同一请求在 child 之前还创建了 root adapter，并重复经过配置和多模态环境检查，日志时间约 `275 ms`。root 虽跳过 live DeepAgent，仍支付了相当一部分通用初始化成本。
- `stage2(mcp) → stage3(pre_llm)` 为 `2825 ms`；已记录的全部 model rails 合计约 `234 ms`。因此约 `2591 ms` 尚未被当前 rail 统计覆盖，最大风险区在 invoke preparation、上下文初始化/恢复、工具 schema 列举、model context window、checkpoint、prompt attachment 和模型请求日志封装，而不是某个已测 rail 本身。
- 同等规模的 23 个 prompt section、约 6.6 万字符，单纯排序、渲染和 `join` 的本地微基准均值约 `0.03 ms`。简化形态的 JSON 序列化约 `0.25 ms`、深拷贝约 `0.15 ms`。所以“提示词拼接耗时数秒”的说法不成立；真正可能慢的是提示词内容产生之前的文件扫描、工具 schema 生成、上下文处理、持久化和日志 I/O。
- `SkillUseRail.before_invoke` 每次都会刷新技能目录：即使 skill 内容命中进程缓存，仍遍历目录并执行 `exists/is_dir/resolve/stat`；在 Windows、杀毒软件、网络盘或技能目录较多时，这类元数据 I/O 容易达到几十到上百毫秒。
- `JiuSwarmStreamEventRail.before_model_call` 会在第一次 LLM 调用前同步执行 `save_contexts` 与 `post_agent_execute`，以保证进程在首模型调用前崩溃时不丢用户消息。当前日志中该 rail 为 `78 ms`。这不是多余逻辑，但说明可靠性持久化位于首调用串行路径。
- `AbilityManager.list_tool_info()` 每次重新创建全部 `ToolInfo`；agent card 可能重新生成 Pydantic JSON Schema；MCP 工具还会访问全局资源管理器，并把 MCP 工具写回 `_tools`。这属于可缓存、可版本化的目录装配，不是 Python 字符串问题。
- OpenJiuwen callback framework 按优先级逐个 `await` 回调。顺序语义是合理的，但意味着每个 rail 的目录扫描、checkpoint、深拷贝或日志成本会线性累加，不能靠 `async` 关键字自动并行或消失。

因此，最优方向不是换语言、使用 Cython、删除功能或并行执行全部 rails，而是建立四个稳定边界：

1. 配置只解析一次，root 和 child 共享同一代不可变快照。
2. 工具对象的执行绑定与模型可见的工具目录/schema 分离；目录按 generation 缓存，请求 MCP 仅做 delta overlay。
3. 技能目录、静态 prompt section 和上下文派生结果按内容版本复用，首请求只处理变更量。
4. 把 invoke-prep 与 model-input-finalize 细分计时，在证实最大耗时点后优化；不能用推测代替埋点。

这些优化不需要改变 Python，也不需要改变最终模型输入。按当前证据，它们比迁移语言具有更高收益、更小复杂度和更低回归风险。

---

## 2. 证据边界与分析方法

### 2.1 已验证的请求时间线

最新详细日志 `logs/service_default/.logs/agent_server.log` 中，请求 `47ec551a-7fd9-4eda-8003-d8a38fc440fd` 的关键时间为：

```text
09:00:04.006  root create 开始
09:00:04.281  root create 完成                         约 275 ms
09:00:04.485  child create 进入主要配置处理
09:00:05.485  child create_instance 完成               985 ms
09:00:05.437  stage1 init
09:00:05.547  start_interaction 完成
09:00:06.320  请求级 MCP 完成
09:00:06.332  stage2 mcp
09:00:06.978  runtime invoke 工具相关日志
09:00:08.954  runtime agent tool filter/swap
09:00:09.153  model rail timing 输出
09:00:09.157  stage3 pre_llm，进入底层模型调用
```

其中：

```text
create_instance total                  985 ms
  config                               266 ms
  tool_cards                           437 ms
  rails                                 63 ms
  deep_agent                            47 ms
  ensure_initialized                    62 ms
  workspace_seed                        31 ms
  config_mcp                            32 ms
  packages                              47 ms

stage2 → stage3                       2825 ms
  已记录 model rails 合计               234 ms
  未被 rail 统计覆盖的下界             2591 ms
```

这里的 `2591 ms` 不是某一个函数的已证明耗时，而是“当前观测缺口”。本文对缺口内模块只给出代码候选和补充埋点方案，不把候选直接写成根因。

### 2.2 原始 2 秒目标仍未满足

早期完整样本 `549df18e-0177-4b49-9ce2-8f221d4a904e`：

```text
recv → pre_llm                         4200 ms
可被算作 MCP 的最大可能区间             406 ms
非 MCP 下界                           3794 ms
目标                                  2000 ms
至少需稳定降低                         1794 ms
```

因此不能只优化 `create_instance` 的 985 ms。即使把它优化一半，如果 model-call 前的 2.8 秒观测缺口不解决，端到端仍达不到 2 秒。

### 2.3 微基准的用途与限制

使用与日志中 prompt 规模相当的输入做纯 Python 微基准：

```text
prompt_chars=66073, sections=23
section sort/render/join mean           0.0302 ms
json.dumps simplified payload mean      0.2505 ms
copy.deepcopy simplified payload mean   0.1484 ms
```

这只能证明“纯拼接和简化对象操作不可能解释数秒”，不能证明真实请求的 Pydantic dump、工具 schema、上下文压缩、同步日志写盘也只有亚毫秒。真实对象图可能更深，且磁盘 handler 可能阻塞。因此后续必须在真实链路做 span，而不是把微基准当作生产耗时替代品。

---

## 3. 是 Python 语言慢，还是模块实现慢

### 3.1 Python 语言层真实存在的成本

Python 在该场景确实有以下成本：

- 首次 `import` 会执行模块和 package `__init__.py` 的顶层代码。实测首次导入 OpenJiuwen prompt builder 会连带注册 connector/parser 等并出现秒级墙钟时间。这是 Python import 语义与包副作用共同造成的。
- 大量小对象、Pydantic model、字典深拷贝和 JSON Schema 构造受解释器与内存分配器影响，比静态语言更贵。
- CPU 密集型 Python 代码受 GIL 限制；把多个 CPU 型 rail 放进同一事件循环也不会自然并行。
- `asyncio` 每次 coroutine、callback、lock 和 task 切换都有固定开销。

但这些只是放大器，不能解释当前主要数字：

- `prompt_builder.build()` 的核心是约 23 项排序和字符串连接，实测远低于 1 ms。
- 当前高耗时模块包含文件系统元数据 I/O、全局资源管理、checkpoint、配置重复刷新和 schema 重建；换成其他语言后这些工作仍然存在。
- `create_instance` 已有分段数据显示，`deep_agent` 对象构造本身仅 `47 ms`，`ensure_initialized` 也只有 `62 ms`。真正更重的是外围系统工程工作。

结论是：这是“Python 执行了过多、过重复、过晚的系统工程工作”，而不是“Python 无法在 2 秒内创建 Agent”。

### 3.2 哪些属于实现或架构问题

当前主要是五种实现形态叠加：

1. **生命周期边界过粗**：root 和 child 走相似的配置/环境刷新路径，而 root 随后又跳过 live DeepAgent。
2. **静态元数据与运行态对象耦合**：为注册可执行工具对象，同时反复生成模型工具目录和 schema。
3. **一致性验证采用全量扫描**：技能、配置、prompt attachment 通过每次遍历或 stat 来确认未变化。
4. **可靠性操作处于严格串行路径**：首次模型调用前 checkpoint 必须完成。
5. **观测边界不完整**：rail 有计时，但 invoke preparation、context window 和工具列举的内部时间未形成同一请求的完整 breakdown。

这些都可以在不改变业务语义的前提下优化。

---

## 4. 模块级代码分析

### 4.1 Agent adapter：root/child 重复配置与环境刷新

主要代码：`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`

`create_instance()` 在构建 root/child 时依次处理：

- runtime dotenv/config；
- instance config 合并与拷贝；
- memory/enterprise 配置；
- 多模态 config/env 刷新；
- 模型与 A2X；
- tool cards；
- rails；
- DeepAgent 与 rail 初始化；
- workspace、MCP config、packages。

root 的特殊分支会跳过 live DeepAgent，但该判断发生在若干配置、环境和目录准备之后。日志中 root 与 child 连续出现相同的多模态配置检查，说明同一个请求内存在重复工作。

`common/config.py` 已有 YAML parse cache 和 namespace/overlay TTL cache，因此 `config=266 ms` 不能简单归因于一次 YAML 解析。更可能的组合是：dotenv/config 调用、config 深拷贝、环境 overlay 读取、多模态刷新、文件系统布局和同步日志。当前 `config` span 仍过粗，需要继续拆分。

无语义优化空间：

- 在请求解析后生成 `ResolvedAgentConfigSnapshot`，包含 config generation、tip/env generation、多模态派生配置和功能开关。
- root 与 child 只读同一快照；只有会话私有字段在 child overlay。
- 快照的失效条件严格绑定现有配置热更新 generation，不用 TTL 猜测。
- root 快速分支应在确定不创建 live DeepAgent 后，只完成 root 真正需要的字段，不执行 child 专属派生。
- 保留现有慢路径作为 fallback；快照构建失败或 generation 不匹配时走当前逻辑。

该优化不改变配置值，只改变相同派生结果被计算的次数。

### 4.2 local env/multimodal：重复构造有效环境视图

主要代码：

- `jiuwenswarm/common/local_env_config.py`
- `jiuwenswarm/server/runtime/agent_adapter/multimodal_config.py`

`effective_tip()` 会复制并合并 active/staged 字典；未绑定 overlay 的 `read_env()` 可能反复构造有效视图。多模态检查本身大多只是 dict/env 读取，但在 root/child、多种模态分组中重复调用，会产生很多小对象、日志和函数边界。

无语义优化空间：

- 每次配置 generation 构造一个不可变 `EnvSnapshot`，一次合并，多次 O(1) 读取。
- 多模态派生结果作为 config snapshot 的字段，不在每个 agent 实例重算。
- 日志只在派生结果或 generation 改变时记录；请求级 DEBUG 只记录摘要与 snapshot id。

这里不建议使用全局可变 dict 直接共享；应使用冻结映射或复制后只读，防止会话间污染。

### 4.3 `_get_tool_cards`：当前已证实的最大 create-instance 单项

主要代码：`interface_deep.py::_get_tool_cards()` 与 `jiuwenswarm/common/tool_ownership.py`

该阶段为 `437 ms`，主要工作包括：

- 获取 per-agent web content cache；
- 构建并注册 web、multimodal、skill、retrieval、Symphony、ACP、deepresearch 等工具；
- 逐个经过 `Runner.resource_mgr` 的全局资源注册；
- 对共享工具执行全局查找、thread lock、二次查找和幂等 add；
- 对 agent-owned 工具改写 id 并 `refresh=True` 注册；
- 中间还存在再次 `get_config()` 的调用。

`AgentCacheRegistry.get_cache()` 使用单一 `asyncio.Lock`。首次单请求竞争通常不重，但多 agent 同时预热时会被全局串行化。`tool_ownership.ensure_tool_registered()` 也有进程级 `threading.Lock`；正确性没有问题，但逐工具注册会放大锁和全局字典访问成本。

无语义优化空间：

- 引入 `ToolCatalogSnapshot`，缓存模型侧只读元数据：name、description、input schema、顺序、owner policy、来源 generation。
- 执行工具实例仍按现有 ownership 规则绑定，绝不跨会话共享 stateful tool。
- 资源管理器增加等价的 batch register 路径：一次锁定、一次冲突检查、一次提交；错误时整体回退逐项注册。最终 registry 内容必须完全一致。
- stateless 工具使用 process generation 幂等注册；后续 child 只引用已注册实例。
- agent-owned 工具仍创建会话实例，但 schema 引用不可变 snapshot，不重复 model schema 生成。
- `_get_tool_cards()` 接收已解析 config snapshot，禁止在内部再次读取同一代配置。
- `AgentCacheRegistry` 将“查询已有 cache”设为无 await 快路径，仅创建/淘汰时进入锁；保持相同容量和 LRU 语义。

不建议直接复用整个 tool instance 列表，因为其中存在 stateful、agent-owned 工具，会引入跨会话状态泄漏。

### 4.4 Rail 构建与初始化：有优化空间，但不是第一优先级

日志中：

```text
rails build             63 ms
deep_agent              47 ms
ensure_initialized      62 ms
合计                   172 ms
```

`_instantiate_rails()` 创建约 20 个 rail，并可能加载 hooks config、Observability、AgentTraceBindingRail 等 lazy import。OpenJiuwen `_ensure_initialized()` 按优先级逐个初始化和注册 rail callback。

这些步骤涉及可变状态和顺序依赖，不能安全地简单 `asyncio.gather()`。例如 SkillUseRail 要在文件系统工具初始化后判断工具 ownership；多个 rail 也可能修改同一个 prompt builder。

无语义优化空间：

- 进程 ready 前导入冷路径上必需模块，消除首 query lazy import；同时减少 package `__init__` 的无关注册副作用。
- 编译 rail callback dispatch plan：在 rail 集合不变时缓存 event→ordered callbacks，不在每个实例重复反射/排序。
- rail 的纯静态构造参数从 blueprint 读取；rail 实例和会话状态仍独立。
- callback 注册提供 batch API，保持原优先级和顺序。

预期收益有限，应该在 config/tool catalog 和未解释的 2.6 秒之后处理。

### 4.5 SkillUseRail：缓存了内容，却仍在每次 invoke 扫目录

主要代码：`.venv/Lib/site-packages/openjiuwen/harness/rails/skills/skill_use_rail.py`

调用链：

```text
before_invoke
  → refresh_skill_prompt
    → _prepare_skills
      → _refresh_skills_incrementally
        → root.exists/is_dir
        → sorted(root.iterdir())
        → item.is_dir
        → SKILL.md.exists
        → item.resolve
        → SKILL.md.stat().st_mtime
        → process cache hit or frontmatter load
```

当前实现已经有 `_PROCESS_SKILL_INDEX`，能避免 cache hit 时重新读取整个 `SKILL.md`；但仍以全目录扫描和逐文件 mtime 作为一致性判断。最新 rail 计时中 `SkillUseRail=93 ms`，与该实现特征一致。

无语义优化空间：

- 服务启动或明确的 skill install/uninstall/config reload 事件生成 `SkillCatalogGeneration`。
- 每个 generation 保存排序后的目录项、mtime、frontmatter 和最终 skills section。
- `before_invoke` 只比较 generation；相同则 O(1) 引用快照，不做目录扫描。
- 为防止外部绕过管理接口直接修改文件，保留低频后台一致性扫描；发现变化后原子发布新 generation。首次请求不等待该扫描，前提是服务 ready 前已经完成一次扫描。
- 如果必须对“任意时刻手工改文件立即可见”保持严格语义，可使用 OS 文件事件驱动 invalidation，并保留定期校验。事件丢失时回退扫描。
- evolution text 按 `(skill_name, evolution_store_version)` 缓存；store 无变更时不逐 skill await。

最终 skills 列表、顺序、描述和演进文本必须通过 digest 与旧路径逐字节比较。

### 4.6 AbilityManager：每次 invoke 重建 ToolInfo，并混合 MCP 副作用

主要代码：`.venv/Lib/site-packages/openjiuwen/core/single_agent/ability_manager.py::list_tool_info()`

当前实现每次：

- 遍历 `_tools/_workflows/_agents`；
- 为每一项新建 `ToolInfo`；
- 对 Pydantic agent input model 调 `model_json_schema()`；
- 对每个 MCP server await `Runner.resource_mgr.get_mcp_tool_infos()`；
- 改写 MCP `ToolInfo.name`；
- 把 MCP tool card 写入 `_tools`。

这把“只读列举”“schema 构造”“外部资源读取”“内部目录写入”混合在一个接口中，导致无法安全缓存，也使首调用耗时和锁等待难以分辨。

无语义优化空间：

- 将接口内部拆为 `base_catalog_snapshot()` 和 `mcp_delta_snapshot(request_generation)`，对外仍返回相同 `List[ToolInfo]`。
- 本地工具/agent/workflow schema 在注册或 config generation 变化时生成一次。
- Pydantic `model_json_schema()` 按 class identity/version 缓存。
- MCP delta 仍遵循当前请求隔离和 allowlist；仅在 MCP generation 变化时重取。
- 不再在只读列举过程中原地修改资源管理器返回的 `ToolInfo`；基于 immutable schema 创建名字 overlay，避免共享对象污染。
- 最终列表必须保持 paid/free search 排序、工具名、description、parameters、MCP 前缀和 allowlist 完全一致。

这是 model-call 前 2.6 秒观测缺口中的高优先级候选，但必须先给该函数单独加 span 才能确认生产收益。

### 4.7 Prompt builder：拼接不是问题，内容生产与重复预览才是问题

主要代码：

- `.venv/Lib/site-packages/openjiuwen/core/single_agent/prompts/builder.py`
- `.venv/Lib/site-packages/openjiuwen/core/single_agent/agents/react_agent.py`

`SystemPromptBuilder.build()` 只是获取 section、排序、render 和 join。6.6 万字符规模约 `0.03 ms`。

真正值得注意的是 ReAct 调用前存在两个阶段：

1. `_build_preview_messages()` 对 `context.get_messages()` 深拷贝，并构建一次 prompt，供 BEFORE_MODEL_CALL rails 查看。
2. `_railed_model_call()` 在 rails 修改完成后再次构建最终 prompt，然后执行 `get_context_window()`。

不能直接删除 preview：rail 可能依赖 `ctx.inputs.messages/tools`。无语义优化方式是：

- prompt builder 对 section 建立 version/digest；未变化时复用已经生成的不可变字符串。
- preview messages 使用 copy-on-write 视图；只有 rail 真正修改时才 materialize 深拷贝。必须通过 rail 兼容性审计确保列表和消息的可见行为一致。
- 静态 sections 预渲染，动态 sections 只替换变化项；最终字符串逐字节一致。
- 对同一 prompt generation，preview 与 final 可共享静态 section 结果；rails 变更 generation 后只重建一次。

这里的收益主要来自减少上游 section 生成、消息对象复制和 schema materialization，不是优化 `"\n".join()`。

### 4.8 ModelContext.get_context_window：最大的未测候选之一

主要代码：`.venv/Lib/site-packages/openjiuwen/core/context_engine/context/context.py`

该函数：

- 获取 `_processor_lock`；
- 构造 window；
- 顺序调用所有 context processor 的 `trigger_get_context_window()`；
- processor 命中时生成 compression state、执行 processor、再次生成 state；
- 顺序执行 prompt/window mutator；
- 两次 validate/fix；
- 统计 window。

即使第一轮没有压缩，每个 processor trigger 仍会执行；mutator 可能处理大 prompt、attachments 和工具 schema。锁还可能等待同会话其他 context 操作。当前没有同 request_id 的 processor/mutator/lock-wait 明细，所以不能断言它占 2.6 秒中的多少。

无语义优化空间：

- 记录 `processor_lock_wait`、`window_select`、每个 `trigger`、每个 `on_get`、每个 mutator、validate、statistic 的独立耗时。
- 第一轮上下文建立“已知不触发 processor”的可证明 fast path：只有 processors 均声明基于 generation 的 `may_trigger=False` 才跳过回调；否则走旧路径。
- token count、message digest、tool schema digest 增量维护，避免对未变化前缀重算。
- prompt attachment manager 按 attachment generation 生成 immutable overlay；无变化时直接复用。
- 保持 processor 顺序、异常吞吐策略、compression state 和最终 ContextWindow 完全一致。

任何跳过压缩或减少上下文的方案都不满足本文约束，不能采用。

### 4.9 StreamEventRail：checkpoint 是可靠性成本，不能直接删除

`before_model_call()` 的主要动作包括：

- pause/abort 检查；
- CWD state rebind；
- 给工具 schema 注入 `call_goal`，要求 deepcopy；
- 修复不完整 tool context；
- 第一次模型调用前 early checkpoint：`save_contexts()` + `post_agent_execute()`。

最新日志 `JiuSwarmStreamEventRail=78 ms`。该 checkpoint 明确用于防止进程在 `post_run` 前崩溃导致用户消息丢失，因此不能为了 TTFT 删除或改成不等待的后台任务，否则稳定性指标下降。

可行的无降级优化：

- checkpoint 使用增量/append-only 写入，只持久化新增用户消息、context delta 和 agent state digest；提交成功语义不变。
- 在请求接收后尽早启动持久化，但在模型调用前仍 await 同一个 durability future。这样只做合法重叠，不弱化 durability barrier。
- checkpointer 支持 batch/transaction 内一次 fsync，而不是多个细碎写操作；失败路径和重试保持一致。
- `call_goal` schema 注入按 tool catalog digest 缓存不可变副本，工具目录未变化时不逐工具 deepcopy。

### 4.10 日志与异常路径：通常不是主因，但会放大冷启动

`llm_io_trace.py` 在 DEBUG 开启时会对完整 messages/tools 做 model dump/JSON dump，并按 8192 字符分块写日志。真实 prompt 为约 6.6 万字符、21 个工具，若 handler 同步写磁盘或被杀毒软件拦截，可能造成明显墙钟耗时。

无语义优化空间：

- LLM 完整 envelope 使用独立显式开关，不与普通 DEBUG 绑定。
- 默认记录 request id、message/tool count、chars、digest；需要审计时才记录完整 payload。
- 完整 trace 通过有界队列异步写入；队列满时不能静默丢审计数据，应按配置选择同步 fallback 或只关闭非必需 trace。
- 计时必须覆盖 model/Pydantic dump、JSON encode、handler enqueue/write，区分 CPU 与 I/O。

如果完整日志是合规必需，则保留同步 durability 语义，只能优化序列化复用和批量写，不能降低记录完整性。

---

## 5. 建议的系统工程架构

建议增加四个“深模块”，把复杂缓存和一致性规则封装在窄接口后面，而不是在现有函数中分散增加多个全局 cache。

### 5.1 `ResolvedAgentConfigProvider`

```text
snapshot(config_key, env_generation) -> ResolvedAgentConfigSnapshot
invalidate(new_generation)
```

内部负责 YAML/env/tip/multimodal 的解析和派生；输出冻结、可 hash、带 generation。root、child、tool factory、rail factory 只消费快照。

### 5.2 `ToolCatalogProvider`

```text
base_snapshot(agent_blueprint_generation) -> ToolCatalogSnapshot
overlay_mcp(base, request_mcp_generation) -> ToolCatalogView
bind_runtime_tools(view, owner_id, session) -> RuntimeToolBindings
```

明确区分：

- 模型只读元数据；
- 进程共享 stateless 实例；
- 会话私有 stateful 实例；
- 请求级 MCP delta。

这样既能缓存 schema，又不会共享会话可变状态。

### 5.3 `SkillCatalogProvider`

```text
snapshot(skill_roots, enabled_set, generation) -> SkillCatalogSnapshot
```

内部管理文件事件、mtime 校验、frontmatter cache、evolution version 和最终 section digest。SkillUseRail 只把 snapshot 绑定到当前 session baseline，不自行全盘扫描。

### 5.4 `ModelInputAssembler`

```text
assemble(prompt_blueprint, runtime_sections, context_delta, tool_catalog_view)
  -> ModelCallEnvelope(messages, tools, digest)
```

内部封装静态 prompt 缓存、copy-on-write preview、context processor/mutator、tool schema overlay 和最终一致性 digest。它不是重写业务流程，而是给“最终模型输入装配”一个可观测且可校验的边界。

四个模块都必须遵循：

- immutable snapshot；
- generation 驱动失效，不用不可靠的时间猜测；
- single-flight，避免并发首请求重复构建；
- 构建失败回退旧路径；
- 快慢路径输出 digest 对比；
- 不把 session/request 可变状态放进全局缓存。

---

## 6. 优化优先级与可实现收益

### P0：先封闭 2.6 秒观测缺口

必须新增同一 request_id 下的 span：

```text
invoke.lifecycle.before_invoke
invoke.load_interruption_state
invoke.init_context
invoke.render_system_prompt
invoke.update_skill_section
invoke.list_tool_info.local
invoke.list_tool_info.mcp
invoke.admit_user_message
model.preview.deepcopy
model.preview.prompt_build
model.rails
model.final_prompt_build
model.context.lock_wait
model.context.processor.<name>.trigger/on_get
model.context.mutator.<name>
model.context.validate/stat
model.kv_cache_hook
model.llm_trace.serialize/write
model.client_enter
```

验收要求：`stage2→stage3` 的子 span 覆盖率应达到 95% 以上，未归属时间小于 50 ms。没有这一步，任何“预计节省 1 秒”的说法都不可靠。

### P1：消除已证实的重复与重建

1. config/env/multimodal snapshot，root/child 同代复用。
2. tool catalog/schema snapshot，执行绑定增量化。
3. batch/idempotent resource registration。
4. skill catalog generation，首 invoke O(1) 快路径。
5. `list_tool_info` 按 generation 缓存，本地目录与 MCP delta 分开。

基于现有测量，可保守期待：

- root/child config 重复：约 `200–400 ms` 可优化空间；
- tool_cards `437 ms`：约 `150–350 ms` 可优化空间，需二级 span 证实；
- SkillUseRail `93 ms`：稳定降到个位数毫秒；
- rail 构建/注册：约 `30–80 ms` 可优化空间。

这些区间不是承诺值，而是实施排序参考。

### P2：优化模型输入最终装配

根据 P0 结果，优先处理实际最大项：

- context lock/processor/mutator 的增量 fast path；
- early checkpoint 的增量写与提前重叠；
- tool schema 与 `call_goal` 注入副本缓存；
- preview/final prompt 的 versioned materialization；
- LLM trace 序列化复用与写入策略。

该阶段决定能否从约 3.8 秒非 MCP 下界降到 2 秒以内。未经测量，不应预先认定 context window 或日志是唯一根因。

### P3：低收益解释器级微优化

仅在 P1/P2 后考虑：

- 减少临时 dict/list；
- `slots`/轻量 dataclass；
- 局部避免重复 Pydantic validation；
- 调整 Python 版本或解释器启动参数。

这些通常只能带来个位数到几十毫秒，不能作为 2 秒方案主线。当前不建议迁移 Rust/Go/C++ 或引入 Cython：它会显著增加构建、调试、跨平台和故障恢复复杂度，却不消除 I/O、checkpoint、资源注册和重复生命周期问题。

---

## 7. 不可采用的“优化”

以下做法可能快，但违反“性能、效果、稳定性不下降”的约束：

- 删除工具、缩短技能列表、裁剪 system prompt 或降低工具 schema 精度。
- 跳过 memory、context processor、权限、checkpoint 或异常恢复。
- 不等待 early checkpoint，直接把它变成 fire-and-forget。
- 跨 session 复用 live DeepAgent、stateful tool、rail 或 mutable prompt builder。
- 把所有 rails 并行执行，破坏优先级和共享状态修改顺序。
- 用固定 TTL 缓存配置/技能并容忍短时间不一致。
- 为了计时好看，把本地工作错误地记入“排除 MCP”区间。
- 只优化字符串 join、换 JSON 库或升级硬件，而不解决重复工作。

---

## 8. 非回归验证设计

### 8.1 正确性等价

每个快路径在 shadow 阶段同时计算旧路径结果，并比较：

- resolved config canonical JSON digest；
- prompt 最终 UTF-8 字节 digest；
- ToolInfo 有序列表 canonical digest；
- context messages 有序 digest；
- runtime tool ownership/registry snapshot；
- checkpoint state digest；
- rail callback 顺序。

任何不一致自动使用旧路径并记录差异，不把错误输入发送给模型。

### 8.2 性能门槛

主指标：

```text
T_non_mcp = T_model_client_enter - T_query_received - Σ exact MCP spans
```

建议验收：

- 冷进程首请求：p50 ≤ 1.5 s，p95 ≤ 2.0 s；
- 预热失效/回退路径仍可用，不要求退化路径满足 2 秒，但要单独告警；
- MCP 单独统计，不允许混入本地优化收益；
- 采用至少 30 次真正冷进程样本，覆盖 Windows Defender 开/关、SSD 抖动、技能目录规模和不同工具配置。

### 8.3 效果与稳定性门槛

- 最终 prompt、tools、messages digest 与旧路径 100% 一致。
- 工具调用成功率、权限拒绝行为、memory 命中、context compression、恢复行为不得下降。
- 多 session 并发和 config/skill 热更新压测无状态串扰。
- 缓存构建异常、文件事件丢失、generation 变化、MCP 变化时均能原子回退。
- checkpoint 崩溃恢复测试保持现有 durability 保证。

---

## 9. 推荐实施顺序

1. **观测补齐**：先把 stage2→stage3 覆盖到 95%，连续采集 30 个冷启动样本。
2. **配置快照**：合并 root/child 同代配置、env、多模态派生，先拿到低风险的数百毫秒收益。
3. **工具目录分层**：ToolCatalogSnapshot + runtime binding + MCP delta，解决 437 ms 已证实热点和 `list_tool_info` 候选热点。
4. **技能 generation**：把首 invoke 全量 stat 扫描改为 ready 前扫描和事件驱动失效。
5. **模型输入装配**：按新 span 的最大项逐一处理 context、checkpoint、schema deepcopy、attachment 和 trace。
6. **rail/导入微优化**：预导入、callback plan、batch registration，作为尾部收敛。
7. **shadow 与灰度**：先只比较 digest，不启用快路径；一致后按进程灰度，保留强制旧路径开关。

目标预算建议不是卡在 2000 ms，而是：

```text
query dispatch + session lookup             50 ms
config/session delta binding               150 ms
runtime tool binding                       250 ms
invoke/context preparation                 500 ms
rails + durable checkpoint                 300 ms
model input finalization + trace            250 ms
event-loop/OS 抖动保护                      100 ms
------------------------------------------------
非 MCP 内部设计预算                       1600 ms
对 2 秒 SLO 的保护余量                     400 ms
```

预算不通过删功能获得，而通过把静态工作前移到 ready、把重复工作变成版本化快照、把请求路径变成增量绑定获得。

---

## 10. 最终判断

从现有代码和日志看，Python 足以支撑该服务的非 MCP 冷启动首模型调用 2 秒目标。当前障碍是生命周期和缓存边界，而不是解释器吞吐：

- Agent/DeepAgent 本体构造只占几十毫秒；
- prompt 字符串拼接是微秒级到亚毫秒级；
- 已证实热点是配置重复处理和工具目录/资源注册；
- 最大未知是 invoke preparation 到 model client enter 之间约 2.6 秒的观测缺口；
- 技能目录扫描、工具 schema 重建、context window 和 durable checkpoint 都有不改变最终业务结果的系统工程优化空间。

综合性能、效果、稳定性和设计复杂度，推荐采用“**不可变 generation 快照 + 执行对象增量绑定 + 精确上下文装配观测 + 强一致回退**”的方案。它不改变主题流程，不减少任何模型输入或功能能力，也不引入跨会话共享可变状态，是当前达到 2 秒目标的最简洁、低风险且综合收益最高的路径。
