# Windows 沙箱接入对齐 BK(Linux) 方案 — 统一为 yaml 驱动

> 目标：把当前 Windows 改造出的「env 注入 + agent_id 归属 + self 自检守卫 + app_agentserver spawn」这套，对齐到 BK 的「yaml 驱动 + project_dir 摘要归属 + 跨 instance 全局复用守卫 + agent_ws_server bootstrap」，做到两边逻辑统一，消除 Windows 遇到的 env 注入失败、isolation 冲突两类问题。

## 一、背景与根因回顾

当前仓库（Windows 改造版）与 BK（Linux）在沙箱接入上存在 5 处架构分叉，正是 Windows 端到端跑不通的根因：

| 维度 | BK(Linux，能跑通) | 当前仓库(Windows 改造，遇阻) |
|---|---|---|
| 配置源 | `config.yaml::sandbox` 段 | env `JIUWENCLAW_SANDBOX_*` 经 `local_env_config` 白名单 |
| 配置落盘 | `update_sandbox_*` 写 yaml | `set_local_config` 写 namespaced env |
| isolation custom_id | `project_{sha256(project_dir)[:16]}` 同 project 共享 | `agent_id` 每 agent 独立 |
| 复用守卫 | 跨 instance 全局查 `_sandbox_key_owner_map` + add 失败兜底 | 只 `self._sys_operation` 自检 |
| spawn 位置 | `agent_ws_server._bootstrap_internal_jiuwenbox` | `app_agentserver._ensure_jiuwenbox_internal` |
| spawn 门控 | `startup_mode_explicit==internal`（不依赖 enabled） | `startup_mode==internal AND runtime.enabled`（双开关） |

根因映射：
1. env 注入读不到 = `BUSINESS_MIRROR_KEYS` 白名单不含 `JIUWENCLAW_SANDBOX_*` + spawn 后 `set_local_config` 走 namespaced env 时序问题。BK 不依赖 env，全走 yaml，无此问题域。
2. isolation 冲突（full.log:28059 `already registered`）= 按 agent_id 算 key + 守卫只自检不跨 instance，撞 agent-core 单例约束。BK 按 project_dir 摘要天然共享 + 4 步全局查表兜底，Linux 从不撞 key。

## 二、设计原则

1. **统一为 yaml 驱动**：sandbox 配置（url/type/startup_mode/policy_file/enabled/runtime）全部读写 `~/.jiuwenclaw/config/config.yaml::sandbox`，废弃 env 注入链路。
2. **复用 BK 的 API 形态**：移植 BK `common/config.py` 里 sandbox 相关函数到当前 `jiuwenclaw/config.py`，签名与语义保持一致，避免两套逻辑。
3. **isolation 按 project_dir 摘要**：`custom_id = project_{sha256(project_dir)[:16]}`，同 project 多 agent 共享 sandbox，不同 project 隔离。
4. **跨 instance 全局复用守卫**：`_create_sys_operation` 在 add 前后都按 isolation_key 查 `_sandbox_key_owner_map`，命中则复用。
5. **spawn 单开关 + 平台门控**：门控改为 `startup_mode_explicit==internal`（不依赖 enabled），但**平台门 BK 写死 Linux-only，本方案改为放行 Windows**（因 `_create_windows` 分支已存在，是本次适配的目标）。
6. **不影响旧功能（铁律三）**：Linux 下行为与 BK 一致；Windows 下作为新增能力。`local_env_config` 那套 env 读取不删（别处仍用），只是 sandbox 不再走它。

## 三、与既有强改的取舍

会话过程中为让 Windows 先跑起来，已做 5 处临时强改。本方案统一后，这些强改的处置：

| # | 强改位置 | 处置 |
|---|---|---|
| 1 | `app_agentserver.py:180` 强制 enabled 拉起 | **删除**。spawn 迁到 `agent_ws_server`，按 yaml 门控 |
| 2 | `interface_deep.py:2525-2527` 硬编码 url/type + 强制 enabled | **删除**。改读 `config.yaml::sandbox` |
| 3 | `workspace.py:7` `import pwd` 平台感知 | **保留**。这是 jiuwenbox Windows 移植必需，BK 无对应（Linux-only），不属本方案但保留 |
| 4 | `process.py` `import grp/pwd` 条件 import | **保留**。同上 |
| 5 | `interface_deep.py:2516` `self._sys_operation` 自检守卫 | **替换**为 BK 的跨 instance 全局查表守卫 |

## 四、实施步骤

### 步骤 1：sandbox 配置从 env 驱动改为 yaml 驱动（对齐 BK）

**现状核实**（已 grep 确认）：当前 `config.py` sandbox 相关只有 6 个函数，且 `get_sandbox_endpoint`/`get_sandbox_runtime` 是 **env 版**（经 `_read_sandbox_env` 读 `JIUWENCLAW_SANDBOX_*`）。BK 那批 yaml 版函数当前分支缺。逐项分类：

**A. 当前已有且与 BK 同名同语义，保留不动：**
- `_normalize_sandbox_startup_mode(value)` ✓

**B. 当前已有但实现是 env 版，需改成读 yaml（改实现，不新增）：**
- `get_sandbox_endpoint()` — 从读 `_read_sandbox_env("URL"/"TYPE"/...)` 改成读 `get_config().get("sandbox")`（对齐 BK:2197）
- `get_sandbox_runtime()` — 从读 env 改成读 yaml `sandbox.<key>`（对齐 BK:1990）

**C. BK 有而当前分支缺，需新增（共 8 个 yaml 函数 + 1 常量）：**
- `get_sandbox_startup_mode()` (BK:2048) — 默认归一 internal
- `get_sandbox_startup_mode_explicit()` (BK:2059) — boot 门控关键，仅返回显式合法值
- `update_sandbox_startup_mode(mode)` (BK:2081)
- `resolve_sandbox_policy_path(value)` (BK:2144)
- `get_sandbox_policy_file()` (BK:2167) / `get_sandbox_policy_path()` (BK:2174) / `update_sandbox_policy_file(value)` (BK:2184)
- `update_sandbox_endpoint(url, type, *, startup_mode, policy_file)` (BK:2222) — 落盘 yaml 关键
- `get_sandbox_preserve_file_sharing_mode()` (BK:2282) / `update_sandbox_preserve_file_sharing_mode(mode)` (BK:2303)
- `update_sandbox_runtime(patch)` (BK:2322)
- 常量 `_DEFAULT_SANDBOX_POLICY_FILE="code-agent-policy.yaml"`（当前缺）

**D. 删除（env 版专有，yaml 版不需要）：**
- `_read_sandbox_env`（读 env 的，yaml 版直接读 cfg）
- `_coerce_bool_env`（若仅 sandbox 用则删；若别处仍调则保留）
- `_coerce_optional_positive_int`（同上，评估调用方）

实现时复用当前仓库已有的 `get_config()`/`_load_yaml_round_trip()`/`_dump_yaml_round_trip()`（`config.py:76-283` 已有），落盘语义与 BK 一致。

### 步骤 2：`sysop_builder.py` — isolation 改 project_dir 摘要

`jiuwenclaw/agentserver/deep_agent/sysop_builder.py`：
- 移植 BK 的 `_resolve_project_dir(override)` (BK:182) 和 `_sandbox_isolation_custom_id(project_dir)` (BK:236)
- `create_sandbox_sysop_card` 签名**加 `project_dir: str|Path|None=None` 参数**（对齐 BK:432）
- `SandboxIsolationConfig` 的 `custom_id` 从 `agent_id` 改为 `_sandbox_isolation_custom_id(project_dir)` (对齐 BK:459-464)
- `agent_id` 参数保留（仍用于 shared_dir/日志），但不再作 isolation key

### 步骤 3：`interface_deep.py` — 复用守卫升级 + 读 yaml

`jiuwenclaw/agentserver/deep_agent/interface_deep.py`：
- 移植 BK 的 `_sys_operation_isolation_key` (BK:3093)、`_get_registered_sys_operation_by_isolation_key` (BK:3105) 两个 staticmethod
- 移植 `_resolve_project_dir_for_sandbox` (BK)，从 `self._workspace_dir` / `self._instance_overrides["project_dir"]` 取（当前仓库 line 4933 有 `effective_project_dir` 概念，可映射）
- **替换** `_create_sys_operation` (当前 2514) 为 BK 版逻辑 (BK:3129-3199)：
  1. 从 `get_config().get("sandbox")` 读 url/type（删 env 读 + 删硬编码兜底）
  2. `runtime = get_sandbox_runtime()` 读 yaml
  3. 构 sysop_card（sandbox 或 local）
  4. **add 之前**按 isolation_key 查已注册，命中 return
  5. `add_sys_operation` 失败后**再查一次**兜底，命中 return
  6. 调 `create_sandbox_sysop_card` 时传 `project_dir=self._resolve_project_dir_for_sandbox()`
- 删除我加的 `self._sys_operation`+`_sandbox_fingerprint` 自检守卫（改动 5），由 BK 全局查表守卫取代
- `get_sandbox_endpoint`/`get_sandbox_runtime` 的 import 来源不变（函数名同，实现换成 yaml）

### 步骤 4：spawn 迁到 `agent_ws_server.py`，对齐 BK bootstrap

`jiuwenclaw/agentserver/agent_ws_server.py`：
- 移植 BK 的 `_bootstrap_internal_jiuwenbox` (BK:879-1001) 为方法
- **平台门控改写**：BK 是 `if not sys.platform.startswith("linux"): return`；本方案改为**放行 Windows**（删除该 early return，或改成 `if sys.platform not in ("linux","win32","darwin"): return`），因 Windows 走 `_create_windows` 分支
- 移植 BK 辅助 `_parse_sandbox_host_port`、`_allocate_internal_jiuwenbox_port`（当前 `app_agentserver._allocate_jiuwenbox_port` 可复用/迁移）
- 在 `start()` (当前 line 241) 流程里 `server.start` 后调 `_bootstrap_internal_jiuwenbox`
- runner 用现有 `JiuwenBoxRunner.instance()`（已移植，方法齐全：ensure_running/resolve_policy_path/base_url/get_stderr_tail/stop）

`jiuwenclaw/app_agentserver.py`：
- **删除** `_ensure_jiuwenbox_internal`（改动 1）、`_allocate_jiuwenbox_port`（迁走）、`_run` 里 line 317 的调用
- 关停逻辑（`_run` finally 的 `JiuwenBoxRunner.instance().stop()`）保留或迁到 `agent_ws_server`，二选一（倾向保留在 `_run`，关停与启动同处更清晰；BK 在 `agent_ws_server` 里有 stop 钩子，但对齐成本高，暂不迁）

### 步骤 5：清理 env 注入残留

- `app_agentserver.py:213-238` 注入 `JIUWENBOX_VENV_DIR`/`JIUWENBOX_BUNDLED_PYTHON` 的逻辑：这是给 box-server 子进程的 runtime env（非 sandbox 配置），**保留**（BK 也有类似 `dict(os.environ)` 注入）
- `_ensure_jiuwenbox_internal` 删后，`set_local_config("JIUWENCLAW_SANDBOX_URL", ...)` 回写 env 的逻辑删除，改由 `update_sandbox_endpoint` 落盘 yaml（BK:990）

## 五、验证标准

重启 agent-server（Windows，relay-claw 接入）后：

1. **配置源**：在 `~/.jiuwenclaw/config/config.yaml` 写 `sandbox:` 段（startup_mode: internal 等），不再需要 export 任何 `JIUWENCLAW_SANDBOX_*` env
2. **spawn 链路**（agent_ws_server）：日志 `[AgentWebSocketServer] ... jiuwenbox auto-start` → `[JiuwenBoxRunner] jiuwenbox ready at 127.0.0.1:8321`（不再 rc=1，pwd/grp 平台感知已在改动 3/4 解决）
3. **sysop 注册**（interface_deep）：`[sysop_builder] ... isolation_custom_id=project_xxxx` → 首次 add 成功；同 project 第二个 agent/二次 create_instance 日志 `reuse registered sys_operation: <id>`，**不再** `already registered` 冲突
4. **会话**：不再 `sys_operation is not available`，工具调用能进沙箱（`whoami` → `jbx-sandbox`）
5. **回归**：Linux 下（若有环境）行为与 BK 一致；未配 sandbox 段时走 local sysop，不影响现有无沙箱用户

## 六、风险与边界

- **平台门控放行 Windows 是对 BK 的偏离**：BK 当年没 Windows 分支才写死 Linux-only。本方案放行后，Windows 走 `_create_windows`（已存在），逻辑自洽。若 Windows sandbox 子系统（win_setup/win_acl/win_wfp）未就绪，spawn 仍会失败但报错可定位（非 silent skip）。
- **`update_sandbox_endpoint` 落盘 yaml**：relay-claw 接入下 `~/.jiuwenclaw/config/config.yaml` 路径是否可写需实测（当前仅 116 字节，由 jiuwenclaw init 生成）。若 relay-claw 把 config 目录重定向到只读位置，落盘失败需 fallback（BK 也只 warning 不阻断）。
- **`_resolve_project_dir_for_sandbox` 在当前仓库的映射**：当前有 `self._workspace_dir` 和 metadata 的 `effective_project_dir`（line 4933），需确认两者哪个对应 BK 的"用户 project dir"。倾向用 `effective_project_dir`（请求级，更准），None 时 fallback。
- **不删 `local_env_config`**：env 注入机制别处仍用，仅 sandbox 不再走它，避免发散（铁律一）。
- 范围控制在 config.py / sysop_builder.py / interface_deep.py / agent_ws_server.py / app_agentserver.py 五个文件 + workspace.py/process.py 已有的平台守卫。

## 七、预计改动文件

1. `jiuwenclaw/config.py` — 移植 14 个 sandbox yaml 函数，删 env 版
2. `jiuwenclaw/agentserver/deep_agent/sysop_builder.py` — 加 project_dir 参数 + isolation 摘要
3. `jiuwenclaw/agentserver/deep_agent/interface_deep.py` — BK 守卫 + 读 yaml + 删强改
4. `jiuwenclaw/agentserver/agent_ws_server.py` — 移植 bootstrap（放行 Windows）
5. `jiuwenclaw/app_agentserver.py` — 删 `_ensure_jiuwenbox_internal` + port 函数迁走
（`jiuwenbox/.../workspace.py`、`runtime/process.py` 的平台守卫已在前序步骤完成，本方案不重做）
