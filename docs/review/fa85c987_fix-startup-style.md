# 代码审查：fa85c987 `fix:修启动风格`

- Commit：`fa85c9872fc1cd59a7b994f641099b92e0dc25ec`
- 作者：lby（2026-07-25）
- 规模：7 文件，约 +705 / -364
- 审查人：资深代码审查员（启动流程与配置体系方向）
- 审查日期：2026-08-01

> 标注约定：🔴 风险/行为变化，🟡 需关注/建议跟进，🟢 良好实践。

---

## 1. 概述

这个 commit 标题叫"修启动风格"，但实质是一次**带行为修正的重构**，并非纯风格调整。三条主线：

1. **配置源迁移**：sandbox 配置从 `JIUWENCLAW_SANDBOX_*` 环境变量改为 `config.yaml::sandbox.*` 扁平字段，并新增一套对称的 `get_/update_` 读写 API（`config.py` +514 重构）。env var 解析层（`_read_sandbox_env` / `_coerce_bool_env` / `_parse_list_env`）整体删除。
2. **启动流程搬家**：`_ensure_jiuwenbox_internal` 从 `app_agentserver.py` 外迁到 `AgentWebSocketServer._bootstrap_internal_jiuwenbox`，调用点从 `_run` 里的 `await server.start()` 之后改到 `AgentWebSocketServer.start()` 内部末尾。逻辑顺势改为"按 `sandbox.startup_mode` 显式值判断"，不再依赖 `sandbox.enabled`。
3. **isolation key 改造**：`sysop_builder` 的 `custom_id` 从 `agent_id` 改为按 `project_dir` 摘要算（`project_{sha256[:16]}`），`interface_deep` 增加 add 前/失败后两次全局复用守卫，避免撞 agent-core "isolation key already registered" 单例约束。

附带两处 Windows 兼容：`process.py` / `workspace.py` 把 `pwd`/`grp` 改成 `sys.platform != "win32"` 条件导入。

整体方向正确，迁移动机清晰，但引入了一处**行为变化**（bootstrap 触发条件变更）、一处**潜在启动竞态**（ws listen 后 bootstrap），以及若干健壮性与向后兼容问题，详见第 6、7 节。

---

## 2. 变更范围

| 文件 | 增删 | 角色 |
|---|---|---|
| `jiuwenclaw/config.py` | +514/-... 重构 | sandbox 配置体系：env → yaml，新增 `get_/update_*` API 全家桶 |
| `jiuwenclaw/app_agentserver.py` | -171 | 删除 `_allocate_jiuwenbox_port` + `_ensure_jiuwenbox_internal`，`_run` 只留 `await server.start()` |
| `jiuwenclaw/agentserver/agent_ws_server.py` | +195 | 新增 `_parse_sandbox_host_port` / `_is_tcp_port_bindable` / `_pick_free_tcp_port` / `_allocate_internal_jiuwenbox_port` / `_bootstrap_internal_jiuwenbox`，`start()` 末尾调用 bootstrap |
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | +110 | 新增 isolation_key 复用守卫两个 staticmethod + `_resolve_project_dir_for_sandbox`；`_create_sys_operation` 加 add 前/失败后复用兜底 |
| `jiuwenclaw/agentserver/deep_agent/sysop_builder.py` | +64 | 新增 `_resolve_project_dir` / `_sandbox_isolation_custom_id`；`create_sandbox_sysop_card` 加 `project_dir` 入参，`custom_id` 改摘要 |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | +7 | `grp`/`pwd` 改条件导入 |
| `jiuwenbox/src/jiuwenbox/server/workspace.py` | +8 | `_effective_user_home` 加 Windows 分支走 `Path.home()` |

---

## 3. 架构与设计概述

### 3.1 配置体系（config.py）

新模型：`sandbox` 是 `config.yaml` 顶层一段扁平 dict，字段全集 `{url, type, startup_mode, policy_file, preserve_file_sharing_mode, enabled, excluded_commands, files, idle_ttl_seconds, idle_check_interval, fallback_on_failure}`。所有读取走 `get_config()`（带 20s TTL 缓存 + 写盘即 `clear_config_cache`），所有写入走 `_load_yaml_round_trip` + `_dump_yaml_round_trip`（ruamel，保留注释）。

关键设计点：
- **`get_sandbox_startup_mode_explicit()`**：只在 yaml 里**显式**写了合法值时返回，否则 `None`。这是 bootstrap 判断的关键——避免从没碰过沙箱的用户升级后突然多出一个 jiuwenbox 进程。🟢 思路正确。
- **`resolve_sandbox_policy_path()`**：纯文件名→在 `jiuwenbox/configs/` 下找；含分隔符→按整路径。`_jiuwenbox_configs_dir()` 先在仓库树里找，再回退到已安装的 `jiuwenbox` 包内。🟢 双形态解析合理。
- 保留 `_sandbox_yaml_to_env_overlay`（在 `agent_manager.py` 调用）把 `url/type/enabled` 翻译成 `JIUWENCLAW_SANDBOX_*` env overlay 写进 `ENV_CONFIG_DICT`——这是给历史依赖 env 的代码留的兼容层。

### 3.2 启动流程（app_agentserver + agent_ws_server）

旧：`_run` → `server.start()` → `_ensure_jiuwenbox_internal()`（在 `_run` 里）。
新：`_run` → `server.start()`（内部末尾 `await self._bootstrap_internal_jiuwenbox()`）。

bootstrap 内部顺序：平台判断 → `get_sandbox_startup_mode_explicit()` 判断 → 解析 endpoint/policy → 端口分配 → 注入 venv/bundled-python env → `runner.ensure_running(timeout=...)` → 失败取 stderr tail → 成功落盘真实 url。所有失败包 `try/except: warning`，best-effort 不阻断 agent-server。

### 3.3 isolation key 改造（interface_deep + sysop_builder）

- `custom_id`：`agent_id`（每 agent 独立）→ `project_{sha256(project_dir)[:16]}`（同 project 共享）。
- 复用守卫：`_create_sys_operation` 在 add 之前先按 `isolation_key_template` 查 `_sandbox_key_owner_map`，命中即复用；add 失败后再查一次。设计文档 `docs/windows_sandbox_bk_unify_design.md` 把动机讲清楚了：撞 agent-core "already registered" 单例约束。

---

## 4. 关键代码检视

### 4.1 `agent_ws_server.py` — bootstrap 触发条件变了（🔴 行为变化）

`jiuwenclaw/agentserver/agent_ws_server.py:273-287`（目标 commit 文件，下同）：

```python
explicit_mode = get_sandbox_startup_mode_explicit()
if explicit_mode is None:
    ... return
if explicit_mode != "internal":
    ... return
```

对比父版本 `app_agentserver.py::_ensure_jiuwenbox_internal`：旧逻辑是 `(endpoint.get("startup_mode") or "internal") != "internal"` 才跳过（即默认 internal 就 spawn），**且**随后还要 `if not bool(runtime.get("enabled")): return`。

新逻辑有两个行为变化：

- 🔴 **不再看 `sandbox.enabled`**：只要显式写了 `startup_mode: internal`，即使 `enabled: false` 也会拉起 jiuwenbox-server。docstring 在 `agent_ws_server.py:248-251` 明确写了"不单独依赖 `sandbox.enabled`"。这对"只想用 jiuwenbox 但忘记开 enabled"的用户更友好，但对"只想配置 sandbox 字段但暂不启用"的用户会意外多出一个子进程。建议至少在 docstring/log 里点明这层语义变化，或保留 `enabled` 作为强门控。
- 🟡 **`startup_mode` 未显式配置时不再 spawn**：旧逻辑 env 未设时默认值是 `internal` 会 spawn（前提 enabled）；新逻辑要求**显式**写 `internal` 才 spawn。这是有意为之（避免升级用户突然多进程，见 `get_sandbox_startup_mode_explicit` docstring），但对从 env-var 迁移过来的存量用户，如果他们之前靠 `JIUWENCLAW_SANDBOX_STARTUP_MODE=internal` env 而非 yaml 工作，迁移到 yaml 后**必须显式写 `startup_mode: internal`**，否则启动后没有 jiuwenbox。需要在升级/迁移文档里点明。

### 4.2 `agent_ws_server.py` — bootstrap 在 ws listen 之后（🟡 潜在竞态）

`jiuwenclaw/agentserver/agent_ws_server.py:467-475`：

```python
logger.info("[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port, ...)
# 按 config.yaml::sandbox.startup_mode 自动拉起 jiuwenbox 子进程 (internal 模式)。
await self._bootstrap_internal_jiuwenbox()
```

ws server 已经开始 listen 并打印"已启动"之后才 bootstrap jiuwenbox。如果 bootstrap 慢（Windows 首次 install 子进程 + UAC，作者自己在后续 commit 把 `ensure_running` timeout 提到 120s），这段时间 Gateway 可能已经连上来发请求，`_create_sys_operation` 会因为 `sandbox.url` 还没落盘真实端口 / box-server 还没 ready 而走 local fallback 或失败。

虽然 best-effort 设计下这不算硬 bug（沙箱任务真发起时 provider 连不上会报错，主进程照跑），但存在一个**初始化顺序窗口**。建议要么把 bootstrap 移到 `server.start()` **之前**（listen 之前），要么在 ws handler 里对 sandbox 请求做"等 bootstrap 完成"的 gate（用 `asyncio.Event`）。

### 4.3 `agent_ws_server.py` — 端口分配 TOCTOU（🟡 已知 best-effort）

`jiuwenclaw/agentserver/agent_ws_server.py:296-308` `_allocate_internal_jiuwenbox_port`：

```python
runner = JiuwenBoxRunner.instance()
if runner.is_owned_listener(host, preferred_port):
    return preferred_port
if self._is_tcp_port_bindable(host, preferred_port):
    return preferred_port
new_port = self._pick_free_tcp_port(host)
```

`_is_tcp_port_bindable` 用 `socket.bind` 探测，bind 成功立即 close，再到 `runner.ensure_running` 让 uvicorn bind 同端口，存在 TOCTOU race。父版本 `app_agentserver.py::_allocate_jiuwenbox_port` 的注释自己就承认了这点（"存在 TOCTOU race ... best-effort"）。🟡 可接受，但建议把 `is_owned_listener` 的快路径优先级保持好（已做），并在 random port 分支后**也校验一次**真起来了。

另外 `_pick_free_tcp_port` 的 `with` 语句关闭后端口也可能被别人抢，同样是 TOCTOU。这是 OS 层面无法根除的问题，记录在案即可。

### 4.4 `config.py` — `_normalize_sandbox_startup_mode` 从抛错改成静默回落（🔴 行为变化）

`jiuwenclaw/config.py:1233-1238`：

```python
def _normalize_sandbox_startup_mode(value: Any) -> str:
    """归一化 ``sandbox.startup_mode``; 非法或空值回落到默认 ``internal``."""
    text = str(value or "").strip().lower()
    if text not in _VALID_SANDBOX_STARTUP_MODES:
        return _DEFAULT_SANDBOX_STARTUP_MODE
    return text
```

父版本对非法值抛 `ValueError`（"must be one of ..."），新版本静默回落到 `internal`。🔴 这丢失了**用户写错值时的反馈**：写 `startup_mode: iternal`（拼错）会被悄悄当 internal，用户以为设的是 external 其实跑的是 internal。

`update_sandbox_startup_mode`（`config.py:1310-1323`）和 `update_sandbox_endpoint`（`config.py:1421-1428`）在**写入路径**仍然校验非法值抛 `ValueError`，这是对的。但**读取路径**的静默回落与写入路径的严格校验**不一致**：用户直接编辑 yaml 写错值，启动时不会报错，只有调 RPC 写才会报错。建议读取路径对"非空但非法"也抛 `ValueError`（区分 `None`/空 vs 非法值）。

### 4.5 `config.py` — `get_sandbox_endpoint` 的 `preserve_file_sharing_mode` 默认值变了（🟡 向后兼容）

`jiuwenclaw/config.py:1386-1396`：

```python
return {
    "url": ...,
    "type": ...,
    "preserve_file_sharing_mode": mode or "",   # ← 空串
    ...
}
```

父版本返回 `mode or _DEFAULT_PRESERVE_FILE_SHARING_MODE`（即默认 `"mount"`），新版本返回空串，docstring 说"由调用方决定默认值"。检查下游：`sysop_builder.py` 的 `create_sandbox_sysop_card` 没读 endpoint 的 `preserve_file_sharing_mode`，而是用模块常量 `_PRESERVE_FILE_SHARING_MODE = "mount"` 写死在 `extra_params` 里。🟢 所以下游不依赖这个返回值，行为不变。但 `get_sandbox_endpoint` 的返回契约变了，如果有别的调用方（前端 RPC、relay-claw）读这个字段，要确认它们容忍空串。建议 grep 一遍 `preserve_file_sharing_mode` 的所有消费者。

### 4.6 `interface_deep.py` — 复用守卫依赖 agent-core 私有属性（🟡 脆弱耦合）

`jiuwenclaw/agentserver/deep_agent/interface_deep.py:2514-2543`：

```python
@staticmethod
def _sys_operation_isolation_key(sysop_card: SysOperationCard) -> str | None:
    try:
        sys_operation = SysOperation(sysop_card)
        return sys_operation.isolation_key_template   # ← agent-core 内部属性
    except Exception as exc:
        ... return None

@staticmethod
def _get_registered_sys_operation_by_isolation_key(isolation_key_template, ...):
    ...
    resource_registry = getattr(Runner.resource_mgr, "_resource_registry", None)
    ...
    sys_operation_mgr = resource_registry.sys_operation()
    owner_map = getattr(sys_operation_mgr, "_sandbox_key_owner_map", {})   # ← agent-core 内部
    existing_op_id = owner_map.get(isolation_key_template)
    ...
```

`isolation_key_template`、`_resource_registry`、`_sandbox_key_owner_map` 全是 `openjiuwen`（agent-core）的**非公开**属性。仓库内 `git grep` 确认这些符号只在 `interface_deep.py` 出现，agent-core 的 vendored 源码里没有定义（`openjiuwen` 是外部包）。

🟡 含义：这段复用守卫当前**很可能全是 no-op**（`getattr(..., "_sandbox_key_owner_map", {})` 拿不到属性→空 dict→`get` 返回 None→函数返回 None），即"复用守卫"实际从未命中，每个 instance 仍走 add 路径。如果 agent-core 升级版**真有**这些属性，守卫才生效。

设计文档（`docs/windows_sandbox_bk_unify_design.md:13,27`）说 BK 那套是"跨 instance 全局查 `_sandbox_key_owner_map`"，说明这是**依赖 agent-core 特定版本**的实现。问题：
- 没有版本门控 / capability 检测。如果 agent-core 改名或删掉这些私有属性，`getattr` 默认 `{}` 会让守卫静默失效，无人察觉。
- 依赖私有属性名，agent-core 任何重构都可能悄悄让守卫失效或行为变化。

建议：要么让 agent-core 暴露**公开** API（如 `sys_operation_mgr.get_by_isolation_key(key)`），interface_deep 调公开 API；要么在启动时探测一次这些属性是否存在并 log，让运维知道守卫是否真生效。

### 4.7 `interface_deep.py` — `_resolve_project_dir_for_sandbox` 用 `_workspace_dir` 作 project_dir（🟡 语义偏差）

`jiuwenclaw/agentserver/deep_agent/interface_deep.py:2545-2556`：

```python
def _resolve_project_dir_for_sandbox(self) -> str | None:
    overrides = getattr(self, "_instance_overrides", None)
    if isinstance(overrides, dict):
        value = overrides.get("project_dir")
        if value:
            return str(value)
    workspace = getattr(self, "_workspace_dir", None)
    if workspace:
        return str(workspace)
    return None
```

`_workspace_dir` 在 adapter 里通常是**agent 工作区**（`agent_root` 那种 sessions/agent 目录），不是用户的"项目代码目录"。把它作为 `project_dir` 去 rw-bind 进沙箱，意味着沙箱里 rw 的是 agent 工作区而非用户代码目录。设计文档 `windows_sandbox_bk_unify_design.md:129` 自己也提到这点："当前有 `self._workspace_dir` 和 metadata 的 `effective_project_dir`...需确认两者哪个对应 BK 的'用户 project dir'"。🟡 这是个**未决问题**，直接影响沙箱里用户能不能编辑自己的代码。建议确认 `_workspace_dir` 的实际语义，或显式从请求 metadata 取 `effective_project_dir`。

### 4.8 `sysop_builder.py` — 拒绝 rw-bind 文件系统根（🟢）

`jiuwenclaw/agentserver/deep_agent/sysop_builder.py:60-71`：

```python
if resolved == Path(resolved.anchor):
    logger.warning(
        "[sysop_builder] refusing to mount filesystem root %s as rw ...", resolved,
    )
    return None
```

拒绝把 `/` 或 `C:\` 当 rw project dir bind 进沙箱，避免 shadow 所有 ro mount + 暴露宿主机密。🟢 这是很好的安全护栏。`_resolve_project_dir` 的三级 fallback（override → env → cwd）也合理。

唯一小问题：`Path(resolved.anchor)` 在某些边界（如 UNC 路径 `\\host\share`）的 `anchor` 行为可能出乎意料，但常规 Windows/Linux 路径 OK。

### 4.9 `config.py` — 写回 runtime 时全量刷字段（🟡 形状稳定但有副作用）

`jiuwenclaw/config.py:1530-1538` `update_sandbox_runtime`：

```python
for key in _SANDBOX_RUNTIME_KEYS:
    sandbox_block[key] = merged[key]
_dump_yaml_round_trip(_current_config_yaml_path(), data)
```

每次 update 把所有 runtime key（含 `files: {allow, deny}`）全量写回 yaml。docstring 说"保证 yaml 形状稳定"。🟡 副作用：如果用户 yaml 里 sandbox 段有**注释**紧贴某个 runtime key，ruamel 全量覆盖可能丢注释（`_dump_yaml_round_trip` 设了 `preserve_quotes` 但注释保留靠 ruamel 对同一 node 的 in-place 修改；整体替换 dict value 时注释会丢）。建议测试一次"带注释的 sandbox 段 update 后注释是否还在"。

### 4.10 Windows 兼容（process.py / workspace.py）🟢

`jiuwenbox/src/jiuwenbox/server/runtime/process.py:19-23`：

```python
if sys.platform != "win32":
    import grp  # noqa: F401
    import pwd  # noqa: F401
```

`jiuwenbox/src/jiuwenbox/server/workspace.py:7-14` 同理。🟢 把 Unix-only 模块改成条件导入，Windows 不再 `ImportError`。`workspace.py` 的 `_effective_user_home` 在 Windows 走 `Path.home()`（USERPROFILE），合理。

唯一小问题：`process.py` 里 `grp`/`pwd` 被 `F401` 标记 unused，说明本文件内其实没用到这两个模块（只是历史导入）。如果真没用到，直接删掉导入比条件导入更干净；如果有别处通过 `from process import grp` 拿（不太可能），保留没问题。🟡 建议确认 process.py 内是否真有 `pwd.getpwuid` / `grp.getgrnam` 之类的引用，若否就删掉。

---

## 5. 优点

1. **配置源迁移方向正确**：env var → yaml 扁平字段，可读性、可持久化、可 RPC 编辑都更好；读写 API 对称（`get_/update_`）。
2. **`get_sandbox_startup_mode_explicit`** 的"显式值才触发"思路很好，避免升级用户突然多进程。
3. **isolation key 改 project_dir 摘要**：同 project 共享 sandbox、不同 project 隔离，比 per-agent_id 更合理；add 前/失败后双重守卫意图明确。
4. **bootstrap best-effort**：任何失败只 warning 不阻断 agent-server，沙箱任务真发起时报错而非拖垮主进程，符合"可选能力"的定位。
5. **Windows pwd/grp 条件导入**：干净解决 Windows `ImportError`，`workspace.py` 的平台分支合理。
6. **`_resolve_project_dir` 拒绝 rw-bind 文件系统根**：安全护栏到位。
7. **stderr tail hint**：bootstrap 失败时打印 `jiuwenbox stderr` 末 20 行，调试友好。

---

## 6. 问题与风险

| # | 严重度 | 位置 | 问题 |
|---|---|---|---|
| 1 | 🔴 | `agent_ws_server.py:273-287` | bootstrap 不再看 `sandbox.enabled`，只要显式 `startup_mode=internal` 就 spawn；`enabled: false` + `startup_mode: internal` 会意外拉起 jiuwenbox |
| 2 | 🔴 | `config.py:1233-1238` | `_normalize_sandbox_startup_mode` 从抛 `ValueError` 改成静默回落 `internal`，用户 yaml 拼错值无反馈；读写路径校验不一致 |
| 3 | 🟡 | `agent_ws_server.py:467-475` | bootstrap 在 ws listen 之后，存在初始化顺序窗口（Gateway 抢先连入时 sandbox 未 ready） |
| 4 | 🟡 | `interface_deep.py:2514-2543` | 复用守卫依赖 agent-core 私有属性（`isolation_key_template` / `_sandbox_key_owner_map`），无版本门控；当前很可能全 no-op |
| 5 | 🟡 | `interface_deep.py:2545-2556` | `_resolve_project_dir_for_sandbox` 回落到 `_workspace_dir`（agent 工作区）而非用户项目目录，语义存疑；设计文档自己标注未决 |
| 6 | 🟡 | `config.py:1386-1396` | `get_sandbox_endpoint` 的 `preserve_file_sharing_mode` 默认从 `"mount"` 改空串，返回契约变化，需确认所有消费者容忍空串 |
| 7 | 🟡 | `config.py:1530-1538` | `update_sandbox_runtime` 全量刷 runtime key，ruamel 可能丢 sandbox 段注释（需测试） |
| 8 | 🟡 | `agent_ws_server.py:296-308` | 端口分配 TOCTOU（已知 best-effort，记录在案） |
| 9 | 🟢→🟡 | `process.py:19-23` | `grp`/`pwd` 标 `F401` unused，若本文件真没用到建议直接删而非条件导入 |
| 10 | 🔴 | 配置迁移 | 从 env-var 迁到 yaml 的存量用户，若之前靠 `JIUWENCLAW_SANDBOX_STARTUP_MODE=internal` env 工作，迁移后必须 yaml 显式写 `startup_mode: internal`，否则启动后没有 jiuwenbox（无迁移告警） |

---

## 7. 改进建议

1. **bootstrap 触发条件**：要么恢复 `sandbox.enabled` 作为强门控（`enabled && startup_mode==internal(显式)` 才 spawn），要么在 docstring + 升级文档里显式说明"`startup_mode=internal` 即视为启用，`enabled` 仅控制 sysop 创建"。倾向前者，更符合直觉。

2. **`_normalize_sandbox_startup_mode` 读取路径**：对"非空但非法"值抛 `ValueError`，仅 `None`/空串回落默认。与写入路径校验对齐，让用户拼错值时启动就报错。

3. **bootstrap 位置**：移到 `server.start()` 内部、**listen 之前**；或在 ws handler 对 sandbox 请求加 `asyncio.Event` gate，等 bootstrap 完成才放行 sandbox 类请求。

4. **isolation key 复用守卫**：启动时探测一次 agent-core 是否真有 `_sandbox_key_owner_map` / `isolation_key_template`，log 一条 "reuse guard: active/inactive (agent-core version mismatch?)"，让运维知道守卫是否真生效。长期推动 agent-core 暴露公开 API 替代私有属性访问。

5. **`_resolve_project_dir_for_sandbox`**：确认 `_workspace_dir` 语义；若不是用户项目目录，从请求 metadata 取 `effective_project_dir`（设计文档已建议），并在 log 里打出最终 project_dir 帮助排查。

6. **向后兼容/迁移**：在 `agent_manager.py` 的 `_sandbox_yaml_to_env_overlay` 里，如果 yaml 没显式 `startup_mode` 但 env 里有 `JIUWENCLAW_SANDBOX_STARTUP_MODE`，考虑把 env 值透传到 overlay，让存量 env 用户升级后 bootstrap 仍能触发；或在启动时 log 一条迁移提示。

7. **注释保留测试**：对 `update_sandbox_runtime` / `update_sandbox_endpoint` 跑一次"带注释 sandbox 段 update 后注释是否还在"的回归测试。

8. **`process.py` 的 `grp`/`pwd`**：确认是否真用到；没用就删，比条件导入更清晰。

---

## 8. 小结

这是一次**有明确动机、方向正确**的重构：配置源从 env 迁 yaml、bootstrap 搬进 ws server、isolation key 改 project_dir 摘要，三件事都服务于"消除 env 注入失败 + isolation 冲突"的目标（见 `docs/windows_sandbox_bk_unify_design.md`）。代码风格、best-effort 容错、Windows 兼容都处理得不错。

但标题"修启动风格"低估了变更影响——它不是纯重构：

- 🔴 bootstrap 触发条件从 `(startup_mode==internal 默认) && enabled` 变成 `显式 startup_mode==internal`（忽略 enabled），是**行为修正**，存量用户迁移有坑；
- 🔴 `_normalize_sandbox_startup_mode` 读取路径从抛错变静默回落，丢了用户写错值的反馈；
- 🟡 bootstrap 在 ws listen 之后、复用守卫依赖 agent-core 私有属性、`_workspace_dir` 当 project_dir 语义存疑，三处需要跟进。

建议合并前至少处理 #1（enabled 门控或文档点明）、#2（读取路径校验）、#4（守卫生效探测/log），其余可作为 follow-up。整体可以合并但需带迁移说明。
