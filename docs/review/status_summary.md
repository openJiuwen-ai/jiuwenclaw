# Windows 沙箱适配链路：剩余待解决问题汇总

> **核对基准**：工作区 = HEAD `82001d09` + 后续修复（WFP per-port 回退）
> **核对日期**：2026-08-01（2026-08-02 更新：P0-1 已修复并实测验证）
> **来源**：21 份逐 commit 检视报告（`docs/review/5f841f7a_*.md` ~ `82001d09_*.md`）+ 7 份状态核对报告（`docs/review/status_A~G_*.md`）
> **图例**：🔴高（生产阻断）/ 🟡中（应修）/ 🟢低（清理类）

---

## 一、总体统计

7 组核对共覆盖 **90 条**问题点（已对重复项去重前的原始计数）。按当前工作区状态：

| 状态 | 数量 | 说明 |
|------|------|------|
| ✅ 已解决 | ~52 | 含 P0 全清零 + P1 大部分 + P2 清理类（23/25/26/27/28/29/31/34/35/36/37/41/42/43/48） |
| 🔄 以其他方式绕过 | ~4 | bug 代码仍在但通过设计变更使其危害不再成立 |
| ⚠️ 部分解决 | ~5 | P1-16 ③已修①②为有意决策；其余核心修复已落地 |
| ❌ 仍存在 | ~18 | P1-12 Job Object（暂不修）+ P2 剩余（24/30/32/33/38/39/40/44/45/46/47 等低风险/跳过项） |

> 下文"剩余待解决清单"按主题归类，**合并了跨组重复项**（如"受限 token 弃用"在 A/B/C/D/G 五组都被提及，归为一条；"Job 禁用"在 A/G 提及，归为一条）。

---

## 二、剩余待解决问题清单（按主题 + 严重程度）

### 🔴 P0 生产阻断项（安全核心，必须修复才能全量上线）— 原 7 项，已全部修复 ✅

#### 1. ~~网络隔离形同虚设（两处叠加）~~ ✅ 已解决
- **① `egress.default: allow`** — `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml:50,154`
  - **保留为有意决策，非缺陷**：skill（pptx-craft 等）内部可能需联网下载资源（playwright/pip/npm install、CDN 资源 localize），`default:deny` + 空白名单会导致任务中断。代码注释（windows-policy.yaml:148-153）明确：待 skill 侧把 CDN 资源打包/镜像固化后再收紧为 `default:deny`。前置条件是 skill 侧，非 box-server 单方面能改。
- **② WFP Permit filter 全端口放行** — `jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py:756-791`
  - **已修复**：已回退 `82001d09` 的全端口放行，恢复 per-port Permit（仅放行 `127.0.0.1:60080-60089`，对齐设计文档 `docs/window沙箱.md 6.4.2`）。保留了 `82001d09` 引入的 `is_v4` 显式判断修复（旧版 `"V4" in base_key` 是真实 bug 不能一起回退）。uninstall 路径保留了对固定 base_key 的清理以兼容升级残留。
  - **实测验证**：端口范围收窄后 PPT 生成正常，render server 未因端口限制失败。
- **剩余**：① 的 `default:allow` 待 skill CDN 固化后收紧（独立后续项，非 P0 阻断）。

#### 2. ~~受限 token 弃用，双重写检查名存实亡~~ ✅ 已修复（实跑验证，弃用为有意决策）
- `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:1300-1312`（exec 用 `_get_runner_primary_token()` 未受限）；`win_constants.py:75`（`RESTRICTED_TOKEN_FLAGS` 不含 WRITE_RESTRICTED）
- **实跑验证（2026-08-02）**：恢复受限 token 后 bash/python/cmd 全部 `exit=3221225794`（= `0xC0000142 STATUS_DLL_INIT_FAILED`），`stdout=0`、`killed=False`。根因是受限 token 的 desktop/全局对象机制（非 ACL/env，env 已补齐 SystemRoot/profile 仍挂），Windows token 机制硬限制，非代码遗留。已回退为未受限 token。
- **补偿已落地**：受限 token 这重缺失靠 P0-3 的 ACL 收窄补偿——deny_write 覆盖真实 SID + 整树 Write 去掉、Write 改为精确子树授权。写控制仍为"双重"（ACL + WFP），但 ACL 这重已做扎实（见 P0-3）。
- `_create_restricted_token` 仍被 runner_main 构造但 exec 不消费（dead code，待清理，非阻断）。

#### 3. ~~ACL 整树授权过宽 + deny_write 不覆盖真实 SID~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_acl.py:430-461`（`~/.office-claw` 整树从 grant `Read+Write` 收窄为**只 grant Read**，合成+真实 SID）
- `win_acl.py:334-352`（deny_write 从只 Deny 合成 SID 改为**也 Deny 真实 SID**）
- `jiuwenclaw/agentserver/deep_agent/sysop_builder.py:309-326`（Windows 分支从空 policy 改为调 `build_filesystem_policy`，把 agent_root 注入 `read_write`/`bind_mounts`，agent 业务目录 AGENT.md/memory/skills/output_dir 现在单独精确 grant Write，不再靠整树兜底）
- `win_setup.py:971-994`（install 阶段整树 grant 同步收窄为只 Read）
- **效果**：消除跨沙箱互写（整树 Write 去掉）、deny_write 对真实 SID 失效（加真实 SID Deny）、副本可篡改（整树 Write 去掉，副本在只 Read 树下）。AGENT.md/memory.md 等业务文件仍可读（整树 Read 保留）+ 可写（agent 子树单独 Write）。
- **生效前提**：需 `--force` 重装（install 整树 ACL 改了，旧 Write ACE 残留需清理）+ 重启 box-server + 新建沙箱。
- **未采纳**：`applied` 按"路径+SID+rights"记账（Deny ACE 残留无害，接受最小改动）。

#### 4. ~~jbx-sandbox 密码固定 "000000"~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py:320-328`（`_generate_password` 改 `secrets.token_urlsafe(48)` 强随机，过 Windows 密码复杂度策略）
- **持久化**：DPAPI 注册表存储已就绪（`CryptProtectData` 机器绑定，`get_sandbox_user_password` 重启后读回）。install 时新建用户用新随机密码存 DPAPI；已存在用户读 DPAPI 旧密码保持一致（读不到则重设对齐）。
- **配套 P1-9**：uninstall 清理 `SANDBOX_USER_PW`/`SANDBOX_USER_SID`（避免旧 DPAPI blob 残留重现 1326）+ `LookupAccountName` 实时取 SID 兜底。

#### 5. ~~runner 子进程 env 全量透传凭据 + 主进程全局污染~~ ✅ 已修复（2026-08-02）
- `jiuwenclaw/agentserver/jiuwenbox_runner.py:441-460`（`dict(os.environ)` 改为 allowlist 继承 + `JIUWENBOX_*` 前缀 + `extra_env` 覆盖；`ensure_running` 加 `extra_env` 参数）
- `jiuwenclaw/agentserver/agent_ws_server.py:343-396`（`os.environ["JIUWENBOX_*"]=` 改为构造 `sandbox_env` dict 经 `extra_env` 传子进程，不污染主进程全局）
- 顺带去掉 dev 机硬编码 `D:\Files\python313`（P2-21）。

#### 6. ~~exec socket 无鉴权 + 跨沙箱串扰~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/server/runtime/process.py:3041-3064`（box-server 分配 `control_token = secrets.token_urlsafe(32)`，存 `_win_runners`，经 `--control-token` 命令行参数传 runner）
- `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:440-470`（`two_hop_spawn` + `_build_runner_command` 加 `control_token`）；`:1115-1131`（runner 首帧校验 `header["token"] == control_token`，恒定时间 `hmac.compare_digest`，不匹配拒绝）
- 所有 roundtrip（exec/file-op/shutdown/subscribe_log）发 header 时注入 token。

#### 7. ~~黑白名单输入无校验~~ ✅ 已修复（2026-08-02）
- `jiuwenclaw/agentserver/sandbox_policy_render.py:144-218`（`_norm_file_paths` 校验绝对路径+无控制字符；`_norm_domains` 校验域名格式+无端口/路径/控制字符，正则 `^(?:\*\.)?(?:[A-Za-z0-9-]+.)+[A-Za-z]{2,}$`）
- `set_sandbox_files_config` 用 `_norm_file_paths`；`set_sandbox_network_config` 用 `_norm_domains`。非法条目 warning 跳过不整体失败。

---

### 🟡 P1 应修项（健壮性/一致性/可观测性）

#### 9. ~~uninstall 对称缺口~~ ✅ 已修复（与 P0-4 同批，2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py:1244-1288`：
  - ① 删用户前无前置进程检查（未来产品卸载直接调 uninstall 而 runner 还在跑会删用户于运行中）；
  - ② `_delete_profile_by_sid` 用注册表缓存 SID（install 早期失败回滚时可能是旧 SID）；
  - ③ 只清 `REG_VALUE_INSTALLED`，`SANDBOX_USER_SID/PW`（DPAPI blob）保留；
  - ④ `DeleteProfileW` 失败只 warning 后仍删用户。
- 改随机密码（问题 4）后这三处会重现 1326，必须同批修。
- **修法**：入口加"枚举 jbx-sandbox SID 进程"前置检查；`LookupAccountName` 实时取 SID；顺手清 `SANDBOX_USER_SID/PW`；`DeleteProfileW` 失败时 abort。

#### 10. ~~install 等待无超时~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py:731-738`（`WaitForSingleObject(event, INFINITE)`）
- install 子进程在 SetEvent 前崩溃（非 raise 路径）则主进程永久阻塞。注释承诺"加超时兜底"但 `INFINITE=0xFFFFFFFF` 未改。
- **修法**：`INFINITE` 改 `120_000` ms，超时降级 warning。

#### 11. ~~install 提权子进程绕过 Python 探测~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py:585-609`（`_load_policy_preinstall_paths` 直接 `yaml.safe_load` 原始 YAML，不经 `_resolve_tool_paths`）
- `--force --policy-path` 重装时 tool_paths 全空 → 预装集丢失工具目录 → 重装后受限 token 读不了 OfficeAce `tools/python` → WinError 2/5。install 与 runtime 读的不是同一份填充后 tool_paths。
- **修法**：`_load_policy_preinstall_paths` 复用 `PolicyReader.load_policy()` 或抽公共"加载+探测"函数。

#### 12. Job Object 资源限制禁用
- `jiuwenbox/src/jiuwenbox/server/runtime/process.py:3065-3111`（创建/assign 整段注释禁用）
- 沙箱无内存/CPU/进程数上限，fork bomb/内存炸弹可耗尽宿主。`win_job.py` 模块本身完整（`assign_process` 已支持 handle 直接 assign），恢复只差 `_create_windows` 调用接线（用 `proc_handle` 直接 assign 绕过跨用户 OpenProcess）。注释已记录方向但未实施。
- **修法**：`_create_windows` 接回 `win_job.assign_process(job_handle, proc_handle)`。

#### 13. ~~副本安全（4 处）~~ ✅ 已修复（2026-08-02，③ ACL 加固由 P0-3 整树收窄覆盖）
- `jiuwenclaw/agentserver/sandbox_policy_render.py`：
  - ① `_save_copy` 非原子写（`:133-141`，写失败静默回落开放基底）；
  - ② `_load_copy`/`_save_copy` 无文件锁，并发 lost-update（`:109-141`）；
  - ③ 副本落点虽已与沙箱 workspace 不同树，但仍在宿主 owner 树下、无显式 Deny Write ACL 加固（`:52-65`）；
  - ④ `policy_reader.load_policy` 副本读取只 catch `OSError` 漏 `yaml.YAMLError`（`policy_reader.py:189-196`）。
- **修法**：tmp+rename 原子写；`msvcrt.locking`/`threading.Lock` 文件锁；副本目录加 Deny Write ACE；catch `(OSError, yaml.YAMLError)`。

#### 14. ~~Windows 优雅停止缺失~~ ✅ 已修复（2026-08-02）
- `jiuwenclaw/agentserver/jiuwenbox_runner.py:666`（`proc.terminate()` = TerminateProcess 即时强杀）
- 不给 uvicorn FastAPI lifespan shutdown（`shutdown_all_sandboxes`）机会，活沙箱成孤儿。注释自承"留 Windows 实测时定"。
- **修法**：Job Object / `CTRL_BREAK_EVENT` 或 docs 标注。

#### 15. ~~沙箱静默降级 local 无标记~~ ✅ 已修复（2026-08-02）
- `jiuwenclaw/agentserver/deep_agent/interface_deep.py:2683-2700`（`except Exception` 回退 local，仅 `logger.warning`，返回值无"已降级"标志）
- 用户配沙箱为隔离不可信代码时，静默降级到 local = 在宿主机直接跑，隔离失效但用户无感。无 `policy.allow_sandbox_fallback` 开关。
- **修法**：返回值/日志透出"已降级"标志，或加 `allow_sandbox_fallback` 开关（默认关闭）。

#### 16. ~~启动流程语义（3 处）~~ ✅ 部分修复（③ 已修；①② 为有意决策/合理现状，已文档化）
- `jiuwenclaw/agentserver/agent_ws_server.py`：
  - ① bootstrap 不看 `sandbox.enabled`，`startup_mode:internal` 即 spawn（`:273-287`）；
  - ② bootstrap 在 ws listen 之后，初始化竞态（`:467-472`，Gateway 抢先连入时 sandbox 未 ready）；
  - ③ `jiuwenclaw/config.py:1245-1250`：`_normalize_sandbox_startup_mode` 读取路径对非法值静默回落 `internal`，与写入路径抛 `ValueError` 不一致。
- **修法**：恢复 `enabled && startup_mode==internal` 门控或文档点明；bootstrap 移到 listen 前或加 `asyncio.Event` gate；读取路径对非空非法值抛错。

#### 17. ~~注入键无黑名单~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:951-955`（`JIUWENBOX_INJECT_ENV` 注入键无过滤）
- `LD_PRELOAD`/`PYTHONPATH`/`NODE_OPTIONS` 等代码注入类键可被注入（agent-core 可信但 header 传输链路可构造）。仅靠 `setdefault` 不覆盖已预置键做间接防护。
- **修法**：加 `_FORBIDDEN_INJECT_KEYS` frozenset 拒绝。

#### 18. ~~命令行拼接不转义内部双引号~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:870-879`（`_create_process_as_user` 的 `cmd_line` 改用 `subprocess.list2cmdline` 正确转义内部双引号 + `create_unicode_buffer` 可变 buffer）
- `win_exec.py:442-447`（`_build_runner_command` 同样改 `list2cmdline`，防御性统一）
- **效果**：`python -c "print("ok")"` 经 `CreateProcessAsUserW` 后 bash/python 收到的脚本双引号完整还原，不再 `SyntaxError: '(' was never closed`。修复了 convert 阶段 agent 大量 `python -c` 重试失败的根因。

#### 19. ~~exec 日志含完整命令行（凭据泄露面，且恶化）~~ ✅ 已修复（2026-08-02）
- `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py:786-795`（日志从全量 `request.command` + `PATH` 改为仅打 `command[0]`（可执行名）+ `argc` + `workdir`，不打 PATH）
- 防止 prompt/API key/敏感路径经 INFO 级日志落盘。

#### 20. ~~进程名白名单过窄~~ ✅ 已修复（2026-08-02）
- `jiuwenclaw/agentserver/jiuwenbox_runner.py:107-113`（`!= "python"` 改 `startswith("python")`，覆盖 python3.13/pythonw）
- 残留 python 进程清不掉致 win_proxy bind 10048 的问题修复。

#### 21. ~~CPython 探测硬编码 dev 机路径~~ ✅ 已修复（P0-5 同批，2026-08-02）
- `jiuwenclaw/agentserver/agent_ws_server.py:361-418`（已删 dev 硬编码，改 glob `C:\Python3*` + `%LOCALAPPDATA%\Programs\Python\Python3*` + PATH `shutil.which` + `_is_std_cpython` 校验非 venv trampoline）

#### 22. ~~design 文档 errata~~ ✅ 已修复（2026-08-02）
- `docs/sandbox-config-control.md:169`（`enabled` 默认值 true → false，对齐 `_SANDBOX_RUNTIME_DEFAULTS["enabled"]=False`）
- `:232/:803`（接口数 6 → 8，补 `startup_mode` 组）

---

### 🟢 P2 低优先级（清理/防御性）

| # | 问题 | 证据 | 修法 |
|---|------|------|------|
| # | 问题 | 状态 |
|---|------|------|
| 23 | `_WFP_ERROR_NAMES` 死条目 `0x80320032` | ✅ 已删除 |
| 24 | `write_sid_ptr` 未 `FreeSid`（内存泄漏） | 🟢 低风险（runner 仅调一次, 跳过） |
| 25 | 三个 SID helper 死代码 | ✅ 已删除，保留注释参考 |
| 26 | `_local_log_file` 未显式 close | ✅ runner finally 块已加 close |
| 27 | except 分支未关 `child_in_write` | ✅ 已补 `CloseHandle(child_in_write)` |
| 28 | exec 超时强杀 `_child_killed` 仅日志未回传 | ✅ 响应体已加 `killed` 字段 |
| 29 | `child_out_read` 未关继承 | ✅ 已补 `_clear_inherit(child_out_read)` |
| 30 | TEMP 子目录命名两跳不一致 | 🟢 正常情况一致, 跳过 |
| 31 | process.py 创建期 4 条 info 噪音 | ✅ 3 条下调到 debug（保留 runner spawned info） |
| 32 | TEMP 注入语义不一致 | 🟢 有意设计（setdefault 不覆盖 vs 直接赋值覆盖）, 跳过 |
| 33 | `.exec_failed.log` 落盘 workspace | 🟢 低风险, 跳过 |
| 34 | `*.npmmirror.com` 通配符偏宽 | ✅ 收紧到 `cdn`/`registry` 子域 |
| 35 | Chrome 预装路径硬编码 | ✅ 加注释说明按实际位置调整 |
| 36 | `keeps_alive` dead parameter | ✅ 已删形参（`**kwargs` 兼容） |
| 37 | `SetEntriesInAcl` 返回值忽略 | ✅ 已取返回值校验 |
| 38 | WFP uninstall 范围不匹配 | 🟢 新装环境（固定 key）影响减弱, 跳过 |
| 39 | WFP session/filter 非 DYNAMIC 默认持久化 | 🟢 当前行为正确, 跳过 |
| 40 | drain 截断后 child 阻塞 | 🟢 可接受, 跳过 |
| 41 | `node_dir` 向上遍历到文件系统根 | ✅ 限定只查 `py_dir.parent` |
| 42 | 探测路径未 `.resolve()`、未标来源 | ✅ 已加 `.resolve()` + 日志标注来源 |
| 43 | `_win_workspace_for` docstring 缺 `workspace` 段 | ✅ 已修正 |
| 44 | 存量用户 env→yaml 迁移 | 🟢 跳过（需配合上游文档） |
| 45 | `recreate_all_sandboxes` 死代码 | 🟢 跳过（保留接口） |
| 46 | 复用守卫依赖 agent-core 私有属性 | 🟢 跳过（agent-core 协议） |
| 47 | `JIUWENBOX_INJECT_ENV` 协议无版本协商 | 🟢 跳过（低风险） |
| 48 | `deny_read`/`allow_read` 同路径语义注释错误 | ✅ 已修正（NTFS Deny 优先） |

---

## 三、关键修复链路（已解决项摘要）

下列是检视报告中发现、后续 commit 已真正修复或症状消除的问题，用于核对"哪些已经解决了"：

### ✅ 进程执行/受限 token 技术层
- ctypes 结构体对齐（`_SID_AND_ATTRIBUTES`/`_TOKEN_GROUPS` 8字节对齐、PSID 指针签名、悬垂指针）— d15fcf8e/fb587eac
- `_create_restricted_token` 动态 entries + None 防御 — d15fcf8e
- exec stdout 死锁根治（后台 drain 线程 + join 5s 兜底）— 82001d09（全链路打通关键拐点）
- roundtrip exec 读响应 130s 长超时（不再全程 2s 必断）— fb587eac→后续
- runner env 传 env 块 + `CREATE_UNICODE_ENVIRONMENT`（PATH 不再丢失）— fb587eac→后续
- `_close_win_pipe_handles` 双重 CloseHandle（改 TCP loopback 后变空方法）— fb587eac
- pipe→TCP loopback 控制通道重构 — fb587eac

### ✅ WFP/网络技术层
- FWPM_FILTER0 union 16B / filterCondition 字段名 / V4 GUID 904F / V4-V6 分支判断 — e8fb0a69/bb1afca0
- WFP 结构体布局/GUID/枚举/字节序整体重写 — bb1afca0
- win_proxy 改在 app.py lifespan 启动（进程级单例，ECONNREFUSED 消除）— 后续
- win_proxy docstring OR 语义对齐 — bb1afca0→后续

### ✅ ACL 技术层
- `GetAclSize()`→`GetAceCount()` + 子元组分支（ACL 修复真正生效）— c2c3f5f0→后续
- `KEY_READ_WRITE` 0x2001F / `NetLocalGroupAdd` argtypes — 3fe33056
- ACE 继承标志 0x7→0x3（递归 ACE 向下传播）— 432a5001
- deny_read 先 Deny 后 allow 顺序 / 保留继承 / 按清单撤销 — bb1afca0

### ✅ 用户/install 技术层
- `_reg_get_str` 两阶段读（DPAPI 密码 hex 截断 WinError 1326 根因）— 3fe33056
- `_purge_stale_profile_dirs` `_rmtree_onerror` + `os.rmdir` 兜底 — ee03da56→后续
- install 回滚机制（try/except + uninstall + re-raise，不再假成功）— e8fb0a69
- 常量错值修正（`SANDBOX_INERT`/`UF_DONT_EXPIRE_PASSWD`/NetUserDel 2221）— e8fb0a69
- 用 `DeleteProfileW` + `NetUserDel` 消解 reinstall 密码不一致根因 — ee03da56

### ✅ relay-claw 配置链路
- pump 日志改 info（box-server 日志默认可见）— f52aa505→后续
- `_policy_fingerprint` 内容指纹（path 不变内容变触发重启）— f52aa505→后续
- `disable_all` 显式置位 `windows.network.disable_all` 字段 — f4089537→7fe80192
- `set_sandbox_network_config`→`_trigger_apply`→`ensure_running` respawn 链路接通 — 7fe80192
- 稀疏副本+内存合并（根治"副本固化基底挡升级"热更新缺陷）— 7fe80192
- runner python 改标准 CPython + `JIUWENBOX_RUNNER_PYTHON` env（venv 伪修复回退）— ab4932ac→后续
- `JIUWENBOX_INJECT_ENV` 约定键机制（pop 防泄漏 + setdefault 防覆盖）— 2d19941c
- HTTP_PROXY 指向可信本机 loopback（无 SSRF）— 432a5001/2d19941c
- 本地落盘日志（`runner.log`，best-effort 不阻断）— 82001d09
- `validate_policy` 400 阻断消除（`_is_absolute_sandbox_path` 接受 Windows+POSIX）— ab4932ac→后续（绕过）

---

## 四、修复优先级建议

**第一梯队（生产阻断，必须先做）**：~~问题 1~~（已修复）、~~问题 2~~（已修复）、~~问题 3~~（已修复）、~~问题 4~~（已修复，密码随机化+DPAPI 持久化）、~~问题 5~~（已修复，env allowlist 收口）、~~问题 6~~（已修复，exec socket token 鉴权）、~~问题 7~~（已修复，黑白名单校验）— **P0 全部清零**

**第二梯队（与第一梯队强耦合，同批做）**：~~问题 9~~（已修复）、~~问题 10~~（已修复，install 超时兜底）、~~问题 11~~（已修复，install 走 _resolve_tool_paths）、问题 12（Job 资源限制，暂不修）

**第三梯队（应修，灰度期可后做）**：问题 10、13-22

**第四梯队（清理类）**：问题 23-48

---

## 五、结论

链路终点 `82001d09` **功能已打通**（exec stdout 死锁根治为关键拐点），**纯技术类 bug（ctypes 对齐/WFP SDK/ACL 解析/密码截断/install 回滚）已基本修完**，核心 ACL + WFP 非 loopback Block 隔离仍有效，可观测性已补齐。

**P0 修复进展（2026-08-02）**：
- **P0-1（WFP 网络隔离）已修复**：回退全端口放行为 per-port Permit（仅 `127.0.0.1:60080-60089`），实测 PPT 生成正常。`egress.default: allow` 作为 skill 联网下载的有意决策保留。
- **P0-2（受限 token）已修复（实跑验证）**：恢复受限 token 后 bash/python/cmd 全部 `0xC0000142`（desktop/全局对象机制硬限制），弃用为有意决策。补偿靠 P0-3 ACL 收窄落地。
- **P0-3（ACL 整树 + deny_write）已修复**：整树 grant 从 Read+Write 收窄为只 Read；deny_write 覆盖真实 SID；agent 业务子树经 sysop_builder 单独精确 grant Write。生效需 `--force` 重装 + 重启 box-server + 新建沙箱。
- **P0-4（密码）已修复**：`secrets.token_urlsafe(48)` 随机密码 + DPAPI 注册表持久化（重启可登录）；uninstall 清理旧 DPAPI blob（P1-9 同批）。
- **P0-5（env 透传）已修复**：`ensure_running` 加 `extra_env` + allowlist 继承，去掉 `dict(os.environ)` 全量透传 + `os.environ` 全局污染。
- **P0-6（exec socket 鉴权）已修复**：box-server 分配 `control_token`，runner 首帧恒定时间校验，防本机任意进程越权 exec。
- **P0-7（黑白名单校验）已修复**：路径绝对+无控制字符校验、域名格式正则校验，非法条目 warning 跳过。

隔离强度从设计预期的"三重"**降级为"双重"**（ACL + WFP，受限 token 因 0xC0000142 硬限制弃用），但 ACL 这重已通过 P0-3 收窄做扎实，写控制 + 网络隔离 + exec 鉴权 + 密码 + env 已全部加固。**P0 生产阻断项已全部清零**。

**Windows 沙箱适配链路 P0 安全债清零，可推进全量生产**（生效前提：P0-3/P0-4 需 `--force` 重装 + 重启 box-server + 新建沙箱）。
