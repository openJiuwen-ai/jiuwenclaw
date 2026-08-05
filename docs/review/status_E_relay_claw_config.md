# 状态核对 E：relay-claw 配置链路

核对基准：当前工作区 = HEAD `82001d09`（含 `7fe80192`/`f4089537`/`fa85c987`/`a3d0d2bd`/`f52aa505` 之后的全链路 OK 状态）。
核对日期：2026-08-01。

图例：✅已解决 / ❌仍存在 / ⚠️部分解决 / 🔄以其他方式绕过。

---

## A. runner 子进程 env / 生命周期（f52aa505）

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|---|---|---|---|---|
| 1 | `jiuwenbox_runner.py` 用 `env = dict(os.environ)`，把 agent-server 敏感 env（token/DB 口令）全量灌进 box-server 子进程。应改 allowlist + extra_env。 | f52aa505 §4.5/六-1 | ❌仍存在 | `jiuwenbox_runner.py:441` `env = dict(os.environ)` | `ensure_running` 仍硬编码 `dict(os.environ)` 全量复制；`ensure_running` 签名（`jiuwenbox_runner.py:321-329`）无 `extra_env` 参数，调用方无法传入独立 env。无 allowlist 收口。 |
| 2 | `app_agentserver.py` 直接写 `os.environ["JIUWENBOX_VENV_DIR"/"BUNDLED_PYTHON"]`，污染主进程全局 env。应构造独立 env 传子进程。 | f52aa505 §4.5/六-2 | ❌仍存在 | `agent_ws_server.py:354-356` `os.environ["JIUWENBOX_VENV_DIR"] = str(venv_dir)`<br>`os.environ["JIUWENBOX_BUNDLED_PYTHON"] = str(bundled_python.parent)`<br>`os.environ["JIUWENBOX_RUNNER_PYTHON"] = _cand`（357-366） | 代码从 `app_agentserver.py` 搬到 `agent_ws_server.py` `_bootstrap_internal_jiuwenbox`，但仍是 `os.environ[...] =` 直接写主进程全局；后续所有子进程继承。runner 已支持 `env` 透传（spawn_kwargs 用 `env=env`，`jiuwenbox_runner.py:470`），但调用方没构造独立 env 传入。 |
| 3 | `_pump_stream` 用 `logger.debug`，默认 INFO 下 box-server 运行日志全被过滤，沙箱内部失败无法定位。 | f52aa505 §4.2/六-3 | ✅已解决 | `jiuwenbox_runner.py:545` `logger.info("[jiuwenbox/%s] %s", kind, line)`<br>`:549` `logger.info("[JiuwenBoxRunner] pump %s stopped: %s", kind, exc)`<br>docstring `:521-527` 显式说明"用 INFO 而非 DEBUG" | pump 已改 `logger.info`，box-server 运行期日志在默认 INFO 级别可见。 |
| 4 | `runner.stop()` 走 `proc.terminate()`（Windows = TerminateProcess 即时强杀），不给 uvicorn FastAPI lifespan shutdown（`shutdown_all_sandboxes`）机会。 | f52aa505 §4.1/§4.7/六-4 | ❌仍存在 | `jiuwenbox_runner.py:666` `proc.terminate()`<br>`app_agentserver.py:208-210` 注释自承"Windows 上 `proc.terminate()`=TerminateProcess 是即时强杀" | Windows 上 `stop()`/`_stop_no_lock` 仍走 `proc.terminate()`（= TerminateProcess），60s grace 后 `proc.kill()`。lifespan shutdown 在 Windows 上跑不到；活沙箱成孤儿。Linux 走 `os.kill(SIGTERM)` 才 graceful。注释自承"留 Windows 实测时定"。 |
| 5 | 复用判定仅比 `policy_path` 不比内容，运行时副本被改写（path 不变内容变）不触发重启。应补 `_policy_fingerprint` 指纹逻辑。 | f52aa505 §4.1/六-5 | ✅已解决 | `jiuwenbox_runner.py:279-293` `_policy_fingerprint`（sha256）<br>`:389` `owned_match` 含 `self._spawned_policy_fingerprint == new_fp`<br>复位点 `:481,498,657,662,670,696` | `_policy_fingerprint` 已落地，`ensure_running` mismatch 判定含指纹比对，覆盖"path 不变但内容变"场景；spawn 成功/stop/异常路径均复位 `_spawned_policy_fingerprint`。 |

---

## B. 配置 RPC / 渲染 / 副本（f4089537 + 7fe80192 + fa85c987）

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|---|---|---|---|---|
| 6 | 网络 `egress.default` 由 `deny` 改为 `allow`，叠加渲染逻辑在用户未配网络时保留基底 `default:allow` → 沙箱默认全放行出站、网络隔离形同虚设。 | f4089537 §关键代码检视/六-🔴高 | ❌仍存在 | `windows-policy.yaml:50` `default: allow`（顶层 network.egress）<br>`windows-policy.yaml:154` `default: allow`（windows.network.egress）<br>`sandbox_policy_render.py:195-223` `set_sandbox_network_config` 不碰 `egress.default` 字段 | 基底 `egress.default` 仍为 `allow`（两处：顶层 network + windows.network）。注释（`:148-153`）自承"适配期临时放开，待 skill 固化后收紧"，但无 TODO/issue/env 守卫。`set_sandbox_network_config(disable_all=False, allow=[], deny=[])` 不写 `default` 字段 → 保留基底 `allow` → 用户即使想"只 deny 不 allow"也得到 default:allow 基底，实质放行所有非 deny 域名。 |
| 7 | 黑白名单输入只做 `str().strip()`，无文件路径越界校验、无域名格式校验。 | f4089537 §关键代码检视/六-🔴高 | ❌仍存在 | `sandbox_policy_render.py:144-145` `_norm_str_list` = `[str(v) for v in values if str(v).strip()]`<br>`:171-172` `allow_norm`/`deny_norm` 走 `_norm_str_list`<br>`:211-212` `allow_domains`/`deny_domains` 走 `_norm_str_list` | 仍只做 `str().strip()`，无 `Path.resolve` 越界检查、无域名正则/`urlsplit` 校验。`"*.*.*.evil.com"`、含端口/路径/控制字符的串、绝对路径越界均被原样接受并写进副本，进 WFP/文件 ACL。`sandbox_config_rpc.py:209-210,228-229` 只校验 `isinstance(list)`。 |
| 8 | `recreate_all_sandboxes()` 是死代码（全仓无调用方）；`_apply_sandbox_change` 的 `files` 分支 docstring 称"不重启 box-server"但代码实际重启，文档与实现矛盾。 | f4089537 §关键代码检视/六-🔴高 | ❌仍存在 | `sandbox_lifecycle.py:154-172` `recreate_all_sandboxes` 定义；全仓 grep 仅自身 + `__all__` + docs/review 引用，**无任何代码调用方**<br>`sandbox_config_rpc.py:16-17` docstring："files: ... 不重启 box-server (ACL 在沙箱创建时读)"<br>`sandbox_config_rpc.py:107-117` `files` 分支实际走 `runner.ensure_running(...)`（:123-129）重启 box-server | `recreate_all_sandboxes` 仍是死代码（未被接上）。docstring 仍称 files"不重启 box-server"，但 `files`/`network` 分支都调 `runner.ensure_running` 重启 box-server。docstring 还称"已 render 副本"，但 `7fe80192` 后 `set_sandbox_files_config` 只稀疏写副本、不再调 render——docstring 三处与实现矛盾。 |
| 9 | WS 派发 `sandbox.*` 仅靠 Origin 主机名校验，无用户级鉴权，同机任意进程可改写沙箱安全策略。 | f4089537 §关键代码检视/六-🟡中 | ❌仍存在 | `ws_origin.py:87-116` `is_allowed_browser_origin`：`AGENT_RUNTIME` env 非空全放行（`:100-102`）；否则 hostname ∈ {127.0.0.1, localhost}（`:15,116`）<br>`agent_ws_server.py:784-788` `sandbox.*` 派发仅按 `req_method ∈ get_sandbox_config_req_methods()` 路由，无 principal_user_id/channel 白名单/能力校验<br>`_handle_sandbox_config` `:1237-1244` 无鉴权 | 同机任意进程连上 WS 端口即可下发 `sandbox.files.set`/`sandbox.network.set` 改写沙箱安全策略。`AGENT_RUNTIME` env 非空时全放行（任意本机进程可设此 env）。与 `permissions.*` 同病，但 sandbox 配置影响面更大。 |
| 10 | `disable_all` 未在 `windows.network.disable_all` 字段置位，靠 `default=deny`+空 allow 副作用等价；基底改回 `deny` 后此等价会破裂。 | f4089537 §关键代码检视/六-🟡中 | ✅已解决 | `sandbox_policy_render.py:215` `net["disable_all"] = disable_all`（显式置位副本 `windows.network.disable_all`）<br>`app.py:324` `net_disable_all = bool(root_policy.windows.network.disable_all)`（读合并后 policy 的字段）<br>`models/policy.py:850` `disable_all: bool = False`（模型字段） | `set_sandbox_network_config` 显式把 `disable_all` 写进副本 `windows.network.disable_all` 字段；box-server lifespan 从合并后 root_policy 读该字段传给 `EgressFilter(disable_all=...)`。不再靠 `default=deny`+空 allow 副作用等价。基底 `windows-policy.yaml` 无该字段（靠模型 default `False`）。 |
| 11 | `_save_copy` 非原子写，写失败静默回落开放基底。 | f4089537 §关键代码检视/六-🟡中 | ❌仍存在 | `sandbox_policy_render.py:133-141` `_save_copy`：`p.write_text(yaml.safe_dump(data, ...), encoding="utf-8")`；except `OSError` 只 `logger.warning` | 仍直接 `write_text` 覆盖，无 `tmp + replace/rename` 原子写。写中途崩溃留半截 YAML → box-server `load_policy` `yaml.safe_load` 失败 → 走"User policy copy unreadable, using base only"（`policy_reader.py:191-196`）→ 退化为基底（当前 `default:allow`），用户无感知。 |
| 12 | 副本 `windows-policy.runtime.yaml` 落在 workspace 下（沙箱可写路径），沙箱内恶意进程可改写副本 `allowed_domains`/`allow_read`，box-server respawn 后 merge 进基底白名单 → 沙箱内进程自行放宽隔离。 | 7fe80192 §问题与风险-🔴安全 | ⚠️部分解决 | `sandbox_policy_render.py:52-65` `_runtime_copy_path` = `_config_dir()` / `_RUNTIME_COPY_NAME`，`_config_dir` = `~/.jiuwenclaw/config`（`utils.py:1369,1378`）<br>沙箱 `{{ workspace }}` = `~/.jiuwenclaw/jiuwenbox/workspace/<id>`（`workspace.py:61`，`process.py:2825`）<br>`win_acl.py:266-334` 仅对 `allow_write`/`deny_write` 施 ACE，未对 `config` 目录加 Deny Write | 副本落点已与沙箱 `{{ workspace }}` 不同树（config 是 `~/.jiuwenclaw/config`，沙箱 workspace 是 `~/.jiuwenclaw/jiuwenbox/workspace/<id>`，config 不在沙箱 `allow_write` 子树内）。但：① 副本仍在宿主 owner 树下（`~/.jiuwenclaw`，宿主用户 owner），沙箱受限 token 仍是宿主用户身份，默认 owner ACE 通常仍授写；② `win_acl` 未对 `config` 目录设显式 Deny Write ACE；③ `win_acl.py:407-410` 对 `JIUWENCLAW_DATA_DIR_PATH` 只加非递归 traverse read（不给写）。实际可写性取决于 Windows 默认 DACL（通常 owner=host user 有写），沙箱受限 token 若继承 owner 写 ACE 仍可改副本。风险较旧机制降低（不再在沙箱 workspace 内），但未根治（无显式 ACL 加固）。 |
| 13 | 并发写入无锁，lost-update（`_load_copy`/`_save_copy` 全程无文件锁）。 | 7fe80192 §问题与风险-🔴/🟡 | ❌仍存在 | `sandbox_policy_render.py:109-130` `_load_copy` 无锁<br>`:133-141` `_save_copy` 无锁<br>`set_sandbox_files_config`（`:162-180`）/`set_sandbox_network_config`（`:195-223`）各 `_load_copy`→改→`_save_copy` | 全程无 `asyncio.Lock`/`threading.Lock`/文件锁（`msvcrt.locking`/`fcntl.flock`）。并发 `set_sandbox_files_config` + `set_sandbox_network_config`（officeAce 前端同时改文件和网络）会 lost-update。当前 WS handler 串行，单用户概率低，但多租户/多实例场景是真问题。 |
| 14 | `policy_reader` 副本 YAML 损坏只 catch `OSError`，漏 `yaml.YAMLError`。 | 7fe80192 §问题与风险-🟡 | ❌仍存在 | `policy_reader.py:191` `except OSError as exc:`（副本读取，`:189-196`）<br>对比 `_load_copy`（`sandbox_policy_render.py:114`）`except (OSError, yaml.YAMLError) as exc:`<br>`is_proxy_only`（`policy_reader.py:223`）`except (OSError, yaml.YAMLError):` | `load_policy` 副本读取仍只 catch `OSError`。副本是用户可写 YAML，被手改坏（语法错误）时 `yaml.safe_load` 抛 `yaml.YAMLError`，向上抛出，由 `app.py:334` 外层兜底但导致 win_proxy 不启动。与 `_load_copy` / `is_proxy_only` 两处异常处理不一致。 |
| 15 | `set_sandbox_network_config` 改副本后只 `_save_copy` + return，不主动触发 respawn，生效依赖 `sandbox_config_rpc._apply_sandbox_change` 调 `ensure_running`（链路假设）。 | 7fe80192 §问题与风险-🟡 | ✅已解决 | `sandbox_config_rpc.py:236` `SANDBOX_NETWORK_SET` → `_trigger_apply("network")`<br>`:136-146` `_trigger_apply` → `loop.create_task(_apply_sandbox_change("network"))`<br>`:107-130` `_apply_sandbox_change` network 分支 → `await runner.ensure_running(..., policy_path=runner._spawned_policy_path, timeout=120.0)`<br>`jiuwenbox_runner.py:381-390` `ensure_running` 比对 `_policy_fingerprint` → mismatch → stop+spawn | 链路已接通且有效：`set_sandbox_network_config` 写副本后，`dispatch_sandbox_config_request` 调 `_trigger_apply("network")` → `_apply_sandbox_change("network")` → `runner.ensure_running` → `_policy_fingerprint` 检测内容变 → stop+spawn 重启 box-server 重载副本。不再依赖"链路假设"，代码实打实接上。 |
| 16 | `interface_deep.py` 用 `except Exception` 捕获所有异常回退 local（沙箱静默降级 local = 隔离失效但用户无感）。 | 7fe80192 §问题与风险-🟡 | ❌仍存在 | `interface_deep.py:2683` `except Exception as exc:`<br>`:2685-2699` 若原走沙箱则 `create_local_sysop_card` + `add_sys_operation` 回退 local，只 `logger.warning(... "fallback to LOCAL mode")`，返回 local sysop，无"已降级"标记<br>三处回退：sysop_card None（`:2619-2629`）、add 失败（`:2666-2679`）、整体异常（`:2683-2701`） | 仍 `except Exception` 捕获所有异常静默回退 local，调用方拿不到"已降级"信号（返回值是 local sysop，无 flag）。无 `policy.allow_sandbox_fallback` 开关。若用户配沙箱为隔离不可信代码，静默降级到 local = 在宿主机直接跑，安全隔离失效但用户无感。 |
| 17 | bootstrap 触发条件不再看 `sandbox.enabled`，只要 `startup_mode: internal`（显式）就拉起，即使 `enabled: false` 也 spawn。 | fa85c987 §4.1/六-1 | ❌仍存在 | `agent_ws_server.py:273-287` `_bootstrap_internal_jiuwenbox`：`explicit_mode = get_sandbox_startup_mode_explicit()`；`None`→return；`!= "internal"`→return；不看 `sandbox.enabled`<br>docstring `:248-251`："不单独依赖 `sandbox.enabled`: 只要 `startup_mode=internal` 就拉" | 仍只看显式 `startup_mode`，不看 `enabled`。`enabled: false` + `startup_mode: internal` 会 spawn box-server。docstring 明确此行为是有意为之。 |
| 18 | `_normalize_sandbox_startup_mode` 把非法值从抛 `ValueError` 改成静默回落 `internal`。读取路径对非法值不抛错。 | fa85c987 §4.4/六-2 | ❌仍存在 | `config.py:1245-1250` `_normalize_sandbox_startup_mode`：`if text not in _VALID_SANDBOX_STARTUP_MODES: return _DEFAULT_SANDBOX_STARTUP_MODE`（静默回落）<br>写入路径 `config.py:1355-1358` `update_sandbox_startup_mode` 对非法值抛 `ValueError` | 读取路径（`get_sandbox_startup_mode` → `_normalize_sandbox_startup_mode`）仍对非法值（如 `iternal` 拼错）静默回落 `internal`。与写入路径抛 `ValueError` 口径不一。用户直接编辑 yaml 写错值，启动时不报错。 |
| 19 | bootstrap 在 ws listen 之后，初始化竞态（Gateway 抢先连入时 sandbox 可能未 ready）。 | fa85c987 §4.2/六-3 | ❌仍存在 | `agent_ws_server.py:447-456` `legacy_serve(...)`（listen）<br>`:467-470` `logger.info("[AgentWebSocketServer] 已启动: ws://%s:%s", ...)`<br>`:472` `await self._bootstrap_internal_jiuwenbox()`（bootstrap 在 listen 之后） | 仍 listen → 打"已启动" → bootstrap。Windows 首次 install 子进程 + UAC，bootstrap timeout=120s（`:394`），此窗口 Gateway 抢先连入发 sandbox 请求时 box-server 可能未 ready，走 local fallback 或失败。无 `asyncio.Event` gate。 |
| 20 | 复用守卫依赖 agent-core 私有属性（`isolation_key_template`/`_sandbox_key_owner_map`），仓库内无定义，getattr 兜底意味着守卫当前大概率从未命中。 | fa85c987 §4.6/六-4 | ⚠️部分解决 | `interface_deep.py:2518` `sys_operation.isolation_key_template`<br>`:2537` `getattr(sys_operation_mgr, "_sandbox_key_owner_map", {})`<br>agent-core 已定义：`.venv/.../openjiuwen/core/runner/resources_manager/sys_operation_manager.py:16` `self._sandbox_key_owner_map: dict[str, str] = {}`，`:38-48` add 时写入 owner_map，`:67-68` delete 时 pop | 守卫当前**会命中**（agent-core vendored 源码定义了 `_sandbox_key_owner_map` 与 `isolation_key_template`，add 失败时 owner_map 已有条目 → 守卫复用）。"大概率从未命中"的判断已被证伪。但：① 仍依赖 agent-core **私有**属性名（`_resource_registry`/`_sandbox_key_owner_map`），无版本门控/capability 探测；② `getattr(..., "_sandbox_key_owner_map", {})` 兜底意味着 agent-core 若改名/删除则守卫静默失效无人察觉。耦合脆弱性仍在。 |
| 21 | 接口数 6 vs 8 文档矛盾；`enabled` 默认值文档说 true 但 `config.py _SANDBOX_RUNTIME_DEFAULTS["enabled"]=False`。 | a3d0d2bd §4.4/§4.9/六-1/六-5 | ❌仍存在 | 设计文档 `docs/sandbox-config-control.md:169` "默认 true"（与代码矛盾）<br>`:232` "返回 6 个方法的 frozenset"（应 8）<br>`:803` "只需把 6 个 WS 接口（`sandbox.{enabled,files,network}.{get,set}`）"（漏 startup_mode，应 8）<br>`config.py:1189-1190` `_SANDBOX_RUNTIME_DEFAULTS = {"enabled": False, ...}`<br>`sandbox_config_rpc.py:34-45` `_SANDBOX_CFG_METHODS` frozenset 含 8 成员 | 设计文档（`a3d0d2bd` commit 后未再修改）仍有口径不一：第 232/803 行"6 个"漏 startup_mode；第 169 行"默认 true"与代码 `_SANDBOX_RUNTIME_DEFAULTS["enabled"]=False` 矛盾。实现侧 `_SANDBOX_CFG_METHODS` 正确含 8 成员，但文档未发 errata 修正。前端照文档"默认 true"会误判沙箱开关状态。 |

---

## C. 核对汇总

本组共核对 **21** 条（runner env/生命周期 5 条 + 配置 RPC/渲染/副本 11 条 + 启动风格 4 条 + 设计文档 1 条）。

### 已解决（X = 5）
- #3 pump 日志级别改 info（f52aa505）
- #5 policy 内容指纹 `_policy_fingerprint`（f52aa505）
- #10 `disable_all` 显式置位 `windows.network.disable_all` 字段（f4089537）
- #15 `set_sandbox_network_config` → `_trigger_apply("network")` → `ensure_running` respawn 链路接通（7fe80192）
- #20 agent-core vendored 源码已定义 `_sandbox_key_owner_map`/`isolation_key_template`，守卫会命中（fa85c987，但私有属性耦合仍在 → 归 ⚠️部分解决更准确，见下）

> 注：#20 实际更接近 ⚠️部分解决（守卫会命中，但私有属性耦合 + 无版本门控仍在）。若严格按"守卫是否命中"判则 ✅，按"脆弱耦合是否消除"判则 ⚠️。本表归 ⚠️。

### 仍存在（Y = 13）
- **#1** runner `env = dict(os.environ)` 全量透传，无 allowlist + extra_env（f52aa505）
- **#2** `os.environ["JIUWENBOX_VENV_DIR/BUNDLED_PYTHON/RUNNER_PYTHON"]` 污染主进程全局（f52aa505，从 app_agentserver 搬到 agent_ws_server 但同模式）
- **#4** Windows `proc.terminate()` 即时强杀，lifespan shutdown 跑不到（f52aa505）
- **#6** 网络 `egress.default: allow`，用户未配网络时沙箱默认全放行（f4089537）
- **#7** 黑白名单只做 `str().strip()`，无路径越界/域名格式校验（f4089537）
- **#8** `recreate_all_sandboxes` 死代码 + docstring 称 files"不重启 box-server"但代码重启（f4089537，7fe80192 后 docstring 更过时）
- **#9** WS 派发 `sandbox.*` 仅靠 Origin 主机名校验，无用户级鉴权（f4089537）
- **#11** `_save_copy` 非原子写，写失败静默回落开放基底（f4089537）
- **#13** `_load_copy`/`_save_copy` 无文件锁，lost-update（7fe80192）
- **#14** `policy_reader.load_policy` 副本读取只 catch `OSError` 漏 `yaml.YAMLError`（7fe80192）
- **#16** `interface_deep.py except Exception` 静默回退 local，无"已降级"标记（7fe80192）
- **#17** bootstrap 不看 `sandbox.enabled`，`startup_mode: internal` 即 spawn（fa85c987）
- **#18** `_normalize_sandbox_startup_mode` 读取路径对非法值静默回落 `internal`（fa85c987）
- **#19** bootstrap 在 ws listen 之后，初始化竞态（fa85c987）
- **#21** 设计文档 6/8 口径不一 + `enabled` 默认值文档 true vs 代码 False（a3d0d2bd）

### 部分解决（Z = 3）
- **#12** 副本落点已与沙箱 `{{ workspace }}` 不同树（config ≠ sandbox workspace），但仍在宿主 owner 树下、无显式 Deny Write ACL 加固（7fe80192）
- **#20** 守卫会命中（agent-core 已定义私有属性），但私有属性耦合 + 无版本门控仍在（fa85c987）

### 以其他方式绕过
- 无。

---

## D. 关键文件清单（核对依据）

- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/jiuwenbox_runner.py`（runner：env/pump/stop/指纹）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/sandbox_config_rpc.py`（RPC 派发 + `_apply_sandbox_change` + docstring 矛盾）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/sandbox_policy_render.py`（副本读写/校验/原子写/锁/disable_all）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/sandbox_lifecycle.py`（`recreate_all_sandboxes` 死代码）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/agent_ws_server.py`（bootstrap/env 注入/listen 顺序/WS 派发/`_handle_sandbox_config`）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/security/ws_origin.py`（Origin 校验，无用户级鉴权）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/app_agentserver.py`（关停顺序注释）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/config.py`（`_SANDBOX_RUNTIME_DEFAULTS`/`_normalize_sandbox_startup_mode`/`get_sandbox_startup_mode_explicit`）
- `d:/Workspace/community/jiuwenclaw/jiuwenclaw/agentserver/deep_agent/interface_deep.py`（`_create_sys_operation` 回退 local + 复用守卫）
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/server/policy_reader.py`（`load_policy` 异常处理）
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/server/app.py`（`disable_all` 读取）
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/server/workspace.py`（路径根）
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml`（`egress.default: allow`）
- `d:/Workspace/community/jiuwenclaw/docs/sandbox-config-control.md`（设计文档口径不一）
- `d:/Workspace/community/jiuwenclaw/.venv/.../openjiuwen/core/runner/resources_manager/sys_operation_manager.py`（agent-core 私有属性定义）

---

## E. 优先级建议（仅按安全/阻断性排序）

1. **🔴 收紧网络 `egress.default`**（#6）：沙箱网络隔离形同虚设，无 env 守卫/TODO。改回 `deny` + 显式 CDN 白名单或 env 守卫。
2. **🔴 env 透传收口**（#1/#2）：凭据泄漏面 + 主进程全局污染。`ensure_running` 加 `extra_env` 参数，用 allowlist 合并。
3. **🔴 黑白名单输入校验**（#7）：路径越界 + 域名格式。`Path.resolve` + 域名正则。
4. **🔴 WS 鉴权**（#9）：同机任意进程可改写沙箱策略。加 channel 白名单/principal_user_id 校验。
5. **🟡 副本安全**（#11/#12/#13/#14）：原子写 + 文件锁 + ACL 加固 + `yaml.YAMLError` catch。
6. **🟡 docstring/死代码**（#8）：对齐 docstring 与实现，删除或接上 `recreate_all_sandboxes`。
7. **🟡 启动语义**（#17/#18/#19）：恢复 `enabled` 门控或文档点明；读取路径非法值抛错；bootstrap 移到 listen 前。
8. **🟡 降级可见性**（#16）：回退 local 标记"已降级"，加 `policy.allow_sandbox_fallback` 开关。
9. **🟡 文档 errata**（#21）：修正设计文档 6/8 与"默认 true"。
10. **🟡 Windows graceful stop**（#4）：Job Object/CTRL_BREAK_EVENT 或 docs 标注。
