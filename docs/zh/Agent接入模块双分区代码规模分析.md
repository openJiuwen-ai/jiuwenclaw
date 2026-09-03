# Agent 接入模块双分区代码规模分析

> 统计日期：2026-08-31  
> 统计范围：Agent 接入模块的两部分代码；不含 Gateway、Channel、通用 `common/`、测试代码及已规划但尚未创建的文件。

## 结论摘要

按“接入控制面与持久会话”和“运行时实例与 SDK 适配”划分后，范围内共有 **50** 个 Python 文件、**55,520** 物理行。其中第一部分为 **19,535** 行（35.2%），第二部分为 **35,985** 行（64.8%）。

第二部分的规模主要集中在 DeepAgent adapter：`interface_deep.py` 单文件 17,795 行，占第二部分 49.5%、全部范围 32.1%。因此两人看护不应按行数机械均分：第一部分承担更广的接口、会话和工作空间兼容责任；第二部分承担更深的运行时生命周期和 Agent SDK 风险。

## 统计口径

- **物理行数**：文件中的全部文本行，包含空行、注释和文档字符串；用于衡量维护规模和阅读负担。
- **非空非单行注释行**：去除空行和以 `#` 开头的单行注释后的行数；用于近似比较实现密度。多行字符串和文档字符串仍计入此列。
- 目录按递归方式纳入其中全部 `.py` 文件；空的 `__init__.py` 计为 0 行。
- `jiuwenswarm/server/runtime/prewarm.py` 在当前工作树中不存在。现有预热逻辑位于 `agent_warm_pool.py`、`agent_manager.py` 以及 `handlers/bootstrap.py`；若后续抽出该文件，应归入第二部分。

## 双分区范围

### 第一部分：接入控制面与持久会话

负责请求业务编排、Agent 定义和配置同步、会话/项目/工作空间的持久状态，以及 fork、rewind、restore 等会话操作。

### 第二部分：运行时实例与 SDK 适配

负责实例与租户路由、预热、热更新、SDK 门面、Deep/Code Agent adapter，以及活跃会话的队列、缓存和任务生命周期。

## 汇总对比

| 部分 | 文件数 | 物理行数 | 非空非单行注释行 | 物理行占比 |
| --- | ---: | ---: | ---: | ---: |
| 第一部分：接入控制面与持久会话 | 27 | 19,535 | 16,548 | 35.2% |
| 第二部分：运行时实例与 SDK 适配 | 23 | 35,985 | 31,336 | 64.8% |
| 合计 | 50 | 55,520 | 47,884 | 100.0% |

第一部分的 27 个文件比第二部分多 4 个，但总规模少 16,450 行。它的复杂度分散在业务 handler、会话元数据、历史、项目和 Git 状态等多个业务域；第二部分则高度集中于 adapter 实现。

## 第一部分：接入控制面与持久会话

### 分组汇总

| 分组 | 文件数 | 物理行数 | 非空非单行注释行 | 主要责任 |
| --- | ---: | ---: | ---: | --- |
| 业务处理器 | 15 | 8,338 | 7,144 | `ReqMethod` 业务处理、请求编排和响应组织 |
| Agent 定义控制 | 1 | 521 | 412 | Agent 定义文件 CRUD |
| 配置同步控制 | 1 | 460 | 393 | 同步请求校验、配置合成和规范化 |
| 持久会话 | 9 | 8,862 | 7,513 | history、metadata、project、Git 和 work mode |
| 会话业务操作 | 1 | 1,354 | 1,086 | fork、rewind、compact、restore、redo |
| **小计** | **27** | **19,535** | **16,548** |  |

### 文件明细

| 分组 | 文件 | 物理行数 | 非空非单行注释行 |
| --- | --- | ---: | ---: |
| 业务处理器 | `server/handlers/__init__.py` | 44 | 37 |
| 业务处理器 | `server/handlers/_default.py` | 832 | 678 |
| 业务处理器 | `server/handlers/_shared.py` | 476 | 384 |
| 业务处理器 | `server/handlers/agents.py` | 412 | 350 |
| 业务处理器 | `server/handlers/bootstrap.py` | 590 | 505 |
| 业务处理器 | `server/handlers/chat.py` | 375 | 316 |
| 业务处理器 | `server/handlers/commands.py` | 1,113 | 968 |
| 业务处理器 | `server/handlers/extensions.py` | 474 | 405 |
| 业务处理器 | `server/handlers/mcp.py` | 565 | 510 |
| 业务处理器 | `server/handlers/ops.py` | 312 | 269 |
| 业务处理器 | `server/handlers/permissions.py` | 100 | 72 |
| 业务处理器 | `server/handlers/sandbox.py` | 831 | 710 |
| 业务处理器 | `server/handlers/schedule.py` | 172 | 136 |
| 业务处理器 | `server/handlers/session.py` | 882 | 769 |
| 业务处理器 | `server/handlers/team.py` | 1,160 | 1,035 |
| Agent 定义控制 | `server/runtime/agent_config_service.py` | 521 | 412 |
| 配置同步控制 | `server/runtime/sync_agents_configs.py` | 460 | 393 |
| 持久会话 | `server/runtime/session/session_history.py` | 1,243 | 1,029 |
| 持久会话 | `server/runtime/session/session_metadata.py` | 1,690 | 1,394 |
| 持久会话 | `server/runtime/session/session_rename.py` | 69 | 53 |
| 持久会话 | `server/runtime/session/project_store.py` | 1,121 | 882 |
| 持久会话 | `server/runtime/session/project_git.py` | 2,192 | 1,986 |
| 持久会话 | `server/runtime/session/git_diff_status.py` | 1,058 | 908 |
| 持久会话 | `server/runtime/session/git_diff_watcher.py` | 1,310 | 1,116 |
| 持久会话 | `server/runtime/session/work_mode.py` | 179 | 145 |
| 持久会话 | `server/runtime/session/__init__.py` | 0 | 0 |
| 会话业务操作 | `agents/harness/common/session_ops_service.py` | 1,354 | 1,086 |

第一部分中，`handlers/` 与持久会话分别占 42.7% 和 45.4%。因此该看护人需要同时覆盖“请求语义正确”和“重启后状态可恢复”两种质量目标，不能只按接口功能验收。

## 第二部分：运行时实例与 SDK 适配

### 分组汇总

| 分组 | 文件数 | 物理行数 | 非空非单行注释行 | 主要责任 |
| --- | ---: | ---: | ---: | --- |
| 实例与租户路由 | 2 | 2,838 | 2,537 | Agent 创建/销毁、按租户复用和淘汰 |
| 预热池 | 1 | 900 | 821 | warm pool、预热声明和会话命中 |
| 热更新结果 | 1 | 390 | 330 | reload 结果模型和聚合 |
| SDK 门面与 Agent adapter | 15 | 30,587 | 26,575 | `JiuWenSwarm` 门面、协议、Deep/Code adapter 与团队辅助逻辑 |
| 活跃会话运行时 | 4 | 1,270 | 1,073 | 会话任务队列、权限响应账本、KV cache 生命周期 |
| **小计** | **23** | **35,985** | **31,336** |  |

### 文件明细

| 分组 | 文件 | 物理行数 | 非空非单行注释行 |
| --- | --- | ---: | ---: |
| 实例与租户路由 | `server/runtime/agent_manager.py` | 1,683 | 1,498 |
| 实例与租户路由 | `server/runtime/tenant_agent_pool.py` | 1,155 | 1,039 |
| 预热池 | `server/runtime/agent_warm_pool.py` | 900 | 821 |
| 热更新结果 | `server/runtime/reload_result.py` | 390 | 330 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/__init__.py` | 0 | 0 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/agent_adapters.py` | 199 | 156 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/code_agent_rail.py` | 434 | 349 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/compact_partial_prompts.py` | 201 | 194 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/evolution_helpers.py` | 964 | 817 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/evolution_slash.py` | 421 | 352 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/evolution_version.py` | 454 | 395 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/interface_code.py` | 1,701 | 1,375 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/interface_deep.py` | 17,795 | 15,678 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/interface.py` | 3,491 | 3,005 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/llm_io_trace.py` | 547 | 472 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/recap_prompts.py` | 86 | 73 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/session_skill_dirs.py` | 50 | 37 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/sysop_builder.py` | 1,004 | 884 |
| SDK 门面与 Agent adapter | `server/runtime/agent_adapter/team_helpers.py` | 3,240 | 2,788 |
| 活跃会话运行时 | `server/runtime/session/session_manager.py` | 498 | 427 |
| 活跃会话运行时 | `server/runtime/session/permission_response_ledger.py` | 104 | 83 |
| 活跃会话运行时 | `server/runtime/session/kv_cache_affinity_lifecycle.py` | 418 | 344 |
| 活跃会话运行时 | `server/runtime/session/kv_cache_product_hooks.py` | 250 | 219 |

第二部分的 adapter 分组占本部分 85.0%。其中 `interface_deep.py` 单文件即占 49.5%；再加上 `interface.py` 和 `team_helpers.py`，三个文件合计 24,526 行，占本部分 68.2%。因此该看护人应以运行时行为、并发/取消、资源回收、DeepAgent 与 SDK 兼容性为主要验收面，而不是只维护文件归属。

## 两人看护的规模解读与边界

| 看护角色 | 主要范围 | 规模特征 | 首要质量责任 | 对方评审触发点 |
| --- | --- | --- | --- | --- |
| A：接入控制面与持久会话 | 第一部分全部文件 | 19,535 行，功能面分散；handler 与持久会话合计 88.1% | 请求协议语义、会话可恢复、历史/工作空间/Git 数据一致性 | 变更触及 agent 创建、预热命中、运行中会话清理或 DeepAgent 状态时 |
| B：运行时实例与 SDK 适配 | 第二部分全部文件 | 35,985 行，复杂度高度集中；adapter 占 85.0% | 实例生命周期、租户隔离、预热/热更新、并发与取消、SDK/DeepAgent 行为兼容 | 变更影响请求响应形态、持久化字段、会话 fork/rewind/restore 语义时 |

规模不对称不表示 A 的工作量天然较小。A 维护的是多个外部契约和持久化边界，需求变化和兼容验证会较多；B 维护的是高集中度的核心运行时，单点回归和性能/资源风险更高。推荐以“主责 + 必要的交叉评审”而非按文件行数再拆分：这样避免把 `interface_deep.py` 人为切开，也避免让会话持久化和其对外 handler 分属不同负责人。

### 建议的共同变更规则

1. A 主责的 `handlers/_default.py`、`handlers/bootstrap.py`、`handlers/session.py` 若调用方式、session key 或响应语义改变，B 必须评审。
2. B 主责的 `agent_manager.py`、`tenant_agent_pool.py`、`agent_warm_pool.py`、`agent_adapter/interface.py`、`agent_adapter/interface_deep.py` 若改变持久状态或会话操作语义，A 必须评审。
3. `session_ops_service.py` 由 A 主责；涉及活跃 DeepAgent 实例的 fork、rewind、restore、compact 和资源回收时，由 B 联合评审。

## 使用与更新建议

- 本文衡量的是当前工作树的代码文本规模，不等同于变更频率、故障率、测试覆盖率或业务重要性；排期时应叠加这些指标。
- 新增或迁移文件时，应先按职责归入两部分之一，再同步更新本表；不要因为目录相邻而改变归属。
- 如果未来新增 `server/runtime/prewarm.py`，应放入第二部分的“预热池”分组，并按相同口径补充行数。
