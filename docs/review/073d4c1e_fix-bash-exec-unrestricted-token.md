# 代码审查报告 — commit 073d4c1e

- **Commit**: `073d4c1e0b924a3382aa96c6a7c6c992fb0ba9aa`
- **信息**: `fix: bash工具沙箱内执行成功，非受限token启动`
- **作者**: lby，2026-07-29
- **规模**: 9 文件，+493 / −133
- **审查侧重**: bash 工具沙箱内执行链路、非受限 token 启动方式变更、win_setup 同步机制、沙箱隔离有效性

---

## 一、概述

本次 commit 的核心目标是**让 bash（以及 cmd/python）工具能在 Windows 沙箱内成功执行**。为此作者放弃了原设计的 "Write-Restricted Token" 第二跳隔离，改为用 runner 自身的未受限 primary token 起 child；同时禁用了 Job Object 资源限制；并给 jbx-sandbox 真实 SID 在 allow_write 路径上授予了与合成 SID 等同的 Write 权限。配套补齐了 SystemRoot/TEMP/PATHEXT 等环境变量、tool_paths 展开的 PATH、python3→python 归一化、install 子进程命名 Event 同步、base64 编码 preinstall_paths 等工程性修复。

整体属于"先跑通、后加固"的调试期改动。注释极为详尽、可追溯性强，但**安全隔离在三层中退化了两层（受限 token、Job）**，剩余隔离主要依赖文件 ACL + WFP 出网管控。命令注入面有限但命令行构造仍有瑕疵。

---

## 二、变更范围

| 文件 | +/− | 性质 |
|---|---|---|
| `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py` | +302 | install 同步 Event、base64 编码、collect_preinstall_paths、自动 UAC 补预装、rmtree onerror |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | +132 | 复用 collect_preinstall_paths；**禁用 Job Object** |
| `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` | +71 | **新增 _get_runner_primary_token，exec 改用非受限 token**；env 兜底；python3 归一化 |
| `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py` | +55 | _build_windows_exec_env 接收 tool_paths；补 DLL 基础 env |
| `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` | +36 | read_acl_preinstall 清空（改由 tool_paths 展开合并） |
| `jiuwenbox/src/jiuwenbox/server/app.py` | +7 | lifespan 用 collect_preinstall_paths 统一集合 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_acl.py` | +15 | **真实 SID grant 从 FILE_GENERIC_READ 升级为 ALLOW_WRITE_RIGHTS** |
| `jiuwenbox/src/jiuwenbox/supervisor/win_constants.py` | 1 行 | `RESTRICTED_TOKEN_FLAGS` 去掉 `WRITE_RESTRICTED` |
| `jiuwenclaw/agentserver/agent_ws_server.py` | +6 | box-server health check 超时 30s → 120s |

---

## 三、关键变更分析

### 3.1 "非受限 token 启动" —— 这是否削弱了沙箱隔离？为何改？

**结论：是显著的隔离退化，但目前有可接受的工程理由，且补偿措施部分有效。**

原设计（文档 §6.5、`win_exec.py` 顶部 docstring 仍如此描述）是两跳：

1. **第一跳 broker→runner**：`CreateProcessWithLogonW("jbx-sandbox", ...)`，token 未受限，runner 跑在 jbx-sandbox 真实 SID 上下文。
2. **第二跳 runner→child**：runner 调 `CreateRestrictedToken(WRITE_RESTRICTED, restricting=[Everyone, Logon, JHXSandboxWrite])`，再用该受限 token `CreateProcessAsUserW` 起用户代码 child。`WRITE_RESTRICTED` 让 child 的写操作除常规 DACL 外再过一遍 restricting SID 双重检查——这是写控制的关键一环。

本次改动把第二跳的受限 token **整体弃用**（不是只去 `WRITE_RESTRICTED` 一个 flag，而是 exec 路径直接改用 `_get_runner_primary_token()` 拿 runner 自身未受限 token 起 child）：

`win_exec.py:760-785` `_get_runner_primary_token()` 注释自述：
> "受限 token (CreateRestrictedToken) 会让任何 child 进程启动即 0xC0000142 (DllMain 失败, 实测 cmd/bash/python 全挂)... 故 exec 改用 runner 自身的未受限 token 起 child... 代价: 失去 Write-Restricted 双重写检查, 写控制只剩合成 SID 的 ACL (allow-only 仍挡越权写). 安全降一重"

`win_exec.py:1024-1036`（_handle_exec_request）：
```python
# 方案1: 不用受限 token (它让 child 启动即 0xC0000142), 改用 runner 自身 primary token (未受限). 见 _get_runner_primary_token 注释.
_self_token = _get_runner_primary_token()
try:
    pid, proc_handle = _create_process_as_user(
        _self_token, list(command), env, workdir, ...
    )
finally:
    _get_kernel32().CloseHandle(wintypes.HANDLE(_self_token))
```

同时 `win_constants.py:76` 把 `RESTRICTED_TOKEN_FLAGS` 也去掉了 `WRITE_RESTRICTED`：
```python
RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT  # 临时去掉 WRITE_RESTRICTED(0x8) 定位 0xC0000142
```
注意：runner_main 启动时**仍会调 `_create_restricted_token()`** 生成一个受限 token（`win_exec.py:1074`），但该 token **在 exec 路径里已不再被使用**——它被 `_get_runner_primary_token()` 取代。这是一处**遗留死代码 + 注释与实现不符**：模块顶部 docstring 仍写"第二跳用 CreateRestrictedToken"，`runner_main` 仍费力构造 restricted token 并日志化，实际 exec 用的是 `_self_token`。

#### 为何改（合理性）
- 实测受限 token 下 cmd/bash/python 启动即 `0xC0000142 STATUS_DLL_INIT_FAILED`，根因是受限 token 的 desktop/全局对象访问机制导致 msys-2.0.dll 等 runtime 初始化失败，不是 ACL 或 env 能修的。这是 Windows 受限 token 的固有摩擦，作者试过补 SystemRoot/TEMP 等 env 仍救不回来。
- bash 是用户代码执行的核心载体（agent-core 跨平台送 `bash` 裸名），跑不起来 = 沙箱不可用。优先跑通属合理。

#### 补偿措施是否有效
| 隔离层 | 改动前 | 改动后 | 评价 |
|---|---|---|---|
| 受限 token（第二跳） | Write-Restricted 双重写检查 | **弃用** | 🔴 写控制降一重 |
| Job Object 资源限制 | memory/cpu/进程数上限 | **禁用**（process.py 整段注释掉，仅 resume） | 🔴 无资源围栏 |
| 文件 ACL（合成 SID） | allow_write 授权 Write，deny_write 拒绝 | **保留**（win_acl 仍施加） | 🟢 写面仍受 ACL 约束 |
| 真实 SID ACL | allow_write 只 grant Read | **升级为 ALLOW_WRITE_RIGHTS（Write+Execute+Delete）** | 🟡 runner 可写 allow_write，见 3.2 |
| WFP 出网管控 | Permit 代理端口范围 + 拦截其余出网 | 未改 | 🟢 网络面仍受控 |
| 独立用户身份 | jbx-sandbox 独立低权用户 | 未改 | 🟢 身份隔离仍在 |
| `CREATE_NO_WINDOW` / `CREATE_NEW_PROCESS_GROUP` | 有 | 有 | 🟢 无控制台、不可 Ctrl-C 波及 |

**剩余隔离有效性判断**：放弃受限 token 后，写控制从"双重检查（ACL + Write-Restricted）"降为"单重 ACL 检查"。由于合成 SID 的 ACL 仍是 allow-only 模型（allow_write 之外默认 deny），**对 allow_write 路径以外的越权写仍能挡**；但 child 现在跑的是 runner 的未受限 token（jbx-sandbox 真实 SID 全权），而 3.2 又给真实 SID grant 了 Write——意味着 child 进程对 allow_write 路径有完整写权，且没有 Write-Restricted 二次校验。**如果 allow_write 配置过宽或 ACL 施加有遗漏路径，child 可写到那些路径**。网络面 WFP 仍在，是当前最可靠的一道。Job 禁用后无内存/进程数/CPU 围栏，恶意/失控 child 可耗尽宿主资源（fork bomb、内存炸弹）。

**总体：隔离从"纵深防御 3 层（token + ACL + Job）"降到"1.5 层（ACL + WFP）"，属于可运行的最低保障，但不再是设计文档声称的强隔离。应在能复现 0xC0000142 根因后尽快回退到受限 token。**

---

### 3.2 win_acl.py：真实 SID 从 Read 升级为 Write

`win_acl.py:309-322`（apply_sandbox_acl，allow_write 分支）：
```python
# 第一跳 runner 进程用 jbx-sandbox 真实 SID、token 未受限
# (CreateProcessWithLogonW 拉起), 合成 SID 的 ACE 对它不生效. runner 负责
# upload 文件进沙箱 (AGENT.md/SOUL.md/产物等), 是写操作 — 真实 SID 必须
# 有 Write 才能写 allow_write 路径...
if sandbox_user_sid:
    grant_ace(
        expanded, sandbox_user_sid,
        rights=const.ALLOW_WRITE_RIGHTS,   # 旧: const.FILE_GENERIC_READ
        mode="ALLOW",
        recursive=recursive,
    )
```

- **合理性**：runner 要 upload AGENT.md/SOUL.md/产物进 workspace，旧版只给真实 SID Read 导致 upload 全部 EACCES，这是真问题。
- **风险**：注释声称"真正执行用户代码的是 runner 用受限 token 起的 child（第二跳），那层仍受合成 SID 双重 ACL 检查约束"——但**这一前提在 3.1 已被打破**：child 现在用 runner 自身未受限 token，不是受限 token，没有"双重 ACL 检查"。注释的安全论证与实际实现脱节。child 与 runner 共享同一个 jbx-sandbox 真实 SID，child 对 allow_write 路径享有与 runner 相同的 Write 权限。这在 allow_write=workspace 时可接受，但**若 policy 把 allow_write 配成多个外部目录，child 可写所有这些目录**。🟡

---

### 3.3 Job Object 禁用

`process.py:3031-3081` 把整个 Job 创建/assign 逻辑注释掉，只保留 `resume_process`。注释自述原因：跨用户 `OpenProcess(jbx-sandbox 进程)` 拿不到 `PROCESS_SET_QUOTA` → WinError 5。

- **风险**：沙箱内进程无内存/CPU/进程数上限。fork bomb 或内存炸弹可影响宿主。注释指出后续解法是用 `two_hop_spawn` 返回的 `proc_handle` 直接 assign（绕过 OpenProcess），这是正确方向，应尽快实施。🔴
- 注释保留了恢复路径，便于回退，这是优点。

---

### 3.4 bash 执行链路（命令行构造、cwd、环境、exit code）

#### 命令行构造 —— 存在引号注入面
`win_exec.py:838-854`（_create_process_as_user）：
```python
cmd_line = " ".join(
    f'"{c}"' if " " in c or "\t" in c else c for c in command
)
```
这是经典的 **Windows 命令行引号拼接**，只在含空格/制表符时加外层双引号，**不转义参数内部的双引号、反斜杠、% 等**。`CreateProcessAsUserW` 的 lpCommandLine 会被 child 的 CRT 按 Microsoft CRT 规则二次解析，内部含 `"` 的参数会被拆分/闭合。

- command 来自 agent-core（经 daemon IPC JSON 帧传入 header["command"]），是 list[str]，不是 shell 字符串，**不经过 cmd.exe 解析**，因此没有经典 shell 元注入（`;` `&` `|`）。这点 🟢。
- 但若某个参数值含 `"` 或空格，拼接后 child 端 argv 会错位。典型场景：bash `-c "脚本含双引号"`。当前归一化只动 command[0]，其余参数原样塞进 cmd_line。🟡
- `_quote_arg`（win_setup.py:749）用于 install 命令行，逻辑同样简陋（只判断空格/制表符加外层引号，不转义内部引号）。本次已用 base64 规避了 preinstall-paths 的引号问题，是正确做法。

#### cwd
`win_exec.py:989` `cwd = workdir if workdir else None`，workdir 来自 header["workdir"]，由 sandbox_manager 传入（request.workdir）。未做存在性/合法性校验。workdir 若指向 allow_write 之外或不存在路径，CreateProcessAsUserW 会失败或落到系统目录。🟡 应校验 workdir 在 workspace 子树内且存在。

#### 环境
- `sandbox_manager.py` 的 `_build_windows_exec_env` 和 `win_exec.py` 的 `_create_process_as_user` 两处都补 SystemRoot/windir/TEMP/TMP/PATHEXT/COMSPEC，用 setdefault 不覆盖。🟢
- 自动注入 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 指向代理端口，NO_PROXY 放行 loopback。🟢 与 WFP 双保险。
- `JIUWENBOX_INJECT_ENV` 约定键：从 env 解析 JSON 后 setdefault 注入，再删除该键不泄漏给 child。🟢 设计干净，解析失败只 warning 不阻断。
- TEMP 指向 `<profile>\AppData\Local\Temp\jiuwenbox\<sandbox_id>`，每沙箱隔离，降级回落 workspace/.tmp。🟢

#### exit code
`_handle_exec_request` 用带超时循环 WaitForSingleObject 等 child 退出，超时强杀，再 drain stdout（先 wait 后 read 避免 pipe 死锁）。🟢 逻辑周全，注释说明了为何不能先 read 后 wait。

#### python3 归一化
`win_exec.py:992-1000`：把裸名 `python3`/`python3.13` 等归一为 `python`，只改 command[0]，不碰带路径的。🟢 合理的 Windows 兼容。硬编码版本号列表略糙，但 `startswith("python3.")` 兜底了未来版本。

---

### 3.5 win_setup +302 行新增逻辑

#### install 同步 Event（核心新增）
`win_setup.py:617-735`：主进程 `CreateEventW`（非信号、自动复位）→ 名字经 `--install-done-event` 传给 install 子进程 → `ShellExecuteW("runas")` 弹 UAC 拉起子进程 → 主进程 `WaitForSingleObject(event, INFINITE)` 阻塞 → 子进程 install() 跑完 SetEvent。`_make_install_done_notifier`（win_setup.py:1244-1283）在 install 子进程端 OpenEvent + SetEvent，用 finally 保证异常也通知。

- 🟢 设计正确，解决了旧版 ShellExecuteW 异步返回导致主进程读到半成品状态（密码未就绪 → CreateProcessWithLogonW 1326）的根因。
- 🟡 **死锁/永久阻塞风险**：`WaitForSingleObject(event, INFINITE)`。若 install 子进程在 SetEvent 前崩溃（非 raise 路径，如段错误/被强杀），Event 永不 set，主进程永久阻塞。注释自承"极端: install 中途崩溃, Event 永不 set → 本进程永久阻塞. 加超时兜底"，**但代码里并没有加超时**——INFINITE 是 `0xFFFFFFFF`。agent_ws_server.py 把 health check 超时提到 120s 只是 box-server 进程被外部杀的兜底，不是 install 等待的超时。应改 `WaitForSingleObject(event, 120000)` 超时后降级。🔴
- 🟡 **Event 安全性**：`CreateEventW(None, False, False, event_name)` 用 `lpSecurityAttributes=None`，名字在 `Global\` 命名空间。注释说"默认 ACL 允许同会话/管理员子进程打开"。`Global\` 对象默认 ACL 实际是 Administrators + LocalSystem + 当前会话的 Everyone（取决于会话），**同会话任意进程可 OpenEvent 并 SetEvent**，恶意本地进程可伪造 install 完成信号让主进程误判 install 完成。名字虽含 `secrets.token_hex(8)` 随机不可猜，但若同会话有恶意进程枚举 Global 命名对象 + 暴力 SetEvent 仍有面。低危但应注明。🟡 建议改 `Local\` 命名空间 + 自定义 SECURITY_ATTRIBUTES 限当前进程 SID。

#### base64 编码 preinstall_paths
`win_setup.py:670-678`：`base64.b64encode(json.dumps(preinstall_paths))`，子进程 `base64.b64decode` 还原。🟢 彻底避开 Windows 命令行引号/空格转义问题，注释详述了为何不能直接传 json.dumps（内层双引号被命令行解析当闭合）。正确且优雅。

#### collect_preinstall_paths
`win_setup.py:992-1033`：统一收集 read_acl_preinstall + tool_paths 展开目录，lifespan（app.py）和 _create_windows（process.py）两处用同一函数，保证 install 记录的 REG_VALUE_PREINSTALLED_PATHS 与后续比对集合一致，避免每次创建沙箱都因 tool_paths"新增"弹 UAC。🟢 工程质量高，消除了重复逻辑。

#### 自动 UAC 补预装
`win_setup.py:1073-1086`：检测到新增预装路径时自动 `_elevate_and_run_install(force=True, ...)`，不再让用户手动跑 `--install --force`。🟢 适配终端产品（relay-claw/officeace）无法让用户手跑 CLI 的场景。但**每次检测到新路径都会弹 UAC**，若 policy 频繁变更 tool_paths 会反复弹窗，应有节流/记忆。

#### _create_sandbox_user 不再重设密码
`win_setup.py:355-361`：用户已存在（ret=2224）时跳过重设，理由是密码固定"000000"，重设反而撞密码复杂度策略 ret=87。🟡 密码固定"000000"是调试期临时方案（`_generate_password` 注释自承"调试阶段固定"），**生产前必须改随机密码**。当前 jbx-sandbox 是本地用户，密码"000000"易被猜中登录，虽有"从登录界面隐藏"但 RDP/RunAs 仍可用。🔴

#### _purge_stale_profile_dirs onerror
`win_setup.py:522-570`：rmtree 加 onerror 回调，对失败文件 chmod 重试，仍失败 warning 跳过不中止，处理 WinX reparse point 系统锁定。🟢 健壮性提升，注释清晰。

---

### 3.6 sandbox_manager 改动

`_build_windows_exec_env` 新增 tool_paths 参数，PATH 前置 tool_paths 展开目录（git_dir + usr/bin + bin + node_dir + python_dir + bash_path 父目录），与 _create_windows 给 runner 的 PATH 一致。🟢 修正了旧版写死 `%ProgramFiles%\Git\bin` 在 Git 装在 D:\Files\Git 时解析失败的问题。logger 从 debug 升 info 便于排查。🟢

---

## 四、关键代码检视

| 位置 | 评级 | 说明 |
|---|---|---|
| `win_exec.py:760-785` `_get_runner_primary_token` | 🔴 | exec 改用未受限 token，放弃 Write-Restricted 双重写检查，隔离降一重。临时方案应有回退计划。 |
| `win_exec.py:1074` `restricted_token = _create_restricted_token()` | 🟡 | runner 仍费力构造受限 token 并日志化，但 exec 路径已不用它（被 `_self_token` 取代）。死代码 + docstring/注释与实现不符。 |
| `win_constants.py:76` `RESTRICTED_TOKEN_FLAGS` | 🟡 | 去掉 WRITE_RESTRICTED，注释写"临时...定位 0xC0000142"。受限 token 已不被 exec 使用，此 flag 实际无效，但留着会误导读者以为还在用受限 token。 |
| `process.py:3031-3081` Job Object 禁用 | 🔴 | 沙箱无资源围栏，fork bomb/内存炸弹可耗尽宿主。注释给了恢复方向（用 proc_handle 直接 assign）。 |
| `win_acl.py:309-322` 真实 SID grant Write | 🟡 | 注释安全论证（"child 仍受双重 ACL 约束"）前提已被 3.1 打破，child 现用未受限 token。allow_write 配置过宽时 child 可写。 |
| `win_setup.py:735` `WaitForSingleObject(event, INFINITE)` | 🔴 | install 子进程崩溃则主进程永久阻塞，无超时兜底。注释承诺加超时但代码未实现。 |
| `win_setup.py:340` `_generate_password` 返回 "000000" | 🔴 | 调试期固定弱密码，生产前必须改随机。 |
| `win_exec.py:838-854` cmd_line 拼接 | 🟡 | 仅按空格/制表符加外层引号，不转义内部 `"`，参数含双引号会 argv 错位。不经 shell 无元注入，但复杂参数仍脆弱。 |
| `win_setup.py:749-752` `_quote_arg` | 🟡 | 同上简陋逻辑。已用 base64 规避 preinstall-paths，但其他参数仍走此函数。 |
| `win_exec.py:989` `cwd = workdir if workdir else None` | 🟡 | workdir 未校验在 workspace 子树内/存在性。 |
| `win_setup.py:992-1033` `collect_preinstall_paths` | 🟢 | 统一预装集合来源，消除重复，幂等性好。 |
| `win_setup.py:670-678` base64 编码 preinstall-paths | 🟢 | 正确避开命令行转义，注释优秀。 |
| `win_exec.py:992-1000` python3 归一化 | 🟢 | 合理 Windows 兼容，只动裸名 command[0]。 |
| `sandbox_manager.py` `_build_windows_exec_env` tool_paths + DLL env | 🟢 | PATH 与预装 ACL 对齐，DLL env 兜底，与 _create_windows 一致。 |
| `win_exec.py:921-930` 代理 env 自动注入 | 🟢 | 与 WFP 双保险，NO_PROXY 放行 loopback 正确。 |
| `win_setup.py:522-570` rmtree onerror | 🟢 | 健壮处理 WinX 锁定，不中止清理。 |
| `agent_ws_server.py:417` timeout=120.0 | 🟢 | 给 install+UAC 足够时间，避免 health check 误杀。 |

---

## 五、优点

1. **注释极其详尽**：几乎每处改动都有"为什么改、实测现象、根因、权衡、回退方向"的完整叙述，可追溯性极强，这在调试期 commit 中罕见。
2. **install 同步机制设计正确**：命名 Event + base64 编码 + finally 通知，彻底解决了旧版异步返回导致的半成品状态问题，是本次最扎实的工程改进。
3. **collect_preinstall_paths 统一集合来源**：消除了 lifespan 与 _create_windows 两处重复展开 tool_paths 的逻辑，避免集合不一致导致反复弹 UAC。
4. **env 兜底分层防御**：sandbox_manager 和 win_exec 两处都补 DLL 基础 env，代理 env 与 WFP 双保险，TEMP 每沙箱隔离。
5. **base64 规避命令行转义**：比试图正确转义 JSON 内层引号更稳健。
6. **rmtree onerror**：务实处理系统锁定文件，不阻断整体清理。

---

## 六、问题与风险

### 🔴 高危

1. **受限 token 弃用导致写控制降一重**：exec 用 runner 未受限 primary token，child 与 runner 共享 jbx-sandbox 真实 SID 全权，无 Write-Restricted 二次校验。allow_write 之外的越权写仍受 ACL 挡，但 allow_write 内 child 可任意写，且若 ACL 施加遗漏路径则可越界。应作为已知技术债，尽快排查 0xC0000142 根因后回退。
2. **Job Object 禁用**：沙箱进程无内存/CPU/进程数围栏，恶意代码可 fork bomb 或内存炸弹耗尽宿主。
3. **install 等待无超时**：`WaitForSingleObject(INFINITE)` 在 install 子进程崩溃时永久阻塞主进程，注释承诺的超时兜底未实现。
4. **密码固定 "000000"**：jbx-sandbox 用户弱密码，生产前必须改随机。

### 🟡 中危

5. **注释与实现脱节**：win_exec.py docstring + runner_main 仍声称用受限 token 第二跳，实际已改用未受限 token。win_constants.py 的 RESTRICTED_TOKEN_FLAGS 去掉 WRITE_RESTRICTED 但受限 token 根本不被使用。会误导后续维护者。
6. **真实 SID grant Write 的安全论证失效**：win_acl.py 注释称 child 受双重 ACL 约束，前提已被打破。
7. **命令行拼接不转义内部引号**：参数含 `"` 时 argv 错位，复杂 bash -c 脚本可能失败。
8. **workdir 无校验**：未限制在 workspace 子树内。
9. **Global 命名 Event**：同会话恶意进程可伪造 install 完成信号（低危，名字随机但仍可枚举）。
10. **自动 UAC 补预装无节流**：policy 频繁变更 tool_paths 会反复弹窗。

---

## 七、改进建议

按优先级排序：

1. **【P0】给 install 等待加超时**：`WaitForSingleObject(event, 120_000)`，超时后降级 warning 并让上层靠 installed 标记自行判断。对应 win_setup.py:735。注释已承诺，补齐即可。
2. **【P0】密码改随机**：`_generate_password` 改 `secrets.token_urlsafe(24)`，存注册表 DPAPI，uninstall 删用户。对应 win_setup.py:340。`_create_sandbox_user` 已存在不重设的逻辑需配合调整（首次安装即记密码）。
3. **【P0】排查 0xC0000142 根因，回退受限 token**：受限 token 弃用是整个 commit 最大的安全债。0xC0000142 STATUS_DLL_INIT_FAILED 通常因受限 token 无法访问 desktop/window station 或 KnownDlls。可尝试：a) 给 jbx-sandbox 用户 grant `SE_CHANGE_NAME_PRIVILEGE` 或加入 Interactive Desktop；b) `CreateRestrictedToken` 时保留 `SANDBOX_INERT` 但去 `WRITE_RESTRICTED`（当前 win_constants 已这么改，但 exec 没用它）；c) 用 `CreateProcessAsUserW` + `STARTUPINFO` 指定 `lpDesktop`；d) 考虑 AppContainer/SID-based isolation 替代 Write-Restricted。
4. **【P1】恢复 Job Object**：用 `two_hop_spawn` 返回的 `proc_handle` 直接 `AssignProcessToJobObject`（绕过跨用户 OpenProcess），注释已指明方向。对应 process.py:3031。
5. **【P1】清理死代码 + 修正注释**：runner_main 不再构造受限 token（或保留但明确标注"仅诊断用"），win_constants.py RESTRICTED_TOKEN_FLAGS 注明 exec 已弃用，win_exec.py docstring 更新为"第二跳用 runner primary token（临时）"。
6. **【P1】命令行拼接改用 `subprocess.list2cmdline`**：它正确转义内部引号和反斜杠，比手写 `f'"{c}"'` 稳健。对应 win_exec.py:838、win_setup.py:749。
7. **【P2】workdir 校验**：在 _handle_exec_request 校验 workdir 为 workspace 子树内绝对路径且存在，否则回落 workspace。
8. **【P2】Event 改 Local 命名空间 + 自定义 ACL**：限制仅当前进程 SID 可 SetEvent。
9. **【P2】自动 UAC 补预装节流**：注册表记录"已提示过的新路径"，避免同一路径反复弹窗。

---

## 八、小结

本次 commit 是一次目的明确的"让 bash 跑起来"的调试期攻坚，工程细节（Event 同步、base64 编码、collect_preinstall_paths、env 兜底、rmtree onerror）质量很高，注释可追溯性极强。但核心安全决策——**放弃受限 token、禁用 Job、给真实 SID grant Write**——使沙箱从设计的"token + ACL + Job 三层纵深防御"退化为"ACL + WFP 1.5 层"，写控制与资源围栏均出现缺口。命令注入面因不经 shell 而有限，但命令行拼接仍不转义内部引号。

**必须在生产前完成的最低修复**：install 等待加超时、密码改随机、Job 恢复、受限 token 回退或替代方案落地。建议将本次改动视为"可运行的临时态"，在 issue 跟踪中记录技术债并设回退里程碑。

---

*审查人：资深 Windows 系统/安全工程代码审查员*
*审查日期：2026-08-01*
