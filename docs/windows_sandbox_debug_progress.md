# Windows 沙箱适配排查进展记录

> 本文记录 officeAce 调起 jiuwenbox Windows 沙箱从"完全跑不通"到"bash 可执行"的完整排查链，按踩坑时间顺序记录每个根因、修复、遗留问题。对应设计文档 [window沙箱.md](window沙箱.md)，适配工作自提交 `5f841f7ae9f357138740225b29a2c703ff4ca585` 起。

---

## 0. 最终状态（截至本次排查）

**沙箱 bash 执行已跑通**：`bash -lc ls -la ...` 等纯 bash 命令 `exit=0` 成功执行。0xC0000142（受限 token 启动失败）根因已定位并解决（方案1：exec 放弃受限 token）。

剩余失败均为 agent-core 侧命令生成问题（反斜杠路径转义、cmd/powershell 语法混进 bash），非沙箱问题。另有 `WinError 183` upload 残留问题待处理。

---

## 0.1 后续排查：PPT install + convert 必现卡死（已解决）

在 bash 执行跑通后，PPT 任务（pptx-craft skill）的 `playwright install chromium`（P0.1）和 `cli.js convert`（P9）两个阶段必现卡死，导致 PPT 全流程失败。经多轮排查定位到 5 个独立/叠加的根因，详见下方 §14~§18。最终靠"stdout 重定向到文件能退出"这一关键对比定位到真正的死锁根因（§14），修复后 install + convert 全通，PPT 任务成功生成 pptx。

### 后续排查的核心根因（详见 §14~§18）
1. **stdout pipe 写满死锁**（§14，最终根因）：child 写进度到 64KB pipe 写满阻塞，runner 等 wait 不读 → 死锁。修复：后台线程 drain stdout。
2. **WFP 端口 Block render server**（§15）：convert 的 render server 用随机端口，WFP 只放行 60080-60089 → chromium 访问被 Block。修复：临时放开整个 loopback。
3. **网络白名单缺 playwright 下载源 + .npmrc env 断链**（§16）：`.npmrc` 的 `playwright_download_host` 在 npx 直调时不进 env → 走官方源被拒。修复：约定键 `JIUWENBOX_INJECT_ENV` 注入机制。
4. **LOCALAPPDATA/USERPROFILE 缺失 + TEMP 路径不一致**（§17）：child env 缺 profile 变量。修复：`get_sandbox_profile_dir()` + TEMP 指向 jbx-sandbox profile。
5. **tool_paths 硬编码开发者机器路径**（§18）：用户机器路径不存在。修复：`_resolve_tool_paths` 用 `sys.executable` 自动检测。

---

## 1. install 同步阻塞 + UAC 死等（已解决）

### 1.1 现象
启动 officeAce 时，box-server lifespan 调 `ensure_windows_setup`，弹 UAC 后主进程"阻塞等待完成"打不出，2 分钟后 agent-server health check 超时杀掉 box-server。日志反复出现 `health check timeout after 120.0s`。

### 1.2 根因链（逐层剥洋葱）

**坑①：`ShellExecuteExW` 结构体布局错误 → WinError 5**

最初想用 `ShellExecuteExW` + `SEE_MASK_NOCLOSEPROCESS` 拿子进程句柄实现同步等待，但自定义的 `SHELLEXECUTEINFOW` 结构体漏了尾部 8 字节对齐 padding，`sizeof=104` 而非 SDK 的 `112`，`cbSize=104` 不符 Shell 预期 → `ShellExecuteExW` 返回 `WinError 5`（ACCESS_DENIED），连 `open cmd.exe` 都失败。

**教训**：不要自定义 Windows 结构体。实测确认 cbSize 必须等于 Shell 期望值（112），靠 padding 凑数是偷懒。最终放弃 `ShellExecuteExW`。

**坑②：改用 `ShellExecuteW`（异步）+ 命名 Event 实现同步**

旧版用 `ShellExecuteW("runas")` 拉起 install 子进程，**异步返回、不等 install 完成**。主进程以为 install 完了就继续创建沙箱，install 子进程其实还在跑 → `CreateProcessWithLogonW` 拿到半成品密码 → 1326。

修复：沿用原版 `ShellExecuteW`（它能正常弹 UAC、不碰结构体），但加**命名 Event 同步**：
- 主进程 `CreateEventW("Global\JiuwenBox-Install-Done-<hex>")` 建非信号态 Event，名字经 `--install-done-event` 参数传给 install 子进程。
- `ShellExecuteW` 拉起子进程后，主进程 `WaitForSingleObject(event, INFINITE)` 阻塞等。
- install 子进程在 `install()` 跑完后（`try/finally` 保证成功失败都 set）`OpenEventW + SetEvent` 通知主进程。

Event 闭环实测跑通：CreateEventW → Wait(0) 返回 TIMEOUT → SetEvent → Wait 返回 OBJECT_0。

**坑③：install 子进程用错 Python 解释器**

`_elevate_and_run_install` 用 `py = sys.executable`，但 box-server 跑在 uv venv，`sys.executable` = `.venv\Scripts\python.exe`（45KB **trampoline launcher**，真实 CPython 在 `AppData\Roaming\uv\python\...`）。提权新会话下 trampoline 跑 `-m` 崩在 import、到不了 `_main` → `install_force.log` 空、SetEvent 永不发生、主进程死等。

修复：`py = os.environ.get("JIUWENBOX_RUNNER_PYTHON") or sys.executable`。`agent_ws_server` 已探测注入 `JIUWENBOX_RUNNER_PYTHON=D:\Files\python313\python.exe`（真实 CPython），install 子进程用它就能进 `_main`。

**坑④：`--preinstall-paths` 参数 JSON 嵌套引号被命令行解析破坏**

`_elevate_and_run_install` 用 `json.dumps(preinstall_paths)` 编码路径列表，值含内层双引号（`"["C:\\Program Files", ...]"`）。`_quote_arg` 加外层引号后，Windows 命令行解析把内层 `"` 当闭合，含空格路径被拆碎 → 子进程 argparse 报错退出、到不了 install。

修复：改用 `base64(JSON)` 编码，值变纯字母无空格/引号/反斜杠，彻底避开命令行转义。

**坑⑤：密码 `NetUserSetInfo ret=87 err=5`**

install 子进程"用户已存在"时调 `_set_user_password` 重设密码成 "000000" → `NetUserSetInfo` 返回 `ret=87`（ERROR_INVALID_PARAMETER）。`_generate_password` 固定返回 "000000"，用户已存在时真实密码本就是上次装的 "000000"，**重设纯属多余**，且简单密码 "000000" 撞本地密码复杂度策略 → ret=87 → install 失败回滚 → SetEvent 仍发（finally）→ 主进程误以为成功。

修复：删掉 `_create_sandbox_user` 里"用户已存在则重设密码"的分支，改成跳过（密码固定，无需重设）。ret=87 消失，install 能走完。

### 1.3 涉及文件
- [win_setup.py](../jiuwenbox/src/jiuwenbox/supervisor/win_setup.py)：`_elevate_and_run_install`（ShellExecuteW + Event + base64）、`_create_sandbox_user`（删重设密码）、`_get_userenv`/`_delete_profile_by_sid`/`_purge_stale_profile_dirs`
- [agent_ws_server.py](../jiuwenclaw/agentserver/agent_ws_server.py)：box-server health check 超时 30s→120s

---

## 2. 反复弹 UAC（已解决）

### 2.1 现象
首次启动弹一次 UAC（合理），但**发起会话创建沙箱时又弹一次**。

### 2.2 根因
两处调用 `ensure_windows_setup` 传的 preinstall 路径集**不一致**：
- **lifespan（app.py）**：只传 `read_acl_preinstall`（`%USERPROFILE%`/`%SystemRoot%` 等）→ install 记录这个集到 `REG_VALUE_PREINSTALLED_PATHS`
- **创建沙箱（_create_windows）**：传 `read_acl_preinstall` + **tool_paths**（`D:\Files\Git` 等，每台机不同）

`tool_paths` 不在首次记录的集合里 → `ensure_windows_setup` 的"新增路径检测"命中 → 自动弹 UAC（force=True）。且 tool_paths 是每机不同的运行时路径，没法写死进打包 policy。

### 2.3 修复
抽 `collect_preinstall_paths(policy)` 共享函数，两处调用算同一集合：
- [win_setup.py `collect_preinstall_paths`](../jiuwenbox/src/jiuwenbox/supervisor/win_setup.py)：`read_acl_preinstall + tool_paths` 展开（含 git 的 usr/bin、bin）
- [app.py lifespan](../jiuwenbox/src/jiuwenbox/server/app.py)：改用它
- [process.py `_create_windows`](../jiuwenbox/src/jiuwenbox/server/runtime/process.py)：改用它

首次 install 就把 tool_paths 预装 + 记录，后续创建沙箱比对差集为空 → 不再弹 UAC。真正运行时才知道的路径（workspace/venv）仍由会话时 `apply_sandbox_acl` 精确授权（owner=当前用户，普通权限够，不弹 UAC）。

### 2.4 SetEvent 语义缺陷（遗留）
install 失败回滚时，`_make_install_done_notifier` 在 `finally` 里无条件 SetEvent，把失败也通知成"完成"。主进程靠 `installed` 标记判断真假，但首次 install 失败回滚删了用户没写 installed → 第二次 lifespan 又走完整 install。表现为"首次失败、重试成功"的两次 UAC。当前已自然消化（第二次成功写 installed=1），但 SetEvent 语义仍不精确——建议后续改成"install 成功才 SetEvent，失败不 set 让主进程靠超时/installed 判断"。

---

## 3. install 预装大目录卡死（已解决）

### 3.1 现象
install 子进程卡在 `读 ACL 预装已在后台线程启动 (11 路径)`，UAC 窗口不自动退出，主进程死等。

### 3.2 根因
`read_acl_preinstall` 含巨型系统目录：`%USERPROFILE%`（= `C:\Users\liubuyu`，几十万文件）、`%SystemRoot%`（`C:\WINDOWS`）、`%ProgramFiles%`、`%ProgramData%`。`grant_ace(..., recursive=True)` 用 NTFS 继承 ACE（`CONTAINER_INHERIT_ACE|OBJECT_INHERIT_ACE`），对已存在海量子对象的目录，`SetNamedSecurityInfo` 会**同步传播 ACE 到所有子文件**，巨慢。

### 3.3 修复（基于权限分析）
实测 `icacls` 确认这些大目录的 Windows 默认 ACL 已给 `BUILTIN\Users:(RX)+(GR,GE)` 并继承：
- `C:\WINDOWS`/`System32`：Users 组 RX 继承
- `C:\Program Files`/`ProgramData`：同上

jbx-sandbox 是本地用户，默认在 Users 组，靠默认 ACL 就能读 System32 的 dll、Program Files 的 exe。Write-Restricted token 只二次检查写，读不受影响。

故 `read_acl_preinstall` 改成 `[]`，只留 `collect_preinstall_paths` 自动展开的 tool_paths（D:\Files\Git 等，目录小递归快）。系统大目录靠默认 ACL，不预装。

[windows-policy.yaml](../jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml) `read_acl_preinstall: []`，注释说明每个删掉的大目录理由。

---

## 4. bash 0xC0000142（核心问题，已解决）

### 4.1 现象
install 同步阻塞修通后，bash 执行报 `exit=3221225794`（= `0xC0000142` STATUS_DLL_INIT_FAILED），弹窗"应用程序无法正常启动"。cmd/powershell/python/bash **全部** 0xC0000142。

### 4.2 排查过程（排除法）

**排除 PATH/env/ACL**：
- `_build_windows_exec_env` 拼 PATH 含 `D:\Files\Git\usr\bin` ✅
- 补齐 `SystemRoot`/`windir`/`TEMP`/`TMP`/`PATHEXT`/`COMSPEC` ✅
- `icacls` 确认 `cmd.exe`/`kernel32.dll` 给 `BUILTIN\Users:(RX)`，jbx-sandbox 能读 ✅
- 普通用户（非受限 token）跑 bash.exe 正常（`echo bash_ok` 成功）✅

**离线复现（构造受限 token）**：用当前用户 token + `CreateRestrictedToken(flags=0xB)` 起 cmd → 0xc0000142。逐 flag 拆解：

| flags | bash exit |
|---|---|
| 完整 0xB（DISABLE_MAX_PRIVILEGE\|SANDBOX_INERT\|WRITE_RESTRICTED） | 0xc0000142 |
| 去 DISABLE_MAX_PRIVILEGE (0xA) | 0xc0000142 |
| 去 SANDBOX_INERT (0x9) | 0xc0000142 |
| 仅 WRITE_RESTRICTED (0x8) | 0xc0000142 |
| 无限制 token (0x0) | 0x1（成功） |

**关键结论**：只要 token 是 Restricted Token（带 WRITE_RESTRICTED、受限 SID 非空），任何进程都起不来。

**runner 真实场景坐实**：在 runner（jbx-sandbox 上下文）加诊断块，用受限 token 起 `cmd.exe /c echo DIAG_OK_FROM_RUNNER` → `exit=0xc0000142 stdout=''`。cmd 不依赖 msys、ACL/env 都没问题，是受限 token 机制让进程 DllMain 失败。

### 4.3 根因
`CreateRestrictedToken` 产生的受限 token 让 child 进程启动即 0xC0000142（DllMain 失败）。最可能是受限 token 的进程无法关联到交互式 desktop/windowstation（user32.dll 初始化需要），或受限 SID 机制锁死全局对象。**这是受限 token 起子进程的固有机制问题**，和 jbx-sandbox/ACL/env 无关。

设计文档 §6.5 的第二跳（受限 token 起用户命令）与 Windows 进程启动机制根本冲突。

### 4.4 方案选择
- 方案1：exec 不用受限 token，runner 直接用自身真实 token（未受限）起 child。代价：失去 Write-Restricted 双重写检查，写控制只剩合成 SID ACL（allow-only 仍挡越权写）。工作量几行。
- 方案2：cmd/powershell 替代 bash。**不行**——cmd/powershell 同样 0xc0000142。
- 方案3：保留受限 token + 关联 desktop/修全局对象。工作量大、不保证成功（若是 SID 锁死，关联 desktop 没用）。

**采用方案1**。

### 4.5 修复
[win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py)：
- 新增 `_get_runner_primary_token()`：`OpenProcessToken(GetCurrentProcess, TOKEN_QUERY|DUPLICATE|ASSIGN_PRIMARY)` 拿 runner 自身未受限 primary token。
- `_handle_exec_request`：起 child 时不再用 `restricted_token`，改用 `_get_runner_primary_token()`（用完 CloseHandle）。

runner 在 jbx-sandbox 自己上下文，给自己 token 起进程绕开 `SeAssignPrimaryTokenPrivilege` 权限墙（设计 §2.5）。

### 4.6 安全权衡（已确认）
- 失去 Write-Restricted 双重写检查，写控制只剩合成 SID 的 ACL。
- **用户代码层写仍受合成 SID ACL 约束**：sandbox workspace/venv 给合成 SID 授 Allow Write，其他路径没授权，child（真实 SID）写其他路径靠 ACL 第一重挡（独立用户身份 + 没 Allow Write ACE）。
- 安全降一重（双重→单重），换沙箱可用性。和 Codex 第一代沙箱写控制模型等价。
- `_create_restricted_token` 仍创建（write_file/read_file/list_dir 是 runner 直接 open，本就不用它）。

### 4.7 验证
修复后：`bash -lc ls -la ...` `exit=0` 成功，0xc0000142 消失。cmd/python/bash 都能启动。

---

## 5. PATH 与预装 ACL 脱节（已解决）

### 5.1 现象
早期 bash 失败是 `CreateProcessAsUserW 失败`（WinError 2，exe 找不到）。

### 5.2 根因
两套工具路径脱节：
- install 预装 ACL 用 `collect_preinstall_paths` → 含 `D:\Files\Git`（✓ 正确）
- exec 拼 PATH 用 `_build_windows_exec_env` → **写死 `%ProgramFiles%\Git\bin`**（这台机 Git 装在 D:\Files\Git，不在 Program Files）

预装了 D:\Files\Git 的 ACL，但 PATH 指向 C:\Program Files\Git，bash.exe 解析到不存在的路径 → 0xc0000142/WinError 2。

### 5.3 修复
`_build_windows_exec_env` 加 `tool_paths` 参数，拼 PATH 时优先用 policy 的 tool_paths（D:\Files\Git + git 的 usr/bin、bin），删掉写死的 `%ProgramFiles%\Git\bin`。调用点 [sandbox_manager.py](../jiuwenbox/src/jiuwenbox/server/sandbox_manager.py) 传 `self.policy.windows.filesystem.tool_paths`。

同时补齐 child env 基本变量（`SystemRoot`/`windir`/`TEMP`/`TMP`/`PATHEXT`/`COMSPEC`），防 DLL 初始化失败。

---

## 6. python3 裸名解析（已解决）

### 6.1 现象
`python3` 裸名解析到 `isolation_venv\Scripts\python3` 但该文件不存在（venv 里只有 python.exe，python3 是 virtualenv 额外建的，受限 token 下可能读不到）。

### 6.2 修复
[win_exec.py `_handle_exec_request`](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py)：command[0] 归一化 `python3`/`python3.13` 等 → `python`，让 CreateProcess 解析到 venv\Scripts\python.exe（venv 必有）。只改裸名，不碰带路径的命令。

---

## 7. upload 权限（部分解决）

### 7.1 Errno 13 Permission denied（已解决）
upload 到 `agent_office\agent`（业务产物目录）时 `[Errno 13] Permission denied`。根因：upload 由 runner 直接写（`open(path, "wb")`），runner 用 jbx-sandbox 真实 SID（未受限 token），合成 SID 的 ACE 对它不生效；旧版 `apply_sandbox_acl` 对 allow_write 路径只给真实 SID grant Read，没 Write。

修复 [win_acl.py](../jiuwenbox/src/jiuwenbox/supervisor/win_acl.py)：allow_write 路径给真实 SID 也 grant `ALLOW_WRITE_RIGHTS`（Write+Execute+Delete，和合成 SID 一致）。runner 是可信代理进程，写权限符合预期；第二跳用户代码仍受合成 SID 约束。

### 7.2 WinError 183（遗留）
upload `.workspace`/`session_memory.md` 到 `memory`/`context` 等子目录时 `[WinError 183] 当文件已存在时`。根因：这些子目录是**上次会话（7/27）建的**，ACL 是旧版（真实 SID 没 grant Write），runner（jbx-sandbox）对它们缺权限，`os.makedirs(exist_ok=True)` 判断异常。

不致命（后续 bash 命令仍 exit=0），但 agent 上下文文件没送进沙箱。待修：改 `_handle_write_file_request` 的 makedirs 失败降级（直接尝试 open，父目录已存在时能成），或清旧残留目录让新 ACL 生效。

---

## 8. 卸载 profile 残留（已解决）

### 8.1 现象
`--uninstall` 报 `删除 C:\Users\jbx-sandbox 失败 (WinError 5): ...WinX\Group1`，profile 目录留半删残留（`jbx-sandbox`/`.DESKTOP-AKUB4MF`/`.000`）。用户/组/注册表标记其实已删干净。

### 8.2 根因
`WinX` 是 Windows 系统创建的 reparse point 目录（Win+X 快捷菜单），ACL 系统锁定。`shutil.rmtree(ignore_errors=False)` 遇 WinError 5 整体中止 → 前面删的留半删、后面没删到。

### 8.3 修复
`_purge_stale_profile_dirs` 改用 `shutil.rmtree(onerror=...)`：onerror 回调对每个失败文件 chmod 重试、仍失败则跳过继续删其余；WinX 跳过、其余删尽后 `os.rmdir` 兜底删空目录。手动可用 `Remove-Item -Recurse -Force`（PowerShell 能处理 WinX reparse point）。

同时 `_elevate_uninstall` 补调 `uninstall_firewall_rule_fallback`（修 review B2：降级路径防火墙规则之前没卸载）。

---

## 9. Job Object 注释禁用（临时方案）

### 9.1 现象
`assign_process_by_pid` 用 pid 跨用户 `OpenProcess(jbx-sandbox 进程)` 拿不到 `PROCESS_SET_QUOTA` → WinError 5。沙箱创建时 Job 失败（原代码 except 吞掉、不阻断，但资源限制失效）。

### 9.2 修复
`_create_windows` 的 Job Object 段注释禁用（保留 resume runner）。resource 配置保留在 policy 但运行时忽略。后续若需资源限制，改用 `two_hop_spawn` 返回的 `proc_handle` 直接 assign（而非 pid OpenProcess）绕过跨用户 ACL。

---

## 10. 遗留问题清单

| # | 问题 | 严重度 | 状态 | 备注 |
|---|---|---|---|---|
| 1 | **SetEvent 语义不精确**：install 失败回滚也 SetEvent"完成" | 中 | 遗留 | 应改成 install 成功才 SetEvent，失败不 set 让主进程靠超时/installed 判断。当前已自然消化（重试成功），但首次失败会多弹一次 UAC |
| 2 | **WinError 183 upload**：旧会话子目录 ACL 未对齐，runner 建 .workspace 占位失败 | 中 | 遗留 | 改 `_handle_write_file_request` makedirs 失败降级，或清旧残留目录 |
| 3 | **方案1 安全降级**：exec 放弃受限 token，写控制只剩单重 ACL | 低 | 已接受 | 双重→单重写检查，和 Codex 第一代等价。若需恢复双重，需查清受限 token 启动失败根因（desktop vs SID），属方案3 |
| 4 | **agent-core 命令生成**：Windows 上生成 `\\` 路径 + cmd/powershell 语法跑在 bash -lc 里 | 中 | 遗留 | 反斜杠被 bash 转义丢失、`if exist`/`Test-Path` cmd/ps 语法不兼容。属 agent-core 侧，非沙箱。日志后半段纯 bash + 正斜杠路径命令已成功 |
| 5 | **Job Object 禁用**：资源限制（memory/cpu/进程数）失效 | 低 | 临时禁用 | 跨用户 OpenProcess 拿不到权限。需改用 proc_handle 直接 assign |

---

## 11. 关键设计决策记录

### 11.1 install 子进程改用真实 CPython
`_elevate_and_run_install` 的 `py` 从 `sys.executable`（uv trampoline）改为 `JIUWENBOX_RUNNER_PYTHON`（真实 CPython）。trampoline 在提权新会话下崩在 import。

### 11.2 install 同步阻塞靠命名 Event
不用 `ShellExecuteExW`（结构体坑），用原版 `ShellExecuteW` + `Global\` 命名 Event 同步。主进程 `WaitForSingleObject(INFINITE)` 等 install 子进程 SetEvent。

### 11.3 预装只装 tool_paths，系统目录靠默认 ACL
`read_acl_preinstall: []`。系统大目录（SystemRoot/ProgramFiles）的 Windows 默认 ACL 已给 Users 组 RX 继承，jbx-sandbox 靠此读，不递归预装（避免卡死）。tool_paths 由 `collect_preinstall_paths` 展开（owner=Administrators，需预装）。

### 11.4 exec 用 runner 自身 token（方案1）
放弃受限 token（让所有进程 0xC0000142），exec 用 runner 自身未受限 primary token。写控制降为单重 ACL。用户代码层仍受合成 SID ACL 约束。

### 11.5 upload 权限给真实 SID
`apply_sandbox_acl` 对 allow_write 路径，真实 SID 也 grant Allow Write（不只 Read）。runner 直接写文件靠真实 SID（未受限 token，合成 SID ACE 不生效）。

---

## 12. 涉及文件改动清单

| 文件 | 改动 |
|---|---|
| [win_setup.py](../jiuwenbox/src/jiuwenbox/supervisor/win_setup.py) | `_elevate_and_run_install`（ShellExecuteW+Event+base64+真实CPython）、`collect_preinstall_paths`、`_create_sandbox_user`（删重设密码）、`_get_userenv`/`_delete_profile_by_sid`/`_purge_stale_profile_dirs`、`_get_kernel32`（Event API） |
| [win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py) | `_get_runner_primary_token`（方案1）、`_handle_exec_request`（用自身token起child + python3归一化）、删诊断块 |
| [win_acl.py](../jiuwenbox/src/jiuwenbox/supervisor/win_acl.py) | allow_write 给真实 SID grant Allow Write |
| [win_job.py](../jiuwenbox/src/jiuwenbox/supervisor/win_job.py) | （未改，Job 段在 process.py 注释禁用） |
| [process.py](../jiuwenbox/src/jiuwenbox/server/runtime/process.py) | `_create_windows`（用 collect_preinstall_paths、Job 注释禁用）、ensure_windows_setup 调用点注释 |
| [sandbox_manager.py](../jiuwenbox/src/jiuwenbox/server/sandbox_manager.py) | `_build_windows_exec_env`（tool_paths + 基本env变量）、exec PATH 日志改 info |
| [app.py](../jiuwenbox/src/jiuwenbox/server/app.py) | lifespan 用 collect_preinstall_paths |
| [agent_ws_server.py](../jiuwenclaw/agentserver/agent_ws_server.py) | box-server health check 超时 30s→120s |
| [windows-policy.yaml](../jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml) | `read_acl_preinstall: []` |

---

## 13. 排查方法论小结

1. **离线复现 > 玄学推断**：受限 token 0xc0000142 靠"构造受限 token 跑 cmd"+"逐 flag 拆解"坐实，不靠猜。
2. **逐层剥洋葱**：install 死等 → ShellExecuteExW 结构体 → 改 ShellExecuteW+Event → Python 解释器 → base64 编码 → 密码 ret=87，每一层修了才暴露下一层。
3. **看证据别看表象**：弹窗不退出看着像"install 卡住"，实际是 install 子进程用错 Python 到不了 _main；SetEvent 打出看着像"成功"，实际 install 内部失败回滚了。
4. **权限分层清晰**：tool_paths（owner=Administrators，需提权预装）vs workspace/venv（owner=当前用户，普通权限 apply）；install（一次性提权）vs exec（运行时非特权）；runner 真实 SID（未受限，可信代理）vs 受限 token（第二跳用户代码，现已弃用）。
5. **不要自定义 Windows 结构体**：靠 padding 凑数是偷懒，实测 cbSize 必须等于 SDK 期望值。最终用 Event 绕开结构体。

---

## 14. stdout pipe 写满死锁（核心根因，已解决）

### 14.1 现象
- `bash -lc "npx playwright install chromium"` 卡满 600s 超时强杀，stdout=0，进程不退出
- `bash -lc "node cli.js convert ..."` 同样卡死
- 宿主机 Git bash 跑同样命令正常退出，只有沙箱内卡死
- 必现：每次重启重装沙箱用户后，PPT 任务都卡在 install

### 14.2 关键验证（决定性证据）
在沙箱内对比：
| 命令 | 结果 |
|---|---|
| `bash -lc "npx playwright install chromium"`（stdout 接 pipe）| ❌ 卡死 600s，stdout=0 |
| `bash -lc "npx playwright install chromium > /c/Users/jbx-sandbox/install.log 2>&1"`（stdout 重定向文件）| ✅ 正常退出，exit=0 |

stdout 不经 pipe（重定向文件）就能正常退出 → 根因是 **stdout pipe 写满死锁**，与 bash/npx/login shell 无关。

### 14.3 死锁机制
1. child（npx/playwright）写大量下载进度到 stdout pipe
2. anonymous pipe 缓冲区 64KB 写满 → child 阻塞在 `write()`，等 reader 读
3. runner 在 `WaitForSingleObject` 等进程退出（旧逻辑"先 wait 再 read"）
4. child 等被读才能继续→才能退出；runner 等它退出才读 → **互相死锁**
5. 卡满 timeout 强杀（但强杀只杀 bash，孙进程 node/oopDownload 持 pipe 写端不释放，后续 exec 通道全废）

`win_exec.py` 注释曾预见过此死锁（"防 child stdout 写满 pipe 后阻塞在 write 等 runner 读 → 互相死锁"），但旧缓解（带超时循环 wait）只防 runner 永久挂，没解决死锁本身。

### 14.4 修复：后台线程 drain stdout
[win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py) `_handle_exec_request` 把"先 wait 再 read"改成**并行**：
- 起后台线程 `_drain_pipe`：持续 `os.read` stdout pipe，child 写多少读多少，pipe 不满
- 主线程 `WaitForSingleObject`：边等进程退出，pipe 同时被 drain
- 进程退出后 `join` drain 线程（5s 超时防孙进程持写端不 EOF），拿完整 stdout

关键设计：
- drain 用阻塞 `os.read`：有数据读，无数据等，写端全关返回 EOF 自然结束
- 进程正常退出 → 内核回收 handle → pipe EOF → drain 线程自动结束
- 强杀场景（孙进程持写端不 EOF）→ join 5s 超时后关 fd 强制 drain 退出
- stdout 截断 `MAX_STDOUT_BYTES` 防内存撑爆
- 心跳日志带 `stdout_buf=N B`，能看到 pipe 在被读（佐证 drain 工作）

修复后 install 73 秒正常退出（exit=0），有完整 stdout 输出。

### 14.5 走过的弯路（被实测推翻的判断）
- ~~login shell（`-lc` 的 `-l`）导致 bash 不退~~：改 `-c` 后照样卡，宿主机 `-lc` 正常
- ~~bash msys job control + npx spawn 子进程组合~~：宿主机同样组合不卡
- ~~bash 是多余中间层，Windows 不该用 bash~~：根因不在 bash，在 pipe

---

## 15. WFP 端口 Block render server（convert 专属，已解决）

### 15.1 现象
convert（沙箱内手动跑，干净 exec 通道抓到完整错误）：
```
🚀 启动浏览器...
✅ 浏览器启动成功
📄 渲染页面 1/1: page-1.pptx.html
  📝 创建本地化 HTML 副本: __local_assets_...html
  📝 加载 HTML 页面...
❌ 处理页面 1 失败: page.goto: net::ERR_NETWORK_ACCESS_DENIED
  at http://127.0.0.1:6298/__local_assets_...html
```

### 15.2 根因
- convert 流程：`startRenderServer` 用 `get-port` 随机选端口（如 6298）监听 `127.0.0.1`，chromium `page.goto` 访问该 render server
- WFP Permit filter 只放行 `127.0.0.1:60080-60089`（代理端口范围），`6298` 不在范围 → Block
- chromium 收到 `ERR_NETWORK_ACCESS_DENIED`
- `get-port` 纯随机选端口，不读 env/不接受端口范围参数 → 无法从沙箱侧让 render server 用固定端口

### 15.3 修复（临时验证方案，已生效）
[win_wfp.py](../jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py) `install_wfp_filters` 的 Permit filter 条件从 `user + loopback + port==N`（每端口一个 filter）改成 `user + loopback`（每 layer 一个 filter，不限端口）。jbx-sandbox 访问 `127.0.0.1` 任意端口都放行。

uninstall 段对应改：删固定 base_key + 兼容遍历删旧 per-port 残留。

### 15.4 生效前提
`installed=1` 时 `ensure_windows_setup` 走幂等快路径跳过 WFP 重装 → **改了 win_wfp.py 也不会自动生效**，需 force 重装：
```
.venv/Scripts/python.exe -m jiuwenbox.supervisor.win_setup --install --force
```
force 重装只重装 WFP filter（删旧 per-port、装新全放开），**不重建 jbx-sandbox 用户**（`_create_sandbox_user` 幂等，已存在跳过）。实际这次靠"卸载沙箱用户重装"触发了 filter 重装。

### 15.5 端口映射方案评估（不可行）
- render server 端口是 `get-port` 随机选的进程内部变量，沙箱侧拿不到 → 无映射入口
- win_proxy 只支持 CONNECT/SOCKS5，不支持普通 HTTP 转发 → chromium 走代理访问 HTTP render server 不行
- 最终选择全放开 loopback（牺牲部分隔离性换可用性）

### 15.6 定稿收紧候选（当前是临时全放开）
1. 保持全放行 loopback（最简单，隔离性降）
2. 沙箱动态感知 render server 端口并临时 Permit（最安全，但复杂）
3. pptx-craft render server 用固定端口（跨项目，最干净——从 env 读 `JIUWENBOX_RENDER_PORT`，沙箱 Permit 该端口）

---

## 16. 网络白名单缺 playwright 下载源 + .npmrc env 断链（已解决）

### 16.1 现象（早期）
install 走官方源 `playwright.azureedge.net`，不在沙箱 egress 白名单（`default:deny`），被代理拒/卡死。

### 16.2 根因
- `.npmrc` 配了 `playwright_download_host=https://npmmirror.com/mirrors/playwright`，但 npm 只在 `npm script` 上下文把 `.npmrc` 自定义键以 `npm_config_<key>` 注入子进程 env
- 沙箱里跑 `bash -lc npx ...` 直接调 npx（非 npm script）→ `.npmrc` 键不进子进程 → Playwright `getFromENV` 读不到 `npm_config_playwright_download_host` → 回退官方源 → 被白名单拒

验证（Playwright 1.57 `env.js:29-33`）：
```js
function getFromENV(name) {
  let value = process.env[name];                                    // PLAYWRIGHT_DOWNLOAD_HOST
  value = value === void 0 ? process.env[`npm_config_${name.toLowerCase()}`] : value;  // npm_config_playwright_download_host
  ...
}
```
Playwright 认 `npm_config_*`，但 npx 直调时这个 env 不存在。

### 16.3 修复：约定键注入机制（通用）
**沙箱侧**（[win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py) `_create_process_as_user`）：读 env 里的约定键 `JIUWENBOX_INJECT_ENV`（JSON），解析后 `setdefault` 注入子进程，再删掉该约定键（不泄漏）。
- 沙箱只识这一个通用协议键，不认识 npm/playwright/任何工具
- 来新工具（`xxx_config_yyy`）不用改沙箱

**调用方侧**（agent-core [jiuwenbox.py](../agent-core/openjiuwen/extensions/sys_operation/sandbox/providers/jiuwenbox.py)）：`_collect_npmrc_env(cwd)` 读 `.npmrc`，把 `key=value` 转成 `npm_config_<key>`，塞进 `JIUWENBOX_INJECT_ENV` 传给沙箱。
- venv 副本（`.venv/Lib/site-packages/openjiuwen/.../jiuwenbox.py`）同步改了（非 editable install，需双写）

### 16.4 网络白名单确认
`*.npmmirror.com` 已在白名单，`cdn.npmmirror.com`（302 跳转目标）匹配通配符也放行。**不需要改 `.npmrc`**（之前误判要改，被推翻——302 跳转两域名都在白名单）。

### 16.5 egress default 改 allow（临时）
[windows-policy.yaml](../jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml) 的 `windows.network.egress.default` 从 `deny` 改成 `allow`（业务 skill 产物 HTML 引用的 `cdn.digitalhumanai.top` 等不在白名单，default:deny 会卡死 convert 的资源 localize）。临时放开保证功能可用，待 skill 侧把 CDN 资源打包后再收紧。

---

## 17. LOCALAPPDATA/USERPROFILE 缺失 + TEMP 路径不一致（已解决）

### 17.1 现象
- 沙箱 child 进程 `LOCALAPPDATA=`（空）、`USERPROFILE=`（空）、`TEMP=/tmp`（bash 默认）
- Playwright 靠 `os.homedir()` fallback 才装对路径（巧合命中 `C:\Users\jbx-sandbox`）
- 下载临时目录在 `workspace/.tmp`，安装目录在 `jbx-sandbox\AppData\Local\ms-playwright`，两者不同根

### 17.2 根因
第二跳 child env 来自 header（agent-core 传），不含 profile 变量。传了 env block 给 `CreateProcessAsUserW` 后，profile 变量不自动填充（Windows 只在不传 env 时从 profile 自动构造）。

### 17.3 修复
**TEMP/profile 注入**（[win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py) + [process.py](../jiuwenbox/src/jiuwenbox/server/runtime/process.py)）：
- `get_sandbox_profile_dir()`：用 runner token 调 `GetUserProfileDirectoryW` 拿 jbx-sandbox 真实 profile 路径（处理 `.000` 后缀）
- TEMP/TMP 指向 `<jbx-sandbox profile>\AppData\Local\Temp\jiuwenbox\<sandbox_id>\`（每沙箱隔离）
- 补全 `LOCALAPPDATA`/`USERPROFILE`/`APPDATA`

设计：下载临时目录在 jbx-sandbox profile 下（和安装目录同根），每沙箱隔离；安装目录 `ms-playwright` 共用（已装的 chromium 跨沙箱复用，不重下）。

### 17.4 初始问题
第一次没存下日志——runner 的 `USERPROFILE`/`HOME` env 为空（传了 env block，profile 变量不自动填），`_init_local_log` 拿不到 profile 路径跳过。修复：多路兜底（env → `get_sandbox_profile_dir()` API → 标准路径 `C:\Users\jbx-sandbox`）。

---

## 18. tool_paths 硬编码开发者机器路径（已解决）

### 18.1 现象
[windows-policy.yaml](../jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml) 的 `tool_paths` 硬编码 `python_dir: 'D:\Files\python313'`（开发者机器路径），用户机器不存在 → 安装/沙箱创建失败。

### 18.2 修复：自动检测（[policy_reader.py](../jiuwenbox/src/jiuwenbox/server/policy_reader.py)）
`_resolve_tool_paths(policy)`：Windows 下对空的 `tool_paths` 字段用 `sys.executable` 反推：
- `python_dir` ← `os.path.dirname(sys.executable)`（OfficeAce 部署 = `tools/python`，dev = `.venv/Scripts`）
- `node_dir` ← 从 python_dir 往上找 `tools/node`（OfficeAce 结构）
- `git_dir`/`bash_path` ← `shutil.which("git")` 反推（OfficeAce 包未必带 git）
- 只填空字段，不覆盖显式配置；路径校验 `os.path.isdir`，无效跳过
- 不落盘（符合 load_policy 不生成合并文件的新机制）

落点：`policy_reader.py:load_policy`（基底 + 副本合并后），三个返回点都包了 `_resolve_tool_paths`。

`windows-policy.yaml` 的 tool_paths 清空硬编码值，注释说明运行时自动检测。

### 18.3 policy 副本机制（已变更）
副本策略从"完整 dump 基底"改成"稀疏配置"：
- 副本（`sandbox_policy_render.py` 写的 `windows-policy.runtime.yaml`）只存用户可配字段（allow/deny 文件名单、egress 域名、disable_all）
- box-server `policy_reader.py:load_policy` 读基底 + 副本 `merge_policy` 深合并（list 追加去重），不生成合并文件
- tool_paths 不属于用户配置，不进副本，来自基底 + 运行时自动检测

---

## 19. 辅助改动：本地落盘日志 + exec 阶段日志

### 19.1 问题
runner 日志靠 control_port 长连回传 box-server，exec 通道卡死时日志也丢。install 卡死时 stdout=0，定位不到卡在哪。

### 19.2 修复（[win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py)）
- `_init_local_log(sandbox_id)`：runner 启动时建 `C:\Users\jbx-sandbox\jiuwenbox-logs\<sandbox_id>\runner.log`（jbx-sandbox 对自己 profile 有完全控制权）
- `_local_log(level, msg, exc)`：带时间戳写本地文件，线程安全
- `_push_log` 增加落盘：每次 push 日志同时写本地文件（与回传并行）
- exec 关键阶段补日志：
  - `exec child 已启动: pid=X cmd=... timeout_s=...`
  - 等待中 30s 心跳：`exec child 等待中: pid=X waited=Yms/Zms stdout_buf=NB cmd=...`
  - 超时强杀：`exec child 超时未退出 强杀`
  - 结束：`exec child 结束: pid=X exit_code=Y killed=Z stdout_len=N`

---

## 20. 沙箱架构关键认知（补充）

### 20.1 网络隔离两层
- **WFP**（机器级）：Block 所有出站 + Permit loopback。egress policy 只影响代理的域名过滤，**不影响 WFP 端口 Block**
- **win_proxy**（应用级）：HTTP CONNECT + SOCKS5 代理，做域名白名单过滤

`egress default=allow` 只影响代理域名过滤，**不放开 WFP 端口 Block**。chromium 访问 render server 随机端口走的是 WFP，不是代理。

### 20.2 路径传递
- agent-server 由 OfficeAce 用 `tools/python/python.exe` 起，spawn 时传 `JIUWENCLAW_AGENT_ROOT` 等 env
- jiuwenbox 拿 python 路径最可靠通道：`sys.executable`（agent-server 进程自己的 python）

### 20.3 token 机制（已变更）
- **受限 token 已废弃**：`_create_restricted_token` 是历史遗留，第二跳改用 runner 自身 primary token（未受限）。受限 token 让 child 启动即 `0xC0000142`
- **合成 SID 当前基本无用**：合成 SID 的 ACE 只对受限 token 生效，两跳都未受限 → 合成 SID ACE 不约束任何人，真正起作用的是真实 SID 的 ACE。可整体清理（删 `get_synthetic_write_sid` / `SYNTHETIC_WRITE_SID_*` 等 dead code），但需确认无其他路径还用受限 token

---

## 21. 后续排查改动文件清单（补充 §12）

### jiuwenclaw（沙箱适配）
- [win_exec.py](../jiuwenbox/src/jiuwenbox/supervisor/win_exec.py)：后台 drain stdout 线程、本地落盘日志、exec 阶段日志、TEMP/profile 注入、`get_sandbox_profile_dir()`、`JIUWENBOX_INJECT_ENV` 约定键注入
- [win_wfp.py](../jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py)：Permit filter 放开整个 loopback（临时）
- [process.py](../jiuwenbox/src/jiuwenbox/server/runtime/process.py)：TEMP/profile 注入（第一跳 runner）
- [policy_reader.py](../jiuwenbox/src/jiuwenbox/server/policy_reader.py)：`_resolve_tool_paths` 自动检测 + `load_policy` 调用
- [windows-policy.yaml](../jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml)：tool_paths 清空硬编码、egress default=allow（临时）

### agent-core（调用方，跨项目改动，已授权）
- `openjiuwen/extensions/sys_operation/sandbox/providers/jiuwenbox.py`：`_collect_npmrc_env` + `execute_cmd`/`execute_code` 注入 `JIUWENBOX_INJECT_ENV`
- venv 副本 `.venv/Lib/site-packages/openjiuwen/.../jiuwenbox.py` 同步（非 editable install，需双写）

### 回退的无效改动
- ~~`bash -lc` → `bash -c`~~（根因不是 login shell）

---

## 22. 待收紧项（当前是临时验证状态）

1. **WFP 全放开 loopback**：jbx-sandbox 能访问本机任何本地服务，隔离性降。定稿收紧候选见 §15.6
2. **egress default=allow**：沙箱出网不限。待 skill 侧把 CDN 资源（`cdn.digitalhumanai.top` 等）打包/镜像固化后收紧为 `default:deny` + 完整白名单
3. **合成 SID dead code 清理**：受限 token 已废弃，合成 SID ACE 不生效，可整体清理（见 §20.3）
