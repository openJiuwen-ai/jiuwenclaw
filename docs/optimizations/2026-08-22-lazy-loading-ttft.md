# TTFT 优化：Post-DeepAgent 初始化分层延后

> 日期：2026-08-22
> 范围：jiuwenswarm sidecar（`interface_deep.py`）

---

## 1. 问题分析

### 1.1 背景

Session adapter reuse 优化将冷启动 session_init 从 ~15s 降到 ~315ms，但 `create_instance()` 末尾仍有一组串行初始化步骤：

```python
# session_init 路径（create_instance 末尾）
await self._try_init_a2x_client(config_base)        # 50-200ms（网络连接）
await asyncio.to_thread(self._ensure_project_gitignore_agent_history, ...)  # 10-100ms（子进程）
self._ensure_cron_tools_registered(...)              # 5-20ms（同步）
await self._register_mcp_servers_from_config(...)    # 50-500ms（网络）
await self._load_active_packages()                   # 0-50ms
self._sync_preinstance_runtime_tools_to_ability_manager()  # 5-10ms
self._sync_multimodal_tools_for_runtime()            # 5-10ms
await self.load_user_rails()                         # 0-20ms
self._register_extension_tools()                     # 5-10ms
```

这些步骤串行累积 ~125-920ms，全部阻塞在 `create_instance()` 里，延迟了 session_init 事件的发出和首条消息的处理。

### 1.2 思路

将步骤按**缺失后的影响**分为两层，Tier 2 步骤移到后台 `asyncio.create_task`，不阻塞 session_init。

---

## 2. 分层设计

### 2.1 判据

**缺失后模型会不会"自作主张找替代方案"？**

- **会** → 必须内联（Tier 1）。缺失时模型找不到工具，会用 shell 等方式替代，产生静默降级（行为错误但不报错）
- **不会** → 可以延后（Tier 2）。缺失时要么明确报错，要么不影响工具可用性

### 2.2 各步骤分析

| 步骤 | 耗时 | 缺失后模型行为 | 分层 |
|------|------|--------------|------|
| A2X client init | 50-200ms | 无法跨 agent 通信，明确报错 | **Tier 2** |
| gitignore | 10-100ms | 不影响工具，git 的文件级机制 | **Tier 2** |
| _sync_a2x_runtime_state | <5ms | 依赖 A2X，随 A2X 延后 | **Tier 2** |
| cron tools | 5-20ms | 用 shell `crontab`/`schtasks` 替代，定时任务脱离 app 管理 | **Tier 1** |
| MCP servers | 50-500ms | 用 shell 脚本替代 MCP 工具功能，行为不可控 | **Tier 1** |
| active packages | 0-50ms | package 工具不可用，模型找 shell 替代 | **Tier 1** |
| sync tools / multimodal | 5-20ms | 工具对 agent 不可见，模型无法调用 | **Tier 1** |
| user rails | 0-20ms | 影响行为约束，缺失可能导致不安全操作 | **Tier 1** |
| extension tools | 5-10ms | 扩展工具不可用，模型找 shell 替代 | **Tier 1** |

### 2.3 关键认知

**gitignore 不是 LLM 工具**。`.gitignore` 是 git 的文件级排除机制，LLM 不"调用"它。缺失的唯一风险是 `.gitignore` 文件尚未写入 agent 历史目录条目，但：
- 已跑过 agent 的项目，条目早已存在
- 全新项目的 agent 历史目录还不存在，`git add .` 不会扫到

这与 cron 不同——cron tool 是 LLM 工具列表里的一个工具，缺失时 LLM 会主动找替代方案。

---

## 3. 改动

### 3.1 文件

**`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`**（唯一改动文件）

### 3.2 改动一：`__init__` — 添加 `_bg_init_task` 字段

```python
self._bg_init_task: asyncio.Task | None = None
```

### 3.3 改动二：`create_instance()` — 分层执行

**位置**：`ensure_initialized()` 之后

```python
# ── Tier 2: 可安全延后的步骤（A2X client、gitignore）────────────
if self._is_session_scoped_adapter:
    self._bg_init_task = asyncio.create_task(
        self._deferred_background_init(config_base, mode),
    )
else:
    await asyncio.to_thread(
        self._ensure_project_gitignore_agent_history,
        initial_runtime_workspace,
    )
    self._sync_a2x_runtime_state()

# ── Tier 1: 工具/行为可用性关键步骤（必须在 LLM 调用前完成）────
self._ensure_cron_tools_registered(self._parent_session_id)
self._registered_mcp_server_ids.clear()
self._registered_mcp_servers.clear()
await self._register_mcp_servers_from_config(config_base, tag=f"agent.{mode}")
await self._load_active_packages()
self._sync_preinstance_runtime_tools_to_ability_manager()
self._sync_multimodal_tools_for_runtime()
await self.load_user_rails()
self._register_extension_tools()
```

### 3.4 改动三：新增 `_deferred_background_init()` 方法

```python
async def _deferred_background_init(self, config_base, mode):
    """后台完成可安全延后的初始化步骤。"""
    try:
        await self._try_init_a2x_client(config_base)
    except Exception as exc:
        logger.warning("[JiuWenSwarmDeepAdapter] deferred a2x init failed: %s", exc)

    initial_workspace = self._project_dir or str(
        get_default_project_session_workspace_dir()
    )
    try:
        await asyncio.to_thread(
            self._ensure_project_gitignore_agent_history,
            initial_workspace,
        )
    except Exception as exc:
        logger.warning("[JiuWenSwarmDeepAdapter] deferred gitignore init failed: %s", exc)

    self._sync_a2x_runtime_state()
```

每个子步骤独立 try/except，单个失败不影响其他步骤。

### 3.5 改动四：`process_message_impl` / `process_message_stream_impl` — await guard

在消息处理入口添加后台任务等待，防止 A2X 状态未就绪时处理消息：

```python
if self._bg_init_task is not None and not self._bg_init_task.done():
    await self._bg_init_task
```

### 3.6 改动五：`cleanup()` — 取消后台任务

```python
if self._bg_init_task is not None and not self._bg_init_task.done():
    self._bg_init_task.cancel()
```

---

## 4. 效果

### 4.1 Benchmark 数据

| 指标 | 仅 session adapter reuse | + Tier 2 延后 | 节省 |
|------|------------------------|-------------|------|
| cold session_init | ~1,167ms | ~904ms | **~263ms** |
| warm session_init | ~453ms | ~386ms | **~67ms** |
| warm 首 text token | ~2,230ms | ~2,074ms | LLM 波动 |

主要收益来自跳过 A2X client init 的串行 await（含网络连接超时）。

### 4.2 局限性

- Tier 1 步骤（MCP/cron/packages/rails/extension）必须内联，占总 init 的大头
- 真正能安全延后的只有 A2X + gitignore，收益上限约 50-300ms
- 首条消息的 await guard 会等 Tier 2 完成，所以对首条消息的实际 TTFT 改善有限
- 收益主要体现在 session_init 事件更早发出（前端感知更快）

---

## 5. 风险与防护

| 风险 | 应对 |
|------|------|
| Tier 2 步骤失败 | 每步独立 try/except，单步失败不影响其他 |
| 首条消息需 A2X | await guard 确保消息处理前 Tier 2 完成 |
| adapter 清理时后台任务仍在运行 | cleanup() 中 cancel 后台任务 |
| 未来新增初始化步骤误放 Tier 2 | 分层判据：缺失后模型是否"自作主张" |

---

## 6. 后续方向

| 方向 | 预期收益 | 复杂度 | 说明 |
|------|---------|--------|------|
| MCP server 注册并行化 | 50-200ms | 低 | 多 MCP server 用 `asyncio.gather` 并行启动 |
| MCP server 连接跨 session 复用 | 50-200ms | 中 | 类似 Model 复用，但 MCP 有 session 级状态 |
| 简单查询关闭 CoT | TTFB -50%~70% | 高 | 需要请求级判断逻辑，误判风险高 |
| prompt caching (KV cache) | prompt 处理 -30%~50% | 依赖模型端 | DashScope 需支持 prefix caching |
