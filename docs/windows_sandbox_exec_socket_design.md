# Windows 沙箱 exec roundtrip 改 socket 通信 + 1326 长期解法 设计

> 日期: 2026-07-25
> 背景: Windows 沙箱首次端到端实测。沙箱已能进入 READY 状态（1326 已临时绕过），
> 但 exec 报 "runner IPC 失败"。根因是 box-server 端与 daemon 端的 IPC 传输层
> 设计为 stdin/stdout anonymous pipe，但实际 frame 收发依赖 socket 语义，
> 且 pipe 单连接语义与多 exec 串行/并发模型耦合过深。改为 TCP loopback socket，
> 对齐 Linux 路径（AF_UNIX listener + 按需 connect）的成熟模型。

## 一、现状与问题

### 1.1 当前 Windows exec 路径

```
box-server (普通用户 liubuyu 进程)
  └─ _create_windows
       └─ win_exec.two_hop_spawn
            └─ CreateProcessWithLogonW("jbx-sandbox", password, "python -m win_exec runner")
                 ├─ stdin  = anonymous pipe (box-server 写端 stdin_wf, runner 读端 sys.stdin.buffer)
                 └─ stdout = anonymous pipe (box-server 读端 stdout_rf, runner 写端 sys.stdout.buffer)
  └─ _exec_windows → _win_runner_roundtrip → _win_roundtrip_blocking
       └─ send_frame(stdin_wf, ...) / recv_frame(stdout_rf, ...)   ← daemon_ipc 的 frame 函数
```

runner 端 (`win_exec.runner_main`) 循环: `recv_frame(sys.stdin.buffer)` 读请求 → 处理 →
`send_frame(sys.stdout.buffer)` 写响应。

### 1.2 问题

1. **传输层不匹配（表面 bug，已临时修）**: `daemon_ipc.send_frame`/`recv_exact` 用
   `sock.sendall()`/`sock.recv()`（socket 专用）。anonymous pipe 的 BufferedWriter/Reader
   没有这俩方法 → `'_io.BufferedWriter' object has no attribute 'sendall'` (500)。
   已加鸭子类型兼容（有 sendall 走 socket，否则 write+flush/read）。

2. **exec 仍报 IPC 失败（深层问题）**: 即使 frame 函数兼容了 pipe，exec 仍返回
   `exit_code=1, stderr="runner IPC 失败"`。说明 pipe roundtrip 在 frame 层之外还有
   不匹配（可能是 pipe 半关闭语义、BufferedReader 缓冲、或 `_osfhandle_to_fd` 句柄
   方向）。继续在 pipe 模型上打补丁成本高且脆弱。

3. **设计偏差**: Linux 路径用 AF_UNIX socket（box-server bind/listen，daemon adopt
   listener fd，box-server 按需 connect），成熟稳定。Windows 却另起一套 stdin/stdout
   pipe 模型，没有对齐 Linux，导致两套代码、两套 bug。

## 二、方案：改用 TCP loopback socket（对齐 Linux AF_UNIX 模型）

### 2.1 选型对比

| 方案 | 优点 | 缺点 | 选不选 |
|---|---|---|---|
| **TCP loopback (127.0.0.1:port)** | 跨进程清晰、OS 自动分配端口、与 Linux AF_UNIX 语义最接近、Python socket 库原生支持 | 占用 loopback 端口（OS 兜底分配，无冲突） | ✅ 选 |
| Named pipe (`\\.\pipe\...`) | Windows 原生、无端口 | 需 `win32pipe` + 鸭子类型、并发模型（多 client connect）语义与 AF_UNIX 不完全对齐、API 更繁琐 | ✗ |
| AF_UNIX (Win10 1803+) | 与 Linux 同 API | 绑定文件路径语义不同、需双方都启用 AF_UNIX 支持、生态差、调试难 | ✗ |
| 继续用 anonymous pipe | 已有代码 | 传输层/缓冲/半关闭语义脆弱、bug 难修干净 | ✗（放弃） |

### 2.2 目标架构（Windows）

```
box-server (liubuyu 进程)
  └─ _create_windows
       ├─ 1. 创建 TCP listener: sock.bind(("127.0.0.1", 0)) + listen(64)
       │     OS 分配空闲端口 port_X
       ├─ 2. 把 port_X 经 env 注入 runner: JIUWENBOX_CONTROL_LISTENER_PORT=port_X
       │     （对齐 Linux 的 LISTENER_FD_ENV 思路, 但 Windows 不能传 fd,
       │      改传端口号; daemon 自己 connect）
       └─ 3. win_exec.two_hop_spawn(..., env 含 port_X)
            └─ CreateProcessWithLogonW("jbx-sandbox", ..., "python -m win_exec runner")
                 └─ runner_main: 读 JIUWENBOX_CONTROL_LISTENER_PORT, connect 127.0.0.1:port_X
                      └─ recv_frame/send_frame 全走 socket（原 daemon_ipc 不用改）
  └─ _exec_windows → _win_runner_roundtrip
       └─ box-server 每次 exec: socket.connect(("127.0.0.1", port_X)) → send_frame → recv_frame → close
            （对齐 Linux _connect_daemon_socket + _send_request_blob + _read_response_blob）
```

### 2.3 关键设计点

1. **listener 归属**: box-server 创建并持有 listener（bind/listen），runner connect。
   与 Linux 一致（Linux 是 box-server bind AF_UNIX，daemon adopt listener fd + accept，
   box-server connect socket_path）。
   - 注意：Linux 是 daemon adopt listener（daemon accept，box-server connect）。
     Windows TCP 下也可同模型：box-server bind+listen，但谁 accept？
     - 方案 A: box-server bind+listen+accept（box-server 是 server），runner connect。
       这样 box-server 端 `_win_runner_roundtrip` 每次 exec 都 accept 一个新连接。
     - 方案 B: runner bind+listen+accept（runner 是 server），box-server connect。
       runner 在 runner_main 启动时 bind 127.0.0.1:port_X（port 由 box-server 分配传入）。
   - **选方案 B**：runner 是 server（bind+listen+accept），box-server 是 client（connect）。
     理由：runner 生命周期 = sandbox 生命周期，listener 跟 sandbox 走最自然；
     box-server 是多 sandbox 的管理者，做 client 更简单。Linux 模型虽是 daemon adopt，
     但那是因为 bwrap 能传 fd；Windows 不能传 fd，让 runner 自己 bind 更直接。

2. **端口分配**: box-server 用 `sock.bind(("127.0.0.1", 0))` 让 OS 分配空闲端口，
   传给 runner。参考 `agent_ws_server._is_tcp_port_bindable` / fresh port 分配模式。
   - 不用固定端口（避免与 win_proxy 60080-89 / box-server 自身端口冲突）。
   - 端口范围无需限制，OS ephemeral range（49152-65535）足够。

3. **连接生命周期**: 每次 exec 一个新 TCP 连接（connect → send → recv → close），
   对齐 Linux `_connect_daemon_socket`（每次 exec 新 AF_UNIX 连接）。
   - 不复用长连接（避免 pipe 模型那种"单连接串行 + 不能 close"的复杂语义）。
   - runner 端 accept 循环处理一个连接一个请求（或一个连接多请求，先按一连接一请求做，简单）。

4. **frame 协议**: 完全复用 `daemon_ipc.send_frame`/`recv_frame`/`recv_exact`，
   因为现在是真 socket，socket 路径不变。**撤销之前给 daemon_ipc 加的 file 兼容**（恢复纯 socket，
   避免鸭子类型在 socket 路径引入意外）。但保留也无害——决策点见下。

5. **runner 启动时序**: box-server spawn runner（CREATE_SUSPENDED）→ runner 主线程
   resume 后 `runner_main` 先 bind listener → 写一行 "READY port_X" 到 stdout（或
   box-server 直接用已知 port_X，因为 port 是 box-server 分配传给 runner 的）。
   - **简化**: box-server 自己分配 port_X，env 传给 runner，runner bind 同一 port_X。
     如果 bind 失败（端口被占，极少见），runner 报错退出，box-server 检测到 runner
     退出重试。box-server 已知 port_X，无需等 runner 回报。

### 2.4 改动范围

**`daemon_ipc.py`**
- 撤销之前加的 file object 鸭子类型兼容（`send_frame`/`recv_exact` 恢复纯 socket
  `sendall`/`recv`）。socket 方案下两端都是真 socket，file 兼容分支永不命中，
  保留只会让代码读起来困惑。已确认撤销。

**`win_exec.py`（runner 端）**
- `runner_main`: 删掉 `sys.stdin.buffer`/`sys.stdout.buffer` pipe 读写，改为：
  - 读 env `JIUWENBOX_CONTROL_LISTENER_PORT`，`socket.bind(("127.0.0.1", port))` + `listen(64)`
  - accept 循环：每个连接 `recv_frame` → 处理（exec/write_file/read_file/list_dir/shutdown）→ `send_frame` → close
  - frame 收发用 `daemon_ipc.recv_frame`/`send_frame`（纯 socket，无需 file 兼容）
- `_build_runner_command`: 命令行加 `--control-port port_X`（或直接用 env）
- `two_hop_spawn`: 不再需要创建 stdin/stdout pipe（CreateProcessWithLogonW 的
  stdin/stdout 可设为 NULL / 继承默认），env 注入 `JIUWENBOX_CONTROL_LISTENER_PORT`

**`process.py`（box-server 端）**
- `_create_windows`: 删掉 `_osfhandle_to_fd` + `os.fdopen` 那段（stdin_wf/stdout_rf），
  改为：分配 port_X（`socket.bind(("127.0.0.1", 0))` 拿 port 后 close，或直接挑空闲端口），
  env 注入，spawn runner。`_win_runners[sandbox_id]` 存 `{"pid", "port": port_X, ...}`
- `_win_roundtrip_blocking`: 删掉 pipe 版，改为 `_connect_daemon_socket` 等价：
  `sock.connect(("127.0.0.1", port_X))` → `send_frame(sock, ...)` → `recv_frame(sock, ...)` → `sock.close()`
  （直接复用 Linux `_send_request_blob`/`_read_response_blob` 的逻辑，或抽公共函数）
- `_win_pipe_lock`: 保留（同 sandbox 串行），但锁的是 socket connect 而非 pipe 写
- `_stop_windows` / `_cleanup_windows_runner`: 删 pipe 文件对象 close，保留 process_handle/thread_handle/job 清理

## 三、1326 长期解法

### 3.1 根因回顾

- `uninstall()` 不删用户（注释：保留账户避免残留密码）
- `_create_sandbox_user` 用户已存在（2224）时跳过，不重设密码
- `install()` 每次生成新密码存注册表
- → 用户真实密码 ≠ 注册表密码 → `CreateProcessWithLogonW` 1326

### 3.2 修复

**密码固定调试值**: 调试阶段密码固定为 `"000000"`（不再每次随机生成），方便排查。
后期改为从配置文件读取（用户可配）。`_generate_password()` 暂时返回固定值 `"000000"`。

**`_create_sandbox_user(password)`** 在 `ret == 2224`（用户已存在）分支，调
`NetUserSetInfo(server, username, level=1, buf, parm_err)` 重设 `usri1_password`：
```python
elif ret == 2224:
    logger.info("沙箱用户 %s 已存在, 重设密码", const.SANDBOX_USER_NAME)
    _set_user_password(const.SANDBOX_USER_NAME, password)
```
新增 `_set_user_password`：用 `NetUserSetInfo` level=1（USER_INFO_1，含 usri1_password），
需 admin（install 本身 admin，OK）。这样每次 install 都保证用户密码 = 注册表密码。

**`uninstall()` 彻底删用户**: 不再"保留账户"，调 `NetUserDel` 删 jbx-sandbox 用户
+ `NetLocalGroupDel` 删组（如无其他成员）。让 reinstall 干净。需 admin（uninstall
本身 admin）。

### 3.3 验证

- `uninstall`（删用户）+ `install`（建用户 + 固定密码 000000）
- 普通用户 create-sandbox 进 READY，`CreateProcessWithLogonW` 不再 1326
- 多次 install 幂等（用户已存在 → 重设密码 = 000000，注册表也是 000000）

## 四、开发步骤

1. **`win_setup.py`**: `_generate_password()` 改返回固定 `"000000"`（调试，后期改配置）。
   加 `_set_user_password`（NetUserSetInfo level=1），`_create_sandbox_user` 2224 分支调它。
   `uninstall()` 加 `NetUserDel` 删用户 + `NetLocalGroupDel` 删组。home + D 盘两份同步。
2. **`win_exec.py` runner 端改 socket**: `runner_main` 读 `JIUWENBOX_CONTROL_LISTENER_PORT` env，
   `socket.bind(("127.0.0.1", port))` + `listen(64)` + accept 循环，用 `daemon_ipc.recv_frame`/`send_frame`。
   删 stdin/stdout pipe 读写。`_build_runner_command` 加 `--control-port`。
3. **`win_exec.py` two_hop_spawn**: 删 stdin/stdout pipe 创建，env 注入 `JIUWENBOX_CONTROL_LISTENER_PORT`。
4. **`process.py` _create_windows**: 删 `_osfhandle_to_fd`/`os.fdopen` 段，分配 port + env 注入。
   `_win_runners` 存 port 而非 stdin_wf/stdout_rf。
5. **`process.py` _win_roundtrip_blocking**: 改 socket connect roundtrip。
6. **`daemon_ipc.py`**: 撤销 file 兼容（已确认撤销）。
7. **测试**: uninstall + install（固定密码 000000）→ 临时 box-server + create-sandbox + exec `echo hello` + `dir`，确认 stdout 回显且不 1326。

## 五、风险与回退

- runner bind port 失败（端口被占）: 极少见（OS 分配），失败则 runner 退出，
  box-server 检测到 runner 进程退出 → sandbox error，可重试。
- TCP loopback 安全: listener 绑 127.0.0.1，仅本机可连。runner 是 jbx-sandbox 用户
  进程，box-server 是 liubuyu 进程，跨用户连 127.0.0.1 OK（loopback 无用户隔离限制）。
- 撤销 pipe 方案: 旧 `_win_roundtrip_blocking` pipe 版删除，无回退（但 git 已提交旧版，
  可 git revert）。

## 六、相关

- 设计文档: `docs/window沙箱.md` 6.5（原设计 stdin/stdout pipe，本方案改为 socket）
- 记忆: [[windows-sandbox-install-fixes]] [[windows-sandbox-create-fixes]] [[windows-sandbox-install-state]]
- Linux 对标: `process.py` `_create_daemon_listener` (line 764) + `_connect_daemon_socket` (line 2220)
