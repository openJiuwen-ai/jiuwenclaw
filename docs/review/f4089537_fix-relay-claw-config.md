# Code Review: f4089537 fix:支持relay-claw配置沙箱启停，黑白名单等配置

- **Commit**: `f40895375609480514f96755c291f471cd3fb54e`
- **作者**: lby <liubuyu1@huawei.com>，日期 2026-07-30
- **变更规模**: 9 文件，+736 / -6，新增 3 个核心文件
- **审查范围**: relay-claw 经 WS 下发沙箱启停 / 文件黑白名单 / 网络黑白名单配置链路
- **审查日期**: 2026-08-01

---

## 概述

本 commit 为 Windows 沙箱引入了一套面向 officeAce/relay-claw 的运行时配置通道：通过 8 个
`sandbox.*` WS 方法（`enabled`/`startup_mode`/`files`/`network` 的 get/set），把用户对沙箱
开关、启动方式、文件白/黑名单、网络域名白/黑名单的配置写进**运行时 policy 副本**
(`windows-policy.runtime.yaml`)，并经 `render_runtime_policy()` 把 `user_overrides` 合并进
`windows` 段；生效路径依赖 box-server 重启重载 root policy（网络 egress 与文件 ACL 均在
沙箱创建/box-server 启动时读取，运行时不热加载）。

整体设计清晰、与既有 `permissions.config_rpc` 同形态、注释充分、对 box-server "启动时一次性
加载 root policy" 的约束也有显式认知。但存在若干安全性与一致性风险：网络默认 `allow`、黑白
名单无输入校验（可被用作策略注入面）、`recreate_all_sandboxes` 死代码、docstring 与实现不符、
鉴权仅靠 Origin 校验等。

---

## 变更范围

| 文件 | 行 | 角色 |
|---|---|---|
| `jiuwenclaw/agentserver/sandbox_config_rpc.py` | +251 新增 | 8 个 `sandbox.*` RPC 派发 + 异步生效 `_apply_sandbox_change` |
| `jiuwenclaw/agentserver/sandbox_policy_render.py` | +353 新增 | 运行时副本读写 + `user_overrides`→`windows` 渲染 |
| `jiuwenclaw/agentserver/sandbox_lifecycle.py` | +24 | 新增 `recreate_all_sandboxes()`（未接线） |
| `jiuwenclaw/agentserver/agent_ws_server.py` | +38 | WS 派发 + bootstrap 注入副本路径 |
| `jiuwenclaw/agentserver/jiuwenbox_runner.py` | +29 | policy 文件内容指纹（sha256）比对，触发重 spawn |
| `jiuwenclaw/agentserver/deep_agent/sysop_builder.py` | +17 | Windows 跳过 per-sandbox policy，走 root 继承 |
| `jiuwenclaw/schema/message.py` | +14 | 8 个 `ReqMethod` 枚举 |
| `jiuwenclaw/resources/config.yaml` | +8 | `sandbox:` 基础配置段 |
| `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` | +8/-1 | `egress.default` 由 `deny` 改为 `allow` |

---

## 架构与设计概述

配置分两条落点，设计上区分得很清楚（见 `sandbox_config_rpc.py:11-26`）：

1. **基础配置（config.yaml）**：`sandbox.enabled` / `sandbox.startup_mode` 经
   `update_sandbox_runtime` / `update_sandbox_startup_mode` 写回 `config.yaml`，靠
   `_dump_yaml_round_trip`→`clear_config_cache` 让 TTL 缓存失效，下次 `_create_sys_operation`
   读到新值。
2. **运行时副本（`<config_dir>/windows-policy.runtime.yaml`）**：文件 / 网络配置写进副本的
   `user_overrides` 段，再 `render_runtime_policy()` 从干净基底 deepcopy 重建 `windows` 段并
   合并 `user_overrides`，保证取消配置后干净回落基底原值。

生效语义（`sandbox_config_rpc.py:62-93`）：
- `enabled`：只影响新会话选 LOCAL/SANDBOX，不动 box-server。
- `startup_mode`：internal→external 停掉自拉起的 box-server；反向不主动拉。
- `files` / `network`：通过 `runner.ensure_running` 重启 box-server，靠 lifespan shutdown 的
  `shutdown_all_sandboxes` 副作用清活沙箱；`jiuwenbox_runner._policy_fingerprint`（sha256）
  检测"路径不变但内容变"触发 stop+spawn。

与 box-server 的衔接：副本路径经 `JIUWENBOX_POLICY_PATH` 注入子进程（`jiuwenbox_runner.py:456-460`），
box-server 的 `PolicyReader.load_policy()`（`policy_reader.py:154-202`）做"基底 + 副本"合并
（`policy_engine.merge_policy`：dict 深合并、list 追加去重）。`SandboxManager.__init__`
（`sandbox_manager.py:228`）启动时一次性 `load_policy()` 到 `self.policy`，之后不重读——这正是
MEMORY 里记录的 "box-server root policy load-once" 约束，本 commit 对此有显式认知。

---

## 关键代码检视

### 配置 RPC 协议与派发

🟢 `sandbox_config_rpc.py:39-58` `_ok`/`_err` 与 `permissions/config_rpc.py:72-89` 完全同形态，
错误码 `BAD_REQUEST`/`INTERNAL_ERROR` 一致，`AgentResponse` 契约清晰。

🟢 `sandbox_config_rpc.py:148-200` 参数校验细致：`enabled` 必须 bool、`startup_mode` 走
`update_sandbox_startup_mode` 的 `ValueError` 兜底、`allow`/`deny`/`allow_domains`/`deny_domains`
必须 list。`set` 返回归一化后的值（`[str(p) for p in allow if str(p).strip()]`），客户端可据此
确认服务端实际落库的值。

🟡 `agent_ws_server.py:784-788` 派发仅按 `req_method ∈ get_sandbox_config_req_methods()` 路由，
**无任何用户级鉴权/权限校验**。`is_allowed_browser_origin`（`ws_origin.py:87-116`）只校验 Origin
主机名 ∈ {127.0.0.1, localhost}，或 `AGENT_RUNTIME` 环境变量非空时全放行。这意味着任何能连上
agent-server WS 端口的本机进程（含同机恶意进程）都可下发 `sandbox.files.set` / `sandbox.network.set`
改写沙箱安全策略。与 `permissions.config_rpc` 同病，但沙箱配置直接关系到文件读写/网络出站边界，
影响面更大。

### 黑白名单合并 / 去重 / 校验

🟢 `sandbox_policy_render.py:236-249` 文件白名单合并到 `allow_read`+`allow_write`、黑名单合并到
`deny_read`+`deny_write`，去重用 `if p not in existing: existing.append(p)`，语义为"叠加基底必需集，
不丢"。

🟢 `sandbox_policy_render.py:262-289` 网络分支明确 `disable_all` 总开关"只压不删"：true 时设
`egress.default=deny` + 旁路 `allowed_domains`（不写入），但 `user_overrides.network.allow_domains`
原样保留，关掉即恢复——与 `win_proxy.EgressFilter`（`win_proxy.py:118-119`）的 `disable_all` 短路
语义对齐。

🔴 `sandbox_policy_render.py:193-205` / `218-231` **`allow`/`deny`/`allow_domains`/`deny_domains`
只做 `str(p).strip()`，无任何格式/语义校验**：
- 文件路径无规范化（无 `Path.resolve`、无越界检查），用户可写入任意路径串；虽然 box-server 侧
  最终由 `SecurityPolicy` 的 Pydantic 模型按 `list[str]` 接收（不校验路径合法性），但这些串会
  原样进 WFP/文件 ACL，构成潜在的策略注入面（例如极长串、控制字符、伪装成路径的 YAML 特殊字符
  经 `yaml.safe_dump` 仍安全，但若未来有消费方按 glob/regex 解析则风险放大）。
- 域名无格式校验：`"*.*.*.evil.com"`、`"evil.com/../../"`、含空格/端口的串都会被原样接受并写进
  `blocked_domains`/`allowed_domains`。`EgressFilter._domain_matches`（`win_proxy.py:81-95`）只做
  `*.prefix` 通配前缀匹配，非通配串走精确匹配，恶意/错误域名会静默"不匹配"→ 实际不生效但用户
  以为生效，属于安全错觉。
- 未对 `deny` 与 `allow` 的交集做冲突检查：同一域名既在 allow 又在 deny 时，`EgressFilter.allow`
  先查 deny（`win_proxy.py:123-126`，deny 优先）→ deny 胜出，语义正确，但 RPC 不返回冲突提示，
  用户难自查。

🔴 **网络默认 `allow` 是本次最高风险点**。`windows-policy.yaml:154` 把基底 `egress.default` 从
`deny` 改为 `allow`（理由：CDN 未穷举）。渲染逻辑 `sandbox_policy_render.py:278-280`：当用户
`allow_domains`/`deny_domains` 都空时走 `pass`，保留基底 `default: allow`。即**用户从未配置网络 →
沙箱默认全放行出站**。叠加 WFP `mode: wfp_loopback_proxy` 本意是阻断出站，此改动使沙箱网络隔离
形同虚设。注释（`windows-policy.yaml:148-153`）虽说明"适配期临时放开，待 skill 固化后收紧"，但
（a）无 TODO/issue 跟踪、无环境变量守卫、无"非 Windows 测试环境才 allow"的条件，发布即默认开放；
（b）`set_sandbox_network_config(disable_all=False, allow=[], deny=[])` 也会回到这个 default:allow
基底，用户即使想"只 deny 不 allow"也得到 default:allow 基底 → 实质放行所有非 deny 域名。

🟡 `sandbox_policy_render.py:268-271` `disable_all` 分支只设 `egress.default=deny` + pop
`allowed_domains`，**未在 `windows.network.disable_all` 字段上置位**。而 box-server lifespan
（`app.py:324`）读的是 `root_policy.windows.network.disable_all` 作为 `EgressFilter.disable_all`
构造参数。好在 `EgressFilter.allow` 在 `default=deny` + 无 allow 规则时同样拒绝（`win_proxy.py:188-189`），
效果等价，但这是"靠副作用等价"而非"按字段语义生效"——一旦基底 `default` 改回 `deny` 或 box-server
调整 `disable_all` 分支逻辑，此等价可能破裂。

### 策略渲染正确性

🟢 `sandbox_policy_render.py:232-249` `render_runtime_policy` 每次**从干净基底 deepcopy 重建
`windows` 段**（`data["windows"] = copy.deepcopy(base.get("windows") or {})`），而非在副本旧
`windows` 上累积。这保证用户取消某配置后 `windows` 干净回落基底原值（pypi/npmmirror egress、
workspace allow_write），无渲染残留。是本 commit 的设计亮点。

🟢 `sandbox_policy_render.py:62-80` 对 `SecurityPolicy` 未设 `extra="forbid"`（Pydantic v2 默认
`extra="ignore"`）的认知准确：顶层 `user_overrides` 段被 box-server `model_validate` 静默忽略，
不报错。**但**注释（`sandbox_policy_render.py:21-25`）已预警"若将来给 `SecurityPolicy` 加
`extra="forbid"`，需把 `user_overrides` 移到副本外独立存储"——这是个埋好的雷，建议加单测锁住。

🟡 `sandbox_policy_render.py:155-168` `_ensure_copy_exists` 在副本不存在时从基底复制并注入空
`user_overrides`。**并发写无锁**：两个并发 `set_sandbox_files_config` 都走 `_load_copy`→改→
`_save_copy`，后写覆盖前写（lost update）。`_apply_sandbox_change` 用 `loop.create_task` 异步触发，
但 `set_*` 本身是**同步**函数（在 `dispatch_sandbox_config_request` 里同步执行），RPC 层一次只
处理一条请求（WS handler 串行），实际并发概率低；但若未来有多 worker / 异步 set 入口则需补锁。

🟡 `sandbox_policy_render.py:117-126` `_save_copy` 写文件非原子（直接 `write_text`，无 tmp+rename）。
写中途崩溃会留下半截 YAML，box-server 下次 `load_policy` 会 `yaml.safe_load` 失败→走 "User policy
copy unreadable, using base only"（`policy_reader.py:191-196`），退化为基底（当前 `default:allow`）。
即写副本失败 → 静默回落到开放基底，用户无感知。

### 生命周期启停的并发与状态机

🟢 `jiuwenbox_runner.py:280-294` 新增 `_policy_fingerprint`（sha256），`ensure_running`
（`jiuwenbox_runner.py:381-390`）把"指纹变"纳入 mismatch 判定，正确覆盖"path 不变但内容变"场景。
`_spawned_policy_fingerprint` 在 spawn 成功/stop/异常路径均复位，状态机一致（`jiuwenbox_runner.py:480,497,656,661,669,694`）。

🔴 `sandbox_lifecycle.py:151-175` 新增的 `recreate_all_sandboxes()` **在本 commit 内无任何调用方**
（全仓 grep 仅自身定义与 `__all__`）。`_apply_sandbox_change` 的 `files` 分支
（`sandbox_config_rpc.py:107-117`）靠 `runner.ensure_running` 重启 box-server 顺带清沙箱，**未调用**
`recreate_all_sandboxes`。该函数是死代码，其 docstring 描述的"files.set 后销毁重建沙箱"语义并未
真正接入生效链路。

🔴 `sandbox_config_rpc.py:18-24` docstring 与实现严重不符：
- docstring 称 `files`: "显式销毁活沙箱（新 ACL 只作用于新沙箱），**不重启 box-server**（ACL 在
  沙箱创建时读）"；
- 但 `files` 分支实际走 `runner.ensure_running(...)`（`sandbox_config_rpc.py:113-117`），指纹变→
  mismatch→`_stop_no_lock`+重 spawn，**恰恰重启了 box-server**。
- 即 docstring 声称的"不重启"与代码"会重启"矛盾。若按 docstring 设计（不重启、仅销毁沙箱），
  应调 `recreate_all_sandboxes()`；但代码没这么做，反而不必要地重启了整个 box-server（开销远大于
  仅销毁沙箱）。二者必有一错。

🟡 `sandbox_config_rpc.py:135-145` `_trigger_apply` 用 `asyncio.get_event_loop()`（3.12+ 已弃用，
应 `asyncio.get_running_loop()`），且 `loop.create_task` 创建的任务**未序列化**：连续两次
`sandbox.network.set` 会起两个 `_apply_sandbox_change` task，可能交叉（task A 在
`await runner.ensure_running` 等锁，task B 已开始读 `runner._owns_process`）。runner 内部
`self._lock`（`jiuwenbox_runner.py:202`）能序列化 `ensure_running` 本身，但两个 task 的外层读取
（`runner._owns_process`/`runner._process`）在锁外，存在 TOCTOU（虽不致命）。建议加一个模块级
`asyncio.Lock` 或把多次 set 合并成一次 apply。

🟡 `sandbox_config_rpc.py:128-130` `except RuntimeError` 降级分支注释称"正常路径不会走到"，
但 `dispatch_sandbox_config_request` 是**同步**函数、在 `_handle_sandbox_config`（`agent_ws_server.py:1237-1244`）的 async 上下文里调用，此时事件循环必然在跑，`get_event_loop()` 不会抛
`RuntimeError`。该分支实际只在测试/非主线程触发，但若真触发则 files/network 的 IO 生效被完全跳过
——`set` 返回 ok 却永不生效，是静默失败。

### 与 box-server 策略加载的衔接

🟢 `sandbox_config_rpc.py:66-74` 注释准确点明 box-server `SandboxManager.__init__`
（`sandbox_manager.py:228`）启动时一次性 `load_policy` 到 `self.policy`，之后不重读——与 MEMORY
记录一致。因此 files/network 改副本后必须重启 box-server，本 commit 的 `_apply_sandbox_change`
正是靠此设计。

🟢 `sandbox_manager.py:248-268` `_resolve_effective_policy`：`policy_data=None` 时 deep-copy root
policy。`sysop_builder.py:315-317` Windows 下 `policy={}` + `policy_mode="append"` 会让 box-server
走 `merge_policy(root, {})` → 等价 deep-copy root，即 per-sandbox 继承 root（含用户渲染的副本合并
结果）。设计自洽。

🟡 `policy_reader.py:187-202` `load_policy` 合并语义：副本作为 `override_data` 与基底做
`merge_policy`（list 追加去重）。但副本的 `windows.filesystem.allow_read` 已是"基底 + 用户"
（由 `render_runtime_policy` 合并），再与基底 `allow_read` 做一次 `merge_policy` → 基底项被去重，
结果仍是"基底 + 用户"。逻辑正确但做了**两次合并**（render 一次、load_policy 一次），若未来
`merge_policy` 语义从"追加去重"改为"替换"，会立即破裂。建议在 `render_runtime_policy` 里只写
`user_overrides` 的增量，让 `load_policy` 的合并做唯一真相源；或反之。当前两处合并职责重叠。

---

## 优点

1. **与既有 `permissions.config_rpc` 同形态**，新接 8 个方法的学习成本低，错误处理/响应契约一致。
2. **`render_runtime_policy` 从干净基底 deepcopy 重建**，避免累积渲染残留，取消配置能干净回落——
   这是策略渲染的正确做法。
3. **policy 内容指纹（sha256）**正确覆盖"路径不变但内容变"的 box-server 重启场景，状态机复位
   完整。
4. **`disable_all` 只压不删**设计（保留 `allow_domains` 原值，关掉即恢复）对用户友好。
5. **注释质量高**：对 box-server load-once 约束、`SecurityPolicy.extra` 行为、生效语义均有显式
   记录，可维护性好。

---

## 问题与风险

| 级别 | 问题 | 位置 |
|---|---|---|
| 🔴 高 | 网络 `egress.default` 改为 `allow`，用户未配网络时沙箱默认全放行出站，网络隔离形同虚设。无环境变量守卫、无 TODO 跟踪。 | `windows-policy.yaml:154`；`sandbox_policy_render.py:278-280` |
| 🔴 高 | `allow`/`deny`/`allow_domains`/`deny_domains` 无格式/语义校验，可原样写入策略；域名不合法时静默不匹配，制造安全错觉。 | `sandbox_policy_render.py:193-205,218-231` |
| 🔴 高 | `recreate_all_sandboxes()` 为死代码，docstring 描述的"files.set 后销毁重建沙箱"语义未接入生效链路。 | `sandbox_lifecycle.py:151-175`；`sandbox_config_rpc.py:107-117` |
| 🔴 高 | `_apply_sandbox_change` 的 `files` 分支 docstring 称"不重启 box-server"，但代码实际重启，文档与实现矛盾。 | `sandbox_config_rpc.py:18-24 vs 107-117` |
| 🟡 中 | WS 派发无用户级鉴权，仅 Origin 校验（localhost/AGENT_RUNTIME 全放行），同机任意进程可改写沙箱安全策略。 | `agent_ws_server.py:784-788`；`ws_origin.py:87-116` |
| 🟡 中 | `disable_all` 未在 `windows.network.disable_all` 字段置位，靠 `default=deny`+空 allow 副作用等价；基底 default 改回 deny 后等价可能破裂。 | `sandbox_policy_render.py:268-271`；`app.py:324` |
| 🟡 中 | `_save_copy` 非原子写，写中途崩溃→半截 YAML→box-server 静默回落开放基底。 | `sandbox_policy_render.py:117-126`；`policy_reader.py:191-196` |
| 🟡 中 | render 与 `load_policy` 两次合并职责重叠，`merge_policy` 语义若变更会破裂。 | `sandbox_policy_render.py:232-249`；`policy_reader.py:187-202` |
| 🟡 中 | `_trigger_apply` 用已弃用 `get_event_loop`，task 未序列化，外层读存在 TOCTOU。 | `sandbox_config_rpc.py:135-145` |
| 🟡 低 | `sandbox_lifecycle.py` 末尾无换行（`\ No newline at end of file`）。 | `sandbox_lifecycle.py:175` |
| 🟡 低 | `agent_ws_server.py:309-327` bootstrap 里调用 `_ensure_copy_exists`（私有函数），跨模块私有引用。 | `agent_ws_server.py:312` |

---

## 改进建议

1. **网络 default:allow 收紧**：把基底改回 `default: deny`，在 agent-server 侧对"适配期"场景
   用显式白名单（含已知 CDN）或环境变量 `JIUWENCLAW_SANDBOX_NET_DEFAULT_ALLOW=1` 守卫；至少加
   TODO + issue 跟踪"skill CDN 固化后收紧"。

2. **黑白名单输入校验**：
   - 文件路径：`Path(p).resolve()` 后做越界检查（相对 workspace），拒绝绝对路径越界。
   - 域名：用 `urlsplit`/正则校验 hostname（含通配符 `*.x` 形态），拒绝含端口/路径/控制字符的串。
   - 返回 `allow`/`deny` 交集冲突提示。

3. **接上或删除 `recreate_all_sandboxes`**：若 `files` 分支确实只想销毁沙箱而非重启 box-server
   （与 ACL 在沙箱创建时读的语义一致），改为 `await recreate_all_sandboxes()` 并去掉
   `runner.ensure_running` 调用；否则删除该函数，并修正 `_apply_sandbox_change` docstring 与
   `sandbox_config_rpc.py:18-24` 顶层 docstring 使其与"会重启 box-server"的实现一致。

4. **`disable_all` 字段显式置位**：在 `render_runtime_policy` 的 `disable_all` 分支同时设
   `net_block["disable_all"] = True`，让 `app.py:324` 读到正确字段，不靠副作用等价。

5. **`_save_copy` 原子写**：`tmp = p.with_suffix(".tmp"); tmp.write_text(...); tmp.replace(p)`。

6. **WS 鉴权**：对 `sandbox.*` 方法加独立的能力校验（至少要求 `AGENT_RUNTIME` 之外的 token 或
   channel 白名单），不只靠 Origin 主机名。

7. **合并职责收口**：让 `render_runtime_policy` 只写 `user_overrides` 增量，`windows` 段的合并
   交给 `policy_reader.load_policy` 的 `merge_policy` 做唯一真相源；或反之。避免双合并。

8. **`_trigger_apply` 改 `get_running_loop` + 模块级 `asyncio.Lock`** 序列化 apply 任务。

---

## 小结

本 commit 的设计骨架是扎实的：与既有 RPC 同形态、render 从干净基底 deepcopy 重建、policy 指纹
检测内容变更、对 box-server load-once 约束有显式认知，整体可读性与可维护性高。但有四处需在合入
前处理：网络 `default:allow` 使沙箱网络隔离失效（🔴）、黑白名单无输入校验构成策略注入面（🔴）、
`recreate_all_sandboxes` 死代码与 docstring/实现矛盾（🔴）、WS 鉴权仅靠 Origin（🟡）。建议优先
收紧网络默认 + 补输入校验，其次对齐 docstring 与实现（或接上 `recreate_all_sandboxes`），再补
原子写与字段显式置位。其余为可维护性改进，不阻塞合入。
