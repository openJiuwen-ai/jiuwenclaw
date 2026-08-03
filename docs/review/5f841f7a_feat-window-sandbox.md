# Commit 检视报告：5f841f7a feat:window 沙箱

## 一、概述
- Commit：5f841f7ae9f357138740225b29a2c703ff4ca585
- 日期：2026-07-21
- 作者：lby
- 说明：本 commit 为 Windows 沙箱功能基线提交，新增约 4575 行（14 个文件，1 行删除）。
- 定位：这是 Windows 沙箱适配链路的**起点 commit**。该链路后续（develop/enterprise_dev_windowbox 分支上）还有多轮修启停、自动探测 python、relay-claw 配置等修复（见 82001d09/cafaa1f1/7fe80192 等）。本 commit 建立了从用户/组创建、ACL、两跳启动、受限 token、WFP 网络隔离、Job Object、出站代理、运行时进程管理到策略配置的完整 Windows 沙箱骨架。代码中已大量内嵌注释，记录了此前轮次迭代的 bug 修复（标注 "review CRITICAL/MAJOR #"），这些是上一轮评审的修复痕迹。

## 二、变更范围

| 文件 | +/- | 说明 |
|---|---|---|
| jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml | +132 | Windows 沙箱示例策略：proxy 端口范围、filesystem allow/deny 写读控制、tool_paths、network egress/ingress、resource 限制 |
| jiuwenbox/src/jiuwenbox/models/__init__.py | +10 | 模型导出 |
| jiuwenbox/src/jiuwenbox/models/common.py | +3 | 公共模型补充 |
| jiuwenbox/src/jiuwenbox/models/policy.py | +147/-1 | SecurityPolicy 扩展 windows 子模型（proxy/filesystem/tool_paths/network/resource）|
| jiuwenbox/src/jiuwenbox/server/app.py | +58 | lifespan 启动时 win32 分支调 ensure_windows_setup |
| jiuwenbox/src/jiuwenbox/server/runtime/process.py | +427 | ProcessRuntime 增加 _create_windows/_stop_windows/_exec_windows/_win_runner_roundtrip/_win_log_reader_blocking 等 |
| jiuwenbox/src/jiuwenbox/supervisor/win_acl.py | +253 | NTFS DACL 控制（合成 SID、grant_ace、apply/revoke_sandbox_acl）|
| jiuwenbox/src/jiuwenbox/supervisor/win_constants.py | +307 | 常量集中定义（Token/Job/WFP/ACL/SID/注册表 key）|
| jiuwenbox/src/jiuwenbox/supervisor/win_exec.py | +756 | 两跳启动（broker→runner→child）、受限 token、CreateProcessAsUserW、runner TCP 控制循环、exec/file-op 处理 |
| jiuwenbox/src/jiuwenbox/supervisor/win_job.py | +223 | Job Object 资源限制（内存/CPU/进程数/KILL_ON_JOB_CLOSE）|
| jiuwenbox/src/jiuwenbox/supervisor/win_proxy.py | +485 | asyncio HTTP CONNECT + SOCKS5 出站代理 + EgressFilter 域名/IP/端口过滤 |
| jiuwenbox/src/jiuwenbox/supervisor/win_setup.py | +552 | 一次性安装：jbx-sandbox 用户/组、密码 DPAPI、WFP filter、读 ACL 预装、UAC 提权、卸载 |
| jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py | +565 | WFP filter 安装/卸载（Block + Permit，ALE_USER_ID + loopback 条件，PowerShell 降级）|
| jiuwenbox/tests/integration/test_server_api_windows.py | +658 | Windows API 集成测试 |

## 三、架构与设计概述

本 commit 建立的 Windows 沙箱整体架构如下，各 `win_*.py` 模块职责清晰，与 `box-server`（ProcessRuntime）/`agent-server` 解耦：

```
agent-server 拉起 box-server (普通用户进程, liubuyu)
  │
  ├─ ProcessRuntime.__init__: 初始化 _win_runners/_win_job_handles/_win_acl_paths/_win_pipe_locks/_win_log_threads 等 per-sandbox 状态
  │
  ├─ app.py lifespan (win32 分支) → win_setup.ensure_windows_setup
  │     └─ 首次安装走 UAC 提权子进程 (ShellExecuteW runas + 命名 Event 同步)
  │           ├─ win_setup.install: NetUserAdd jbx-sandbox + NetLocalGroupAdd + LookupAccountName 存 SID
  │           ├─ win_wfp.install_wfp_filters: Block(全出站) + Permit(loopback:port_range) 基于 ALE_USER_ID(SD)
  │           ├─ win_acl.grant_ace: 对 ~/.office-claw 整树 + tool_paths 递归预装 Read/Write ACL (管理员)
  │           └─ DPAPI 加密密码存注册表
  │
  └─ ProcessRuntime.create → _create_windows (per-sandbox)
        ├─ win_setup.ensure_windows_setup (幂等检查 + 新增预装路径检测 + 密码一致性校验)
        ├─ win_acl.apply_sandbox_acl: 对 workspace/allow_write/deny_write/allow_read/deny_read
        │     施加合成 SID + jbx-sandbox 真实 SID 的 ACE (deny-then-allow, allow-only 写)
        ├─ win_exec.two_hop_spawn (CREATE_SUSPENDED):
        │     第一跳: CreateProcessWithLogonW(jbx-sandbox, LOGON_WITH_PROFILE, runner)
        │     第二跳 (runner 内): CreateRestrictedToken → 本版实际改用 runner 自身未受限 token
        │           CreateProcessAsUserW 起 child (受限 token 已弃用, 见 4.2)
        ├─ (Job Object 本版禁用, 仅 resume_process)
        ├─ win_job.resume_process(thread_handle) → CloseHandle(thread)
        └─ 起 _win_log_reader_blocking 后台线程 (TCP loopback 长连接收 runner 日志)

  exec 请求:
  ProcessRuntime.exec (win32) → _win_runner_roundtrip → _win_roundtrip_blocking
        └─ TCP connect 127.0.0.1:control_port → send_frame(header+body) → recv_frame(response)
              runner 端 (win_exec.runner_main): accept → _handle_exec_request
                → _create_process_as_user (受限/未受限 token 起 child)
                → 后台 _drain_thread 持续读 stdout pipe 防 deadlock
                → WaitForSingleObject(WAIT_TIMEOUT_MS=500) 轮询 + 超时强杀
                → 回传 {ok, exit_code, stdout}
```

数据流：agent-core 经 box-server HTTP API → ProcessRuntime.exec → TCP loopback → runner（jbx-sandbox 上下文）→ CreateProcessAsUserW child（受限/未受限 token）。WFP Block 拦截所有出站，仅放行 loopback:port_range，沙箱内遵守代理协议的程序经 win_proxy（HTTP CONNECT/SOCKS5）出网，win_proxy 按 EgressFilter 域名/IP/端口规则放行或拒绝。

## 四、关键代码检视

### 4.1 win_acl.py

合成 SID（`S-1-5-21-<sub0>-<sub1>-<RID>`，常量 `SYNTHETIC_WRITE_SID_SUBAUTHS=(0xBABE0013, 0x00002000)` + `RID=0x0000C0DE`）作为"允许写入"标记。`grant_ace` 读现有 DACL → `_rebuild_acl_with_order`（Deny 在前 Allow 在后）→ `SetNamedSecurityInfo`。

**发现：**

- 🟡 中 — `apply_sandbox_acl` 在末尾（win_acl.py:437-464）对 `~/.office-claw` 整树递归 grant `ALLOW_WRITE_RIGHTS + FILE_GENERIC_READ`（合成 SID + 真实 SID），且**不进 `applied` 清单**（注释称"幂等, 不进 revoke 清单避免跨沙箱误删"）。这意味着**这些 ACE 永远不会被 `revoke_sandbox_acl` 撤销**。`~/.office-claw` 含所有沙箱 workspace，递归授权让各沙箱能互相读 workspace 内容。注释承认"单用户本地部署, 跨沙箱读 workspace 可接受"，但这是**实质性的安全降级**：合成 SID 的"allow-only 写"隔离语义在 `~/.office-claw` 子树被完全绕过（任何沙箱进程持合成 SID 即可读写整个数据根下所有沙箱数据）。属于设计权衡，建议至少在文档与 policy 中显式标注此降级。

- 🟡 中 — `revoke_sandbox_acl`（win_acl.py:509）对每个 root 做 `root.rglob("*")` 递归收集后逐路径清理。大 workspace（含 node_modules/.venv 等数十万文件）下，**revoke 会扫整树逐文件 GetNamedSecurityInfo + SetNamedSecurityInfo**，耗时极长且同步阻塞 stop。apply 时 grant_ace 是递归一条 ACE（靠继承传播），但 revoke 是逐文件重建 ACL，复杂度不对等。

- 🟢 低 — `_parse_getace_tuple`（win_acl.py:77-114）对 3 元组旧版形态 `(access_mask, ace_flags, sid)` 的回退解析（line 104-108）把 `access_mask` 当 `ace_flags`、`first` 当 `access_mask`，依赖 pywin32 版本。若 pywin32 升级返回形态变化，此兼容层可能误判。已有注释说明，可接受。

### 4.2 win_exec.py（核心，756 行）

两跳启动 + 受限 token + runner TCP 控制循环。

**发现：**

- 🔴 高 — **受限 token 实际未用于起 child，安全降一重**。`_create_restricted_token`（win_exec.py:650）创建 Write-Restricted Token（restricting=[Everyone, LogonSession, JHXSandboxWrite]），但 `_handle_exec_request`（win_exec.py:1303）实际调用 `_get_runner_primary_token()` 拿 **runner 自身未受限 token** 起_child，注释（win_exec.py:760-769）明说"受限 token 让 child 启动即 0xC0000142（DllMain 失败），改用 runner 自身未受限 token"。这意味着：
  - `CreateRestrictedToken` 的 `WRITE_RESTRICTED` 双重写检查**完全失效**。
  - 写控制只剩合成 SID 的 ACL（allow-only 仍挡越权写），但 child 进程以 **jbx-sandbox 真实身份 + 未受限 token** 跑，**所有给 jbx-sandbox 真实 SID grant 的 ACE（apply_sandbox_acl 里大量施加，含 ~/.office-claw 整树 Write+Read）对 child 全部生效**。
  - 等价于：沙箱内执行的**用户代码以 jbx-sandbox 完整权限运行**，仅受 NTFS ACL 约束，无受限 token 的二次写检查、无 SeAssignPrimaryToken 权限墙隔离。
  - 这是本 commit 最重要的安全降级。文档 §6.5 设计要求两跳 + 受限 token，实际第二跳受限 token 被弃用。应优先定位 0xC0000142 根因（可能是受限 token 缺 desktop 关联或 DACL 默认条目），恢复受限 token 路径。

- 🔴 高 — `win_constants.py:75`：`RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT`，注释明写"临时去掉 WRITE_RESTRICTED(0x8) 定位 0xC0000142"。即 `CreateRestrictedToken` 的 `WRITE_RESTRICTED` 标志**被临时移除**，即便受限 token 被创建，也非 Write-Restricted 语义（不做写操作双重 ACL 检查）。与上一条共同构成：受限 token 创建了但既未用于起 child、其标志也不含 WRITE_RESTRICTED。属于调试态遗留，**生产前必须恢复**。

- 🔴 高 — **`_create_process_as_user` 的 `restricted_token` 参数名不副实**。`_handle_exec_request` 传入 `_self_token`（runner 自身未受限 primary token，win_exec.py:1303-1312），但 `_create_process_as_user` 形参仍命名 `restricted_token`（win_exec.py:839），并在 CreateProcessAsUserW 调用（win_exec.py:991-1001）用之。参数名误导，且 `finally` 块 `CloseHandle(_self_token)`（win_exec.py:1312）正确关闭，但若未来误传受限 token，`CreateProcessAsUserW(restricted_token, ...)` 需 token 有 `TOKEN_ASSIGN_PRIMARY`，受限 token 默认无此权限会失败。建议重命名形参为 `primary_token` 并加断言。

- 🟡 中 — **`_handle_exec_request` 的 stdout pipe drain + 进程等待逻辑**（win_exec.py:1331-1431）。`_drain_thread` 后台读 stdout pipe 防死锁，设计正确。但：
  - 超时强杀 child 后（win_exec.py:1401 `TerminateProcess`），`_drain_thread.join(timeout=5.0)`（win_exec.py:1408）。若孙进程仍持 pipe 写端，5s 后 drain 仍 alive，代码 `os.close(read_fd)`（win_exec.py:1415）强制关 fd。但**此时 drain 线程可能正阻塞在 `os.read`**，强制 close 其正在使用的 fd 在 Windows 上行为未定义（可能触发 `OSError` 被线程内 except 吞掉）。可接受但脆弱。
  - `GetExitCodeProcess`（win_exec.py:1428）在 `TerminateProcess(handle, 1)` 之后调用，返回 1（强杀退出码）。但**未区分"正常退出"与"被强杀"的退出码语义**，调用方拿到 exit_code=1 无法区分。`_child_killed` 标志仅记录在日志，未回传给 box-server。

- 🟡 中 — **`_create_restricted_token` 的 SID buffer 生命周期**（win_exec.py:676-735）。`everyone_buf`、`logon_buf` 是函数内局部 ctypes 数组，`AllocateAndInitializeSid` 分配的 `write_sid_ptr` 是堆分配。三者通过 `entries` 列表的 `_SID_AND_ATTRIBUTES` 结构引用。`restricting = (_SID_AND_ATTRIBUTES * len(entries))(*entries)`（win_exec.py:737）拷贝构造数组，**但 `_SID_AND_ATTRIBUTES.Sid` 字段是 `c_void_p`（裸指针）**，拷贝的是指针值而非对象引用。若 Python GC 在 `CreateRestrictedToken` 调用前回收 `everyone_buf`/`logon_buf`，指针悬垂。代码在函数体内持有这些 buffer 的局部变量引用直到 `return`，故实际不悬垂（CPython 引用计数保证局部变量存活）。但 `write_sid_ptr` 由 `AllocateAndInitializeSid` 分配，**函数返回后未 `FreeSid`**，内存泄漏（每次 `_create_restricted_token` 调用泄漏一个 SID）。runner 生命周期内仅调一次，影响小，但应 `FreeSid`。

- 🟡 中 — **`_get_logon_session_sid`（win_exec.py:559-603）与 `_create_restricted_token` 内的 logon session SID 提取逻辑重复**，且前者返回 `g.Sid`（c_void_p），后者重新提取。`_get_logon_session_sid` 函数定义但**在 runner_main 流程中未被调用**（仅 `_create_restricted_token` 内联提取）。死代码或冗余。

- 🟡 中 — **`_local_log` 文件句柄永不关闭**（win_exec.py:121 `open(path, "a", ...)` 赋值 `_local_log_file`，runner 整个生命周期持有，runner 退出时 finally 块未 close）。daemon 进程退出 OS 会回收，但显式 close 更佳。

- 🟢 低 — `_build_runner_command`（win_exec.py:417）`py = os.environ.get("JIUWENBOX_RUNNER_PYTHON") or sys.executable`。若 env 未注入且 sys.executable 是 uv venv trampoline，runner 启动会崩。注释已说明，依赖调用方注入，可接受但有隐式契约。

- 🟢 低 — `_normalize_bash_script_backslashes`（win_exec.py:1211）对 `bash -lc script` 双引号段内反斜杠规整为正斜杠。正则 `_DQ_SEGMENT_RE = r'"[^"]*"'` 不处理转义双引号（`\"`），但 bash script 内罕见，可接受。

### 4.3 win_wfp.py

WFP filter 安装：Block（ALE_USER_ID == sandbox SID，全出站）+ Permit（ALE_USER_ID + loopback，放行代理端口）。

**发现：**

- 🔴 高 — **Permit filter 实际放行所有 loopback 端口，未限制 port_range**。`install_wfp_filters`（win_wfp.py:756-791）注释明写"临时方案: Permit 条件 = user + loopback (去掉 port 限制), 放行 jbx-sandbox 访问 127.0.0.1 任意端口"。原因注释解释：pptx-craft render server 用 `getPort()` 随机选端口，固定范围 Permit 无法覆盖。这意味着：
  - **沙箱用户可访问 127.0.0.1 上任意端口**，包括 box-server 自身的 HTTP API 端口（如 8321）、其他本地服务。WFP 的"出网唯一出口是 win_proxy"语义**被破坏**：沙箱内代码可直接 `connect 127.0.0.1:<任意端口>` 绕过 win_proxy 的域名/IP 白名单过滤。
  - Linux 侧有 `_install_sandbox_host_firewall_rules`（iptables OUTPUT uid block）保护 box-server 端口，Windows 侧 **无等价保护**。
  - 属于临时降级，生产前必须恢复 port_range 限制（改 render server 用固定端口，或沙箱侧动态 Permit）。

- 🟡 中 — **`_build_ale_user_condition`（win_wfp.py:527-611）构造 SD 的逻辑复杂且依赖 pywin32 `SetEntriesInAcl` + `SECURITY_DESCRIPTOR`**。`bytes(sd)`（win_wfp.py:587）取 self-relative SD 字节，依赖 pywin32 内部实现（注释称"PySECURITY_DESCRIPTOR 对象内部始终以 self-relative 格式存储"）。若 pywin32 版本变更内部表示，BFE marshal 校验可能失败（注释记录了 S9 实跑 0x6F7 RPC_X_BAD_STUB_DATA 的排查历程）。当前可工作但脆弱。

- 🟡 中 — **`_KeepAlive`（win_wfp.py:614）持有 ctypes 对象引用防 GC**，但 `_build_loopback_v4_condition`/`_build_ale_user_condition` 返回的 keep-alive 对象**只在 `_add_filter` 调用期间存活**（`install_wfp_filters` 的 `keeps` 列表持有到所有 FwpmFilterAdd0 完成才允许 GC，win_wfp.py:732）。逻辑正确，但 `_add_filter` 内 `FwpmFilterAdd0` 返回后，BFE 是否已复制 SD 字节？若 BFE 是引用而非复制，filter 安装后 keep-alive 释放会导致 BFE 持悬垂指针。WFP 文档表明 BFE 在 `FwpmFilterAdd0` 时复制 filter 数据，故安全，但代码无断言。

- 🟢 低 — `FWPM_FILTER0` 结构体布局（win_wfp.py:349-366）注释详尽，对齐 windows-sys SDK。`_FWPM_FILTER0_UNION` 16B（UINT64 8B 与 GUID 16B 取 max）正确。

### 4.4 win_setup.py

一次性安装：用户/组、密码、WFP、ACL 预装、UAC 提权、卸载。

**发现：**

- 🔴 高 — **`_generate_password`（win_setup.py:320-327）返回固定密码 `"000000"`**。注释称"调试阶段固定, 方便排查"。这是**硬编码弱密码**：
  - jbx-sandbox 用户密码为 "000000"，任何知道此代码的人可登录该账户。
  - 该用户虽从登录界面隐藏（`SpecialAccounts\UserList=0`），但 `LogonUserW`/`CreateProcessWithLogonW` 不受隐藏影响，攻击者可 `runas /user:jbx-sandbox` 用 "000000" 登录获取本地执行权限。
  - 注释承诺"后期改为从配置文件读取"，但基线 commit 未实现。生产前必须改为随机密码（`secrets.token_urlsafe`，且 `SANDBOX_USER_PASSWORD_LENGTH=64` 常量已定义但未使用）。

- 🔴 高 — **密码用 DPAPI 加密存注册表，但 DPAPI 是机器范围加密**（win_setup.py:916 `CryptProtectData(..., None, None, None, 0)`，第 6 参数 `dwFlags=0` 即无 `CRYPTPROTECT_LOCAL_MACHINE`）。`CryptProtectData` 默认是**用户级**加密（绑定到调用方用户）。install 在提权子进程（管理员）中跑，加密的密码只能由**同一管理员用户**解密。但 box-server 以 `liubuyu`（普通用户）跑 `_create_windows` 调 `get_sandbox_user_password`（win_setup.py:1219-1235）解密——**若 install 提权子进程的管理员身份 ≠ liubuyu，DPAPI 解密会失败**，拿不到密码，`two_hop_spawn` 报"无法读取密码"。注释未澄清提权子进程的 DPAPI 上下文与 box-server 的关系。若 liubuyu 本身是管理员且 install 不提权（`_is_admin()` True），则同一用户加解密 OK；但 UAC 提权路径下行为存疑。

- 🟡 中 — **`_elevate_and_run_install`（win_setup.py:621-756）用 ShellExecuteW "runas" + 命名 Event 同步**。`CreateEventW(None, False, False, event_name)`（win_setup.py:662）创建 `Global\JiuwenBox-Install-Done-<random>` 事件。**`Global` 命名空间默认 ACL 允许同会话/管理员子进程打开**（注释说明），但**任意同会话进程均可 `OpenEventW` 该事件名**（名字虽含 `secrets.token_hex(8)` 随机后缀，但同会话进程可枚举/猜测）。若恶意进程提前 `SetEvent`，主进程会误以为 install 完成而继续，此时 install 可能还在跑或失败。低概率但属于同步原语的安全弱点。

- 🟡 中 — **`_verify_or_reset_sandbox_user_password`（win_setup.py:1146-1210）在 `ensure_windows_setup` 幂等路径中调用，但运行时进程（普通用户）可能无权重设密码**。`LogonUserW` 校验密码失败后 `_set_user_password` 调 `NetUserSetInfo`（需 admin）。注释（win_setup.py:1206）承认"运行时非管理员可能无权"。失败仅 `logger.error`，不 raise，后续 `two_hop_spawn` 会 WinError 1326。错误处理链不完整。

- 🟡 中 — **`_purge_stale_profile_dirs`（win_setup.py:521-582）按 `jbx-sandbox` 前缀 `shutil.rmtree` 清理 `C:\Users` 下残留 profile**。前缀匹配 `name == SANDBOX_USER_NAME or name.startswith(SANDBOX_USER_NAME + ".")` 较安全，但**若用户故意创建 `jbx-sandbox.evil` 目录**，会被误删。低概率但需注意。

- 🟢 低 — `_is_admin`（win_setup.py:484）用 `ctypes.windll.shell32.IsUserAnAdmin()`，此 API 在新 Windows 版本被 deprecated，建议改用 `CheckTokenMembership` + `DOMAIN_ALIAS_RID_ADMINS`。

### 4.5 win_job.py

Job Object 资源限制。本版 **Job Object 在 `_create_windows` 中被禁用**（process.py:3065-3111 大段注释），仅 `resume_process` 被调用。

**发现：**

- 🟡 中 — **Job Object 被禁用，资源限制（memory_max/cpu_rate/max_processes）失效**。`windows-policy.yaml` 配置了 `memory_max: 512M, cpu_rate: 50, max_processes: 32`，但 `_create_windows` 注释（process.py:3068-3075）说明"assign 跨用户 OpenProcess(jbx-sandbox 进程) 拿不到 PROCESS_SET_QUOTA → WinError 5"。原因是 box-server（liubuyu）无法对 jbx-sandbox 进程做 `AssignProcessToJobObject`（跨用户权限不足）。注释建议"改回用 two_hop_spawn 返回的 proc_handle 直接 assign"。**当前实现：沙箱完全无内存/CPU/进程数限制**，恶意用户代码可耗尽宿主机资源。应优先修复跨用户 Job assign（用 CreateProcessAsUserW 时 inherit Job，或在 runner 内部 self-assign）。

- 🟢 低 — `create_job`（win_job.py:131）设 `ext.BasicLimitInformation.LimitFlags` 含 `KILL_ON_JOB_CLOSE`。`SetInformationJobObject` 后若 CPU 限制设置失败仅 warning（win_job.py:190），Job 仍创建但无 CPU cap。可接受。

### 4.6 win_proxy.py

asyncio HTTP CONNECT + SOCKS5 代理 + EgressFilter。

**发现：**

- 🟡 中 — **`EgressFilter.allow`（win_proxy.py:104-190）的域名解析在代理侧进行**（win_proxy.py:139-148 `socket.getaddrinfo`）。这意味着：
  - 沙箱内程序通过 HTTP CONNECT 发 `CONNECT evil.com:443`，win_proxy 解析 `evil.com` 得 IP，比对 IP 规则。
  - **DNS 查询由 win_proxy（box-server 上下文）发起**，非沙箱内发起。沙箱内 DNS 查询（如 `getaddrinfo` 在沙箱内调用）会走 WFP Block（沙箱用户的 UDP/TCP 53 出站被拦）。沙箱内程序只能用 win_proxy 的 DNS 或硬编码 IP。这改变了沙箱的网络语义，需文档化。

- 🟡 中 — **`_handle_client`（win_proxy.py:400-431）按首字节分派**：`b"C"` → HTTP CONNECT，`b"\x05"` → SOCKS5，其他 → 400。但 **HTTP CONNECT 请求行第一字节应是 `C`（CONNECT 的 C），但 HTTP/1.1 请求也可能是 `G`(GET)/`P`(POST) 等**。若沙箱内程序发普通 HTTP 请求（非 CONNECT）到代理端口，会被当未知协议拒绝。这是设计选择（代理仅做隧道，不转发明文 HTTP），但需确保沙箱内程序用 CONNECT（HTTPS）或 SOCKS5，普通 HTTP 流量无出口。当前 policy `egress.default: allow` 放行所有域名，但**代理协议层不支持明文 HTTP 转发**。

- 🟢 低 — `_pipe_streams`（win_proxy.py:200-218）`reader.read(_TUNNEL_BUF=65536)` + `writer.drain()`，单隧道缓冲 64KB。恶意对端发海量数据时 `_pipe_streams` 持续读，但 `_TUNNEL_BUF` 是单次 read 上限非总量上限，内存可被撑大。可接受（单隧道）。

### 4.7 win_constants.py

常量集中定义。

**发现：**

- 🟡 中 — **`RESTRICTED_TOKEN_FLAGS`（win_constants.py:75）临时去掉 `WRITE_RESTRICTED`**（见 4.2）。`TOKEN_SANDBOX_INERT = 29`（win_constants.py:56）定义但 `RESTRICTED_TOKEN_FLAGS` 用的是 `SANDBOX_INERT = 0x2`（win_constants.py:70），二者命名易混淆（前者是 TOKEN_INFORMATION_CLASS 枚举值，后者是 CreateRestrictedToken 标志位）。建议重命名以区分。

- 🟢 低 — `LOOPBACK_IPV4_INT = 0x7F000001`（win_constants.py:346）host byte order 正确（127.0.0.1）。注释记录了旧版网络序 bug 的修复。

### 4.8 process.py（Windows 分支）

`_create_windows`/`_stop_windows`/`_exec_windows`/`_win_runner_roundtrip`/`_win_log_reader_blocking`。

**发现：**

- 🔴 高 — **`win_proxy` 未在 `_create_windows` 中启动**。grep 显示 process.py 中无 `serve_windows_proxy`/`win_proxy` 调用。`_create_windows` 仅施加 ACL + two_hop_spawn + resume + 日志线程，**未启动 win_proxy 代理进程/任务**。WFP Block 拦截沙箱所有出站，Permit 放行 loopback:port_range，但**port_range 上无 win_proxy 监听**（除非在别处启动，但 process.py 内未见）。沙箱内程序配置了 `HTTP_PROXY=http://127.0.0.1:<port>`（win_exec.py:923-928），但该端口无人 listen → 所有走代理的出网连接 ECONNREFUSED → 沙箱无法出网。`windows-policy.yaml` 的 `network.egress` 规则形同虚设（win_proxy 未跑）。需补全 win_proxy 的启动/停止接线（应在 `_create_windows` 起 asyncio task，`_stop_windows` set stop_event）。

- 🟡 中 — **`_win_runner_roundtrip`（process.py:3409-3440）用 `asyncio.Lock` 串行化 per-sandbox roundtrip**。同一 sandbox 的 exec/file-op 请求串行，无并发。Linux 侧用 `_exec_semaphore`（CPU 并发控制），Windows 侧 `_ensure_win_exec_semaphore`（process.py:3618-3619）在 `exec` 中获取。但 `_win_runner_roundtrip` 内部 `run_in_executor` 把 blocking socket 调用丢到默认线程池，**若默认线程池容量有限（默认 min(32, cpu+4)），高并发 exec 会耗尽线程池**。且 `asyncio.Lock` 串行化使得即便线程池有空闲，同 sandbox 也只能一个 roundtrip 在跑。设计如此（runner 单连接），可接受但吞吐受限。

- 🟡 中 — **`_win_log_reader_blocking`（process.py:3503-3587）重试 50 次 × 0.1s = 5s 窗口**等待 runner bind。若 runner 5s 内未 ready（如 `_create_restricted_token` 失败重试或慢），日志长连握手失败，后续 runner 早期异常无法回传。`_create_restricted_token` 失败会 `return 1` 退出（win_exec.py:1085），runner 进程退出，但日志线程仍可能在 5s 窗口内 connect 成功后立即断开（runner 已退出）。边界处理 OK 但窗口偏短。

- 🟡 中 — **`_stop_windows`（process.py:3144-3188）顺序**：发 shutdown → join 日志线程(2s) → `_stop_runner`(TerminateProcess + CloseHandle) → 关 Job → 撤销 ACL。**`_stop_runner` 内 `CloseHandle(process_handle)`（win_exec.py:553）后，若 Job 已 assign，`win_job.teardown(job)` 会 `CloseHandle(job_handle)` 触发 KILL_ON_JOB_CLOSE 强杀残留 child**。但本版 Job 禁用，`_win_job_handles` 为空，故无 Job 清理。runner 的 child（exec 起的 bash/node）**未在 stop 中显式清理**——依赖 runner 进程退出时内核回收其子进程。若 runner 在 child 执行中崩，child 可能孤儿化。无 Job 保护下风险更高。

- 🟢 低 — `_alloc_loopback_port`（process.py:160-174）bind `127.0.0.1:0` 拿端口后立即 close，runner resume 后 bind 同端口。`SO_REUSEADDR` 由 runner 端设置（win_exec.py:1094），但 box-server close 到 runner bind 之间有 TIME_WAIT 窗口，低概率冲突。注释已说明。

### 4.9 windows-policy.yaml

**发现：**

- 🟡 中 — **`network.egress.default: allow`（yaml:154）注释明写"适配期先放开"**。与 WFP Permit 全 loopback 放行（见 4.3）共同构成：沙箱出网几乎无限制。win_proxy 的 EgressFilter 即便启动，default:allow 也会放行所有未命中 deny 的域名。生产前需收紧为 `default: deny` + 完整白名单。

- 🟡 中 — **`resource`（yaml:191-195）配置了 memory_max/cpu_rate/max_processes，但 Job 禁用（见 4.5）导致全部失效**。配置与实现不一致，用户可能误以为资源受限。

## 五、优点

1. **架构分层清晰**：`win_acl`/`win_exec`/`win_wfp`/`win_job`/`win_setup`/`win_proxy` 职责单一，通过 `win_constants` 集中常量，模块间无循环依赖。
2. **跨平台兼容设计**：所有 win32 调用延迟到函数体内（`_require_windows` 守卫 + 惰性 `import`/`ctypes.WinDLL`），Linux 下可 import 不崩，允许统一代码树。
3. **详尽的实跑修复注释**：代码内嵌大量 "S9/S12 实跑"、"review CRITICAL/MAJOR #" 注释，记录了 ctypes 布局、指针生命周期、ACE 顺序、GUID 字节序等 Win32 陷阱的排查与修复，对后续维护极有价值。
4. **降级路径完备**：WFP 主路径失败有 PowerShell `New-NetFirewallRule` 降级；install 失败有 `uninstall` 回滚；runner 早期异常有本地落盘日志 + 长连回传双通道。
5. **资源清理路径清晰**：`_stop_windows` 按序撤销 ACL/关 Job/关 handle；`revoke_sandbox_acl` 按 apply 时返回的清单逐路径清理（修复了旧版只扫 workspace 漏系统路径 ACE 的 bug）。
6. **两跳启动的 SUSPEND→Assign→Resume 设计**（虽 Job 当前禁用，但 resume 流程保留）正确避免了 Job 逃逸窗口。
7. **stdout pipe drain 线程**正确解决了 child 写满 pipe 导致 runner wait 死锁的经典问题。
8. **DPAPI 加密密码**（虽存在上下文问题，见 4.4）优于明文存储。
9. **集成测试覆盖**（test_server_api_windows.py 658 行）。

## 六、问题与风险（按严重程度排序）

### 🔴 高（生产前必须修复）
1. **受限 token 被弃用**（win_exec.py:1303）：child 用 runner 未受限 primary token 起进程，`CreateRestrictedToken` 的 Write-Restricted 双重写检查完全失效，沙箱内用户代码以 jbx-sandbox 完整权限运行，仅靠 NTFS ACL 约束。安全降一重。（4.2）
2. **`RESTRICTED_TOKEN_FLAGS` 临时去掉 `WRITE_RESTRICTED`**（win_constants.py:75）：即便受限 token 创建，也非 Write-Restricted 语义。调试态遗留。（4.2）
3. **WFP Permit 放行所有 loopback 端口**（win_wfp.py:756）：沙箱可访问 127.0.0.1 任意端口，绕过 win_proxy，且无 box-server 端口保护（Windows 侧无 iptables uid block 等价物）。（4.3）
4. **win_proxy 未在 `_create_windows` 中启动**（process.py）：WFP Permit 放行的端口范围上无代理监听，沙箱内走代理的出网全 ECONNREFUSED，egress 规则形同虚设。（4.8）
5. **jbx-sandbox 密码硬编码 "000000"**（win_setup.py:327）：弱密码，任何知晓代码者可登录沙箱用户。`SANDBOX_USER_PASSWORD_LENGTH=64` 常量定义但未用。（4.4）
6. **DPAPI 密码加解密上下文不一致风险**（win_setup.py:916）：提权子进程加密 vs 普通用户进程解密，若用户身份不同则解密失败。（4.4）
7. **Job Object 禁用，资源限制全失效**（process.py:3065）：memory_max/cpu_rate/max_processes 配置不生效，沙箱无资源约束。（4.5/4.9）

### 🟡 中（应修复）
8. `~/.office-claw` 整树递归 grant Write+Read 且不进 revoke 清单，跨沙箱数据隔离被绕过（4.1）。
9. `revoke_sandbox_acl` 逐文件重建 ACL，大 workspace 下 stop 耗时极长（4.1）。
10. exec 超时强杀后退出码语义未区分，`_child_killed` 未回传 box-server（4.2）。
11. `_create_restricted_token` 的 `write_sid_ptr` 未 `FreeSid`，内存泄漏（4.2）。
12. `_elevate_and_run_install` 命名 Event 可被同会话进程抢先 SetEvent（4.4）。
13. `_verify_or_reset_sandbox_user_password` 在非管理员进程调 `NetUserSetInfo` 会失败，错误链不完整（4.4）。
14. `egress.default: allow` 适配期放开，出网几乎无限制（4.9）。
15. EgressFilter 的 DNS 在代理侧解析，沙箱内 DNS 走 WFP Block，网络语义改变需文档化（4.6）。
16. win_proxy 不支持明文 HTTP 转发，仅 CONNECT/SOCKS5（4.6）。
17. `_win_runner_roundtrip` per-sandbox asyncio.Lock 串行化，吞吐受限（4.8）。
18. `_win_log_reader_blocking` 握手重试窗口 5s 偏短（4.8）。
19. `_stop_windows` 无 Job 保护下 runner child 可能孤儿化（4.8）。

### 🟢 低
20. `_parse_getace_tuple` pywin32 版本兼容依赖（4.1）。
21. `_get_logon_session_sid` 死代码/冗余（4.2）。
22. `_local_log_file` 未显式 close（4.2）。
23. `_build_runner_command` 依赖 env 注入 runner python 路径（4.2）。
24. `IsUserAnAdmin` deprecated API（4.4）。
25. `TOKEN_SANDBOX_INERT` 与 `SANDBOX_INERT` 命名易混淆（4.7）。
26. `exec_background_windows` 降级为同步 exec（process.py:3264）。

## 七、改进建议

1. **优先恢复受限 token 路径**：定位 0xC0000142 根因（检查受限 token 的 desktop 关联、默认 DACL、KnownDlls 访问），恢复 `WRITE_RESTRICTED` 标志与受限 token 用于起 child。这是恢复文档 §6.5 安全模型的关键。
2. **恢复 WFP port_range 限制**：Permit filter 加 `IP_REMOTE_PORT in [start, end]` 条件（`_build_port_eq_condition` 已实现）。改 pptx-craft render server 用固定端口，或沙箱侧动态查询并 Permit。
3. **接线 win_proxy**：在 `_create_windows` 起 `serve_windows_proxy` asyncio task（或独立线程），`_stop_windows` set stop_event。确保 WFP Permit 的端口范围有代理监听。
4. **随机化密码**：`_generate_password` 改用 `secrets.token_urlsafe(SANDBOX_USER_PASSWORD_LENGTH)`，存注册表后 install 与运行时同用户加解密（或改用 LSA Secret 存储）。
5. **恢复 Job Object**：用 `two_hop_spawn` 返回的 `proc_handle` 直接 `AssignProcessToJobObject`（绕过跨用户 OpenProcess），或让 runner 在自身上下文 self-assign Job 后再起 child。
6. **收紧 egress**：`default: deny` + 完整白名单；`~/.office-claw` 整树授权改为按 sandbox 子目录精确授权。
7. **优化 revoke**：`revoke_sandbox_acl` 改为按 apply 时施加的 ACE 类型批量删除（用 `DeleteAce` 而非重建整 ACL），或限制递归深度。
8. **回传 child_killed 标志**：exec 响应增加 `killed: bool` 字段，box-server 据此区分超时强杀。
9. **显式 close `_local_log_file`**：runner finally 块加 `_local_log_file.close()`。
10. **`_create_process_as_user` 形参重命名** `restricted_token` → `primary_token`。

## 八、小结

本 commit 作为 Windows 沙箱基线，架构设计与代码质量整体优秀：分层清晰、跨平台兼容、实跑修复注释详尽、降级与回滚路径完备。但存在**多处调试态遗留导致的安全降级**，集中表现为：受限 token 创建但未用于起 child（`RESTRICTED_TOKEN_FLAGS` 去 WRITE_RESTRICTED）、WFP Permit 全 loopback 放行、win_proxy 未接线、密码硬编码 "000000"、Job 禁用。这些降级在适配期可理解，但**生产前必须修复 #1-#7 的高风险项**，否则沙箱的文件/网络/资源隔离语义均被打折。建议优先级：受限 token 恢复 > WFP port_range + win_proxy 接线 > 密码随机化 > Job 恢复。中等风险项（#8-#19）可在后续迭代中逐步收敛。
