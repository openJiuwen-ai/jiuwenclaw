# TTFT 优化：DB 层问题分析与方案

> 日期：2026-08-22
> 范围：jiuwenswarm sidecar（Python）+ relay-claw API（Node.js）

---

## 1. 问题总览

当前 TTFT 管线中，DB 操作分布在两个进程、两条路径上：

```
用户消息 POST
  │
  ├─ relay-claw API (Node.js) ─── 15-20 次 Redis 串行调用 ──→ chat.send
  │                                                              │
  └─ jiuwenswarm sidecar (Python) ─── SQLite/Redis 操作 ──→ LLM 首 token
```

本地 Redis 单次调用 ~0.1ms，本地 SQLite 单次 ~5-15ms。**单次开销不大，但串行累积 + 阻塞事件循环的同步调用是真正的瓶颈。**

---

## 2. jiuwenswarm sidecar — 问题与方案

### 2.1 🔴 MemoryIndexManager 同步 SQLite 阻塞事件循环

**严重度：CRITICAL**

**位置**：`jiuwenswarm/agents/harness/common/memory/manager.py`

**问题**：`MemoryIndexManager` 使用同步 `sqlite3`（`self.db.execute()`），所有方法虽然声明为 `async def`，但内部 DB 调用**同步阻塞事件循环线程**。

关键路径：
```
MemoryRail.before_model_call()
  └─ MemoryIndexManager.search()
       ├─ if dirty: sync(reason="search")   ← 同步写入！可阻塞 100ms-5s
       │    └─ _sync_memory_files()          ← 逐文件哈希 + FTS 索引
       └─ _search_vector() / _search_keyword()  ← 同步读取，10-100ms
```

每次 LLM 调用前都会执行 `search()`。如果内存文件有变动（dirty），先跑一轮完整 `sync()`（哈希所有文件 + 写入 chunk + FTS 索引），再搜索。这在大型 workspace 下可达数秒。

**方案 A（推荐）：将同步 sqlite3 替换为 aiosqlite**

```python
# 现在（阻塞）
self.db = sqlite3.connect(db_path, check_same_thread=False)
rows = self.db.execute("SELECT ...").fetchall()

# 改为（非阻塞）
self.db = await aiosqlite.connect(db_path)
rows = await self.db.execute_fetchall("SELECT ...")
```

- 改动集中在 `manager.py` 一个文件
- 所有 `self.db.execute/commit/rollback` 改为 `await self.db.execute/commit/rollback`
- `check_same_thread=False` 可移除（aiosqlite 自带线程安全）
- **预估收益**：消除事件循环阻塞，TTFT 减少几十到数百毫秒（取决于 memory 文件数量）

**方案 B（低风险）：将 sync() 移出搜索热路径**

```python
async def search(self, query, ...):
    if self.dirty and self.settings.sync.get("onSearch", True):
        # 不在搜索前同步，改为后台异步同步
        asyncio.create_task(self.sync(reason="search_background"))
        self.dirty = False  # 标记为已调度，避免重复
    # 用当前索引直接搜索（可能略旧，但无阻塞）
    return await self._do_search(query, ...)
```

- 搜索结果可能略旧（刚修改的文件不会立即被索引），但对大多数场景可接受
- **预估收益**：完全消除 sync 阻塞，TTFT 减少 100ms-5s

---

### 2.2 🟡 Enterprise Config 每次查询新建 SQLite 连接

**严重度：HIGH**

**位置**：`jiuwenswarm/server/runtime/enterprise_config/gateway_db.py:241`

**问题**：`list_records()` 每次调用都 `aiosqlite.connect()` + `conn.close()`，没有连接池。每次连接付出文件打开 + PRAGMA 协商开销（~5-15ms）。

`create_instance()` 中 `load_effective_enterprise_config()` 调用链：
- `_resolve_policy_match()` → 3 次 `list_records()`（3 个新连接）
- `_fetch_slot_entities()` → 每个模板 1 次 `fetch_template_by_slot()`（每个新连接）
- **总计 6-9 个连接**，串行创建/销毁

**方案：进程级连接池**

```python
# gateway_db.py
class GatewayDB:
    _db: Optional[aiosqlite.Connection] = None

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            await self._db.execute("PRAGMA journal_mode=WAL")
        return self._db

    async def list_records(self, table, ...):
        conn = await self._get_conn()  # 复用连接
        async with conn.execute(sql, params) as cursor:
            return await cursor.fetchall()
```

- 改动 1 个文件
- **预估收益**：消除 6-9 次连接开销，减少 ~30-100ms

---

### 2.3 🟡 Enterprise Config N+1 查询模式

**严重度：HIGH**

**位置**：`jiuwenswarm/server/runtime/enterprise_config/loader.py:174-191`

**问题**：`_fetch_slot_entities()` 遍历每个 `template_id` 单独调用 `fetch_template_by_slot()`，典型 N+1 模式。

**方案：批量查询**

```python
# 现在（N+1）
for template_id in template_ids:
    result = await fetch_template_by_slot(slot, template_id)

# 改为（批量）
results = await gateway_db.list_records(
    table,
    where=f"slot = ? AND template_id IN ({','.join('?' * len(template_ids))})",
    params=[slot, *template_ids],
)
```

- 改动 1-2 个文件
- **预估收益**：减少 N-1 次 DB 调用

---

### 2.4 🟢 MemoryIndexManager._sync_memory_files() N+1 模式

**严重度：MEDIUM**

**位置**：`jiuwenswarm/agents/harness/common/memory/manager.py:614-628`

**问题**：逐文件 `SELECT hash FROM files WHERE path = ?`，N 个文件 N 次查询。

**方案：批量查询**

```python
# 现在
for f in files:
    row = self.db.execute("SELECT hash FROM files WHERE path = ?", (f,)).fetchone()

# 改为
placeholders = ",".join("?" * len(files))
rows = self.db.execute(
    f"SELECT path, hash FROM files WHERE path IN ({placeholders})",
    list(files),
).fetchall()
hash_map = {r[0]: r[1] for r in rows}
```

- **预估收益**：对大量 memory 文件场景明显

---

## 3. relay-claw API — 问题与方案

### 3.1 🔴 同一线程 4 次重复 `threadStore.get()`

**严重度：HIGH**（远程 Redis 场景下）

**位置**：消息处理热路径，4 次对同一 key 做 `HGETALL`：

| 调用位置 | 文件:行 |
|---------|---------|
| `messages.ts` 路由入口 | messages.ts:488 |
| `AgentRouter.resolveTargetsAndIntent()` | AgentRouter.ts:263 |
| `invokeSingleCat()` 取工作目录 | invoke-single-agent.ts:1067 |
| `invokeSingleCat()` 取 mission 上下文 | invoke-single-agent.ts:1182 |

本地 Redis 每次约 0.1ms（共 ~0.4ms），可忽略。但远程 Redis 每次约 1-5ms（共 ~4-20ms），且 4 次串行。

**方案：请求级缓存**

在 `messages.ts` 路由入口读取一次 thread，将对象挂在 `request` 上下文上，下游复用：

```ts
// messages.ts 路由入口
const thread = await threadStore.get(threadId);
req.threadCache = thread;  // 挂上下文

// AgentRouter.ts / invoke-single-agent.ts
const thread = req.threadCache ?? await threadStore.get(threadId);
```

- 改动 3 个文件
- **预估收益**：减少 3 次 Redis 往返（本地 ~0.3ms，远程 ~3-15ms）

---

### 3.2 🟡 3+ 次重复 `sessionChainStore.getActive()`

**严重度：MEDIUM**

**位置**：`invoke-single-agent.ts`，`getActive()` 被调用至少 3 次（行 828, 935, 1563+），每次发出 `GET` + `HGETALL` 两个 Redis 命令。

**方案：函数顶部读取一次，后续复用**

```ts
// invokeSingleCat() 顶部
const activeSession = await sessionChainStore.getActive(agentId, threadId);

// 后续所有使用处
// const active = await sessionChainStore.getActive(...)  // 删除
// → 直接用 activeSession
```

- 改动 1 个文件
- **预估收益**：减少 4-6 次 Redis 往返

---

### 3.3 🟡 串行预检可并行化

**严重度：MEDIUM**

**位置**：`invoke-single-agent.ts` 中以下操作无数据依赖，可以并行：

```
sessionManager.get()           ─┐
sessionChainStore.getActive()  ─┤─ 无依赖，可并行
sessionChainStore.getChain()   ─┤
threadStore.get()              ─┘
```

**方案**：

```ts
const [session, activeSession, thread] = await Promise.all([
  sessionManager.get(userId, agentId, threadId),
  sessionChainStore.getActive(agentId, threadId),
  threadStore.get(threadId),
]);
```

- 改动 1 个文件
- **预估收益**：3 次串行 → 1 次并行，减少 ~2 次往返的延迟

---

### 3.4 🟢 InvocationRecordStore.update() 多余的 HGET

**严重度：LOW**

**位置**：`RedisInvocationRecordStore` 的 `update()` 方法先 `HGET` 检查 `usageRecordedAt`，再 `EVAL` 执行 Lua 原子更新，再 `HGETALL` 读回结果。3 次往返可缩减为 2 次。

**方案**：将 `usageRecordedAt` 检查逻辑合并进 Lua 脚本。

---

## 4. 影响对比总结

| 问题 | 严重度 | 位置 | 预估本地收益 | 预估远程收益 | 改动量 |
|------|--------|------|-------------|-------------|--------|
| MemoryManager 同步 SQLite 阻塞 | 🔴 CRITICAL | sidecar | 50-5000ms | 同左 | 1 文件 |
| Enterprise Config 无连接池 | 🟡 HIGH | sidecar | 30-100ms | 同左 | 1 文件 |
| Enterprise Config N+1 | 🟡 HIGH | sidecar | 10-50ms | 同左 | 1-2 文件 |
| Thread 4 次重复读取 | 🟡 HIGH(API) | API | ~0.3ms | 3-15ms | 3 文件 |
| SessionChain 3+ 次重复读取 | 🟡 MEDIUM | API | ~0.6ms | 3-10ms | 1 文件 |
| 串行预检可并行 | 🟡 MEDIUM | API | ~0.2ms | 2-10ms | 1 文件 |
| Memory sync N+1 | 🟢 MEDIUM | sidecar | 取决于文件数 | 同左 | 1 文件 |
| InvocationRecord 多余 HGET | 🟢 LOW | API | ~0.1ms | 1-5ms | 1 文件 |

**注**：本地 Redis（127.0.0.1）单次往返 ~0.1ms，问题不明显；远程/云 Redis 单次 1-5ms，串行累积效果显著。

---

## 5. 推荐实施顺序

1. **MemoryIndexManager 同步→异步**（收益最大，且是唯一会秒级阻塞的点）
2. **Enterprise Config 连接池 + 批量查询**（同一模块，一起改）
3. **API 侧 thread 缓存 + sessionChain 去重 + 并行化**（改动集中，收益叠加）
4. **Memory sync N+1**（锦上添花）
5. **InvocationRecord Lua 合并**（低优先级）
