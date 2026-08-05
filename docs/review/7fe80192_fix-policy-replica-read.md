# 代码审查报告：fix:修启动时读取策略副本，基底

- **Commit**: `7fe80192ed4f299b3cdd3e532e81583c0b3c9d91`
- **作者**: lby (2026-07-31)
- **变更规模**: 11 文件, +373 / -302
- **审查重点**: 启动时"策略副本"读取机制、policy 渲染重构、端口工具抽取、disable_all 总开关、沙箱失败回退 local
- **审查日期**: 2026-08-01

---

## 概述

本次 commit 是 Windows 沙箱适配链路的一次中等规模重构，核心是**把"渲染合并进副本 windows 段"的机制改为"只写稀疏用户副本，box-server 启动时读基底+副本在内存合并"**，对齐 jiuwenclaw config.yaml 的 template+override 范式。同时抽出 `sandbox/port_util.py` 供 Linux/Windows 共用，新增 `disable_all` 网络总开关（只压不删语义），并为 `interface_deep` 加入沙箱失败回退 local 的容错。

整体方向正确：消除了"副本固化基底 → 升级时新字段被旧副本挡住"的根本缺陷，热更新语义清晰；稀疏副本 + 内存合并 + 不落盘合并文件，符合最小写盘原则。但**并发写入无锁、TOCTOU、副本篡改面、端口分配的竞态窗口**等问题值得在后续迭代收紧。

---

## 变更范围

| 文件 | 性质 | 规模 | 备注 |
|---|---|---|---|
| `jiuwenclaw/agentserver/sandbox_policy_render.py` | 大重构 | -333/+... 净 | 去掉 `render_runtime_policy`/`user_overrides` 段，副本改稀疏 |
| `jiuwenbox/src/jiuwenbox/server/policy_reader.py` | 重点 | +61 | `load_policy` 改为读基底+副本合并 |
| `jiuwenclaw/agentserver/agent_ws_server.py` | 调整 | +88/-... | 端口工具外移，启动注入副本路径 |
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | 增强 | +57 | 沙箱失败回退 local |
| `jiuwenclaw/agentserver/sandbox/__init__.py` | 新增 | +19 | 包导出 |
| `jiuwenclaw/agentserver/sandbox/port_util.py` | 新增 | +79 | 端口解析/分配 |
| `jiuwenbox/src/jiuwenbox/bundled_configs.py` | 新增函数 | +12 | `base_policy_path()` |
| `jiuwenbox/src/jiuwenbox/server/app.py` | 增强 | +6 | 传 `disable_all` 给 EgressFilter |
| `jiuwenbox/src/jiuwenbox/server/policy_engine.py` | 1 行 | +1/-1 | `open` 加 `encoding=utf-8` |
| `jiuwenbox/src/jiuwenbox/models/policy.py` | 新增字段 | +4 | `WindowsNetworkPolicy.disable_all` |
| `jiuwenbox/src/jiuwenbox/supervisor/win_proxy.py` | 增强 | +14 | `EgressFilter.disable_all` 短路拒绝 |

---

## 架构与设计概述

### 1. "策略副本"机制（核心变更）

**旧机制**：`sandbox_policy_render.py` 在 `_ensure_copy_exists` 时把**整个基底 windows-policy.yaml** 复制成运行时副本，再在其上加 `user_overrides` 段；`set_sandbox_*_config` 改 `user_overrides` 后调 `render_runtime_policy()`，后者从干净基底 deepcopy 重建 `windows` 段并把 user 配置合并进去写盘。box-server 直接读副本的 `windows` 段。

**新机制**：
- 副本 = 稀疏空骨架（只含 `windows.filesystem.{allow_read,allow_write,deny_read,deny_write}` + `windows.network.{disable_all,egress.{allowed_domains,blocked_domains}}`），**不 dump 基底**（`sandbox_policy_render.py:68-86`）。
- `set_sandbox_*_config` 直接改副本对应字段，**不再调 render**（`sandbox_policy_render.py:162-180`、`195-223`）。
- box-server 启动时 `PolicyReader.load_policy()` 读**基底** `base_policy_path()`（`bundled_configs.py:24-32`，随 wheel）+**副本** `self.policy_path`（`JIUWENBOX_POLICY_PATH` env 指向稀疏 user_config），在内存用 `PolicyEngine.merge_policy` 深合并（dict 递归合并，list 追加去重），**不生成合并文件**（`policy_reader.py:154-202`）。
- 副本不存在 → 退化为只读基底；副本路径等于基底（未配 env）→ 也只用基底（`policy_reader.py:181-185`）。

**为何改？** 旧机制"副本固化基底"：每次 render 从基底 deepcopy 重建 windows，但**副本文件本身仍含完整基底内容**。基底随 wheel 升级新增字段后，若副本已存在（`_ensure_copy_exists` 不重建），旧副本的 windows 段不含新字段 → box-server 读不到新字段（除非手动删副本）。新机制副本只存用户配置，基底由 box-server 每次重读刷新，**新字段自然生效**。注释明确点出"热更新安全"（`sandbox_policy_render.py:23-25`、`policy_reader.py:157-158`）。

### 2. `disable_all` 总开关（只压不删）

`WindowsNetworkPolicy` 新增 `disable_all: bool = False`（`models/policy.py:850`）。`set_sandbox_network_config` 把它写进副本 `windows.network.disable_all`（`sandbox_policy_render.py:215`）。box-server `app.py` lifespan 读出传给 `serve_windows_proxy(disable_all=...)`（`app.py:324-332`），`EgressFilter.allow` 在最前面短路 `return False`（`win_proxy.py:118-119`）。关键设计：**不清空 allow/blocked_domains**，关掉总开关（副本改 false）即恢复——"只压不删"。

### 3. 端口工具外移

`AgentWebSocketServer` 的 4 个内联静态方法（`_parse_sandbox_host_port` / `_is_tcp_port_bindable` / `_pick_free_tcp_port` / `_allocate_internal_jiuwenbox_port`）原样搬到 `sandbox/port_util.py`，行为零变化（注释明确"照搬自 jiuwenswarm PR #4088...行为与原内联实现完全一致"）。`agent_ws_server.py` 启动时调 `_ensure_copy_exists()`（替代旧 `render_runtime_policy()`）拿副本路径注入 `JIUWENBOX_POLICY_PATH`。

### 4. 沙箱失败回退 local

`interface_deep.py:_create_sys_operation` 三处加 fallback：sysop_card 为 None（创建失败）、`add_sys_operation` 失败、整体异常——均回退 `create_local_sysop_card` 重试。语义是"沙箱不可用时让用户任务仍能跑完，而非直接报错"。

---

## 关键代码检视

### policy_reader.py（策略副本读取核心）

🟢 `policy_reader.py:167-179` — 基底读取有 `OSError` 守护 + `isinstance(base_data, dict)` 校验，基底不可读回落 `SecurityPolicy()` 默认，稳健。

🟢 `policy_reader.py:182-185` — "副本路径等于基底（未配 env）→ 直接用基底"的短路，避免无 env 时对基底文件做无意义 merge，正确。

🟡 `policy_reader.py:189-190` — 副本读取用 `yaml.safe_load`，但**只 catch `OSError`，未 catch `yaml.YAMLError`**。副本是用户可写的 YAML，若被手改坏（语法错误），`yaml.YAMLError` 会向上抛出，`load_policy` 无对应降级。对比 `sandbox_policy_render._load_copy:114` 是 catch `(OSError, yaml.YAMLError)` 的——**两处异常处理不一致**，副本损坏时行为不可预测（app.py:319 调用方有外层 try/except 兜底，但会走到"Windows 出站代理启动失败"分支，沙箱网络隔离不可用）。建议统一 catch `yaml.YAMLError` 并回落基底。

🔴 `policy_reader.py:197` — `if not isinstance(override_data, dict) or not override_data:` 当副本内容是空 dict `{}` 时走基底，合理；但**未对副本顶层结构做校验**。若副本被篡改成 `{"windows": "not-a-dict"}` 或 `{"windows": {"network": "evil"}}`，`merge_policy` 的 `_merge_value`（`policy_engine.py:264-284`）会按"base 是 dict、extra 是非 dict Mapping 时直接替换"语义处理——`_merge_value` 对 `extra` 不是 Mapping 的情况走 `return extra`（`policy_engine.py:284`），即用户可把 `windows` 整段替换成任意字符串，最终 `SecurityPolicy.model_validate` 会因类型不符抛 `ValidationError`。虽有 pydantic 兜底，但错误路径不友好。更关键见下方安全风险。

### sandbox_policy_render.py（副本读写）

🟢 `sandbox_policy_render.py:89-106` — `_ensure_copy_exists` "已存在不重建"逻辑正确，exe/box-server 重启不重拷，基底不固化。

🟢 `sandbox_policy_render.py:119-129` — `_load_copy` 对旧副本缺字段用 `setdefault` 补齐，兼容从旧 user_overrides 结构迁移，迁移友好。

🟡 `sandbox_policy_render.py:109-130` 与 `133-141` — `_load_copy` / `_save_copy` **全程无文件锁**。`set_sandbox_files_config` 和 `set_sandbox_network_config` 各自 `_load_copy` → 改 → `_save_copy`，若两个 WS 请求并发（officeAce 前端同时改文件和网络配置），存在 lost-update：A 读→B 读→A 写→B 写，A 的改动被 B 覆盖。当前单用户场景概率低，但多租户/并发配置时是真问题。建议 `fcntl`/`msvcrt.locking` 或临时锁文件。

🟡 `sandbox_policy_render.py:174` — `fs = data["windows"]["filesystem"]` 直接下标访问，依赖 `_load_copy` 已 `setdefault` 补齐。若 `_load_copy` 的 `setdefault` 链有任何遗漏（未来加新字段时），这里会 `KeyError`。`get_sandbox_*_config` 用了安全的 `.get()` 链（`:155-159`），`set_*` 却用下标——**风格不一致**，set 路径更脆。建议 set 也用 `setdefault` 链或先 `ensure` 再赋值。

🟢 `sandbox_policy_render.py:226-239` — `fingerprint_runtime_policy` 保留，`JiuwenBoxRunner._policy_fingerprint`（`jiuwenbox_runner.py:280-293`）对副本路径算 sha256，`ensure_running` 比对 `_spawned_policy_fingerprint`（`:389`）检测内容变触发 stop+spawn，与 box-server"启动加载一次"约束配合正确（见 memory note `box-server-root-policy-load-once`）。

### port_util.py（端口分配）

🟢 `port_util.py:53-79` — `allocate_internal_jiuwenbox_port` 三级策略清晰：自己拥有的复用 → preferred 空闲则用 → 内核挑随机。`is_owned_listener` 优先判断避免误把自有进程当外部占用换端口（`jiuwenbox_runner.py:257-268`）。

🟡 `port_util.py:33-43` — `is_tcp_port_bindable` 用 `socket.bind` 探测，**bind 成功立即 close，再到真正 spawn 监听之间有 TOCTOU 窗口**：close 后、uvicorn bind 前，别的进程可能抢占该端口。这是"探测式端口分配"的固有缺陷，非本次引入（原内联实现即如此），注释也写明"照搬"。短期内可接受（失败时 uvicorn 报错 + 下次 ensure_running 重试），但高并发启停场景可能偶发失败。`pick_free_tcp_port` 同理（`:46-50`）。

🟡 `port_util.py:22-30` — `parse_sandbox_host_port` 默认 `127.0.0.1:8321`。若 url 为 `http://0.0.0.0:8321`，host 解析为 `0.0.0.0`，后续 `is_owned_listener` 比对 `self._host == host` 可能失配（runner 记的是 `127.0.0.1`），导致误判外部占用。边界场景，低优先级。

🟢 `port_util.py:57` — `runner: JiuwenBoxRunner | None` 可注入参数，便于测试，设计良好。

### win_proxy.py / app.py（disable_all）

🟢 `win_proxy.py:118-119` — `disable_all` 短路在 `allow` 最前面，`return False, "network disabled (disable_all)"`，语义明确，不清空 allow/blocked_domains（`:67-68` 保留原列表）。

🟢 `app.py:324` — `net_disable_all = bool(root_policy.windows.network.disable_all)`，从合并后 policy 取值，基底 default false + 副本用户值，正确。

🟡 `app.py:319` — `root_policy = policy_reader.load_policy()` 在 lifespan 启动期读一次，`EgressFilter` 实例化后**整个 box-server 生命周期不复读**。这是 box-server"启动加载一次"约束（见 memory）。disable_all 改了副本后，必须靠 `JiuwenBoxRunner` respawn box-server 才生效——而 `set_sandbox_network_config`（`sandbox_policy_render.py:218`）**不再调 render，也不主动触发 respawn**。respawn 依赖 `sandbox_config_rpc._apply_sandbox_change` 调 `ensure_running`（memory note 指出）。需确认 `_apply_sandbox_change` 对 network 变更确实调了 `ensure_running`——本次 diff 未含该文件，属链路衔接假设，建议补查。

### interface_deep.py（沙箱回退 local）

🟢 `interface_deep.py:2613-2629` — sysop_card 为 None 时回退 local，注释清晰区分"创建阶段失败回退"vs"执行阶段失败抛错"，合理。

🟡 `interface_deep.py:2660-2681` — `add_sys_operation` 失败后回退 local 重试。**潜在风险**：若 sandbox card 已半注册（`isolation_key` 已占），local card 重试可能创建第二个 sysop，隔离键冲突或资源泄漏。代码先查 `_get_registered_sys_operation_by_isolation_key`（`:2646-2656`）兜底复用，但 local card 与 sandbox card 的 isolation_key 模板不同（`_sys_operation_isolation_key`），不会撞键。总体可接受，但多了一次 add 尝试，错误日志路径变复杂。

🟡 `interface_deep.py:2683-2696` — 整体异常回退 local。**注意**：此分支用 `except Exception as exc` 捕获**所有异常**，包括 `KeyboardInterrupt`/`SystemExit` 的子类外的所有。沙箱创建抛 `PermissionError`（提权失败）时静默回退 local——用户可能**误以为在沙箱里跑，实则在 local**。建议至少在日志里区分"沙箱降级"并视安全策略考虑是否允许降级（若用户配沙箱是为隔离恶意代码，静默降级到 local 有安全语义风险）。当前只 `logger.warning`，调用方拿不到"已降级"信号。

---

## 优点

1. **热更新根治**：稀疏副本 + 内存合并彻底解决"副本固化基底 → 升级新字段被旧副本挡住"，基底随 wheel 升级自然生效，设计正确且注释交代清楚。
2. **机制对齐**：基底(default)+副本(user_config) 合并范式与 jiuwenclaw config.yaml template+override 一致，认知负担低。
3. **disable_all"只压不删"**：不清空 allow/blocked_domains，关掉即恢复，用户体验好，注释充分。
4. **端口工具外移**：消除 Linux/Windows 重复，可注入 runner 便于测试，行为零回归。
5. **迁移友好**：`_load_copy` 用 `setdefault` 补齐旧副本缺字段，平滑从 user_overrides 结构迁移。
6. **容错增强**：沙箱失败回退 local 让用户任务跑完，降级路径完整（创建/注册/异常三阶段）。
7. **`encoding="utf-8"` 修复**：`policy_engine.py:308`、`policy_reader.py:169` 显式指定编码，避免 Windows 默认 GBK 解析 YAML 中文注释乱码。

---

## 问题与风险

### 🔴 安全：副本可被篡改，路径无权限加固

副本位于 `<config_dir>/windows-policy.runtime.yaml`，即 workspace 下（`sandbox_policy_render.py:52-60`，跟随 `JIUWENCLAW_DATA_DIR`/workspace）。`_save_copy` 用 `write_text` 直接覆盖，**无 ACL/chmod 收紧**。任何能写 workspace 的进程（包括**沙箱内进程本身**，因为 workspace 在沙箱可写路径 `allow_write: ["{{ workspace }}"]`，见 `windows-policy.yaml:111-112`）都能改写副本。

后果：沙箱内恶意进程可往副本 `windows.network.allowed_domains` 追加任意域名，box-server 下次重启（respawn）后 merge 进基底白名单 → **沙箱内进程可自行放宽出网白名单**，绕过网络隔离。同理可改 `allow_read/allow_write` 放宽文件 ACL。

这是**本次重构引入的真实安全面**（旧机制副本同样在 workspace，但旧 render 从基底重建 windows 段，用户改副本 user_overrides 后才生效——攻击面类似，但新机制副本直接是 `windows` 段，被 box-server merge，篡改更直接）。建议：副本落点移出沙箱可写路径，或对副本文件设仅宿主可写 ACL（沙箱合成 SID 不授写）。

### 🔴 并发写入无锁（lost-update）

`sandbox_policy_render._load_copy`/`_save_copy` 无文件锁（见 `:109-141`）。并发 `set_sandbox_*_config` 会 lost-update。单用户低概率，但多租户/officeAce 前端同时触发 files+network 配置时真实存在。

### 🟡 policy_reader 副本 YAML 损坏未捕获

`policy_reader.py:189-190` 只 catch `OSError`，漏 `yaml.YAMLError`。与 `_load_copy`（`:114` catch 两者）不一致。副本损坏时 `load_policy` 抛 `YAMLError`，由 `app.py:334` 外层兜底但导致 win_proxy 不启动。建议统一 catch 并回落基底。

### 🟡 disable_all 生效依赖链路衔接（未在本 commit 验证）

`set_sandbox_network_config` 改副本后**不触发 respawn**（`:218` 只 `_save_copy` + return）。disable_all 生效完全依赖 `sandbox_config_rpc._apply_sandbox_change` 调 `ensure_running`。本 commit 未含该文件，若 `_apply_sandbox_change` 对 network 未调 `ensure_running`，**disable_all 改了不生效**（box-server 缓存旧 policy）。属链路假设，建议补查 `_apply_sandbox_change` 实现。

### 🟡 端口分配 TOCTOU

`is_tcp_port_bindable`/`pick_free_tcp_port` 的 bind-close-spawn 窗口（`port_util.py:33-50`），高并发启停偶发抢占失败。非本次引入，可接受但值得记入技术债。

### 🟡 沙箱静默降级 local 的安全语义

`interface_deep.py:2683-2696` 捕获所有异常回退 local。若用户配沙箱为隔离不可信代码，静默降级到 local = 在宿主机直接跑，安全隔离失效但用户无感。建议至少返回值/日志透出"已降级"标志，或按 policy 决定是否允许降级。

### 🟡 `is_proxy_only` 与新副本路径的语义偏差

`policy_reader.is_proxy_only`（`:208-237`）读 `self.policy_path` 判断是否仅代理模式。当 env 指向稀疏副本（只含 `windows` 段，无 `inference_privacy_proxies`），`is_proxy_only` 返回 False，正确；但当未配 env、`self.policy_path` 等于基底时，读基底 `windows-policy.yaml`——基底无 `inference_privacy_proxies` 段，`is_proxy_only` 返回 False，也正确。边界 OK，但 `is_proxy_only` 读的是副本而非合并后 policy，语义上"是否仅代理"应基于最终生效 policy，当前实现读原始文件路径，对稀疏副本场景是巧合正确而非设计正确。低优先级。

---

## 改进建议

1. **副本文件权限加固**（🔴 高）：`_save_copy` 后对副本设 ACL，仅宿主用户可写，沙箱合成 SID 不授写；或副本落点移出 `{{ workspace }}` 可写树。
2. **副本写入加文件锁**（🔴 高）：`_load_copy`/`_save_copy` 用 `msvcrt.locking`（Windows）/ `fcntl.flock`（Linux）包裹，或临时 `.lock` 文件。
3. **统一副本 YAML 异常处理**（🟡 中）：`policy_reader.load_policy` 副本读取 catch `(OSError, yaml.YAMLError)`，损坏回落基底并 warn。
4. **补查 disable_all 生效链路**（🟡 中）：确认 `sandbox_config_rpc._apply_sandbox_change` 对 network 变更调 `ensure_running` 触发 respawn；若未调，补上。
5. **沙箱降级可见性**（🟡 中）：`interface_deep` 回退 local 时在返回的 sysop 或日志中标记"已降级"，调用方可感知；对"隔离不可信代码"场景考虑 `policy.allow_sandbox_fallback` 开关，默认关闭。
6. **set_* 用 setdefault 链**（🟡 低）：`set_sandbox_files_config`/`set_sandbox_network_config` 改用 `data.setdefault("windows",{}).setdefault(...)` 链，与 get_* 风格一致，抗结构漂移。
7. **副本结构校验**（🟡 低）：`load_policy` merge 前对 `override_data` 顶层做 `isinstance(override_data.get("windows"), dict)` 校验，非 dict 则 warn + 回落基底，避免 merge 产生怪异结构。
8. **端口分配记录已分配集合**（🟡 低）：runner 维护"已分配端口"集合，spawn 后登记，避免 TOCTOU 期间重复分配同一随机端口（虽有内核保证，但记录便于诊断）。

---

## 小结

本次重构方向正确，"稀疏副本 + 内存合并"根治了热更新被旧副本挡住的根本缺陷，机制对齐 config.yaml template+override，disable_all"只压不删"与端口工具外移都是合理增强。代码注释质量高，设计依据交代清楚。主要风险集中在**副本文件可被沙箱内进程篡改（安全）**和**并发写入无锁（一致性）**两点，建议优先处理；disable_all 生效依赖未在本 commit 验证的 `_apply_sandbox_change` 链路，需补查。其余为健壮性与可见性改进，不阻塞合入。
