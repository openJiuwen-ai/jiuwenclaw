# 代码审查报告：d15fcf8e fix:修box启动

> 审查人：资深 Windows 系统工程代码审查员
> 审查日期：2026-08-01
> Commit：d15fcf8ea6be5b5237dc37a6816a11c25dec8d95（lby，2026-07-27）
> 变更：10 文件 +2502 / -27（其中约 2480 行为 docs/ 下设计文档，代码改动 win_acl.py +11、win_exec.py +122、agent_ws_server.py +18）

---

## 一、概述

本 commit 标题 "fix:修box启动"，目标是修复 Windows 沙箱 runner（jbx-sandbox 身份的第一跳进程）起不来的问题。代码改动分三块：

1. **win_exec.py（+122）**：修复 `_create_restricted_token` 中 ctypes 结构体布局与 SID buffer 生命周期的多个真实 bug；改 `_build_runner_command` 用标准 CPython 替代 uv trampoline。
2. **win_acl.py（+11）**：给 jbx-sandbox 真实 SID 在 allow_write 路径上 grant `FILE_GENERIC_READ`，让第一跳 runner 能读到只授合成 SID 的路径。
3. **agent_ws_server.py（+18）**：探测系统 CPython 路径注入 `JIUWENBOX_RUNNER_PYTHON` env。

**重要校正**：任务描述推测 "win_exec +122 行很可能是 exec socket 执行通道"。实际**不是**。exec socket（stdin/stdout pipe → TCP loopback control_port）的改造在更早 commit 已完成（`docs/windows_sandbox_exec_socket_design.md` 日期 2026-07-25，本 commit 2026-07-27）。本 commit 的 win_exec +122 行是 SID 结构体/指针生命周期修复 + runner python 解析改动，不涉及 socket 传输层。当前 win_exec.py 文件全文里的 TCP loopback socket（`runner_main` bind/listen 127.0.0.1:control_port）是既有代码，非本 commit 引入。

---

## 二、变更范围

| 文件 | 增删 | 性质 |
|---|---|---|
| `docs/windows_sandbox_exec_socket_design.md` | +199 | 设计文档（exec socket 改造，已完成） |
| `docs/windows_sandbox_runner_python_acl_design.md` | +210 | 设计文档（runner python + ACL 修复，本 commit 对应） |
| `docs/windows_sandbox_review_fix_design.md` | +186 | 设计文档（review CRITICAL/MAJOR 修复） |
| `docs/windows_sandbox_bk_unify_design.md` | +140 | 设计文档（Windows 对齐 BK yaml 驱动） |
| `docs/windows_sandbox_officeace_integration_design.md` | +502 | 设计文档（接入 officeAce 产品） |
| `docs/windows_sandbox_review_fix_steps.md` | +76 | 设计文档（修复步骤） |
| `docs/window沙箱.md` | +1065 | 总体技术分析文档 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_acl.py` | +11 | 代码（真实 SID grant Read） |
| `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` | +122/-... | 代码（SID 结构体修复 + runner python） |
| `jiuwenclaw/agentserver/agent_ws_server.py` | +18 | 代码（CPython 路径探测注入） |

---

## 三、设计文档概述

六份设计文档 + 一份总文档，质量较高，根因链清晰：

- **exec_socket_design.md**（2026-07-25）：exec 传输层从 anonymous pipe 改 TCP loopback socket 的设计。根因：`daemon_ipc.send_frame` 用 `sock.sendall()`，pipe 的 BufferedWriter 无此方法；且 pipe 半关闭语义与多 exec 串行模型耦合过深。改为 box-server 分配 control_port，runner bind+listen，box-server 每次 exec connect。对齐 Linux AF_UNIX listener 模型。
- **runner_python_acl_design.md**（2026-07-27，本 commit 对应）：四根因因果链 —— (1) `{{ workspace }}` 占位不展开；(2) ACL 整批跳过；(3) runner python 是 uv trampoline；(4) 合成 SID ACE 对第一跳真实 SID runner 无效。本 commit 修的是 #3 和 #4。
- **review_fix_design.md**：review 发现的 5 个 CRITICAL（C1-C5）+ 10 个 MAJOR（M1-M10）问题清单与根因，含 SDK 权威依据。C1（合成 SID 两处不一致）、C3（环境块悬垂指针）正是本 commit win_exec 改动针对的。
- **bk_unify_design.md**：把 Windows 的 env 注入 + agent_id 归属对齐到 BK 的 yaml 驱动 + project_dir 摘要归属，消除 env 注入失败、isolation 冲突。
- **officeace_integration_design.md**：把 box-server 接进 officeAce 桌面产品的拉起层（G1 拉起层 + G0/G2/G3 执行层）。
- **window沙箱.md**（1065 行）：总体技术分析，含 Windows 安全基础（Token/SID/ACL/Job/WFP）与移植方案，第 6 章是执行层设计。

文档优点：每个根因都有实测日志佐证（如 `WinError 998`、`os error 5`、`0xC0000142`），非推测；改动手顺与 SDK 依据并列。缺点：6 份文档共 2480 行信息密度可压缩，部分内容跨文档重复（如 SID 一致性在 review_fix 与 runner_python_acl 两处都讲）。

---

## 四、代码改动分析

### 4.1 win_exec.py — SID 结构体与指针生命周期修复（核心，正确性高）

**`_SID_AND_ATTRIBUTES` 新增结构体** 🟢 `win_exec.py:248-255`
```python
class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]
```
正确。Win32 `SID_AND_ATTRIBUTES` 布局就是 `{PVOID Sid; DWORD Attributes}`。旧版用 `c_void_p*3` 传给 `c_void_p` 参数，ctypes marshal 错指针 → WinError 998。模块级定义供 argtypes 引用，合理。

**`_TOKEN_GROUPS.Groups` 字段对齐修复** 🟢 `win_exec.py:258-268`
```python
_fields_ = [
    ("GroupCount", wintypes.DWORD),
    ("Groups", _SID_AND_ATTRIBUTES * 1),  # 变长, 实际按 count 重新 cast
]
```
这是关键修复。旧版 `c_byte*0` 对齐 1 → `Groups.offset=4`；64 位下 `SID_AND_ATTRIBUTES` 对齐 8，ctypes 自动给 `GroupCount`(DWORD,4B) 后加 4 字节 padding → `Groups.offset=8`，与 Win32 `TOKEN_GROUPS` 实际布局一致。旧版手动加 `sizeof(DWORD)` 在 64 位漏 padding → 读错位 → logon_sid 垃圾值 → WinError 998。改用 `_SID_AND_ATTRIBUTES*1` 让 ctypes 自动算 offset，正确。

**`CreateRestrictedToken` argtypes 修正** 🟢 `win_exec.py:296`
```python
wintypes.DWORD, ctypes.POINTER(_SID_AND_ATTRIBUTES),  # restricting sids
```
SDK 原型 `CreateRestrictedToken` 的 restricting sids 参数是 `PSID_AND_ATTRIBUTES`（指向结构数组的指针），旧版声明 `c_void_p` 与实际传 `c_void_p*3` 数组不匹配。改正确。

**`AllocateAndInitializeSid` argtypes 补全参数** 🟢 `win_exec.py:316-321`
旧版 argtypes 少了一个 DWORD（SDK 有 8 个 sub-authority 参数位），本 commit 补齐。避免 ctypes 按错误签名 marshal。

**`_create_restricted_token` 内联构造 SID buffer 并持引用** 🟢 `win_exec.py:676-737`
这是本 commit 最核心的修复。旧版调用 `_get_everyone_sid()` / `_get_logon_session_sid()` / `_get_synthetic_write_sid_ptr()` 三个 helper，前两者返回的指针指向 Python 管理的 `c_byte` buffer，函数返回后 buffer 被 GC → 悬垂指针，`CreateRestrictedToken` 读已释放内存 → WinError 998。新版内联构造 `everyone_buf` / `logon_buf` / `write_sid_ptr` 并在 try 块内持有引用直到 `CreateRestrictedToken` 返回。正确解决悬垂指针。同时用 `_TOKEN_GROUPS.Groups.offset`（ctypes 自动算含 padding）代替旧版手动 `sizeof(DWORD)`，修 64 位对齐。

**`_build_runner_command` 改用标准 CPython** 🟢 `win_exec.py:417-419`
```python
py = (os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip()
if not py or not os.path.isfile(py):
    py = sys.executable or "python"
```
旧版用 `JIUWENBOX_VENV_DIR/Scripts/python.exe` 或 `sys.executable`，部署环境 box-server 跑在 uv trampoline 上，jbx-sandbox 对 trampoline 及其依赖路径无读/执行权限 → trampoline spawn child 时 `permission denied (os error 5)` → runner 起不来。改用 `JIUWENBOX_RUNNER_PYTHON` env 指向自包含标准 CPython，grant RX 到安装目录即可跑。优先级合理（env 显式 > sys.executable 兜底）。

**d15fcf8 版本 `_create_restricted_token` 的 logon_sid 防御缺失** 🟡 `win_exec.py:711-719`（当前文件已是后续 commit 修复版）
本 commit d15fcf8 的 diff 版本里，`restricting = (_SID_AND_ATTRIBUTES * 3)(everyone, logon_sid_val, write_sid_ptr)` 硬编码 3 个元素，**假设 `logon_sid_val` 一定非 None**。若 `count==0` 或无 `SE_GROUP_LOGON_ID` 组导致 `logon_sid_val is None`，则 `_SID_AND_ATTRIBUTES(None, 0)` 把 NULL 塞进 restricting 数组 → `CreateRestrictedToken` 返回 WinError 87。当前工作区文件（后续 commit）已改为 `entries` 动态列表 + `if logon_sid_val is not None` 防御。即本 commit 在正常路径（logon session SID 一般能拿到）下可跑，但边界路径有隐患，后续 commit 补全。**不算阻断，标黄**。

### 4.2 win_acl.py — 真实 SID grant Read（不充分，后续补 Write）

**allow_write 块给真实 SID grant `FILE_GENERIC_READ`** 🟡 `win_acl.py:317-332`（d15fcf8 diff 加的是 `FILE_GENERIC_READ`，当前文件已升级为 `ALLOW_WRITE_RIGHTS`）
d15fcf8 这个 commit 在 allow_write 循环里新增：对每个 allow_write 路径，除给合成 SID grant `ALLOW_WRITE_RIGHTS` 外，还给 `sandbox_user_sid`（真实 jbx-sandbox SID）grant `FILE_GENERIC_READ`。注释明言 "Write 仍只给合成 SID (受限 token 第二跳才写), 真实 SID 能读能执行不能写"。

这解决了根因 #4（合成 SID ACE 对第一跳真实 SID runner 无效，runner 读不了 venv python → CreateProcessWithLogonW WinError 5）。但**不充分**：runner 第一跳用真实 SID 还负责 upload 文件进沙箱（AGENT.md/SOUL.md/产物等），是写操作，真实 SID 没 Write → upload 会 `[Errno 13] Permission denied`。当前工作区文件已升级为 `ALLOW_WRITE_RIGHTS`（Write+Execute+Delete，和合成 SID 一致），证明本 commit 的 `FILE_GENERIC_READ` 是阶段性不充分修复。从 "修box启动" 目标看（runner 启动只需读+执行 venv python），本 commit 够用；但完整链路需后续 Write 补丁。**标黄**。

安全权衡注释（`win_acl.py:319-325`）写得清楚：runner 是 box-server 自己起的可信代理进程，给它写权限符合预期；真正执行用户代码的是第二跳受限 token child，仍受合成 SID 双重 ACL 约束。逻辑成立。

### 4.3 agent_ws_server.py — CPython 路径探测（硬编码 dev 机路径）

**探测候选 CPython 路径注入 env** 🟡 `agent_ws_server.py:357-366`
```python
for _cand in (
    r"D:\Files\python313\python.exe",  # dev 实测机
    r"C:\Python313\python.exe",
    r"C:\Python312\python.exe",
    str(Path(__file__).resolve().parents[2] / "tools" / "python" / "python.exe"),  # 打包
):
    if _cand and Path(_cand).is_file():
        os.environ["JIUWENBOX_RUNNER_PYTHON"] = _cand
        break
```
逻辑可行：优先用已注入 env（显式指定），否则按候选列表探测第一个存在的 CPython。问题：
- `D:\Files\python313\python.exe` 是 dev 实测机硬编码路径，泄漏到仓库代码里。生产环境用户的 Python 可能装在 `C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe`（官方安装器默认）或 `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`（Store 版），这两个常见路径不在候选列表里 → 探测失败 → 回退 `sys.executable`（可能又是 uv trampoline）→ runner 仍起不来。
- 候选列表写死 Python 3.13/3.12，未来 3.14+ 需手改代码。
- 建议改为：探测 `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`（glob）+ 查注册表 `HKCU\Software\Python\PythonCore\*\InstallPath` + PATH 里的 `python.exe`（且校验非 trampoline）。

**注入 env 的时序** 🟢 `agent_ws_server.py:357`
`if not (os.environ.get("JIUWENBOX_RUNNER_PYTHON") or "").strip()` —— 只在未显式设置时探测，允许外部覆盖。合理。

---

## 五、优点

1. **根因定位扎实**：每个改动都有实测日志佐证（WinError 998/5、os error 5、0xC0000142），非推测。ctypes 结构体布局问题（64 位 padding、变长数组对齐）是 Windows 系统编程的高频坑，本 commit 定位准确。
2. **ctypes 修复正确**：`_SID_AND_ATTRIBUTES` 结构体、`_TOKEN_GROUPS.Groups` 用 `_SID_AND_ATTRIBUTES*1` 让 ctypes 自动算 offset、`CreateRestrictedToken` argtypes 改 `POINTER(_SID_AND_ATTRIBUTES)`，均与 Win32 SDK 布局对齐。
3. **悬垂指针防护到位**：`_create_restricted_token` 内联构造 SID buffer 并持引用直到 `CreateRestrictedToken` 返回，并配详细注释解释 GC 时序，这是 ctypes 与 Python GC 交互的经典陷阱。
4. **错误处理与可观测性强**：`_push_log` 三路输出（日志长连回传 box-server + logger + 本地落盘 `runner.log`），`CreateProcessAsUserW` 失败时诊断 PATH/ACL/resolved 路径，单连接异常不杀 runner（`win_exec.py:1147-1172` try/except 包住每个 request handler）。这些是长期可维护性的保障。
5. **设计文档与实现一致**：`runner_python_acl_design.md` 的四根因（#3 runner python uv trampoline、#4 合成 SID ACE 对真实 SID 无效）与本 commit 的 win_exec（改 runner python）+ win_acl（给真实 SID grant）改动对应。`review_fix_design.md` 的 C1（SID 不一致）、C3（悬垂指针）正是 win_exec 改动针对的。

---

## 六、问题与风险

### 6.1 exec socket 无认证/鉴权（既有代码，非本 commit，但属本文件核心）🔴 `win_exec.py:1093-1107`

当前 win_exec.py 的 `runner_main` 在 jbx-sandbox 上下文 bind `127.0.0.1:control_port` 并 `listen(64)`，`accept()` 任何连接后直接 `recv_frame` 读 header 按 `type` 分发 `exec`/`write_file`/`read_file`/`list_dir`/`shutdown`/`subscribe_log`。**全程无认证/鉴权**：没有共享密钥、token 握手、连接方身份校验。任何能连到 `127.0.0.1:control_port` 的本机进程都能发 exec 请求，以 jbx-sandbox 身份（受限 token 或 runner primary token）起任意子命令、读写沙箱文件。

风险评估：
- 127.0.0.1 loopback 限制了只有本机能连，但同一台机器上可能有多个用户/进程。control_port 由 box-server 分配，端口号可能被扫描或猜测。
- 沙箱的隔离边界本应由 ACL + 受限 token 保证（即便有人连上发 exec，child 仍受限），但 `_handle_exec_request` 实际用 `_get_runner_primary_token()`（**未受限**的 runner 自身 token）起 child（`win_exec.py:1303-1312`，注释 "方案1：不用受限 token，它让 child 启动即 0xC0000142"）。这意味着能连上 control_port 的进程可以让 runner 以 jbx-sandbox **未受限**身份执行任意命令。这是越权执行风险。
- 严重度：🔴 高。建议加握手 token（box-server 分配 control_port 时同时分配随机 token，经命令行参数传 runner，box-server connect 后首帧带 token 校验，runner 校验失败 close）。

### 6.2 `_handle_exec_request` 用未受限 token 起 child（既有设计降级）🔴 `win_exec.py:1300-1312`

如上所述，`_create_restricted_token` 产出的受限 token 实际**没用于起 exec child**，child 用的是 `_get_runner_primary_token()`（runner 自身未受限 primary token）。代码注释解释：受限 token 让任何 child 启动即 `0xC0000142`（DllMain 失败，实测 cmd/bash/python 全挂，根因是受限 token 的 desktop/全局对象机制）。这是安全降一重：失去 Write-Restricted 双重写检查，写控制只剩合成 SID 的 ACL（allow-only 仍挡越权写）。

风险：第二跳 child 的写操作只受 ACL 约束，不受受限 token 的双重检查。若 ACL 配置有遗漏路径，child 可越权写。这与设计文档 §2.5 的"受限 token 双重保护"目标有偏差。属已知降级，文档有记录，但应明确标注这是当前安全模型的实际状态而非临时绕过。

### 6.3 d15fcf8 版本 `_create_restricted_token` 的 logon_sid 防御缺失 🟡 `win_exec.py:711-719`

见 4.1。d15fcf8 的 diff 版本硬编码 `(_SID_AND_ATTRIBUTES * 3)`，假设 logon_sid_val 非 None。边界路径（count==0 或无 LOGON_ID 组）会 NULL 塞进数组 → WinError 87。当前文件后续 commit 已补防御（entries 动态 + `if logon_sid_val is not None`）。本 commit 版本在正常路径可跑，边界路径有隐患。

### 6.4 win_acl 给真实 SID 只 grant Read 不够（阶段性不充分）🟡 `win_acl.py:317-332`

见 4.2。d15fcf8 给真实 SID `FILE_GENERIC_READ`，runner upload 写操作仍 permission denied。后续 commit 升级为 `ALLOW_WRITE_RIGHTS`。本 commit 够 "启动"，不够 "完整链路"。

### 6.5 agent_ws_server CPython 探测路径硬编码 dev 机 🟡 `agent_ws_server.py:357-366`

见 4.3。`D:\Files\python313` 是 dev 机路径，生产用户常见安装路径（`%LOCALAPPDATA%\Programs\Python`、Store 版、注册表注册路径）不在候选列表 → 探测失败回退 sys.executable → 仍可能是 uv trampoline。

### 6.6 `_get_logon_session_sid` / `_get_everyone_sid` / `_get_synthetic_write_sid_ptr` 仍存在（死代码？）🟢 `win_exec.py:559-647`

d15fcf8 把这三个 helper 的逻辑内联进 `_create_restricted_token` 后，这三个函数本身仍在文件里（`win_exec.py:559-647`）。若已无调用方，是死代码，应删或在 commit 里清理。若仍有其他调用方（如测试），保留无妨。当前未确认调用方，标绿（非阻断）。

### 6.7 文档体量过大，跨文档重复 🟢

6 份文档共 2480 行，部分内容跨文档重复（SID 一致性在 `review_fix_design` 与 `runner_python_acl` 两处都讲；officeAce_integration 与 review_fix 都讲 G0/G2/G3 ACL）。建议合并为单一 windows_sandbox.md 索引 + 各子文档去重。

---

## 七、改进建议

1. **🔴 exec socket 加认证**：box-server 分配 control_port 时同时生成随机 token（`secrets.token_bytes(32)`），经命令行参数传 runner（与 control_port 同传，避 env 块 WinError 87）。runner accept 后首帧校验 token，不符则 close。这堵住本机任意进程越权 exec 的风险。优先级最高。
2. **🔴 明确受限 token 降级为已知状态**：在 `win_exec.py` 模块 docstring 和设计文档里明确标注"exec child 实际用未受限 primary token，受限 token 仅作第二跳写 ACL 的合成 SID 授权载体"，避免后续维护者误以为有双重写保护。
3. **🟡 agent_ws_server CPython 探测增强**：候选列表加 `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`（glob）+ 注册表 `HKCU/HKLM\Software\Python\PythonCore\*\InstallPath` 查询 + PATH 里 `python.exe`（校验非 trampoline：`python -c "import sys; print(not sys.executable.lower().endswith('trampoline'))"`）。删 `D:\Files\python313` dev 机硬编码。
4. **🟡 删 win_acl.py 真实 SID grant 的 Read-only 残留注释**：当前文件已升级为 `ALLOW_WRITE_RIGHTS`，但 d15fcf8 引入时的注释（"Write 仍只给合成 SID"）若仍在某处保留会误导。确认注释与代码一致。
5. **🟢 清理死代码**：确认 `_get_logon_session_sid` / `_get_everyone_sid` / `_get_synthetic_write_sid_ptr` 是否仍有调用方，无则删。
6. **🟢 文档去重合并**：6 份文档合并为索引 + 子文档，跨文档重复内容（SID 一致性、G0/G2/G3 ACL）收敛到一处。

---

## 八、小结

本 commit 是一组**扎实的 Windows 系统编程 bug 修复**，核心价值在 win_exec.py 对 `CreateRestrictedToken` 相关 ctypes 结构体布局（64 位 padding、变长数组对齐、PSID_AND_ATTRIBUTES 指针签名）与 SID buffer 生命周期（悬垂指针 → WinError 998）的修复，均与 Win32 SDK 实际布局对齐，根因有实测日志佐证。win_acl 给真实 SID grant、agent_ws_server CPython 探测是配套的阶段性修复。

**主要风险不在本 commit 引入，而在既有 exec socket 设计**：control_port 无认证鉴权 + exec child 用未受限 token 起进程，构成本机越权执行路径（🔴）。本 commit 没改 socket 部分，但 socket 是 win_exec.py 的核心通道，审查必须覆盖。本 commit 自身的 🟡 项（logon_sid 防御缺失、真实 SID 只 grant Read、CPython 探测路径硬编码）均已在后续 commit 修复或属阶段性可接受范围。

总体评价：代码改动质量高，正确性与可观测性强；但 exec socket 的鉴权缺失是整个 Windows 沙箱链路的系统性安全债，需独立排期修复，不应停留在 "本 commit 没碰它" 的口径。
