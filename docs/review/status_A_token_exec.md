# 状态核对 A：受限 token 与进程执行

核对基准：工作区 = HEAD commit 82001d09（链路终点）。
核对源文件：
- `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py`
- `jiuwenbox/src/jiuwenbox/server/runtime/process.py`
- `jiuwenbox/src/jiuwenbox/supervisor/win_constants.py`
- `jiuwenbox/src/jiuwenbox/supervisor/daemon_ipc.py`

判定图例：✅已解决 / ❌仍存在 / ⚠️部分解决 / 🔄以其他方式绕过

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|------|----------|----------|----------------|------|
| 1 | `_create_restricted_token` 创建了 Write-Restricted Token，但 `_handle_exec_request` 实际用 runner 自身未受限 primary token 起 child；`RESTRICTED_TOKEN_FLAGS` 临时去掉 WRITE_RESTRICTED(0x8)；受限 token 沦为 dead code | 5f841f7a | ❌仍存在（已知降级） | win_exec.py:1300-1312（exec 用 `_get_runner_primary_token()`，注释自承"方案1: 不用受限 token"）；win_constants.py:75（`RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE \| SANDBOX_INERT  # 临时去掉 WRITE_RESTRICTED(0x8)`）；win_exec.py:1074（runner_main 仍调 `_create_restricted_token()`）；win_exec.py:1152-1155（restricted_token 传给 `_handle_exec_request` 形参但函数体内未用，仅 finally CloseHandle） | 073d4c1e 的设计决策：受限 token 让 child 启动即 0xC0000142（DllMain 失败），改用 runner 自身未受限 token。受限 token 仍被构造但 exec 路径完全不用它（形参 `restricted_token` 在 `_handle_exec_request` 内被忽略，改用 `_self_token`）。`RESTRICTED_TOKEN_FLAGS` 仍不含 WRITE_RESTRICTED。写控制降一重，只剩合成 SID 的 ACL 单重检查。链路终点未恢复受限 token。 |
| 2 | ctypes 结构体对齐错误：`_SID_AND_ATTRIBUTES`/`_TOKEN_GROUPS` 8 字节对齐、PSID_AND_ATTRIBUTES 指针签名、SID buffer 悬垂指针 | d15fcf8e | ✅已解决 | win_exec.py:248-255（`_SID_AND_ATTRIBUTES` 正确定义 `{Sid: c_void_p, Attributes: DWORD}`）；win_exec.py:258-268（`_TOKEN_GROUPS.Groups` 用 `_SID_AND_ATTRIBUTES * 1`，ctypes 自动算 64 位 padding 让 offset=8）；win_exec.py:296（`CreateRestrictedToken` argtypes 第 7 参为 `ctypes.POINTER(_SID_AND_ATTRIBUTES)`，正确 PSID_AND_ATTRIBUTES）；win_exec.py:316-321（`AllocateAndInitializeSid` argtypes 补齐 8 个 sub-authority 参数位） | 旧版 `c_byte*0` 对齐 1→offset=4 漏 padding、`c_void_p*3` 传 `c_void_p` 参数 marshal 错指针→WinError 998，均已修。结构体与 argtypes 与 Win32 SDK 布局对齐。 |
| 3 | `_create_restricted_token` 硬编码 `(_SID_AND_ATTRIBUTES * 3)` 假设 logon_sid 非 None，边界 NULL 塞数组→WinError 87 | d15fcf8e | ✅已解决 | win_exec.py:715-737（`entries` 动态列表，`if logon_sid_val is not None: entries.append(...)`，`restricting = (_SID_AND_ATTRIBUTES * len(entries))(*entries)`） | 已改为动态 entries 列表 + None 防御。count==0 或无 LOGON_ID 组时只用 [Everyone, JHXSandboxWrite] 两个 restricting SID，不塞 NULL。 |
| 4 | `_create_restricted_token` 用 `c_void_p*3` 传 PSID_AND_ATTRIBUTES 数组（WinError 998 风险）+ `_get_logon_session_sid` 返回悬垂指针 | fb587eac | ✅已解决 | win_exec.py:737（`restricting = (_SID_AND_ATTRIBUTES * len(entries))(*entries)`，用正确结构数组而非 `c_void_p*3`）；win_exec.py:676-735（SID buffer 内联在 `_create_restricted_token` 作用域：`everyone_buf`/`logon_buf` 是函数内局部 ctypes 数组，`write_sid_ptr` 堆分配，三者引用持到 `CreateRestrictedToken` 返回） | `c_void_p*3` 已替换为 `_SID_AND_ATTRIBUTES` 数组。悬垂指针通过内联构造 buffer 并持引用解决（不再调 `_get_logon_session_sid`/`_get_everyone_sid`/`_get_synthetic_write_sid_ptr` 这三个 helper，它们在文件中仍存在但 `_create_restricted_token` 不再调用，属冗余死代码）。 |
| 5 | 正式弃用受限 token，exec 改用 `_get_runner_primary_token`（非受限），安全降一重 | 073d4c1e | ❌仍存在（已知降级，设计决策） | win_exec.py:760-785（`_get_runner_primary_token` 注释自述"受限 token 让 child 0xC0000142，故 exec 改用 runner 自身未受限 token...安全降一重"）；win_exec.py:1303（`_self_token = _get_runner_primary_token()`） | 这是 073d4c1e 的设计决策，链路终点未回退。判定为"仍存在（已知降级）"而非"以其他方式绕过"——这是主动放弃受限 token 双重写检查，写控制只剩 ACL 单重。注释与 docstring 仍声称两跳+受限 token（win_exec.py:1-22 模块 docstring），与实现不符，会误导维护者。 |
| 6 | `child_out_read` 未关继承（`_create_process_as_user` 用 bInheritHandle=True），child 继承 runner stdout 读端，pipe 不 EOF，fh.read() 挂起 | c2c3f5f0 | 🔄以其他方式绕过 | win_exec.py:1282-1297（`sa.bInheritHandle = True`，仅 `_clear_inherit(int(child_in_write.value))`，**未对 `child_out_read` 调 `_clear_inherit`**）；win_exec.py:996（`CreateProcessAsUserW(..., True, ...)` bInheritHandle=True 仍保留） | 技术缺陷仍在：`child_out_read` 仍可被 child 继承。但 82001d09 的后台 drain 线程 + join 5s 兜底 + `os.close(read_fd)` 强制退出（win_exec.py:1346-1424）绕过了"fh.read() 挂起"的后果——drain 线程持续读、进程退出后 join 5s、超时则 close fd 强制 drain 退出。即不再用 `fh.read()` 串行读，死锁路径被根除。但 `_clear_inherit(child_out_read)` 的最小权限修复未补。 |
| 7 | roundtrip 无超时（`_win_roundtrip_blocking`），runner 卡死时 executor 线程永久阻塞 + per-sandbox Lock 永久持有 | c2c3f5f0 | ✅已解决 | process.py:3376（connect 用 `DAEMON_CONNECT_TIMEOUT_SECONDS=2.0`）；process.py:3389-3393（`if request_type == REQUEST_TYPE_EXEC: _exec_read_timeout = 130.0; if read_timeout is not None: _exec_read_timeout = max(_exec_read_timeout, read_timeout + 10.0); sock.settimeout(_exec_read_timeout)`） | exec 读响应现已用 130s 默认（runner 120s + 10s 余量）或 caller_timeout+10s，不再全程 2s。file-op 仍用 connect 超时（短请求）。runner 卡死时 box-server 端 socket.timeout 抛 OSError 被 `_win_runner_roundtrip` 的 except 捕获返回 None（process.py:3435-3440），不再永久阻塞。 |
| 8 | `_close_win_pipe_handles` 双重 CloseHandle（fdopen closefd 默认 True 已关 handle，再显式 CloseHandle 同一 handle） | c2c3f5f0 | ✅已解决 | process.py:3190-3200（`_close_win_pipe_handles` 现为空方法占位，`return`，注释"改 TCP loopback 后不再有 pipe 文件对象/HANDLE"） | fb587eac 把控制通道改 TCP loopback 后，runner dict 只存 control_port/process_handle/thread_handle，不再有 pipe 文件对象/fd/HANDLE。`_close_win_pipe_handles` 变空方法，双重 CloseHandle 问题随之消失。 |
| 9 | runner env 丢失 PATH（two_hop_spawn 接收 env 但 CreateProcessWithLogonW 传 None,None，`_build_env_block` 死代码） | fb587eac | ✅已解决 | win_exec.py:508-513（`if env: env_block_buf = _build_env_block(env); env_block_ptr = ctypes.cast(env_block_buf, ctypes.c_void_p); creation_flags |= const.CREATE_UNICODE_ENVIRONMENT`）；win_exec.py:520（`CreateProcessWithLogonW(..., env_block_ptr, ...)` 传 env 块而非 None） | `_build_env_block` 已被调用，env 块经 `CREATE_UNICODE_ENVIRONMENT` flag 传给 `CreateProcessWithLogonW`。注释说明 env_block_buf 须存活到 API 返回（防悬垂指针）。runner 现能继承调用方拼的 PATH（含工具目录）。 |
| 10 | exec 读响应 2s 超时（`_win_roundtrip_blocking` 全程用 DAEMON_CONNECT_TIMEOUT_SECONDS=2.0），exec>2s 必断 | fb587eac | ✅已解决 | process.py:3389-3393（exec 读响应用 130s+ 超时） | 同 #7。connect 仍用 2s（loopback 够），但读响应前对 EXEC 请求设 130s+ 长超时。 |
| 11 | 命令行拼接不转义内部双引号（`_create_process_as_user` cmd_line + `_quote_arg` 仅按空格加外层引号） | 073d4c1e | ❌仍存在 | win_exec.py:852-854（`cmd_line = " ".join(f'"{c}"' if " " in c or "\t" in c else c for c in command)`，仅按空格/Tab 加外层引号，不转义内部 `"`） | 未改用 `subprocess.list2cmdline`。参数含内部双引号时 child 端 CRT 解析会 argv 错位。不经 shell 故无元注入，但复杂 bash -c 脚本含 `"` 仍可能失败。`_build_runner_command`（win_exec.py:432-437）同样简陋逻辑。 |
| 12 | exec stdout 死锁根治（后台 drain 线程 + join 5s 兜底） | 82001d09 | ✅已解决 | win_exec.py:1346-1368（`_drain_pipe` 后台线程持续 `os.read` 读 stdout pipe，防 child 写满 64KB pipe 阻塞）；win_exec.py:1369-1370（`_drain_thread = _threading.Thread(..., daemon=True); _drain_thread.start()`）；win_exec.py:1385-1406（主线程 `WaitForSingleObject` 循环 wait 进程，超时强杀）；win_exec.py:1408（`_drain_thread.join(timeout=5.0)`）；win_exec.py:1414-1417（drain 仍活则 `os.close(read_fd)` 强制 drain 退出） | 这是 82001d09 的核心修复。wait+drain 并行替代旧串行 wait-then-read，根治 pipe 写满互锁。join 5s 兜底孙进程持写端不 EOF 场景。GIL 安全（bytearray extend 原子）。 |
| 13 | TEMP 子目录命名两跳不一致（win_exec.py 用 basename(workspace) vs process.py 用真实 sandbox_id） | 82001d09 | ❌仍存在 | win_exec.py:889（`_sandbox_id = os.path.basename(workspace.rstrip("\\/")) if workspace else None`，第二跳从 workspace 末段推导）；win_exec.py:893-895（`_child_tmp = os.path.join(_profile_dir, "AppData", "Local", "Temp", "jiuwenbox", _sandbox_id)`）；process.py:2978（`_sandbox_sub = sandbox_id`，第一跳用真实 sandbox_id）；process.py:2986（`os.path.join(_profile_root, "AppData", "Local", "Temp", "jiuwenbox", _sandbox_sub)`） | 两跳 TEMP 目录命名源不同。正常情况 workspace 末段 == sandbox_id，但异常（workspace 末段 != sandbox_id）时第一跳建的目录（真实 id 版）未被使用、清理可能残留。功能不影响（都指向 jbx-sandbox profile 下可写区），但命名不一致。建议第二跳也接收真实 sandbox_id。 |
| 14 | exec_in_sandbox debug 日志含完整 request.command（命令行参数），运维开 DEBUG 会落盘可能含凭据 | 21024d62 | ❌仍存在（且恶化） | sandbox_manager.py:786-790（`logger.info("[SandboxWin] exec sandbox=%s cmd=%s workdir=%s PATH=%s", sandbox_id, request.command, request.workdir, exec_env.get("PATH", ""))`） | 21024d62 报告时此行是 `logger.debug`，报告建议截断 command。当前工作区（82001d09）已改为 `logger.info`（级别上调，更易落盘），且 `request.command` 仍全量未截断。DEBUG 开启或 INFO 级别下命令行参数（可能含 prompt/API key/文件路径）全量进日志。报告的担忧未解决反恶化。 |
| 15 | process.py 创建期 4 条 info 偏密且噪音（ACL applied/toolpaths injected/temp injected/runner spawned） | 21024d62 | ❌仍存在 | process.py:2963-2966（toolpaths injected `logger.info`）；process.py:3002-3005（temp injected `logger.info`）；process.py:3019-3026（ACL applied `logger.info`）；process.py:3052-3056（runner spawned `logger.info`） | 4 条 info 全部保留，级别未下调到 debug。每次创建沙箱刷 4 条 info，频繁创建/销毁场景下偏多。21024d62 报告建议把诊断性日志下放到 debug，仅保留 runner spawned 在 info，未实施。 |

## 补充遗漏问题（报告中提到但未在清单中，核对当前状态）

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|------|----------|----------|----------------|------|
| B1 | `_get_logon_session_sid` / `_get_everyone_sid` / `_get_synthetic_write_sid_ptr` 三个 helper 仍存在（死代码） | d15fcf8e §6.6 | ❌仍存在 | win_exec.py:559-603（`_get_logon_session_sid`）；win_exec.py:606-615（`_get_everyone_sid`）；win_exec.py:618-647（`_get_synthetic_write_sid_ptr`） | `_create_restricted_token` 已内联 SID buffer 构造，不再调这三个 helper。它们在文件中仍存在但无调用方（runner_main 流程不调），属冗余死代码，应删。 |
| B2 | `_create_restricted_token` 的 `write_sid_ptr` 未 `FreeSid`，内存泄漏 | 5f841f7a §4.2 | ❌仍存在 | win_exec.py:723-735（`write_sid_ptr = ctypes.c_void_p(); AllocateAndInitializeSid(...)`，函数返回后未 `FreeSid`） | runner 生命周期内仅调一次 `_create_restricted_token`，影响小，但应 `FreeSid`。 |
| B3 | `_local_log_file` 未显式 close | 5f841f7a §4.2 | ❌仍存在 | win_exec.py:121（`_local_log_file = open(path, "a", ...)`）；win_exec.py:1180-1193（runner finally 块未 close `_local_log_file`） | runner 退出时 OS 回收 fd，但显式 close 更佳。 |
| B4 | exec 超时强杀后退出码语义未区分，`_child_killed` 未回传 box-server | 5f841f7a §4.2 / c2c3f5f0 | ⚠️部分解决 | win_exec.py:1402-1406（`_child_killed = True` 标志记录在日志）；win_exec.py:1434-1436（日志含 `killed={_child_killed}`）；win_exec.py:1437-1442（响应体 `{"ok": True, "exit_code": ec, "stdout": out_text, "stderr": ""}`，**未含 `killed` 字段**） | `_child_killed` 仅在 runner 端日志记录，未回传给 box-server（响应体无 `killed` 字段）。box-server 拿到 exit_code=1 无法区分正常退出 vs 超时强杀。部分解决（runner 端可观测，box-server 端不可区分）。 |
| B5 | `_handle_exec_request` except 分支未关 `child_in_write`（异常路径泄漏 child stdin 写端 handle） | c2c3f5f0 §6 第 8 点 | ❌仍存在 | win_exec.py:1484-1488（except 分支只 `CloseHandle(child_out_write)` + `CloseHandle(child_out_read)`，**未关 `child_in_write`**） | 异常路径泄漏 child stdin 写端 handle。正常路径 win_exec.py:1328/1330 已关，但异常路径漏。 |

## 汇总

本组共核对 15 条主清单 + 5 条补充，共 20 条。

- ✅已解决：8 条（#2, #3, #4, #7, #8, #9, #10, #12）
- ❌仍存在：9 条（#1, #5, #11, #13, #14, #15, B1, B2, B3, B5）—— 其中 #1/#5 是同一设计降级（受限 token 弃用）
- ⚠️部分解决：1 条（B4）
- 🔄以其他方式绕过：1 条（#6）

### 仍存在/部分解决清单（每条一行）

- #1/#5 受限 token 弃用：exec 用 `_get_runner_primary_token()`（未受限），`_create_restricted_token` 沦为 dead code，`RESTRICTED_TOKEN_FLAGS` 不含 WRITE_RESTRICTED — win_exec.py:1303, win_constants.py:75
- #11 命令行拼接不转义内部双引号：`cmd_line = " ".join(f'"{c}"' if " " in c...` — win_exec.py:852-854
- #13 TEMP 子目录命名两跳不一致：win_exec.py:889 用 basename(workspace) vs process.py:2978 用真实 sandbox_id
- #14 exec_in_sandbox 日志含完整 request.command 且级别从 debug 上调到 info — sandbox_manager.py:786-790
- #15 process.py 创建期 4 条 info 未下调到 debug — process.py:2963, 3002, 3019, 3052
- B1 三个 SID helper 死代码未清理 — win_exec.py:559-647
- B2 `write_sid_ptr` 未 FreeSid — win_exec.py:723-735
- B3 `_local_log_file` 未显式 close — win_exec.py:121, 1180-1193
- B4 exec 超时强杀 `_child_killed` 仅日志未回传 box-server — win_exec.py:1437-1442
- B5 except 分支未关 `child_in_write` — win_exec.py:1484-1488
