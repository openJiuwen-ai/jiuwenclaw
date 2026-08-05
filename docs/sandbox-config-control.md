# officeAce 控制 jiuwenclaw 沙箱配置方案

## 0. 背景与现状

需求（见 `interface.txt`）：officeAce 页面提供 3 组配置，通过 ACL/WFP 提权落到沙箱，并在配置变更后销毁重建沙箱使新配置生效：

1. **沙箱开关** `enable_sand: bool`（默认开 True）
2. **文件安全** `white_list/black_list: list[str]`（可访问 / 不可访问文件）
3. **网络安全** `disable_all: bool` + `white_list/black_list: list[str]`（域名白/黑名单，总开关默认关 False）

并要求把配置写入 `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml`（追加到基础配置后），以及感知 officeAce 更新并确保沙箱内应用。

## 1. 调研结论：交互链路与关键事实

### 1.1 通信链路（两层，relay-claw 不直连 box-server）

```
officeAce (relay-claw, Fastify:3004)
   │  WebSocket (ws://127.0.0.1:<AGENT_PORT>, 帧按 request_id 多路复用)
   ▼
jiuwenclaw agent-server (Python sidecar, AGENT_PORT)
   │  HTTP (http://127.0.0.1:8321)
   ▼
jiuwenbox box-server (uvicorn FastAPI, 由 JiuwenBoxRunner spawn)
   │  ACL / WFP / 受限 token
   ▼
沙箱进程 (jbx-sandbox)
```

- relay-claw **不直连 box-server**，它把请求作为 `req_method` 帧发到 agent-server 的 WebSocket。`schema/message.py:ReqMethod` 是固定枚举，派发在 `agent_ws_server.py:_handle_agent_request_body`。
- 已有完全对齐的模板：`/api/config/relayclaw/security`（`routes/config.ts:251-289`）→ `DefaultRelayClawSecurityClient`（`relayclaw-security-proxy.ts`）→ WS 帧 → `agent_ws_server.py:_handle_permissions_config`（`:1244`）→ `permissions/config_rpc.py:dispatch_permissions_config_request` → 写 `config.yaml` + 热重载。新接口照抄此模板。

### 1.2 现有 sandbox 配置骨架（已存在，但语义不对）

`jiuwenclaw/config.py` 已有：
- `sandbox.enabled`（开关，控制 `_create_sys_operation` 走 SANDBOX 还是 LOCAL，`interface_deep.py:2585-2586`）。
- `sandbox.runtime.files.{allow,deny}` —— **但流向是 Linux 路径**：`sysop_builder.py:build_filesystem_policy` 把它们组装成 `filesystem_policy.bind_mounts/read_write/read_only` 的 **append patch**（bwrap 语义），**与 Windows ACL（`windows.filesystem.allow_read/allow_write/deny_write/deny_read`）毫无关系**。
- 没有任何"网络安全域名"配置项；`windows.network.egress` 是 `windows-policy.yaml` 里的静态值。

### 1.3 配置生效机制（决定"销毁重建"的范围）— 关键

| 配置 | 读取时机 | 来源 policy | 变更如何生效 |
|---|---|---|---|
| **文件安全**（ACL）| 沙箱**创建时** `_create_windows`（`process.py:2899-3002` 读 `policy.windows.filesystem.{allow_read,allow_write,deny_write,deny_read}`）| **per-sandbox policy**（create_sandbox 传入，`policy_mode=append/override`）| **销毁重建该沙箱即可**（不需要重启 box-server）|
| **网络安全**（WFP+代理）| box-server **启动时** `app.py` lifespan → `serve_windows_proxy` → `EgressFilter(egress, ingress)`（`win_proxy.py:466`）| **root policy**（`PolicyReader.load_policy()`）| **需重启 box-server**（重跑 lifespan 重建 EgressFilter），再销毁重建沙箱 |
| **沙箱开关** | `_create_sys_operation`（`interface_deep.py:2585`）读 `sandbox.enabled` | `config.yaml` | 热重载 agent 即可（已支持 reload）|

> 结论：interface.txt 说"销毁沙箱并重建一个沙箱使新配置生效"——对**文件安全**成立；对**网络安全**不充分，因为 root policy 的 egress 在 box-server 启动时固化。需要 **重启 box-server**（JiuwenBoxRunner 已支持：`_spawned_policy_path` 变更即 `stop`+`ensure_running` 重 spawn，`jiuwenbox_runner.py:360-393`）。

### 1.4 windows-policy.yaml 结构（已确认）

- `windows.filesystem`：`allow_read / allow_write / deny_write / deny_read / read_acl_preinstall / tool_paths`（`WindowsFilesystemPolicy`，`policy.py:796-830`）。
- `windows.network.egress / ingress`：`default: deny|allow` + `allowed_domains/blocked_domains/allowed_ips/blocked_ips/allowed_ports/blocked_ports`（`NetworkRulePolicy`，`policy.py:450-457`）。
- `egress.default="deny"` + 白名单空 = 全拒；`disable_all=True` 等价于 `default="deny"` 且清空 `allowed_domains`（WFP 已 Block 所有出站，仅放行 loopback:proxy，proxy 再 deny-all 即"拒绝所有网络"）。

## 2. 设计决策（已与用户确认）

### 2.1 配置存哪里 —— 用户黑白名单直接写进运行时副本的对应字段（用户选定）

- **用户配置直接写进 windows-policy 运行时副本的对应字段**（追加到各自归属字段后面），**不存 config.yaml**。config.yaml 仅保留基础配置：`sandbox.{enabled, startup_mode, url, type, policy_file}`（现状，不动）。
- **运行时副本**：以打包内置的 `windows-policy.yaml` 为基底，复制一份到用户数据区 `<workspace>/windows-policy.runtime.yaml`（`OFFICE_CLAW_DATA_ROOT` 下，不改源模板，升级安全）。用户黑白名单**直接追加到副本的对应字段**：
  - 文件白名单 → `windows.filesystem.allow_read` + `allow_write`（追加在基底必需集后，去重）。
  - 文件黑名单 → `windows.filesystem.deny_read` + `deny_write`（追加）。
  - 网络白名单 → `windows.network.egress.allowed_domains`（**直接写入**该字段，用户要求）。
  - 网络黑名单 → `windows.network.egress.blocked_domains`（**直接写入**，黑名单优先）。
  - 网络总开关 `disable_all=true` → `windows.network.egress.default="deny"` 且清空 `allowed_domains`（等价断网）。
- **注入方式**：`config.yaml::sandbox.policy_file` 改指向运行时副本路径（`update_sandbox_policy_file`），或经 `JIUWENBOX_POLICY_PATH` 注入 `JiuwenBoxRunner.ensure_running`。现状 `config.yaml::sandbox.policy_file: windows-policy.yaml` → 改为指向运行时副本。

> 这样**字面**符合 interface.txt 的"追加到 windows-policy.yaml 的对应字段后面"——用户配置确实追加在 windows-policy 的对应字段里；**物理**上写的是运行时副本而非源模板（升级安全、多实例不串台）。读取接口 `sandbox.files.get`/`sandbox.network.get` 直接读副本的对应字段返回（而非读 config.yaml）。

### 2.2 网络"黑名单"语义

Windows egress 现有 `blocked_domains`（deny 优先于 allow，`win_proxy.py:EgressFilter.allow`）。interface.txt 的"黑名单=不允许访问的域名"= `blocked_domains`，"白名单=允许访问的域名"= `allowed_domains`。直接对齐。

**用户特别要求（优先级）**：用户域名配置**直接写入对应字段**（allow_domains→`allowed_domains`、deny_domains→`blocked_domains`），**黑名单优先**（EgressFilter 本就 deny 优先，天然满足）。`disable_all=True` → `default="deny"` 且 `allowed_domains=[]`（WFP 全 Block，等价断网）。

**用户要求"取消配置后沙箱装包正常"**：用户取消（删空 allow/deny_domains）后，副本对应字段回落到基底 `windows-policy.yaml` 的默认值（pypi/npmmirror 白名单），沙箱 pip/npm 恢复正常。实现方式：删空用户配置时，把副本对应字段**还原为基底原值**（不残留用户旧值）。

### 2.3 文件"访问"粒度 —— 读+写（用户选定）

interface.txt：白名单=可访问文件，黑名单=不可访问文件。Windows ACL 有 `allow_read/allow_write` 和 `deny_write/deny_read`。
- 文件"访问"=读+写。白名单 → `allow_read` + `allow_write`；黑名单 → `deny_read` + `deny_write`。

**用户要求"优先读取黑名单，确保用户配置生效"**：deny_read/deny_write 是 NTFS 显式 Deny，本就优先于 Allow（`win_acl.py:_rebuild_acl_with_order` Deny-then-Allow），天然满足。

**沙箱必需集不被饿死**：`windows-policy.yaml` 基底已有 `allow_read/allow_write`（workspace/venv/skills/tool_paths），用户白名单**合并**进运行时副本的对应字段（不覆盖基底必需集），用户黑名单 `deny_*` 叠加（Deny 优先，精细化封锁）。取消用户配置 → 回落基底，沙箱照常。

## 3. 实现方案（仅 jiuwenclaw 后端）

### 3.1 用户配置 → 运行时 policy 副本（直接写对应字段）

config.yaml **只保留基础配置**（不动）：
```
config.yaml::sandbox                          # C:\Users\<user>\.office-claw\.jiuwenclaw\config\config.yaml
  enabled: bool                              # 开关（agent-server 读，控制 LOCAL/SANDBOX）
  startup_mode: internal                    # 基础
  url: http://127.0.0.1:<port>              # 基础
  type: jiuwenbox                           # 基础
  policy_file: windows-policy.runtime.yaml   # 改为指向运行时副本（原为 windows-policy.yaml）
```

用户黑白名单**不存 config.yaml**，直接写进运行时副本 `<workspace>/windows-policy.runtime.yaml` 的对应字段：
```
windows-policy.runtime.yaml                  # = 基底 windows-policy.yaml + 用户配置追加
  windows:
    filesystem:
      allow_read: [基底... , 用户白名单追加]    # 用户 files.allow 追加在此
      allow_write: [基底..., 用户白名单追加]
      deny_read: [用户黑名单]                 # 用户 files.deny 写在此
      deny_write: [用户黑名单]
      ...（基底其余字段不动：read_acl_preinstall/tool_paths 等）
    network:
      egress:
        default: deny                       # disable_all=true 时仍 deny；都空时回落基底
        allowed_domains: [用户白名单 或 基底 pypi/npmmirror]   # 用户 network.allow_domains 直接写此
        blocked_domains: [用户黑名单]         # 用户 network.deny_domains 直接写此
        ...（基底 allowed_ips/ports 等不动）
```

新增 `jiuwenclaw/agentserver/sandbox_policy_render.py`，提供：
- `render_runtime_policy()` —— 以基底 `windows-policy.yaml` 复制为副本（首次/基底升级时）。
- `get_sandbox_files_config()` / `set_sandbox_files_config(allow, deny)` —— 直接读写副本 `windows.filesystem.{allow_read,allow_write,deny_read,deny_write}`。set 时：用户白名单追加到 `allow_read`+`allow_write`（去重，保留基底必需集）；用户黑名单写入 `deny_read`+`deny_write`（覆盖用户段，不碰基底）；**清空用户配置 → 副本对应字段还原为基底原值**。
- `get_sandbox_network_config()` / `set_sandbox_network_config(disable_all, allow_domains, deny_domains)` —— 直接读写副本 `windows.network.egress`。set 时：`disable_all=true` → `default="deny"` 且 `allowed_domains=[]`；否则 `allowed_domains`←用户白名单、`blocked_domains`←用户黑名单（**直接写入**对应字段）；**清空 → 副本 egress 还原基底原值**（pypi/npmmirror，装包正常）。
- `_base_policy_path()` —— 打包基底路径（`_jiuwenbox_configs_dir()/"windows-policy.yaml"`）。
- `_runtime_copy_path()` —— `<OFFICE_CLAW_DATA_ROOT>/windows-policy.runtime.yaml`。
- `fingerprint_runtime_policy()` —— 副本内容 sha256，供 JiuwenBoxRunner 判断重 spawn。

**"用户配置段 vs 基底段"分离**：副本里需要区分"用户追加的"和"基底的"，否则 set 无法只覆盖用户段。两种实现：
- (a) 副本里**只存基底 + 用户追加的最终合并值**，set 时从基底重新合并（用户配置不单独存）——简单，但"取消某条用户白名单"需知道哪些是用户加的。
- (b) 副本里用一个**用户配置区**（如顶层 `user_overrides:` 段存 `files:{allow,deny}, network:{...}`），`render` 时把 `user_overrides` 合并进 `windows` 段，box-server 读的是合并后的 `windows` 段。

> **推荐 (b)**：副本顶层加 `user_overrides:` 段存用户原始配置，`render_runtime_policy()` 把它合并进 `windows` 段产出最终值。get 接口读 `user_overrides` 返回用户配置（不含基底）；set 接口写 `user_overrides` 后重渲染。这样用户配置与基底清晰分离，取消某条不丢基底。

**注入**：`config.yaml::sandbox.policy_file` 指向运行时副本（`update_sandbox_policy_file`），或经 `JIUWENBOX_POLICY_PATH` 注入 `JiuwenBoxRunner.ensure_running`。`_bootstrap_internal_jiuwenbox`（`agent_ws_server.py:301`）启动时先 `render_runtime_policy()`（确保副本存在且合并了 user_overrides）再 `ensure_running(policy_path=<副本>)`。

> **统一模型**：文件 ACL（per-sandbox 创建时读，`process.py:2899-3002`）和网络 egress（box-server 启动时读，`app.py` lifespan）**都从 root policy（副本）取**——per-sandbox policy 经 `_resolve_effective_policy`（`policy_data=None` → deep-copy root，`sandbox_manager.py:253-255`）继承 root，用户文件配置写在副本的 `windows.filesystem` 里，新沙箱自动继承。**不再需要 per-sandbox patch**。

### 3.2 sysop_builder 处理（最小改动）

- `create_sandbox_sysop_card` 的 `extra_params["policy"]` 当前组 Linux `filesystem_policy`（`bind_mounts`，`sysop_builder.py:310-322`）。
- Windows 下：**不传 policy patch**（`sys.platform=="win32"` 时 policy 留空 `{}` 或不传），让 `_resolve_effective_policy` 走 "None → root 副本" 路径，文件 ACL 全由 root 副本提供。
- Linux 分支一行不动（R5 硬约束：`build_filesystem_policy` 原样保留）。

### 3.3 接口契约（8 个 WS req_method + Python handler）

在 `schema/message.py:ReqMethod` 新增（对齐 `permissions.*` 命名）：

```python
SANDBOX_ENABLED_GET        = "sandbox.enabled.get"
SANDBOX_ENABLED_SET        = "sandbox.enabled.set"
SANDBOX_STARTUP_MODE_GET   = "sandbox.startup_mode.get"     # 新增: 沙箱启动方式
SANDBOX_STARTUP_MODE_SET   = "sandbox.startup_mode.set"
SANDBOX_FILES_GET          = "sandbox.files.get"
SANDBOX_FILES_SET          = "sandbox.files.set"
SANDBOX_NETWORK_GET        = "sandbox.network.get"
SANDBOX_NETWORK_SET        = "sandbox.network.set"
```

新增 `jiuwenclaw/agentserver/sandbox_config_rpc.py`（照抄 `permissions/config_rpc.py` 结构），dispatch 8 方法。传输走 agent-server WebSocket（与 `permissions.*` 同帧格式），非 HTTP REST——relay-claw 团队后续按 `permissions.*` 同款 `sendRequestToTarget(target, '<req_method>', params)` 对接。

> 统一返回 `AgentResponse`：`ok=true` + `payload`；失败 `ok=false` + `payload={"error","code"}`（与 `permissions/config_rpc.py:_ok/_err` 一致）。所有 set 成功后返回**生效后的最新全量配置**（供前端回显）。

#### 接口1：沙箱开关 + 启动方式（基础配置，存 config.yaml）

> **语义区分（用户确认）**：`enabled` = 是否开启沙箱（`false`→不开启沙箱走 LOCAL，`true`→开启走 SANDBOX）；`startup_mode` = 开启沙箱时 box-server 的拉起方式（`internal`=agent-server 内部拉起，`external`=K8s/外部部署拉起，默认 `internal`）。两者独立，都开放给 officeAce 配置。

**1a. `sandbox.enabled.get`** — 读取开关
- params：`{}`（无）
- 返回：`{"enabled": <bool>}`（取 `config.yaml::sandbox.enabled`，默认 false）

**1b. `sandbox.enabled.set`** — 设置开关
- params：`{"enabled": <bool>}`（必填布尔；非布尔返回 `enabled must be boolean`，code=BAD_REQUEST）
- 语义：写 `config.yaml::sandbox.enabled`（复用 `update_sandbox_runtime({"enabled": v})`）。`enabled=false`→不开启沙箱（命令走 LOCAL 模式）；`enabled=true`→开启（命令走 SANDBOX 模式，`interface_deep.py:2585-2586`）。
- 生效动作：热重载 agent（`agent.reload_config`，已有）。**不重建沙箱**——开关只影响**新会话**选 LOCAL/SANDBOX；已在跑的沙箱/会话保持原模式到结束。
- 返回：`{"enabled": <bool>}`

**1c. `sandbox.startup_mode.get`** — 读取启动方式
- params：`{}`（无）
- 返回：`{"startup_mode": <"internal"|"external">}`（复用 `get_sandbox_startup_mode()`，默认 `internal`）

**1d. `sandbox.startup_mode.set`** — 设置启动方式
- params：`{"startup_mode": <"internal"|"external">}`（必填；非合法值返回 `startup_mode must be one of (internal, external)`，code=BAD_REQUEST，复用 `update_sandbox_startup_mode` 的校验）
- 语义：写 `config.yaml::sandbox.startup_mode`（复用 `update_sandbox_startup_mode(mode)`）。`internal`=agent-server 内部拉起 box-server 子进程；`external`=box-server 由 K8s/外部部署，agent-server 只健康检查不 spawn。
- 生效动作：热重载 agent；**若当前 box-server 由本 runner 拉起且模式从 `internal`→`external`**，需 `JiuwenBoxRunner.stop()` 停掉自拉起的 box-server（external 模式不 spawn）；`external`→`internal` 则下次需要时 `ensure_running` 拉起。模式切换的生效可在下次 `_bootstrap_internal_jiuwenbox` 或显式触发。
- 返回：`{"startup_mode": <normalized>}`

#### 接口2：文件安全

**2a. `sandbox.files.get`** — 读取文件白/黑名单
- params：`{}`（无）
- 返回：`{"files": {"allow": [<path>, ...], "deny": [<path>, ...]}}`（读运行时副本的 `user_overrides.files`，即用户配置段，不含基底必需集）

**2b. `sandbox.files.set`** — 设置文件白/黑名单
- params：`{"allow": [<path:str>, ...], "deny": [<path:str>, ...]}`（两个 list 都必填，可为空 list 表示"清空"；list 元素去空白、去重；非 list 返回 `allow/deny must be list`）
- 语义：把用户配置写进运行时副本的 `user_overrides.files`，再 `render_runtime_policy()` 把它合并进 `windows.filesystem`：
  - `allow`（白名单）→ 合并进 `windows.filesystem.allow_read` + `allow_write`（保留基底必需集，不饿死沙箱）。
  - `deny`（黑名单）→ 合并进 `windows.filesystem.deny_read` + `deny_write`（NTFS 显式 Deny 优先，满足"优先黑名单"）。
  - `allow/deny` 都空 → 副本 `windows.filesystem.allow_read/write` 还原基底原值。
- 生效动作：① `set_sandbox_files_config` 写副本 `user_overrides.files`；② `render_runtime_policy()` 重渲染副本（合并进 `windows.filesystem`）；③ `recreate_all_sandboxes()` 销毁所有活沙箱——旧沙箱 ACL 已施加无法热改，新沙箱创建时读新副本 ACL。
- 返回：`{"files": {"allow": [...], "deny": [...]}}`（用户配置段）

#### 接口3：网络安全

**3a. `sandbox.network.get`** — 读取网络配置
- params：`{}`（无）
- 返回：`{"network": {"disable_all": <bool>, "allow_domains": [<str>, ...], "deny_domains": [<str>, ...]}}`（读运行时副本的 `user_overrides.network`，默认 `disable_all=false`、两个 list 空）

**3b. `sandbox.network.set`** — 设置网络配置
- params：
  ```
  {
    "disable_all": <bool>,            # 必填，是否拒绝所有网络访问
    "allow_domains": [<str>, ...],    # 必填，允许访问的域名（支持 *.example.com 通配符）
    "deny_domains": [<str>, ...]      # 必填，拒绝访问的域名
  }
  ```
  - `disable_all` 非布尔返回 `disable_all must be boolean`；`allow_domains`/`deny_domains` 非 list 返回 `allow_domains/deny_domains must be list`。
- 语义：把用户配置写进运行时副本的 `user_overrides.network`，再 `render_runtime_policy()` 合并进 `windows.network.egress`：
  - `disable_all=true` → `windows.network.egress.default="deny"` 且 `allowed_domains=[]`（WFP 全 Block，等价断网；`blocked_domains` 仍写 `deny_domains`）。
  - `disable_all=false` → `windows.network.egress.allowed_domains` ← **直接写入** `allow_domains`（用户要求直接写对应字段，非 append）；`blocked_domains` ← `deny_domains`（EgressFilter deny-then-allow，黑名单优先）。
  - `allow_domains`/`deny_domains` 都空 → 副本 `egress` 还原基底 `windows-policy.yaml` 的 egress（pypi/npmmirror），沙箱装包正常（用户要求"取消后装包正常"）。
- 生效动作：① `set_sandbox_network_config` 写副本 `user_overrides.network`；② `render_runtime_policy()` 重渲染副本；③ **重启 box-server**——`JiuwenBoxRunner.ensure_running` 检测 policy 内容指纹变化（§3.3.5）→ stop 旧进程 + spawn 新进程（重跑 lifespan 重建 `EgressFilter`，幂等 `ensure_windows_setup` 不重建 jbx-sandbox 用户）；④ 活沙箱由重启的 `shutdown_all_sandboxes` 副作用自动清（新 EgressFilter 只作用于下次 lazy 建的新沙箱）。
- 返回：`{"network": {"disable_all": ..., "allow_domains": [...], "deny_domains": [...]}}`

#### 派发接入

`agent_ws_server.py:_handle_agent_request_body` 加分支（对齐 `:805-809` permissions 分组）：
```python
if request.req_method in get_sandbox_config_req_methods():
    return _handle_sandbox_config(request)
```
`_handle_sandbox_config` 调 `dispatch_sandbox_config_request(request)`（`sandbox_config_rpc.py`），与 `dispatch_permissions_config_request` 同形态。`get_sandbox_config_req_methods()` 返回 8 个方法的 frozenset（4 组 get/set：`sandbox.{enabled,files,network,startup_mode}`）。

#### 3.3.1 伪代码：`sandbox_policy_render.py`（读写运行时副本，核心模块）

用户配置不存 config.yaml，直接存运行时副本的 `user_overrides:` 段；`render` 时合并进 `windows` 段。本模块提供 get/set/render 三组 API：

```python
# ---- sandbox_policy_render.py 新增 ----
import copy, hashlib, sys
from pathlib import Path
import yaml

from jiuwenbox.server.workspace import OFFICE_CLAW_DATA_ROOT
from jiuwenclaw.config import _jiuwenbox_configs_dir  # 复用基底探测

_RUNTIME_COPY_NAME = "windows-policy.runtime.yaml"
_BASE_POLICY_NAME = "windows-policy.yaml"
_USER_OVERRIDES_KEY = "user_overrides"   # 副本顶层存用户原始配置的段
_FILES_DEFAULTS = {"allow": [], "deny": []}
_NETWORK_DEFAULTS = {"disable_all": False, "allow_domains": [], "deny_domains": []}

def _base_policy_path() -> Path | None:
    configs = _jiuwenbox_configs_dir()
    if configs is None:
        return None
    p = configs / _BASE_POLICY_NAME
    return p if p.is_file() else None

def _runtime_copy_path() -> Path:
    root = OFFICE_CLAW_DATA_ROOT or (Path.home() / ".office-claw")
    root.mkdir(parents=True, exist_ok=True)
    return root / _RUNTIME_COPY_NAME

def _ensure_copy_exists() -> Path:
    """副本不存在时从基底复制一份 (含空 user_overrides)."""
    base_p = _base_policy_path()
    copy_p = _runtime_copy_path()
    if not copy_p.is_file():
        if base_p is None:
            return copy_p   # 无基底 (非 Windows/未装); 调用方自行回落
        base = yaml.safe_load(base_p.read_text(encoding="utf-8")) or {}
        base[_USER_OVERRIDES_KEY] = {"files": dict(_FILES_DEFAULTS),
                                     "network": dict(_NETWORK_DEFAULTS)}
        copy_p.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return copy_p

def _load_copy() -> dict:
    p = _ensure_copy_exists()
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

def _save_copy(data: dict) -> None:
    _runtime_copy_path().write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

# ---- get/set: 读写 user_overrides 段 (用户配置原始值) ----
def get_sandbox_files_config() -> dict:
    data = _load_copy()
    ov = data.get(_USER_OVERRIDES_KEY, {}) if isinstance(data, dict) else {}
    files = ov.get("files") if isinstance(ov, dict) else None
    if not isinstance(files, dict):
        return dict(_FILES_DEFAULTS)
    return {
        "allow": [str(p) for p in (files.get("allow") or []) if str(p).strip()],
        "deny":  [str(p) for p in (files.get("deny") or []) if str(p).strip()],
    }

def set_sandbox_files_config(allow: list, deny: list) -> dict:
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise ValueError("allow and deny must be lists")
    allow = [str(p) for p in allow if str(p).strip()]
    deny  = [str(p) for p in deny if str(p).strip()]
    data = _load_copy()
    ov = data.setdefault(_USER_OVERRIDES_KEY, {})
    ov["files"] = {"allow": allow, "deny": deny}
    _save_copy(data)
    render_runtime_policy()   # 合并进 windows 段
    return {"allow": allow, "deny": deny}

def get_sandbox_network_config() -> dict:
    data = _load_copy()
    ov = data.get(_USER_OVERRIDES_KEY, {}) if isinstance(data, dict) else {}
    net = ov.get("network") if isinstance(ov, dict) else None
    if not isinstance(net, dict):
        return dict(_NETWORK_DEFAULTS)
    return {
        "disable_all": bool(net.get("disable_all", False)),
        "allow_domains": [str(d) for d in (net.get("allow_domains") or []) if str(d).strip()],
        "deny_domains":  [str(d) for d in (net.get("deny_domains") or []) if str(d).strip()],
    }

def set_sandbox_network_config(disable_all: bool, allow_domains: list, deny_domains: list) -> dict:
    if not isinstance(disable_all, bool):
        raise ValueError("disable_all must be boolean")
    if not isinstance(allow_domains, list) or not isinstance(deny_domains, list):
        raise ValueError("allow_domains and deny_domains must be lists")
    net = {
        "disable_all": disable_all,
        "allow_domains": [str(d) for d in allow_domains if str(d).strip()],
        "deny_domains":  [str(d) for d in deny_domains if str(d).strip()],
    }
    data = _load_copy()
    ov = data.setdefault(_USER_OVERRIDES_KEY, {})
    ov["network"] = net
    _save_copy(data)
    render_runtime_policy()
    return net

# ---- render: 把 user_overrides 合并进 windows 段 (box-server 实际读的部分) ----
def render_runtime_policy() -> Path | None:
    """把 user_overrides 合并进 windows 段, 落地最终 policy 值. 返回副本路径.

    box-server 读副本的 windows 段; user_overrides 段只是存储, 运行时不读.
    """
    base_p = _base_policy_path()
    if base_p is None:
        return None
    data = _load_copy()   # 含 user_overrides
    win = data.setdefault("windows", {})
    fs = win.setdefault("filesystem", {})
    egress = win.setdefault("network", {}).setdefault("egress", {})
    ov = data.get(_USER_OVERRIDES_KEY) or {}
    files = ov.get("files") or {}
    network = ov.get("network") or {}

    # 文件白名单 → 合并 allow_read + allow_write (保留基底必需集, 去重)
    for key in ("allow_read", "allow_write"):
        existing = list(fs.get(key) or [])
        for p in (files.get("allow") or []):
            if p and p not in existing:
                existing.append(p)
        fs[key] = existing
    # 文件黑名单 → 合并 deny_read + deny_write
    for key in ("deny_read", "deny_write"):
        existing = list(fs.get(key) or [])
        for p in (files.get("deny") or []):
            if p and p not in existing:
                existing.append(p)
        fs[key] = existing

    # 网络
    if network.get("disable_all"):
        egress["default"] = "deny"
        egress["allowed_domains"] = []                    # 断网
        egress["blocked_domains"] = list(network.get("deny_domains") or [])
    else:
        allow = network.get("allow_domains") or []
        deny = network.get("deny_domains") or []
        if not allow and not deny:
            # 都空 → 回落基底原 egress (pypi/npmmirror), 装包正常: 不覆写
            pass
        else:
            egress["default"] = "deny"                     # 基底本就是 deny
            egress["allowed_domains"] = list(allow)        # 直接写入对应字段
            egress["blocked_domains"] = list(deny)        # 黑名单优先
    _save_copy(data)
    return _runtime_copy_path()

def fingerprint_runtime_policy() -> str | None:
    p = _runtime_copy_path()
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()
```

> **取消配置回落基底**：`render` 时 allow/deny 都空 → **不覆写** egress 的 allowed/blocked_domains，保留基底原值（pypi/npmmirror），沙箱装包正常——用户要求"取消后装包正常"的落点。文件段同理：用户 allow/deny 都空时，allow_read/write 仍含基底必需集（因 `existing` 从基底读起，空追加不丢基底）。

#### 3.3.2 伪代码：`sandbox_config_rpc.py`（dispatch 6 方法）

照抄 `permissions/config_rpc.py` 的 `_ok/_err/dispatch` 结构：

```python
# ---- sandbox_config_rpc.py 新增 ----
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse
from jiuwenclaw.schema.message import ReqMethod

_SANDBOX_CFG_METHODS: frozenset[ReqMethod] = frozenset({
    ReqMethod.SANDBOX_ENABLED_GET,      ReqMethod.SANDBOX_ENABLED_SET,
    ReqMethod.SANDBOX_STARTUP_MODE_GET, ReqMethod.SANDBOX_STARTUP_MODE_SET,
    ReqMethod.SANDBOX_FILES_GET,        ReqMethod.SANDBOX_FILES_SET,
    ReqMethod.SANDBOX_NETWORK_GET,      ReqMethod.SANDBOX_NETWORK_SET,
})

def get_sandbox_config_req_methods() -> frozenset[ReqMethod]:
    return _SANDBOX_CFG_METHODS

def _ok(request: AgentRequest, payload: dict | None) -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id, channel_id=request.channel_id,
        ok=True, payload=payload or {}, metadata=request.metadata,
    )

def _err(request: AgentRequest, message: str, *, code: str = "BAD_REQUEST") -> AgentResponse:
    return AgentResponse(
        request_id=request.request_id, channel_id=request.channel_id,
        ok=False, payload={"error": message, "code": code}, metadata=request.metadata,
    )

async def _apply_sandbox_change(kind: str) -> None:
    """set 后的生效动作: kind ∈ {'enabled','files','network'}.

    - enabled: 仅热重载 agent, 不动沙箱/box-server (开关只影响新会话选 LOCAL/SANDBOX).
    - startup_mode: 热重载 agent; 若 internal→external 且 box-server 由本 runner 拉起, stop() 停掉;
      external→internal 则下次 ensure_running 时拉起. 不重建 jbx-sandbox 用户.
    - files: set_sandbox_files_config 已在 dispatch 里 render 过副本; 这里只显式销毁活沙箱
      (不重启 box-server; ACL 在沙箱创建时读, 副本改即可).
    - network: set_sandbox_network_config 已 render 过副本; 这里重启 box-server (重跑 lifespan
      重建 EgressFilter); 活沙箱由重启的 shutdown_all_sandboxes 副作用自动清, 不额外调
      recreate_all_sandboxes.
    - jbx-sandbox 用户从不重建 (安装期产物, ensure_windows_setup 幂等).
    """
    from jiuwenclaw.agentserver.sandbox_lifecycle import recreate_all_sandboxes
    from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner
    from jiuwenclaw.config import get_sandbox_startup_mode
    if kind == "enabled":
        # 热重载 agent (reload_config 已有, 经 agent.reload_config req_method 触发或直接调)
        return
    if kind == "startup_mode":
        # 模式切换: internal→external 停掉自拉起的 box-server; external→internal 下次拉起.
        runner = JiuwenBoxRunner.instance()
        if get_sandbox_startup_mode() == "external" and runner._owns_process:
            await runner.stop()   # external 模式 agent-server 不 spawn
        # internal 时下次 _bootstrap 或显式 ensure_running 拉起; 这里不主动拉 (留给 bootstrap)
        return
    if kind == "network":
        # 重启 box-server: ensure_running 检测 policy 内容指纹变化 → stop+spawn.
        # 重启的 lifespan shutdown 会调 shutdown_all_sandboxes 自动清活沙箱 runner,
        # 不额外调 recreate_all_sandboxes (避免双重清理). jbx-sandbox 用户不重建 (幂等).
        runner = JiuwenBoxRunner.instance()
        await runner.ensure_running(
            host=runner._host, port=runner._port,
            startup_mode="internal",
            policy_path=runner._spawned_policy_path,   # path 不变, 但内容变了
            timeout=120.0,
        )
        return
    # files: 不重启 box-server, 但要显式销毁活沙箱 (新 ACL 只作用于新沙箱)
    await recreate_all_sandboxes()

def dispatch_sandbox_config_request(request: AgentRequest) -> AgentResponse:
    """执行一条 sandbox 配置 RPC."""
    from jiuwenclaw.config import (
        get_sandbox_runtime, update_sandbox_runtime,
        get_sandbox_startup_mode, update_sandbox_startup_mode,
    )
    from jiuwenclaw.agentserver.sandbox_policy_render import (
        get_sandbox_files_config, set_sandbox_files_config,
        get_sandbox_network_config, set_sandbox_network_config,
    )
    import asyncio

    m = request.req_method
    params = request.params if isinstance(request.params, dict) else {}
    tag = m.value if m is not None else ""

    try:
        # ---- 接口1a/1b: 沙箱开关 (存 config.yaml, 基础配置) ----
        if m == ReqMethod.SANDBOX_ENABLED_GET:
            return _ok(request, {"enabled": bool(get_sandbox_runtime().get("enabled"))})

        if m == ReqMethod.SANDBOX_ENABLED_SET:
            value = params.get("enabled")
            if not isinstance(value, bool):
                return _err(request, "enabled must be boolean")
            update_sandbox_runtime({"enabled": value})   # 开关写 config.yaml
            asyncio.get_event_loop().create_task(_apply_sandbox_change("enabled"))
            return _ok(request, {"enabled": value})

        # ---- 接口1c/1d: 沙箱启动方式 (存 config.yaml, 基础配置) ----
        if m == ReqMethod.SANDBOX_STARTUP_MODE_GET:
            return _ok(request, {"startup_mode": get_sandbox_startup_mode()})

        if m == ReqMethod.SANDBOX_STARTUP_MODE_SET:
            mode = params.get("startup_mode")
            if not isinstance(mode, str) or not mode.strip():
                return _err(request, "startup_mode is required")
            try:
                normalized = update_sandbox_startup_mode(mode)  # 校验 internal/external
            except ValueError as e:
                return _err(request, str(e))
            asyncio.get_event_loop().create_task(_apply_sandbox_change("startup_mode"))
            return _ok(request, {"startup_mode": normalized})

        # ---- 接口2: 文件安全 (读写运行时副本, 不碰 config.yaml) ----
        if m == ReqMethod.SANDBOX_FILES_GET:
            return _ok(request, {"files": get_sandbox_files_config()})

        if m == ReqMethod.SANDBOX_FILES_SET:
            allow = params.get("allow")
            deny = params.get("deny")
            if not isinstance(allow, list) or not isinstance(deny, list):
                return _err(request, "allow and deny must be lists")
            files = set_sandbox_files_config(allow, deny)   # 写副本 user_overrides + render
            asyncio.get_event_loop().create_task(_apply_sandbox_change("files"))
            return _ok(request, {"files": files})

        # ---- 接口3: 网络安全 (读写运行时副本) ----
        if m == ReqMethod.SANDBOX_NETWORK_GET:
            return _ok(request, {"network": get_sandbox_network_config()})

        if m == ReqMethod.SANDBOX_NETWORK_SET:
            disable_all = params.get("disable_all")
            allow_domains = params.get("allow_domains")
            deny_domains = params.get("deny_domains")
            if not isinstance(disable_all, bool):
                return _err(request, "disable_all must be boolean")
            if not isinstance(allow_domains, list) or not isinstance(deny_domains, list):
                return _err(request, "allow_domains and deny_domains must be lists")
            network = set_sandbox_network_config(
                disable_all, allow_domains, deny_domains)  # 写副本 user_overrides + render
            asyncio.get_event_loop().create_task(_apply_sandbox_change("network"))
            return _ok(request, {"network": network})

    except ValueError as e:
        return _err(request, str(e))
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("[%s] %s", tag, e)
        return _err(request, str(e), code="INTERNAL_ERROR")

    return _err(request, "unknown sandbox req_method", code="BAD_REQUEST")
```

> 注：`_apply_sandbox_change` 用 `create_task` 异步触发生效（不阻塞 RPC 响应，前端立即收到 ok）。若要"配置确认应用后"再回 ok，改为 `await _apply_sandbox_change(kind)`。推荐异步触发——沙箱重建/box-server 重启耗时数秒，不应让 RPC 等待。set 接口已同步完成"写副本 + render"，故 `_apply_sandbox_change` 只做"生效"（销毁沙箱/重启 box-server），不重复 render。

#### 3.3.4 伪代码：`agent_ws_server.py` 派发分支 + 启动渲染

```python
# ---- agent_ws_server.py: _handle_agent_request_body 内, 紧跟 permissions 分支 (约 :805-809) ----
from jiuwenclaw.agentserver.sandbox_config_rpc import get_sandbox_config_req_methods, dispatch_sandbox_config_request

if request.req_method in get_sandbox_config_req_methods():
    return dispatch_sandbox_config_request(request)   # 同 _handle_permissions_config 形态

# ---- agent_ws_server.py: _bootstrap_internal_jiuwenbox 内, ensure_running 之前 (约 :406 前) ----
from jiuwenclaw.agentserver.sandbox_policy_render import render_runtime_policy
runtime_policy = render_runtime_policy()           # 启动时先把用户配置渲染进副本
if runtime_policy is not None:
    policy_path = runtime_policy                    # 用副本替代打包基底
    # 落盘到 config.yaml::sandbox.policy_file? 或直接传 ensure_running(policy_path=runtime_policy)
# (Windows 下 sys.platform=="win32" 才渲染; Linux 不走 windows-policy)
...
ok = await runner.ensure_running(
    host=host, port=port, startup_mode="internal",
    policy_path=policy_path,   # = 运行时副本 (带用户配置), 或打包基底 (无用户配置)
    timeout=120.0,
)
```

#### 3.3.5 伪代码：`jiuwenbox_runner.py` 内容指纹检测

现有 `ensure_running` 只比 `_spawned_policy_path`（path 字符串）；同一副本 path 不变但内容变（网络配置改了）时不会重 spawn。补指纹：

```python
# ---- jiuwenbox_runner.py: __init__ 内 ----
self._spawned_policy_fingerprint: Optional[str] = None

# ---- ensure_running 的 owned_match 判定 (现 :360-367) ----
# 新增: 计算 policy_path 内容指纹, 与上次 spawn 时存的比对
def _policy_fingerprint(path) -> Optional[str]:
    if path is None or not Path(path).is_file():
        return None
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

new_fp = _policy_fingerprint(policy_path)
owned_match = (
    self._process is not None and self._process.returncode is None
    and self._owns_process
    and self._host == host and self._port == port
    and self._spawned_policy_path == policy_path
    and self._spawned_policy_fingerprint == new_fp        # 新增: 内容也一致
)
if owned_match:
    # 复用 (path + 内容都没变)
    ...
else:
    # path 变 或 内容变 → stop 旧 + spawn 新
    ...
    self._spawned_policy_path = policy_path
    self._spawned_policy_fingerprint = new_fp            # spawn 后记下指纹
```

> 注：`sandbox_policy_render.fingerprint_runtime_policy()` 可复用为指纹计算；runner 内自带 `_policy_fingerprint` 是为不耦合 jiuwenbox 包（runner 只依赖 stdlib+httpx）。两者选一。

#### 3.3.6 relay-claw 调用示例（交付给 relay-claw 团队的对接样板）

relay-claw 团队按 `permissions.*` 同款对接：在 `DefaultRelayClawSecurityClient` 加方法 + `routes/config.ts` 加 Fastify 路由。下面是完整样板（TS，对齐 `relayclaw-security-proxy.ts:738 sendRequestToTarget` 与 `routes/config.ts:267 PATCH /api/config/relayclaw/security`）：

**(1) client 方法**（`packages/api/src/routes/relayclaw-security-proxy.ts`，仿 `getEnabledFromTarget`/`applyPermissionsPatchToTarget`）：

```typescript
// 仿 permissions.enabled.get / set
export interface SandboxConfig {
  enabled: boolean;
  startup_mode: 'internal' | 'external';
  files: { allow: string[]; deny: string[] };
  network: { disable_all: boolean; allow_domains: string[]; deny_domains: string[] };
}

// 在 DefaultRelayClawSecurityClient 内 (复用 listLiveTargets / sendRequestToTarget)
async getSandboxConfig(): Promise<SandboxConfig> {
  const targets = await this.listLiveTargets(true);
  if (targets.length === 0) throw new Error(NO_LIVE_RUNTIME_ERROR);
  const t = targets[0];
  const [enabled, startup_mode, files, network] = await Promise.all([
    this.sendRequestToTarget(t, 'sandbox.enabled.get', {}),
    this.sendRequestToTarget(t, 'sandbox.startup_mode.get', {}),
    this.sendRequestToTarget(t, 'sandbox.files.get', {}),
    this.sendRequestToTarget(t, 'sandbox.network.get', {}),
  ]);
  return {
    enabled: enabled.payload.enabled,
    startup_mode: startup_mode.payload.startup_mode,
    files: files.payload.files,
    network: network.payload.network,
  };
}

async setSandboxEnabled(enabled: boolean): Promise<void> {
  const targets = await this.listLiveTargets(true);
  await this.sendRequestToTarget(targets[0], 'sandbox.enabled.set', { enabled });
}

async setSandboxStartupMode(mode: 'internal' | 'external'): Promise<void> {
  const targets = await this.listLiveTargets(true);
  await this.sendRequestToTarget(targets[0], 'sandbox.startup_mode.set', { startup_mode: mode });
}

async setSandboxFiles(allow: string[], deny: string[]): Promise<void> {
  const targets = await this.listLiveTargets(true);
  await this.sendRequestToTarget(targets[0], 'sandbox.files.set', { allow, deny });
}

async setSandboxNetwork(
  disable_all: boolean, allow_domains: string[], deny_domains: string[],
): Promise<void> {
  const targets = await this.listLiveTargets(true);
  await this.sendRequestToTarget(
    targets[0], 'sandbox.network.set', { disable_all, allow_domains, deny_domains },
  );
}
```

**(2) Fastify 路由**（`packages/api/src/routes/config.ts`，仿 `:267 PATCH /api/config/relayclaw/security`）：

```typescript
// GET /api/config/sandbox —— 读取全部沙箱配置 (开关 + 启动方式 + 文件 + 网络)
app.get('/api/config/sandbox', async (request, reply) => {
  const operator = resolveTrustedUserId(request);
  if (!operator) { reply.status(400); return { error: 'Identity required' }; }
  try {
    const config = await relayClawSecurityClient.getSandboxConfig();
    return { sandbox: config };
  } catch (err) {
    reply.status(502);
    return { error: err instanceof Error ? err.message : String(err) };
  }
});

// PATCH /api/config/sandbox —— 部分更新 (任一/多个字段)
// body: { enabled?, startup_mode?, files?:{allow,deny}, network?:{disable_all,allow_domains,deny_domains} }
const sandboxPatchSchema = z.object({
  enabled: z.boolean().optional(),
  startup_mode: z.enum(['internal', 'external']).optional(),
  files: z.object({ allow: z.array(z.string()), deny: z.array(z.string()) }).optional(),
  network: z.object({
    disable_all: z.boolean(),
    allow_domains: z.array(z.string()),
    deny_domains: z.array(z.string()),
  }).optional(),
});

app.patch('/api/config/sandbox', async (request, reply) => {
  const parsed = sandboxPatchSchema.safeParse(request.body);
  if (!parsed.success) { reply.status(400); return { error: 'Invalid request', details: parsed.error.issues }; }
  const operator = resolveTrustedUserId(request);
  if (!operator) { reply.status(400); return { error: 'Identity required' }; }

  try {
    const p = parsed.data;
    if (typeof p.enabled === 'boolean') {
      await relayClawSecurityClient.setSandboxEnabled(p.enabled);
    }
    if (p.startup_mode) {
      await relayClawSecurityClient.setSandboxStartupMode(p.startup_mode);
    }
    if (p.files) {
      await relayClawSecurityClient.setSandboxFiles(p.files.allow, p.files.deny);
    }
    if (p.network) {
      await relayClawSecurityClient.setSandboxNetwork(
        p.network.disable_all, p.network.allow_domains, p.network.deny_domains,
      );
    }
    recordAudit('沙箱配置', '修改沙箱配置', operator);
    const config = await relayClawSecurityClient.getSandboxConfig();
    return { sandbox: config };
  } catch (err) {
    reply.status(502);
    return { error: err instanceof Error ? err.message : String(err) };
  }
});
```

**(3) 前端调用**（officeAce 页面调上述 Fastify 路由，示例 fetch）：

```typescript
// 读取
const res = await fetch('/api/config/sandbox', { credentials: 'include' });
const { sandbox } = await res.json();
// sandbox = { enabled, startup_mode, files:{allow,deny}, network:{disable_all,allow_domains,deny_domains} }

// 更新 (示例: 关掉网络总开关)
await fetch('/api/config/sandbox', {
  method: 'PATCH', credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ network: { disable_all: true, allow_domains: [], deny_domains: [] } }),
});
```

> **说明给 relay-claw 团队**：jiuwenclaw 侧已提供 8 个 WS 接口（`sandbox.{enabled,startup_mode,files,network}.{get,set}`），帧格式与 `permissions.*` 完全一致。relay-claw 侧只需：(1) 在 `DefaultRelayClawSecurityClient` 加 5 个方法（getSandboxConfig + 4 个 set）；(2) 在 `routes/config.ts` 加 `GET/PATCH /api/config/sandbox` 两个路由；(3) 前端 Hub 配置页加沙箱分区。set 成功后 jiuwenclaw 自动触发生效（热重载/销毁沙箱/重启 box-server），relay-claw 侧无需额外处理生效——返回的 ok 即表示"已写入并触发应用"。

### 3.4 set 后的"生效"动作汇总（关键，见 §1.3）

| 变更 | 生效路径 | 是否重建 jbx-sandbox 用户 | 是否销毁活沙箱实例 | 是否重启 box-server |
|---|---|---|---|---|
| `enabled` | 热重载 agent（`agent.reload_config`），`_create_sys_operation` 下轮读新值选 LOCAL/SANDBOX | 否 | 否（只影响新会话选模式）| 否 |
| `startup_mode` | 热重载 agent；`internal→external` 且 box-server 由本 runner 拉起时 `stop()` 停掉（external 不 spawn）；`external→internal` 下次 `_bootstrap` 拉起 | 否 | 否（模式切换不动活沙箱，只影响 box-server 拉起方式）| 视方向（internal→external 停，external→internal 下次起）|
| `files` | 重渲染运行时副本（§3.1）→ 显式销毁活沙箱（`recreate_all_sandboxes`，调 box-server `DELETE /api/v1/sandboxes/{id}`）| 否 | **是**（新沙箱创建时读新副本 ACL；旧沙箱 ACL 已施加需重建才应用新 deny/allow）| 否（ACL 在沙箱创建时读，root 副本改了即可，不必重启 box-server）|
| `network` | 重渲染副本 → 重启 box-server（`JiuwenBoxRunner` 重 spawn，重跑 lifespan 重建 EgressFilter）| 否（`ensure_windows_setup` 幂等，用户已存在则跳过）| **是**（重启时 lifespan shutdown 调 `shutdown_all_sandboxes` 自动清旧 runner，非独立步骤；新 EgressFilter 作用于下次 lazy 建的新沙箱）| **是** |

> **jbx-sandbox 用户不重建**（你的确认）：jbx-sandbox 用户/密码/预装 ACL/WFP filter 是安装期一次性产物。box-server 重启时 lifespan 重跑 `ensure_windows_setup`（`app.py:300`）是**幂等**的，用户已存在则跳过。网络变更需重启的只是 box-server 进程（重建 EgressFilter），活沙箱 runner 进程被 lifespan shutdown 的 `shutdown_all_sandboxes()`（`app.py:428`）自动清掉，下次 exec 按需 lazy 建新沙箱。故网络变更**无需额外 `recreate_all_sandboxes()`**——重启自带清理副作用；文件变更则需显式调 `recreate_all_sandboxes()`（不重启 box-server，只清活沙箱让新 ACL 生效）。

新增 `sandbox_lifecycle.py` helper：`recreate_all_sandboxes()`（调 box-server `DELETE /api/v1/sandboxes/{id}`，下次 exec 按需 lazy 建，已有 lazy 机制 `agent_ws_server.py:447-450`）。**仅 `files` 变更调用**；`network` 变更靠重启自动覆盖。

### 3.5 windows-policy.yaml 的角色

`windows-policy.yaml` **保持打包模板不变**。它提供：
- 网络 egress **默认白名单**（pypi/npmmirror 等，沙箱 pip/npm 必需）—— 副本基底；用户 `allow_domains` 覆写 `allowed_domains` 字段；`disable_all=true` 清空。
- 文件 allow_read/allow_write **基础集**（workspace/skills/tool_paths）—— 副本基底；用户白名单合并进；用户黑名单 deny_* 叠加。
- 用户取消配置 → 副本回落基底原值。

## 4. 文件改动清单（仅 jiuwenclaw）

1. `jiuwenclaw/schema/message.py` — 加 8 个 `SANDBOX_*` ReqMethod（含 `STARTUP_MODE_GET/SET`）。
2. `jiuwenclaw/agentserver/sandbox_policy_render.py` — **新增（核心）**，读写运行时副本：
   - `render_runtime_policy()`：把副本 `user_overrides` 合并进 `windows` 段；首次从基底复制。
   - `get_sandbox_files_config()` / `set_sandbox_files_config(allow, deny)`：读写副本 `user_overrides.files`，set 后 render。
   - `get_sandbox_network_config()` / `set_sandbox_network_config(...)`：读写副本 `user_overrides.network`，set 后 render。
   - `fingerprint_runtime_policy()`：副本内容指纹。
3. `jiuwenclaw/agentserver/sandbox_config_rpc.py` — **新增**，dispatch 8 方法：
   - `enabled` → 复用 `config.py` 的 `get_sandbox_runtime`/`update_sandbox_runtime`（开关，存 config.yaml）。
   - `startup_mode` → 复用 `config.py` 的 `get_sandbox_startup_mode`/`update_sandbox_startup_mode`（启动方式，存 config.yaml）。
   - `files` / `network` → 调 `sandbox_policy_render` 的 get/set（读写运行时副本，不碰 config.yaml）。
   - set 后 `_apply_sandbox_change(kind)` 触发生效（enabled 热重载 / startup_mode 停拉 box-server / files 销毁沙箱 / network 重启 box-server）。
4. `jiuwenclaw/agentserver/agent_ws_server.py` — `_handle_agent_request_body` 加 sandbox 分组分支；`_bootstrap_internal_jiuwenbox` 启动时先 `render_runtime_policy()` 再 `ensure_running(policy_path=<副本>)`，并把副本路径写回 `config.yaml::sandbox.policy_file`（经 `update_sandbox_policy_file`）。
5. `jiuwenclaw/agentserver/sandbox_lifecycle.py` — 加 `recreate_all_sandboxes()`（仅 `files` 变更调用）。
6. `jiuwenclaw/agentserver/jiuwenbox_runner.py` — 补"policy 内容变化"检测：现有 `_spawned_policy_path` 只比 path 字符串，path 不变（同一副本文件）但内容变时需重 spawn。用内容指纹（sha256 副本文件）存 `self._spawned_policy_fingerprint`，`ensure_running` 比较。
7. `jiuwenclaw/agentserver/deep_agent/sysop_builder.py` — Windows 分支不传 policy patch（走 root 继承）；`sys.platform=="win32"` 守卫，Linux 不动。
8. `jiuwenclaw/config.py` — **不动用户文件/网络配置**；仅复用现有 `get_sandbox_runtime`/`update_sandbox_runtime`（`enabled` 开关仍存 config.yaml，是基础配置）。`_SANDBOX_RUNTIME_DEFAULTS` 不加 network/files 字段（这些不再走 config.yaml）。

## 5. 风险与约束

- **R5 不改 Linux**：§3.2 Windows 分支用 `sys.platform=="win32"` 守卫，Linux `build_filesystem_policy` 一行不动。
- **网络配置生效重**：需重启 box-server（§1.3），对用户表现为"改完网络配置后沙箱有几秒重建延迟"，可接受（沙箱本就按需 lazy 建）。
- **重启 box-server 不重建 jbx-sandbox 用户**：jbx-sandbox 用户/密码/预装 ACL/WFP filter 是**安装期一次性产物**（`win_setup.ensure_windows_setup`），box-server 重启时 lifespan 重跑 `ensure_windows_setup` 是**幂等**的（用户已存在则跳过），不重建用户。重启会清掉的只是**活沙箱 runner 进程**——lifespan shutdown 调 `shutdown_all_sandboxes()`（`app.py:428`）自动 SIGTERM/SIGKILL 旧 runner，下次 exec 按需 lazy 建新沙箱。故"网络变更销毁沙箱"由 box-server 重启**自动覆盖**，无需额外 `recreate_all_sandboxes()`（§3.4 网络行修正：重建沙箱动作是重启的副作用，非独立步骤）。
- **文件白名单饿死沙箱**：§2.3 用户白名单**合并**进基底（不覆盖必需集），避免用户只写几条导致 python/bash 读不了；取消回落基底。
- **配置一致性**：用户文件/网络配置统一存在运行时副本的 `user_overrides:` 段（不存 config.yaml），`render_runtime_policy()` 统一合并进 `windows` 段，文件与网络同源不分裂。config.yaml 只保留 `sandbox.enabled`（开关，基础配置）+ `sandbox.policy_file`（指向运行时副本）。
- **范围**：本次只做 jiuwenclaw 后端。relay-claw（officeAce）的 Fastify 路由 + 前端 UI 由 relay-claw 团队负责（另有团队），**不在本方案范围**；jiuwenclaw 侧只需把 8 个 WS 接口（`sandbox.{enabled,files,network,startup_mode}.{get,set}`）+ handler + 生效逻辑做好，供 relay-claw 团队按 `permissions.*` 同款对接（对接模板见 §1.1 的 `/api/config/relayclaw/security`）。本方案的"接口契约"（§3.3）即为交付给 relay-claw 团队的对接说明。

## 6. 已确认的决策点（用户答复）

1. 用户文件/网络黑白名单**直接写进 windows-policy 运行时副本的对应字段**（追加到各自归属字段后），不存 config.yaml。config.yaml 仅保留基础配置（`sandbox.enabled`/`startup_mode`/`url`/`type`/`policy_file`）。副本由基底复制，改副本不改源（升级安全）。✅
2. 文件"访问"粒度 = 读+写（白名单 allow_read+allow_write，黑名单 deny_read+deny_write）。✅
3. 网络：用户域名直接写入 allowed_domains/blocked_domains 字段，黑名单优先；用户取消后副本回落基底（pypi/npmmirror）保沙箱装包正常。✅
4. 实现范围 = 仅 jiuwenclaw 后端（供 relay-claw 团队对接，relay-claw 侧另有团队）。✅
5. 重启 box-server 不重建 jbx-sandbox 用户（安装期产物，`ensure_windows_setup` 幂等）；活沙箱由重启的 `shutdown_all_sandboxes` 副作用自动清。✅
6. **`enable_sand`（开关）→ `config.yaml::sandbox.enabled`**：`false`=不开启沙箱走 LOCAL，`true`=开启走 SANDBOX。**`startup_mode` 也开放给 officeAce**，默认 `internal`（box-server 由 agent-server 内部拉起，非 K8s/外部部署拉起）。两者是不同概念：enabled=是否开沙箱，startup_mode=开了怎么拉起 box-server。✅
