# 设计文档审查报告：fix:对接前端relay-claw接口

- **Commit**：`a3d0d2bdc400f31ad14f082911bb07f339af99e6`
- **作者/日期**：lby / 2026-07-30
- **规模**：1 文件，+812 行新增文档（`docs/sandbox-config-control.md`）
- **类型**：纯设计文档 commit（无代码改动）
- **后续落地 commit**：`f4089537`「fix:支持relay-claw配置沙箱启停，黑白名单等配置」（同日提交，736 增/6 删，9 文件）
- **审查方法**：`git show` 全量 diff + Read 真实文档 + 与 `a3d0d2bdc400f31ad14f082911bb07f339af99e6` commit 时刻的源码逐项核对引用（`process.py` / `win_proxy.py` / `sandbox_manager.py` / `jiuwenbox_runner.py` / `app.py` / `config.py` / `permissions/config_rpc.py` / `agent_ws_server.py`），并对照实现 commit `f4089537` 的实际落地。

---

## 一、概述

本 commit 是 Windows 沙箱 officeAce 配置控制链路的**设计文档**，定义了从 officeAce 前端到 jiuwenclaw 后端再到 jiuwenbox box-server 的完整配置下发与生效链路。文档覆盖 8 个 WS 接口契约、运行时 policy 副本渲染机制、文件 ACL / 网络 egress 两套生效路径，以及交付给 relay-claw 团队的对接样板（TS client + Fastify 路由 + 前端 fetch 示例）。

文档质量整体优秀：调研扎实、对既有架构理解准确、生效机制（per-sandbox vs root policy、ACL 创建时读 vs EgressFilter 启动时固化）分析清晰，每个设计决策都标注了"用户确认"点。代码引用（`process.py:2899-3002`、`win_proxy.py:EgressFilter.allow`、`sandbox_manager.py:253-255`、`jiuwenbox_runner.py:360-393` 等）逐项核对属实，与 `f4089537` 的落地实现高度一致（8 个 `ReqMethod` 枚举、`sandbox_policy_render.py` / `sandbox_config_rpc.py` 结构、`jiuwenbox_runner.py` 指纹检测均按设计落地）。

但设计层面存在若干**口径不一**（接口数 6 vs 8 反复横跳）、**与既有模板形态偏离**（dispatch 同步但用 `asyncio.get_event_loop().create_task` 派发异步生效，与 `permissions/config_rpc.py` 同步热重载模板不一致）、**安全考量不充分**（鉴权、配置校验、并发竞态基本未着墨）等问题，下面按维度展开。

---

## 二、变更范围

| 文件 | 行数 | 作用 |
|---|---|---|
| `docs/sandbox-config-control.md` | +812 新增 | officeAce 控制沙箱配置方案设计文档（背景/调研/决策/实现/接口契约/伪代码/交付样板） |

无代码改动。

---

## 三、设计文档内容概述

### 3.1 通信链路（§1.1）

文档厘清两层链路：officeAce(relay-claw Fastify:3004) → jiuwenclaw agent-server(WebSocket) → jiuwenbox box-server(HTTP) → 沙箱进程(ACL/WFP)。明确 relay-claw **不直连 box-server**，走 agent-server 的 WS `req_method` 帧。指出现成可照抄模板 `/api/config/relayclaw/security`（`routes/config.ts:251-289` → `relayclaw-security-proxy.ts` → WS 帧 → `_handle_permissions_config` → `permissions/config_rpc.py`）。

### 3.2 配置生效机制（§1.3，关键）

文档用一张表区分三种配置的生效路径：

| 配置 | 读取时机 | 变更如何生效 |
|---|---|---|
| 文件安全(ACL) | 沙箱**创建时**（`process.py:2899-3002`） | 销毁重建该沙箱即可 |
| 网络安全(WFP+代理) | box-server **启动时**（`app.py` lifespan → `EgressFilter`） | **需重启 box-server** |
| 沙箱开关 | `_create_sys_operation` 读 `sandbox.enabled` | 热重载 agent |

并据此修正 interface.txt 的"销毁重建沙箱"对网络安全**不充分**——这是全文最关键的洞察。

### 3.3 运行时副本机制（§3.1）

以打包 `windows-policy.yaml` 为基底，复制到 `<workspace>/windows-policy.runtime.yaml`，用户配置写进副本顶层 `user_overrides:` 段，`render_runtime_policy()` 合并进 `windows` 段。副本经 `config.yaml::sandbox.policy_file` 或 `JIUWENBOX_POLICY_PATH` 注入 box-server。文件/网络同源不分裂。

### 3.4 接口契约（§3.3，8 个 WS `req_method`）

`SANDBOX_{ENABLED,STARTUP_MODE,FILES,NETWORK}_{GET,SET}`，照抄 `permissions.*` 命名与帧格式。`sandbox_config_rpc.py` dispatch 8 方法，统一 `AgentResponse`。set 后 `_apply_sandbox_change(kind)` 异步触发生效（enabled 热重载 / startup_mode 停拉 box-server / files 销毁沙箱 / network 重启 box-server）。

### 3.5 交付样板（§3.3.6）

TS client 方法（`getSandboxConfig` + 4 个 set）、Fastify `GET/PATCH /api/config/sandbox`、前端 fetch 示例，配 zod schema 校验、`resolveTrustedUserId` 鉴权、`recordAudit` 审计。

---

## 四、设计评价（按维度，引用 file:line 标注 🔴/🟡/🟢）

### 4.1 🟢 调研扎实，代码引用属实

文档对既有架构的引用逐项核对属实：

- `process.py:2899-3002` 确实是 `_create_windows` 读 `policy.windows.filesystem.{allow_read,allow_write,deny_write,deny_read}` 并调 `win_acl.apply_sandbox_acl`（见 `git show a3d0d2bd:jiuwenbox/src/jiuwenbox/server/runtime/process.py` 该区间）。
- `win_proxy.py:EgressFilter` 的 `allow()` 方法（`git show a3d0d2bd:jiuwenbox/src/jiuwenbox/supervisor/win_proxy.py:100-182`）确实是 deny-then-allow 优先级，文档 §2.2 "黑名单优先天然满足"成立。
- `sandbox_manager.py:253-255` 的 `_resolve_effective_policy`（`policy_data=None` → deep-copy root）属实，§3.1 "per-sandbox policy 经继承 root，不再需要 per-sandbox patch" 的设计前提成立。
- `jiuwenbox_runner.py:360-393` 的 `owned_match` 判定确实只比 `_spawned_policy_path`（path 字符串），§3.3.5 "path 不变但内容变时不会重 spawn" 的诊断准确，补指纹的方案合理。
- `app.py:300` 的 `ensure_windows_setup`、`app.py:428` 的 `shutdown_all_sandboxes`、`app.py` lifespan 重建 `EgressFilter` 均属实。

### 4.2 🟢 生效机制分层清晰，修正了 interface.txt 的不足

`docs/sandbox-config-control.md:42-48` 的生效路径表是全文最有价值的设计点。interface.txt 笼统说"销毁沙箱并重建"，文档准确指出对**网络 egress**不充分（root policy 在 box-server 启动时固化，`win_proxy.py:466` `EgressFilter(egress, ingress)` 在 `serve_windows_proxy` 内构造），必须重启 box-server。§3.4 的生效汇总表（`docs/sandbox-config-control.md:759-764`）据此把 files/network/enabled/startup_mode 四种变更的"重建沙箱/重启 box-server/热重载"动作区分清楚，避免实现时一刀切。

### 4.3 🟢 运行时副本 + user_overrides 分离设计合理

`docs/sandbox-config-control.md:128-132` 推荐方案 (b)：副本顶层 `user_overrides:` 段存用户原始配置，`render_runtime_policy()` 合并进 `windows` 段。这解决了三个真问题：

1. "取消某条用户白名单"时能从 user_overrides 重渲染，不丢基底必需集（`docs/sandbox-config-control.md:132`）。
2. get 接口返回用户配置段（不含基底），前端回显干净（`docs/sandbox-config-control.md:191`）。
3. 基底升级时副本可重建而不残留用户旧值。

落地 commit `f4089537` 的 `sandbox_policy_render.py` 采用了此设计，且实测发现 `SecurityPolicy` 未设 `extra="forbid"`，`user_overrides` 段被 Pydantic v2 默认 `extra="ignore"` 静默忽略——文档虽未点出这一点，但落地时补了注释（见 `f4089537:sandbox_policy_render.py` docstring），设计方向正确。

### 4.4 🟡 接口数 6 vs 8 反复横跳（内部不一致）

文档对接口数的表述自相矛盾：

- `docs/sandbox-config-control.md:144`：「### 3.3 接口契约（**8 个** WS req_method + Python handler）」
- `docs/sandbox-config-control.md:232`：「`get_sandbox_config_req_methods()` 返回 **6 个**方法的 frozenset」
- `docs/sandbox-config-control.md:755`：「jiuwenclaw 侧已提供 **8 个** WS 接口（`sandbox.{enabled,startup_mode,files,network}.{get,set}`）」
- `docs/sandbox-config-control.md:779`：「加 **8 个** `SANDBOX_*` ReqMethod」
- `docs/sandbox-config-control.md:803`：「只需把 **6 个** WS 接口（`sandbox.{enabled,files,network}.{get,set}`）」

实际是 **8 个**（4 组 × get/set：enabled / startup_mode / files / network）。第 232 行和第 803 行的"6 个"是笔误（漏数 startup_mode 这组，第 803 行的展开式 `sandbox.{enabled,files,network}` 也漏了 startup_mode）。落地 commit `f4089537` 的 `_SANDBOX_CFG_METHODS` frozenset 含 8 个成员（`f4089537:jiuwenclaw/agentserver/sandbox_config_rpc.py:30-43`），实现正确，但文档口径不一会让对接方困惑。

### 4.5 🟡 dispatch 同步但用 `asyncio.get_event_loop().create_task` 派发，偏离 permissions 模板

`docs/sandbox-config-control.md:499`、`:514`、`:527`、`:544` 在**同步**的 `dispatch_sandbox_config_request` 内用 `asyncio.get_event_loop().create_task(_apply_sandbox_change(kind))` 派发异步生效。对照既有 `permissions/config_rpc.py:92` 的 `dispatch_permissions_config_request`，permissions 是**同步热重载**（`_hot_reload_permission_engine_from_config()` 直接调，见 `git show a3d0d2bd:jiuwenclaw/agentserver/permissions/config_rpc.py`）。

两个问题：
1. `asyncio.get_event_loop()` 在 Python 3.10+ 协程内已被弃用（无运行 loop 时会创建一个，3.12+ 会 `DeprecationWarning`），应改 `asyncio.get_running_loop()` 或 `asyncio.create_task()`。
2. dispatch 是从 `_handle_agent_request_body`（async）调用的，但文档 §3.3.4 的派发伪代码（`docs/sandbox-config-control.md:565-566`）`return dispatch_sandbox_config_request(request)` 直接返回 `AgentResponse`——这与既有 `_handle_permissions_config`（`agent_ws_server.py:1244`，async，内部 `ws.send`）的形态不一致。permissions 是 `_handle_permissions_config` 包了一层 async 做 wire 编码 + `ws.send`，dispatch 只返回 `AgentResponse`。sandbox 设计的 `_handle_sandbox_config` 应同样包 async 壳，文档未明确画出这层壳（§3.3.4 只给了 dispatch 分支的一行 `return`，漏了 wire 编码 + `ws.send`）。

落地 commit `f4089537` 实际补了 async 壳（`agent_ws_server.py` 加了 sandbox 分组分支 + `_handle_sandbox_config`），但文档未把这层壳画清楚，对接方照文档直抄会漏 `ws.send`。

### 4.6 🟡 `_apply_sandbox_change` 的 docstring 与函数体不符

`docs/sandbox-config-control.md:433`：docstring 写 `kind ∈ {'enabled','files','network'}`，但函数体（`:451-457`）实际处理了 `"startup_mode"` 分支。三处矛盾：
- docstring 漏列 `startup_mode`；
- §3.3.6 的交付说明（`:755`）说"set 成功后 jiuwenclaw 自动触发生效（热重载/销毁沙箱/重启 box-server）"——漏了 startup_mode 的"停拉 box-server"动作；
- §3.4 生效汇总表（`:759-764`）倒是 4 行齐全（含 startup_mode），与 docstring 矛盾。

实现 commit `f4089537` 的 `_apply_sandbox_change` docstring（`sandbox_config_rpc.py` 顶部模块 docstring）补全了 startup_mode，但设计文档内的口径不一仍在。

### 4.7 🟡 `recreate_all_sandboxes()` 与既有 `sandbox_lifecycle.py` 原则冲突，且无伪代码

`docs/sandbox-config-control.md:768` 要求新增 `sandbox_lifecycle.py` 的 `recreate_all_sandboxes()`（"调 box-server `DELETE /api/v1/sandboxes/{id}`"），但**没给伪代码**（§3.3.1/3.3.2/3.3.4/3.3.5 都给了伪代码，唯独 `recreate_all_sandboxes` 没给）。

更关键的是，既有 `sandbox_lifecycle.py`（`git show a3d0d2bd:jiuwenclaw/agentserver/sandbox_lifecycle.py`）的模块 docstring 明确**反对**"扫整张表删全部"：

> 设计原则是**"软清"**: 只删本 Python 进程缓存里的 sandbox, 不去 `GET /api/v1/sandboxes` 扫整张表删全部 — 这样多 jiuwenclaw 进程共用同一台 jiuwenbox 时, 不会出现"A 关停顺手把 B 的活跃 sandbox 也回收掉"的串台。

而 `recreate_all_sandboxes()` 要销毁**所有活沙箱**让新 ACL 生效，势必要扫全表（否则只删本进程缓存的沙箱，其他 jiuwenclaw 进程的沙箱仍是旧 ACL）。文档没回答：
1. 多 jiuwenclaw 进程共用一台 box-server 时，A 改文件配置会不会把 B 的活跃沙箱也销毁（串台）？
2. 如果只删本进程沙箱，那 B 的沙箱 ACL 不会更新——生效不完整。
3. `recreate_all_sandboxes` 该走 `GET /api/v1/sandboxes` 全表删，还是只删本进程缓存？文档说"销毁所有活沙箱"（`:199`、`:768`），但既有模块原则反对全表删。

这是设计未兜底的关键缺口。落地 commit `f4089537` 给 `sandbox_lifecycle.py` 加了 24 行（`f4089537 --stat` 显示 +24），具体如何处理需在实现审查时再看。

### 4.8 🟡 安全考量几乎缺失（鉴权/校验/并发）

文档对安全的着墨仅限：
- `docs/sandbox-config-control.md:84`：NTFS Deny 优先于 Allow（这是 ACL 机制，不是设计新增的安全措施）。
- `docs/sandbox-config-control.md:75`：EgressFilter deny 优先（同上）。
- §3.3.6 的 Fastify 路由样板有 `resolveTrustedUserId` + `recordAudit`（但这在 relay-claw 侧，不在本方案范围）。

未着墨的安全维度：
1. **WS 层鉴权**：`agent_ws_server` 收到 `sandbox.*` 帧时，谁有权调用？文档未说。permissions 同样没显式鉴权（隐式信任 relay-claw 的 WS 帧），但 sandbox 配置直接控制沙箱开关/断网/文件封锁，影响面更大。至少应限制 channel_id 来源（仅 `web`/特定 channel）或要求 `principal_user_id` 非空。
2. **配置校验**：`docs/sandbox-config-control.md:194` 文件路径只做"去空白、去重"，**未校验路径合法性**（绝对路径？是否存在？是否含 `..`？）。`docs/sandbox-config-control.md:213` 域名只做"去空白"，**未校验域名格式**（`*.example.com` 通配符位置、是否含端口、是否 IP）。文档 §3.3 接口2b/3b 的校验只到 `isinstance(list)`，过于薄弱。
3. **路径逃逸**：用户白名单写 `C:\` 或 `C:\Windows\System32` 会把整个系统盘放开给沙箱受限 token；黑名单写 `C:\Users\<user>\AppData` 可能饿死沙箱自身。文档 §2.3 只说"合并进基底必需集"，没设黑名单上限或危险路径拦截。
4. **并发竞态**：`set_sandbox_files_config` → `render_runtime_policy` → `_apply_sandbox_change('files')` 是"写副本 + 销毁沙箱"两步，并发两个 set 会出现副本读写竞态（`_load_copy` / `_save_copy` 无锁）。`asyncio.create_task` 派发的生效动作之间也可能交错（files set 后沙箱还在重建，network set 又重启 box-server）。文档完全没提并发控制。
5. **TOCTOU**：`render_runtime_policy` 读副本 → 合并 → 写副本，非原子。多 jiuwenclaw 进程共用同一副本路径（`<OFFICE_CLAW_DATA_ROOT>/windows-policy.runtime.yaml`）时尤其危险——文档 §2.1 说"多实例不串台"靠的是"改副本不改源"，但**多个 agent-server 实例会共用同一份副本**（同一 `OFFICE_CLAW_DATA_ROOT`），反而引入跨进程读写竞态。文档没考虑多 agent-server 实例场景。

### 4.9 🟡 `enabled` 默认值与代码不一致

`docs/sandbox-config-control.md:169`：`sandbox.enabled.get` 返回"取 `config.yaml::sandbox.enabled`，**默认 true**"。interface.txt 也说"默认开启"。但 `git show a3d0d2bd:jiuwenclaw/config.py:1190` 的 `_SANDBOX_RUNTIME_DEFAULTS` 里 `"enabled": False`，`_ensure_sandbox_runtime_shape` 缺省填 `False`。

即 `get_sandbox_runtime().get("enabled")` 在 config.yaml 未显式写 `enabled` 时返回 `False`，不是 `True`。设计文档的接口契约与代码默认值相反。落地 commit `f4089537` 的 `SANDBOX_ENABLED_GET` 实现是 `bool(get_sandbox_runtime().get("enabled"))`（照文档写），所以**实际行为是默认 False**，与文档契约"默认 true"矛盾。这是个会让前端困惑的语义 bug——前端读到 enabled=false 以为沙箱关了，实际只是字段缺失。

### 4.10 🟢 偏离 interface.txt 的两处设计决策都有合理论证

文档两处**刻意偏离** interface.txt：
1. interface.txt 说"写入到 `windows-policy.yaml`，追加到基础配置后"（源模板），文档改为写**运行时副本**（`docs/sandbox-config-control.md:61-69`）。理由：升级安全（不污染打包模板）、多实例不串台。
2. interface.txt 说"3 个对外接口"，文档扩为 **8 个 WS req_method**（4 组 get/set）。理由：relay-claw 已有 `permissions.*` 的 WS 帧机制，照抄模板比新增 3 个 REST 接口更一致；且 get/set 分离让前端可回显。

两处偏离都标注了"用户确认"（§6 决策点 1/4），属于设计主动纠偏，合理。

### 4.11 🟢 Linux 不动守卫明确

`docs/sandbox-config-control.md:142`、`:798` 明确 Windows 分支用 `sys.platform=="win32"` 守卫，Linux `build_filesystem_policy` 一行不动（R5 硬约束）。落地 commit `f4089537` 的 `sysop_builder.py` 确实加了 17 行 Windows 守卫（`f4089537 --stat` 显示 +17），与设计一致。

---

## 五、优点

1. **调研深度罕见**：文档不是空泛的"方案设计"，而是逐行读了 `process.py` / `win_proxy.py` / `sandbox_manager.py` / `jiuwenbox_runner.py` / `app.py` 的真实代码后写的，引用行号准确，生效机制（创建时读 vs 启动时固化）分析到位。这是高质量设计文档的基石。
2. **生效路径分层是核心贡献**：§1.3 的三配置生效路径表 + §3.4 的四动作汇总表，把"销毁沙箱 / 重启 box-server / 热重载 agent"三个动作与每种配置精确对应，避免了实现时"一刀切销毁沙箱"的错误（对 network 不充分）。这一洞察直接驱动了 §3.3.5 的指纹检测设计。
3. **user_overrides 分离设计优雅**：副本顶层 user_overrides 段存用户原始配置，render 合并进 windows 段，既保证 get 返回干净、取消不丢基底，又让 box-server 只读 windows 段无感。落地实现验证了 Pydantic v2 `extra="ignore"` 静默吞掉 user_overrides，设计可行。
4. **交付样板完整**：§3.3.6 给了 TS client 方法 + Fastify 路由 + zod schema + 前端 fetch 三层完整样板，relay-claw 团队可直接照抄。比纯文字描述的接口契约可落地得多。
5. **决策点显式标注**：§6 列了 6 个"已确认的决策点"，每个都标 ✅，让审查者能判断哪些是设计主动决策、哪些是用户拍板。降低了"设计者擅自决策"的风险。
6. **伪代码充分**：§3.3.1（render）、§3.3.2（rpc）、§3.3.4（派发）、§3.3.5（指纹）都给了可直接照着写的 Python 伪代码，落地 commit 与伪代码结构高度一致。

---

## 六、问题与风险

### 6.1 设计层面问题

1. **接口数口径不一**（§4.4）：6 vs 8 反复，第 232/803 行漏数 startup_mode。对接方照"6 个"实现会漏 startup_mode 接口。
2. **`_apply_sandbox_change` docstring 漏 startup_mode**（§4.6）：docstring 写 `kind ∈ {'enabled','files','network'}`，函数体却有 startup_mode 分支。
3. **`recreate_all_sandboxes` 无伪代码 + 与既有 `sandbox_lifecycle.py` 原则冲突**（§4.7）：既有模块明确反对全表删沙箱（防串台），但 files 变更要销毁所有活沙箱。多 jiuwenclaw 进程共用 box-server 时如何不串台？文档没答。
4. **dispatch 派发形态与 permissions 模板不一致**（§4.5）：dispatch 同步返回 `AgentResponse`，但用 `asyncio.get_event_loop().create_task` 派发异步生效；§3.3.4 派发伪代码漏了 async 壳（wire 编码 + `ws.send`）。
5. **`enabled` 默认值与代码矛盾**（§4.9）：文档说默认 true，代码 `_SANDBOX_RUNTIME_DEFAULTS["enabled"]=False`。前端读到 false 会误以为沙箱关了。

### 6.2 安全层面风险

1. **WS 层无鉴权**（§4.8-1）：`sandbox.*` 帧谁都能发，直接控制沙箱开关/断网。
2. **配置校验薄弱**（§4.8-2）：路径不校验绝对/存在/`..`，域名不校验格式/通配符位置。
3. **路径逃逸**（§4.8-3）：白名单写 `C:\` 放开全盘，黑名单写沙箱自身依赖目录饿死沙箱。
4. **并发竞态**（§4.8-4）：副本读写无锁，`create_task` 派发的生效动作可能交错。
5. **多实例串台**（§4.8-5）：多 agent-server 共用同一副本路径，跨进程读写竞态；`recreate_all_sandboxes` 全表删会销毁其他实例的沙箱。

### 6.3 与实现 commit `f4089537` 的对应关系

- ✅ `ReqMethod` 8 个枚举（`f4089537:message.py +14`）——与设计一致。
- ✅ `sandbox_policy_render.py` 新增 353 行——与设计 §3.3.1 伪代码结构一致，且补了 `SecurityPolicy extra="ignore"` 的实测注释。
- ✅ `sandbox_config_rpc.py` 新增 251 行——与设计 §3.3.2 伪代码结构一致。
- ✅ `jiuwenbox_runner.py` +29 行——指纹检测按 §3.3.5 落地。
- ✅ `sysop_builder.py` +17 行——Windows 守卫按 §3.2 落地。
- ✅ `agent_ws_server.py` +38 行——派发分支 + bootstrap 渲染按 §3.3.4 落地。
- ✅ `sandbox_lifecycle.py` +24 行——`recreate_all_sandboxes` 落地（设计未给伪代码，实现自行补全）。
- ✅ `config.yaml` +8 行——`policy_file` 指向运行时副本。
- ✅ `windows-policy.yaml` +8/-? ——基底模板微调。

设计与落地高度对应，设计文档起到了真正的"施工图"作用。

---

## 七、改进建议

1. **统一接口数口径**：全文搜替换"6 个"为"8 个"（或显式说明"4 组 × get/set = 8 个 req_method"），第 232 行的 `get_sandbox_config_req_methods()` 注释和第 803 行的展开式 `sandbox.{enabled,files,network}` 都要补 startup_mode。
2. **补 `_apply_sandbox_change` docstring**：把 `kind ∈ {'enabled','files','network'}` 改为 `kind ∈ {'enabled','startup_mode','files','network'}`，§3.3.6 交付说明也补"startup_mode 停拉 box-server"动作。
3. **补 `recreate_all_sandboxes` 伪代码 + 并发安全论述**：明确是走 `GET /api/v1/sandboxes` 全表删还是只删本进程缓存；若全表删，论述多 jiuwenclaw 进程串台风险及对策（如加 instance_id 标记、或只在单实例部署时启用）。至少在 §5 风险里列出"多实例串台"这条。
4. **补 WS 层鉴权**：在 §3.3 派发分支伪代码里加 `if request.channel_id not in TRUSTED_CHANNELS: return _err(...)`，或要求 `request.metadata.principal_user_id` 非空。relay-claw 侧的 `resolveTrustedUserId` 不够——WS 帧可能来自其他渠道。
5. **强化配置校验**：files.set 加路径绝对化（`os.path.abspath`）+ 危险路径拦截（`C:\`、`C:\Windows` 等系统目录白名单需二次确认）；network.set 加域名正则校验（`*.example.com` 仅允许前缀通配符，禁端口、禁 IP 混入域名字段）。
6. **副本读写加锁**：`_load_copy` / `_save_copy` 用 `asyncio.Lock` 或 `threading.Lock` 包，`render_runtime_policy` 整体在锁内（读-改-写原子）。多 agent-server 实例场景加文件锁（`msvcrt.locking` 或 `portalocker`）。
7. **修正 `enabled` 默认值**：要么改文档"默认 true"为"默认 false"（与代码一致），要么改代码 `_SANDBOX_RUNTIME_DEFAULTS["enabled"]=True`（与 interface.txt 一致）。建议后者——interface.txt 明确"默认开启"。
8. **派发壳层画清楚**：§3.3.4 补 `_handle_sandbox_config` 的 async 壳伪代码（`resp = dispatch_sandbox_config_request(request); wire = encode_agent_response_for_wire(resp, ...); await ws.send(...)`），与 `_handle_permissions_config` 形态对齐。
9. **`asyncio.get_event_loop().create_task` → `asyncio.get_running_loop().create_task`**：避免 Python 3.12+ 弃用警告。

---

## 八、小结

这是一份**质量上乘的设计文档**：调研扎实（代码引用逐项属实）、生效机制分层清晰（files/network/enabled 三路径区分是核心贡献）、user_overrides 分离设计优雅、交付样板完整可落地、伪代码充分到能直接照写。落地 commit `f4089537` 与设计高度对应，文档起到了真正的施工图作用。

主要缺陷集中在**口径不一**（接口数 6/8 反复、docstring 漏 startup_mode、enabled 默认值文档与代码矛盾）、**安全考量不充分**（WS 鉴权、配置校验、并发竞态、多实例串台基本未着墨）、**`recreate_all_sandboxes` 设计未兜底**（与既有 `sandbox_lifecycle.py` "软清"原则冲突且无伪代码）。这些是设计层面可修复的缺陷，不影响整体方向。

**建议**：合入后补充一份 errata 修正口径不一，并在实现审查（`f4089537`）时重点看 `recreate_all_sandboxes` 的并发安全、WS 鉴权、配置校验三处是否在实现层补齐。设计文档本身可作为 Windows 沙箱配置控制链路的权威参考，但落地时需以实现代码为准核对默认值与并发处理。

---

## 附：关键 file:line 引用速查

| 设计点 | 文档行 | 代码核对 |
|---|---|---|
| 生效路径表 | `docs/sandbox-config-control.md:42-48` | `process.py:2899-3002` / `win_proxy.py:466` / `app.py:300,428` 属实 |
| user_overrides 分离 | `docs/sandbox-config-control.md:128-132` | `f4089537:sandbox_policy_render.py` 落地一致 |
| 接口契约 8 个 | `docs/sandbox-config-control.md:144-157` | `f4089537:message.py +14` 一致 |
| 口径不一 6/8 | `:232`、`:755`、`:803` | 应统一为 8 |
| `_apply_sandbox_change` docstring 漏 | `:433` vs `:451-457` | docstring 漏 startup_mode |
| `recreate_all_sandboxes` 无伪代码 | `:768` | `sandbox_lifecycle.py` 既有原则反对全表删 |
| `enabled` 默认 true 矛盾 | `:169` | `config.py:1190` 实为 `False` |
| 指纹检测 | `:583-617` | `f4089537:jiuwenbox_runner.py +29` 一致 |
| Linux 守卫 | `:142`、`:798` | `f4089537:sysop_builder.py +17` 一致 |
