# 代码审查报告：fix:沙箱启动报错

- Commit: `fb587eac8e31c0e5154ac2cd0ba6569da812f27d`
- 作者: lby / 2026-07-25
- 范围: 4 文件，约 229 增 177 删
  - `jiuwenbox/src/jiuwenbox/server/runtime/process.py`（重写约 151 行）
  - `jiuwenbox/src/jiuwenbox/supervisor/daemon_ipc.py`（调整约 33 行）
  - `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py`（重写约 150 行）
  - `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py`（+72）

> 审查方法：用 `git show fb587eac^` 对照父提交，并读取 **本 commit 当时的版本**（非当前 HEAD，后续 commit 已对本 commit 的问题做了修复，本报告只对本 commit 负责）。所有 `file:line` 引用基于本 commit 检出版本。

---

## 概述

本 commit 的核心意图是修复 Windows 沙箱 runner 启动报错。做法是把 box-server 与 runner 之间的控制通道从「继承的 stdin/stdout 匿名 pipe」改为「TCP loopback 端口」：box-server 在 spawn 前分配一个空闲 TCP 端口，经命令行参数传给 runner，runner `bind+listen` 做 server，box-server 每次 exec/file-op `connect` 一条新连接发一帧请求读一帧响应即 close，对齐 Linux 侧 AF_UNIX listener 模型。同时 win_setup 侧新增「用户已存在则重设密码」与 uninstall 时删除用户/组，解决 reinstall 时用户密码与注册表 DPAPI 密码不一致导致 `CreateProcessWithLogonW` 报 WinError 1326（登录失败）。

整体方向正确：用 TCP loopback 绕开「Windows 跨进程无法传 fd、匿名 pipe 经 `CreateProcessWithLogonW` + `os.fdopen` 继承链路脆弱」的根因是合理的工程取舍。但本 commit 在落地时存在两处会导致沙箱「能起但跑不动 exec」的真实缺陷（见下文 🔴），以及若干安全/健壮性问题，需要后续 commit 补齐（事实上后续 `2d19941c`/`82001d09` 等已修）。

---

## 变更范围

| 文件 | 改动性质 |
|------|----------|
| `win_exec.py` | 删除 broker 侧 `CreatePipe`/`SetHandleInformation`/`STARTF_USESTDHANDLES` 全套 pipe 句柄逻辑；`two_hop_spawn` 返回值由 5 元组 `(pid, stdin_w, stdout_r, proc, thread)` 收窄为 3 元组 `(pid, proc, thread)`；新增 `_build_env_block`（但本 commit 未在 `two_hop_spawn` 内调用）；`_stop_runner` 改为纯 `TerminateProcess`；`runner_main` 改为 `socket.bind/listen/accept` 循环，每个请求一连接。 |
| `process.py` | 新增 `_alloc_loopback_port`；`_create_windows` 分配 control_port 并存入 `_win_runners` dict；删除 `_osfhandle_to_fd` 的运行期调用与 stdin/stdout 文件对象生命周期管理；`_close_win_pipe_handles` 变空占位；`_win_roundtrip_blocking` 由 pipe 读写改为每次新建 TCP 连接；`_send_runner_shutdown_blocking` 改为 connect control_port 发 shutdown 帧。 |
| `daemon_ipc.py` | `recv_exact`/`send_frame` 收紧类型签名：去掉 `getattr(sock,"recv")/sock.read` 与 `sendall/write+flush` 的双分支，只接受 `socket.socket`（`.recv`/`.sendall`）。 |
| `win_setup.py` | `_get_netapi32` 补 `NetUserSetInfo`/`NetUserDel`/`NetLocalGroupDel` argtypes；`_generate_password` 改为固定 `"000000"`；`_create_sandbox_user` 用户已存在(2224)时调 `_set_user_password` 重设；新增 `_set_user_password`；uninstall 改为删用户+删组（best-effort）。 |

---

## 根因与修复分析

### 启动报错的根因（本 commit 想修的）

沙箱启动报错在本 commit 上下文至少有三条独立根因，本 commit 覆盖了前两条、第三条是本 commit 自己引入的新隐患：

🟢 **根因 A：跨进程匿名 pipe 经 `CreateProcessWithLogonW` 继承链路脆弱（修对了）**
旧版 broker 侧用 `CreatePipe` 建一对 pipe，经 `STARTF_USESTDHANDLES` 让 runner 继承 stdin 读端/stdout 写端，box-server 端再 `os.fdopen` 包成持久化文件对象。问题在于：`CreateProcessWithLogonW` 的 env 块经 ctypes `c_void_p` 传参易触发 WinError 87，且 `open_osfhandle` 产生的 fd 与底层 HANDLE 的生命周期分裂（文件对象 close 不关底层 fd），「每个 sandbox 只能 exec 一次」的复用语义很难写对。本 commit 改 TCP loopback 后，box-server 不再持有任何 runner 的 pipe 句柄，runner 自己 `bind/listen`，`win_exec.py:580-630`（commit 版 `runner_main`）每次 `accept` 一条短连接，彻底消除了 pipe 生命周期问题。这是对根因的正本清源，覆盖到位。

🟢 **根因 B：reinstall 时用户密码与注册表不一致致 `CreateProcessWithLogonW` 1326（修对了）**
`win_setup.py:320-327`（commit 版 `_generate_password`）注释明确指出：uninstall 不删用户 + `create_sandbox_user` 已存在不重设密码 → 用户真实密码与注册表存的密码不一致 → `CreateProcessWithLogonW` 报 1326 登录失败。本 commit 双管齐下：`_create_sandbox_user` 在 2224（用户已存在）时调 `_set_user_password` 重设为本次密码（`win_setup.py:355-367` commit 版）；uninstall 改为 `NetUserDel`+`NetLocalGroupDel`（`win_setup.py:743-760` commit 版），reinstall 时干净重建。逻辑正确，幂等性处理好（`NetUserDel` 返回 2201 NERR_UserNotFound 也视为成功）。

🔴 **根因 C：本 commit 自己引入「runner env 丢失 PATH」回归（未覆盖，是本 commit 的新 bug）**
`process.py` 的 `_create_windows` 在本 commit 仍把拼好工具目录的 `env`（含 `PATH` 前缀、`SystemRoot`、`TEMP`/`USERPROFILE` 等）经 `env=env` 传给 `two_hop_spawn`（commit 版 `process.py:2873`）。**但本 commit 版的 `two_hop_spawn` 接收了 `env` 参数却完全没用它**：在 `CreateProcessWithLogonW` 调用处传的是 `None, None`（commit 版 `win_exec.py:331-333`，注释 `# env=None 用调用方环境`）。后果：runner 进程拿到的是 box-server 的「调用方环境」而非调用方拼好的带工具目录的 PATH，runner 再起 child（`_create_process_as_user`）时 `env=None` 回退 `os.environ`，PATH 里没有 Git/Node/Python 工具目录 → 可执行名解析失败 → WinError 2。本 commit 还在 `win_exec.py:234` 定义了 `_build_env_block` 但 `two_hop_spawn` 内并未调用它（死代码），说明 env 块传递是「写了一半」的状态。这是本 commit 引入的真实回归，后续 commit 才补上 `env_block_buf = _build_env_block(env)` + `CREATE_UNICODE_ENVIRONMENT` flag。

🔴 **根因 D：本 commit 引入「exec 读响应 2s 超时」回归（未覆盖，是本 commit 的新 bug）**
`_win_roundtrip_blocking` 在 commit 版 `process.py:3160` 设 `sock.settimeout(DAEMON_CONNECT_TIMEOUT_SECONDS)`（=2.0s）后，**整个 roundtrip（connect + send + recv_frame）全程都用这 2s**，从未在读响应阶段延长超时。runner 起一个 `bash -lc 'npx ...'` 随便就跑几十秒，box-server 端 `recv_frame` 必然在 2s 后抛 `socket.timeout` → box-server 关连接 → runner 端 `_send_response` 抛 `ConnectionAbortedError` → 旧版 runner 直接退出。这把「沙箱启动报错」变成了「沙箱能起、但第一次 exec >2s 就全链路断」。后续 commit 把 exec 读响应改成 130s 才修掉。

---

## 关键代码检视

### 1. 命令行构造（`win_exec.py:_build_runner_command`，commit 版约 247-275）

🟢 命令行构造 `python -m jiuwenbox.supervisor.win_exec runner --sandbox-id ... --control-port ...`，对含空格/Tab 的参数加双引号，逻辑正确。`control_port` 经命令行而非 env 传，注释说明是为避开 `CreateProcessWithLogonW` 传 env 块时 WinError 87，合理。

🟡 **命令行引号处理不够稳健**：仅按「含空格或 Tab」决定是否加引号，对含双引号本身的参数未做转义（Windows 命令行反斜杠+引号转义规则复杂）。当前参数源（sandbox_id/workspace/port）都是受控的，注入风险低，但若未来 workspace 路径含 `"` 会断。建议用 `subprocess.list2cmdline`（Windows 专用，正确处理转义）。

🟡 **`py = sys.executable or "python"`**：在 box-server 跑在 venv/uv 环境时，`sys.executable` 指向 venv python，注释（后续 commit 补的）已说明 uv trampoline 体系 python 在 `jbx-sandbox` 下会 WinError 5。本 commit 版还没这个意识，直接用 `sys.executable`，在 dev 实跑环境可能踩坑。后续 commit 改为 `JIUWENBOX_RUNNER_PYTHON` 优先。

### 2. 句柄继承（`win_exec.py:two_hop_spawn`，commit 版约 310-345）

🟢 **删除 pipe 后句柄继承风险消失**：旧版要 `CreatePipe` + `SetHandleInformation` 关 box-server 持有端的继承位防 runner/child 拿到多余 pipe 端（对标 Linux `close_fds=True`）。本 commit 不再创建 pipe，`startup.dwFlags = 0` 不设 `STARTF_USESTDHANDLES`，runner 的 stdin/stdout 走默认（空/inherited），无高危句柄可泄漏。这是安全改进。

🟡 **`CREATE_SUSPENDED` + `LOGON_WITH_PROFILE` 保留正确，但 Job 已被注释禁用**：commit 版 `process.py` 的 `_create_windows` 仍走 `win_job.resume_process(thread_handle)`，但 Job Object 创建那段被注释掉了（注释说跨用户 `OpenProcess` 拿不到 `PROCESS_SET_QUOTA` → WinError 5）。这意味着 `CREATE_SUSPENDED` 的「先 assign Job 再 resume」语义已经退化成「直接 resume」——suspend 只剩防止极短竞态的语义，不再是 Job 隔离的前置。设计文档 6.8 的 SUSPEND→Assign→Resume 链路在 Windows 上实际已不成立，资源限制（memory/cpu/进程数）失效。这是**功能降级**而非 bug，但应在文档/注释里显式标注「Windows 版无 Job 资源限制」，避免误判沙箱受资源限额保护。当前注释已说明，可接受。

### 3. 受限 token 应用（`win_exec.py:_create_restricted_token`，commit 版约 560-620）

🔴 **受限 token 在 exec 路径实际未使用——这是 commit 版的已知降级，但 commit 信息没提**。commit 版 `_create_restricted_token` 仍用旧式 `restricting = (ctypes.c_void_p * 3)(everyone_sid, logon_sid, write_sid)` 传 `c_void_p` 数组给 `CreateRestrictedToken` 的 `PSID_AND_ATTRIBUTES` 参数（注释里自己标了「review CRITICAL #1」会说这会 WinError 998）。同时 commit 版 `_handle_exec_request` 调 `_create_process_as_user(restricted_token, ...)` 用受限 token 起 child——但受限 token 会让 child 启动即 `0xC0000142`（DLL 初始化失败）。这在本 commit 时还是「受限 token 路径」，后续 commit `073d4c1e` 才改成 `_get_runner_primary_token`（runner 自身未受限 token）。也就是说**本 commit 的安全模型处于「受限 token 既不可用、又还在用」的中间态**：`_create_restricted_token` 若成功则 child 必挂；若失败则 runner 直接退出。这是本 commit 未能让沙箱真正跑通 exec 的又一原因。建议审查者注意：本 commit 的受限 token 实现是「待替换」状态，不应作为最终安全模型评价。

🟢 **受限 token 的 SID 数组构造确有 bug（commit 版 `_get_logon_session_sid` 返回悬垂指针）**：commit 版 `_create_restricted_token` 调 `_get_logon_session_sid()`，后者返回指向 `buf`（函数内 `c_byte` 数组）的指针，函数返回后 `buf` 被 GC → 悬垂指针 → `CreateRestrictedToken` 读已释放内存 → WinError 998。后续 commit 把 SID buffer 内联到 `_create_restricted_token` 作用域持有引用才修。本 commit 没修这个，属于遗留 bug。

### 4. 端口分配与竞态（`process.py:_alloc_loopback_port`，commit 版 151-167 / `_create_windows`）

🟢 `_alloc_loopback_port` 用 `bind(("127.0.0.1",0))` 让 OS 选空闲端口后立即 close，返回端口号，标准做法。注释诚实说明 TIME_WAIT 风险与「runner resume 后才 bind，存在极小概率端口被抢占」的竞态，并说明 runner bind 失败会退出、box-server 检测 runner 退出报错。可接受。

🟡 **端口分配到 runner bind 之间存在竞态窗口，且 box-server 侧无探测**：control_port 在 `_create_windows` 分配后立即传给 `two_hop_spawn`（CREATE_SUSPENDED），runner 被 resume 后才进 `runner_main` 去 bind。期间：(a) 端口可能被其他进程抢占（概率低但存在）；(b) box-server 的 `_win_log_reader_blocking` 会带 50 次重试 connect（commit 版 process.py 3520-3547），但 `_win_roundtrip_blocking` 的 connect 只用 2s 超时、无重试——若第一次 exec 赶在 runner bind 前，直接 ECONNREFUSED 返回 IPC 失败。建议 exec 路径也加有限重试或等 runner 就绪信号。

### 5. daemon_ipc 兼容性（`daemon_ipc.py`，commit 版 72-105）

🟢 **收紧 socket-only 是安全的**：`recv_exact`/`send_frame` 去掉 `getattr(sock,"recv") or sock.read` 与 `sendall/write+flush` 双分支，只留 `.recv`/`.sendall`。全仓 grep 确认：Linux 侧 `sandbox_daemon.py` 用的是自己本地 `_recv_exact`/`_send_frame`（带下划线前缀），不依赖 `daemon_ipc`；Windows 侧 roundtrip 全改 socket 后也只用 `.recv`/`.sendall`。无残留 pipe file object 调用 `daemon_ipc.recv_exact`/`send_frame`，不会回归。

🟡 **类型签名收窄但无运行期断言**：`recv_exact(sock: socket.socket, ...)` 只是注解，运行期传非 socket（如误传 file object）不会立即报错，而是在 `.recv` AttributeError 时才暴露。鉴于已无调用方误传，影响低，但可在入口加 `isinstance(sock, socket.socket)` 断言防回归。

### 6. win_setup 新增逻辑（`win_setup.py`，commit 版 276-393 / 743-760）

🟢 **`_set_user_password` 用 `NetUserSetInfo` level=1 重设密码，结构体复用 `_USER_INFO_1`，字段填充与 `_create_sandbox_user` 一致**，`NetUserSetInfo` argtypes 在 `_get_netapi32` 里正确声明（`LPCWSTR, LPCWSTR, DWORD, c_void_p, POINTER(DWORD)`），`parm_err` 出参传 `byref(err)`。逻辑正确。

🔴 **`_generate_password` 改为固定 `"000000"` 是严重安全降级**：`win_setup.py:320-327` commit 版注释自承「调试阶段固定为 000000，方便排查」。但 `jbx-sandbox` 是一个真实的本地用户账户，密码 `"000000"` 极弱。结合 `LOGON_WITH_PROFILE` 会加载 profile、用户对自身 profile 有完全控制权，若该账户被加入任何高权限组或 ACL 授予过宽，弱密码=本地提权面。注释说「后期改为从配置文件读取」，但**本 commit 把弱密码作为正式代码提交**（非 debug flag 门控），生产构建会带这个密码。建议：至少用 `secrets` 生成强密码并存注册表（旧版就是这样，只是 reinstall 一致性靠 uninstall 删用户解决即可，不必退化到固定密码）。事实上 uninstall 已改为删用户，旧版「随机密码+不删用户」的不一致问题已不存在，固定密码这个「方便排查」的取舍代价过高。

🟡 **`_create_sandbox_user` 2224 分支调 `_set_user_password` 重设 `"000000"` 会撞密码复杂度策略**：commit 版注释（后续 commit 改的）指出 `NetUserSetInfo` 重设简单密码 `"000000"` 会撞本地密码复杂度策略 → ret=87 → install 失败回滚。本 commit 版还没意识到这点，仍会在 2224 时调 `_set_user_password`。在启用了密码复杂度策略的机器上，幂等 reinstall 会直接 install 失败。后续 commit 把 2224 分支改回「不重设、信任已存在」才修。

🟢 **uninstall 删用户/组幂等性好**：`NetUserDel`/`NetLocalGroupDel` 返回 0 或 2201(NERR_UserNotFound) 都视为成功，其他才 warning，不阻断 uninstall。逻辑正确。

---

## 优点

1. **TCP loopback 取代匿名 pipe 是正确架构方向**：消除了 `open_osfhandle` fd/HANDLE 生命周期分裂、pipe 单连接复用难写对、`CreateProcessWithLogonW` env 块 ctypes 传参 WinError 87 等一系列 Windows 特有顽疾，对齐 Linux AF_UNIX 一连接一请求模型，可维护性显著提升。
2. **`_alloc_loopback_port` 实现干净**：OS 自动选端口 + bind 后即 close + SO_REUSEADDR 兜底，注释诚实说明竞态与失败回退路径。
3. **runner accept 循环单连接失败不杀 runner**：commit 版 `runner_main` 对 `accept` 后 `recv_frame` 异常只关该连接 continue（commit 版 `win_exec.py:595-600`），单个 exec 的连接异常不会拖垮整个 runner（旧版会）。这是健壮性改进。
4. **uninstall 删用户/组彻底化**：解决了 reinstall 密码不一致根因，幂等错误码处理到位。
5. **日志/诊断意识好**：`_push_log` 往日志订阅长连发回 box-server、本地落盘 `runner.log` 双管齐下，`CreateProcessAsUserW` 失败时打印 cmd0/PATH 片段/文件存在性+可读性，定位链路完整。

---

## 问题与风险

### 🔴 严重（会导致功能不可用）

- **P0-1 runner env 丢失 PATH（根因 C）**：`two_hop_spawn` 接收 `env` 参数但 `CreateProcessWithLogonW` 传 `None, None`，runner 拿不到调用方拼的工具目录 PATH → child 起 bash/node 报 WinError 2。`_build_env_block` 定义了却没调用（死代码）。`win_exec.py:234,331-333`（commit 版）。
- **P0-2 exec 读响应 2s 超时（根因 D）**：`_win_roundtrip_blocking` 全程用 `DAEMON_CONNECT_TIMEOUT_SECONDS=2.0`，exec 跑 >2s 必然 box-server 端 timeout → 连接断 → runner 异常。`process.py:3160`（commit 版）。
- **P0-3 受限 token 路径不可用（根因 E）**：`_create_restricted_token` 用 `c_void_p*3` 传 `PSID_AND_ATTRIBUTES` 数组（WinError 998 风险）+ `_get_logon_session_sid` 返回悬垂指针；且即便成功，受限 token 让 child 启动即 `0xC0000142`。本 commit 的 exec 链路实际跑不通。`win_exec.py:560-620,540-555`（commit 版）。

### 🔴 安全

- **S-1 弱密码 `"000000"` 作为正式代码提交**：`_generate_password` 固定返回 `"000000"`，`jbx-sandbox` 真实账户弱密码=本地提权面。uninstall 删用户的修复已使「随机密码+reinstall 一致性」可行，无需退化到固定密码。`win_setup.py:320-327`（commit 版）。

### 🟡 中等

- **M-1 `_stop_runner` 关 process_handle 但 `_is_running_windows` 仍读它**：`_stop_windows` 调 `win_exec._stop_runner`（内部 `CloseHandle(process_handle)`），但若 `runner` 已从 `_win_runners` pop，`_is_running_windows` 取不到——这点 OK。但若有并发路径在 stop 后仍持旧 handle 引用，`CloseHandle` 后再用会 UB。当前调用链串行，风险低，但 `process_handle` 的所有权应在注释里明确归 `_stop_runner` 独占。
- **M-2 端口竞态无 exec 侧重试**：见「端口分配与竞态」#4 🟡。
- **M-3 `_close_win_pipe_handles` 空方法留作占位**：注释自承「保留方法占位」，但 `_stop_windows` 直接调它（commit 版 `process.py:2988`），空方法+误导性名字（`_pipe_`）是未来维护陷阱。应直接删除调用与方法，或改名 `_close_win_runner_handles`。
- **M-4 `LISTENER_PORT_ENV` 死代码**：`win_exec.py:62` 定义 `LISTENER_PORT_ENV = "JIUWENBOX_CONTROL_LISTENER_PORT"` 但全仓无任何读取（control_port 走命令行参数）。应删除或补 env 注入路径。
- **M-5 2224 重设弱密码撞复杂度策略**：见 win_setup #6 🟡。

### 🟢 轻微

- **L-1 命令行引号转义不完整**：`_build_runner_command` 仅按空格/Tab 加引号，未处理参数内含 `"`。当前参数受控，低风险。建议改 `subprocess.list2cmdline`。
- **L-2 `daemon_ipc` 类型签名收窄无运行期断言**：见 daemon_ipc #5 🟡。
- **L-3 `_stop_runner` 的 `pid` 参数未使用**：commit 版 `_stop_runner(pid, process_handle, timeout_ms=5000)` 签名保留 `pid` 但函数体只用 `process_handle`，`timeout_ms` 也未用。应清理签名。

---

## 改进建议

1. **（必须）补 env 块传递**：在 `two_hop_spawn` 内 `if env: env_block_buf=_build_env_block(env); env_block_ptr=cast(...); creation_flags|=CREATE_UNICODE_ENVIRONMENT`，传给 `CreateProcessWithLogonW` 的 `lpEnvironment`，并确保 `env_block_buf` 存活到 API 返回（防悬垂指针）。（后续 commit 已修）
2. **（必须）exec 读响应用长超时**：`_win_roundtrip_blocking` 在 connect 后、recv_frame 前对 `REQUEST_TYPE_EXEC` 设 `max(130, caller_timeout+10)` 秒，file-op 仍用 connect 超时。（后续 commit 已修）
3. **（必须）换掉受限 token exec 路径或修 SID 数组**：要么把 `restricting` 改成 `_SID_AND_ATTRIBUTES` 数组（后续 commit 已改），要么 exec 改用 runner 自身 primary token（后续 commit `073d4c1e` 的方向）。二选一，当前 commit 两者都没做。
4. **（安全）恢复强密码生成**：`_generate_password` 回到 `secrets.choice` 生成强密码；reinstall 一致性靠「uninstall 删用户 + create_sandbox_user 重建」保证，而非固定密码。若确需调试，用环境变量门控（如 `JIUWENBOX_DEBUG_PASSWORD`）而非硬编码。
5. **（健壮）exec 路径加 runner 就绪探测/重试**：第一次 exec 前确认 runner 已 bind（或 connect ECONNREFUSED 时有限重试 3-5 次），消除端口竞态窗口。
6. **（清理）删 `LISTENER_PORT_ENV` 死代码、`_stop_runner` 清理 `pid`/`timeout_ms` 参数、`_close_win_pipe_handles` 改名或删除调用**。
7. **（安全）命令行构造改用 `subprocess.list2cmdline`**，正确处理 Windows 引号/反斜杠转义。

---

## 小结

本 commit 的架构方向（pipe → TCP loopback）是正确的正本清源，消除了 Windows 匿名 pipe 跨进程继承链路的一系列顽疾，uninstall 删用户也是对的修复。但本 commit 处于「重构进行到一半」的中间态：

- 正面：pipe 生命周期问题、reinstall 密码不一致这两条启动报错根因被有效覆盖；runner 单连接异常隔离、日志诊断链路质量高。
- 负面：env 块传递写了一半（`_build_env_block` 定义未用）、exec 读响应超时没改、受限 token 既是错的又还在用——这三处使本 commit 的沙箱「能起 runner、但 exec 跑不通」，需后续 commit 补齐。弱密码 `"000000"` 是不应进入正式代码的安全降级。

**结论**：本 commit 不应单独作为「修复完成」看待，它是重构序列的第一步，必须与后续 `2d19941c`/`073d4c1e`/`82001d09` 等一起才构成可用的 Windows 沙箱 exec 链路。若要回溯 cherry-pick，至少要连同修复 P0-1/P0-2/P0-3 的后续 commit 一起取。
