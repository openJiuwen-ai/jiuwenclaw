# Commit 检视报告：c2c3f5f0 fix:review

## 一、概述

- Commit：c2c3f5f0c07b8382390848e400fd82c463ee09a7
- 日期：2026-07-21
- 作者：lby
- 说明：基线 `5f841f7a`（feat:window 沙箱）之后的首次 review 修复。
- 定位：紧接基线，修复审查发现的多类问题（IPC 复用、ACL ACE 顺序、Job 内存、WFP 端口范围、安装预装、stdin 透传、句柄继承）。
- 规模：8 文件，+526 / -162。

基线 `5f841f7a` 引入了 Windows 沙箱两跳启动（broker→runner→受限子进程）、ACL/WFP 隔离、Job 资源限制、IPC 帧协议等能力，但存在若干正确性与安全缺陷。本 commit 是对审查发现的批量修复，整体方向正确，但仍有几处遗留风险与一处明显 bug（见第六节）。

---

## 二、变更范围

| 文件 | 增/删 | 性质 | 修复主题 |
|------|------|------|---------|
| `server/app.py` | +6/-2 | 改动 | lifespan 把根 policy 的 `read_acl_preinstall` 透传给 `ensure_windows_setup` |
| `server/runtime/process.py` | +173/-65 | 改动 | runner pipe 持久化文件对象 + per-sandbox Lock 串行化 roundtrip；`_stop_windows` 资源释放顺序重写 |
| `supervisor/win_acl.py` | +82/-21 | 改动 | 新增 `_parse_getace_tuple` / `_rebuild_acl_with_order`；修正 Deny ACE 当 Allow 写回的 bug；`revoke_sandbox_acl` 分桶重建 |
| `supervisor/win_exec.py` | +55/-10 | 改动 | `SetHandleInformation` 关闭 box-server 端继承；exec 支持 stdin 透传 |
| `supervisor/win_job.py` | +7/-2 | 改动 | 内存上限加 `JOB_MEMORY` + `ProcessMemory` 双保险，设 `JobMemoryLimit` |
| `supervisor/win_setup.py` | +92/-15 | 改动 | `install` 接受 `preinstall_paths`；预装线程 `join` 等待；隐藏登录界面用户 |
| `supervisor/win_wfp.py` | +60/-27 | 改动 | 端口范围改为"每端口一个 Permit filter"；`uninstall` 按范围遍历删除；抽 `_delete_filter_by_key` |
| `tests/integration/test_server_api_windows.py` | +71/-0 | 新增 | mock `_osfhandle_to_fd`/`fdopen`；3 个 ACL 解析/重建单测 |

---

## 三、修复内容逐项分析

### 1. runner pipe 持久化 + per-sandbox Lock（process.py）

**原问题**：基线 `_win_runner_roundtrip` 每次调用都 `_osfhandle_to_fd` + `os.fdopen` 同一对 pipe HANDLE，并在 roundtrip 结束 `with` 关闭文件对象。`msvcrt.open_osfhandle` 对同一 HANDLE 二次包装会失败（或行为未定义），导致沙箱只能 exec 一次。另外并发 roundtrip 会让请求帧在 stdin 上交错。

**修复方式**：
- `_create_windows` 创建时一次性 `open_osfhandle` + `fdopen`，把 `stdin_wf`/`stdout_rf` 存进 `_win_runners[sandbox_id]`，后续所有 roundtrip 复用（process.py:2805-2820）。
- 新增 `_win_pipe_locks: dict[str, asyncio.Lock]`，`_win_pipe_lock()` 懒创建 per-sandbox 锁；`_win_runner_roundtrip` / `_with_body` / `_send_runner_shutdown` 均 `async with lock`（process.py:3054-3058, 3098-3126, 3128-3148, 3167-3183）。
- 抽出 `_win_roundtrip_blocking` 静态方法在 executor 线程跑，去掉旧的 `wf.shutdown(SHUT_WR)`（单向关闭会破坏后续 roundtrip）。

**评价**：🟢 修复正确且必要。基线的 `open_osfhandle`-per-roundtrip 确实是硬 bug；per-sandbox Lock 串行化单连接 pipe 是标准做法。去 `shutdown(SHUT_WR)` 也对——半关闭会破坏复用。

### 2. `_stop_windows` 资源释放顺序重写（process.py）

**原问题**：基线先 `win_job.teardown(job)`（内核强杀所有成员含 runner），再 `_send_runner_shutdown` + `TerminateProcess` runner。Job kill 后 runner handle 已失效，shutdown/terminate 无意义；且持久化文件对象无人关闭（泄漏）。

**修复方式**（process.py:2846-2880）：
1. `pop` runner；
2. 发 shutdown（runner 优雅退出，停受限 child）；
3. `TerminateProcess` 兜底；
4. `win_job.teardown`（KILL_ON_JOB_CLOSE 清残留 child）——放 terminate 之后，注释说明"若放前面 Job kill 后 runner handle 失效"；
5. `_close_win_pipe_handles(runner)` 关文件对象 + fd + HANDLE；
6. 撤销 ACL；清 `_win_pipe_locks`。
- runner 已不在的 else 分支仍清 Job。

**评价**：🟢 顺序合理。把 Job teardown 移到 terminate 之后是正确的——先让 runner 有机会回收 child，再用 Job 兜底。`pop` 后 else 分支兜底清 Job 也妥当。

### 3. `_close_win_pipe_handles` 关闭逻辑（process.py:2882-2904）

**评价**：🟡 基本正确但有一处隐患。注释自相矛盾：

> 文件对象用 closefd=False 打开, with/f.close 不关底层 fd; 这里显式关.

但实际代码 `stdin_wf = os.fdopen(stdin_fd, "wb")`（process.py:2815）并未传 `closefd=False`，即 `closefd` 默认 `True`，`f.close()` 会同时关底层 fd。注释与代码不符——不影响正确性（fd 会被关），但可能误导后续维护。更关键的是：`_close_win_pipe_handles` 在关完文件对象后，又对 `stdin_handle`/`stdout_handle`（原始 HANDLE）调用 `CloseHandle`。但 fdopen(closefd=True) 关 fd 时，msvcrt 会连带 `CloseHandle` 底层 HANDLE（取决于 C runtime 实现，CPython 的 `os.fdopen` 关 fd 走 `_close`，对 osfhandle 包装的 fd 会调 `CloseHandle`）。这存在**双重 CloseHandle** 风险——第二次 `CloseHandle` 对已关句柄返回 ERROR_INVALID_HANDLE（被 except 吞掉，无害但说明对生命周期理解有偏差）。建议明确：要么 fdopen(closefd=False) 再手动关 fd+handle，要么 fdopen(closefd=True) 后不再对同一 handle 调 CloseHandle。

### 4. ACL Deny-before-Allow 重建（win_acl.py）

**原问题**：基线 `grant_ace` 拷贝现有 ACE 时无脑 `AddAccessAllowedAceEx`，把 **Deny ACE 也当 Allow 写回**——NTFS 显式 Deny 应优先于 Allow，错序会让 Deny 失效，沙箱写拒绝可被后续 Allow 绕过。`revoke_sandbox_acl` 同样问题。

**修复方式**：
- `_parse_getace_tuple`：兼容 pywin32 不同版本 GetAce 返回元数（5/4/3 元组），返回 `(ace_type, ace_flags, access_mask, sid)`。无法区分时默认 Allow（保守）。
- `_rebuild_acl_with_order`：现有 ACE 按类型分桶，Deny 桶 + Allow 桶，串接时 Deny 在前。
- `grant_ace` / `revoke_sandbox_acl` 都改用分桶重建。

**评价**：🟢 核心安全 bug 修复正确。Deny-before-Allow 是 NTFS DACL 评估的硬性要求。分桶重建逻辑清晰。

🟡 **遗留 bug（c2c3f5f0 版本）**：`_rebuild_acl_with_order` 与 `revoke_sandbox_acl` 用 `for i in range(existing_dacl.GetAclSize())` 遍历。`GetAclSize()` 返回的是 ACL **字节数**（含 ACL 头），不是 ACE 个数。一个典型 ACL 头 8 字节 + 每 ACE ~20 字节，`GetAclSize()` 返回 88 时实际只有 3 个 ACE，`range(88)` 会在 `GetAce(3)` 抛 `pywintypes.error (87, 'GetAce', '参数错误')`，被外层 except 吞掉 → **整个 grant/revoke 静默失败**。当前工作区已改为 `GetAceCount()`（见 `win_acl.py:134`），但 **c2c3f5f0 commit 本身仍用 `GetAclSize()`**，故此 commit 的 ACL 修复在真实大目录上大概率不工作。这是本 commit 最严重的遗留缺陷。

🟡 **`_parse_getace_tuple` 兼容性不完整**：c2c3f5f0 版本只处理 5/4/3 元组，其中 3 元组按 `(access_mask, ace_flags, sid)` 解析。但实测 pywin32 (Python 3.13) 对普通目录返回 `((ace_type, ace_flags), access_mask, sid)`——首元素是 header 子元组。`int(tuple)` 会 `TypeError`。当前工作区已补上 `isinstance(first, tuple)` 分支，但 **c2c3f5f0 本身没有**。故在当前 pywin32 上 `_parse_getace_tuple` 仍会抛异常 → grant/revoke 失败。这与上一条叠加，使 ACL 修复在本 commit 实际不可用（后续 commit 才修好）。

### 5. 句柄继承关闭（win_exec.py）

**原问题**：基线 `two_hop_spawn` 注释说"box-server 侧写端/读端不继承"但实际没调用 `SetHandleInformation`，只是注释"简化:通过 DuplicateHandle 或直接不设置"。结果 runner 继承了 box-server 的 stdin 写端 + stdout 读端；runner 之后 `CreateProcessAsUserW(bInheritHandle=True)` 起 child 时，child 又继承 runner 的全部句柄（含 box-server 这两端）→ pipe 隔离泄露，child 可读 box-server 该收的响应/写 box-server 该发的请求。

**修复方式**：
- `_get_kernel32` 加载 `SetHandleInformation` 原型；
- `_clear_inherit(handle)`：`SetHandleInformation(h, HANDLE_FLAG_INHERIT=0x1, 0)`；
- `two_hop_spawn` 对 `child_stdin_write`（box-server 写端）+ `child_stdout_read`（box-server 读端）调 `_clear_inherit`；
- `_handle_exec_request` 对 child stdin pipe 的 runner 写端 `child_in_write` 也 `_clear_inherit`。

**评价**：🟢 重要隔离修复，对标 Linux `close_fds=True`。方向正确。

🔴 **遗留风险**：`_create_process_as_user`（win_exec.py:516）仍用 `bInheritHandle=True` 起 child，child 会继承 runner 持有的**所有**可继承句柄。虽然 box-server 端已被 `_clear_inherit`，runner 自建的 child stdout pipe 端（`child_out_write` 已 CloseHandle）、child stdin 写端（`child_in_write` 已 `_clear_inherit`）都处理了，但**`_handle_exec_request` 的 `child_out_read`（runner 读端）未关继承**——child 会继承 `child_out_read`。这意味着 child 持有 stdout pipe 的读端副本，runner 关闭写端后 child 仍持有读端 → pipe 不 EOF，`fh.read()` 可能挂起直到 child 退出。实测可能不致命（child 退出即释放），但违背最小权限。建议对 `child_out_read` 也 `_clear_inherit`。

### 6. exec stdin 透传（win_exec.py）

**原问题**：基线 `_handle_exec_request` 写死 `stdin_fd=0`，注释 `TODO: 支持 stdin 透传`。exec 请求无法传 stdin，无法管道输入。

**修复方式**：
- `runner_main` exec 分支读 `header.stdin_size`，若 >0 `recv_frame(stdin, MAX_STDIN_BYTES)` 收 body；
- `_handle_exec_request` 建 child stdin pipe，runner 写、child 读（继承读端），runner 写端 `_clear_inherit`；
- 透传 `stdin_bytes`，写完 `CloseHandle(child_in_write)` 让 child 读 EOF。

**评价**：🟢 功能补全合理。MAX_STDIN_BYTES=64MiB 上限对齐 daemon_ipc。写完关写端是正确的 EOF 语义。

🟡 `child_in_read` 在 `_create_process_as_user` 返回后立即 `CloseHandle`，但 `CreateProcessAsUserW(bInheritHandle=True)` 已让 child 继承了读端副本——runner 关的是自己的副本，child 的副本仍有效，正确。但若 child 退出前 runner 异常，`child_in_write` 在 except 分支是否关？看代码 except 里有 `CloseHandle(child_out_write)`/`child_out_read`，但**没有关 `child_in_write`/`child_in_read`**（child_in_read 已在正常路径关，异常路径漏 child_in_write）。轻微泄漏。

### 7. Job 内存双保险（win_job.py）

**原问题**：基线只设 `JOB_OBJECT_LIMIT_PROCESS_MEMORY` + `ProcessMemoryLimit`，是 per-process 上限。Job 内多进程总和可超 memory_max。

**修复方式**：加 `JOB_OBJECT_LIMIT_JOB_MEMORY` 并设 `JobMemoryLimit = memory_max`，与 `ProcessMemoryLimit` 双保险。

**评价**：🟢 对齐 Linux cgroup memory.max（整个 job 上限）。双保险合理。

### 8. WFP 端口范围每端口一 filter（win_wfp.py）

**原问题**：基线 `_build_port_range_condition` 注释承认用 `port_start` 单值 EQUAL 作占位，实际只放行了范围内第一个端口，其余端口被 Block filter 拦截 → 代理端口范围大部分不通。

**修复方式**：
- `_build_port_eq_condition(port)` 单端口 EQUAL；
- install 时 `for port in range(start, end+1)` 每端口一个 Permit filter，key 带 `-port` 后缀；
- uninstall 同样遍历范围删除。

**评价**：🟢 修复了占位实现。每端口一 filter 在端口范围 ≤10（默认 60080-60089）时开销可忽略。key 命名带后缀保证幂等安装/卸载。

🟡 **潜在问题**：若 policy 配置的端口范围很大（如 60080-60280，200 个端口），会装 200×2(V4+V6)=400 个 filter。WFP filter 数量有上限（默认约 1000+），极端配置可能撞限。建议对大范围回退 FWP_MATCH_RANGE 或在 install 前校验范围大小。

🟡 **卸载不匹配风险**：`uninstall_wfp_filters` 默认用 `const.DEFAULT_PROXY_PORT_RANGE_START/END`，但 install 时用的是调用方传入的 `permit_port_start/end`（可能来自 policy，与默认不同）。若 policy 改了端口范围后卸载，仍按默认范围删 → 旧范围的 filter 残留。应让 uninstall 也接收实际范围，或持久化记录已装范围。

### 9. 安装预装路径 + join 等待（win_setup.py）

**原问题**：基线 `_preinstall_read_acl_async` 是 daemon 线程，install 作为提权子进程退出后 daemon 线程被强杀，预装只做一半；预装路径硬编码 4 个系统目录。

**修复方式**：
- `install(force, preinstall_paths)` 接受 policy 透传路径；
- `_preinstall_read_acl_async` 返回 thread，`install` 里 `thread.join(timeout=120s)`；
- 超时仅 warning，继续写 installed 标记（剩余路径靠后续 grant_ace 幂等补做）；
- app.py lifespan 从根 policy 读 `read_acl_preinstall` 透传。

**评价**：🟢 修复了 daemon 线程被强杀的问题。join+超时降级策略合理（install 提权子进程不应无限挂起）。路径可配置化更好。

🟡 **降级路径的隐患**：超时后写 installed 标记，"剩余路径靠后续 grant_ace 幂等补做"——但 `grant_ace` 是对**沙箱 workspace** 施加 ACE，不是对预装路径。预装路径（USERPROFILE 等）的读 ACL 若没装上，沙箱用户读这些系统目录会失败，不是靠 workspace grant_ace 能补的。注释"剩余路径由后续 ensure 补做"也不准确——`ensure_windows_setup` 已 installed 时直接 return，不会重跑预装。故超时降级后该沙箱用户对系统目录读权限永久缺失。建议：超时不写 installed 标记，让下次 ensure 重跑；或预装失败时记录未完成路径供 ensure 补做。

### 10. 隐藏登录界面用户（win_setup.py）

**修复方式**：`_reg_set_dword_under` 在 `Winlogon\SpecialAccounts\UserList` 写 `jbx-sandbox=0`，隐藏登录界面用户。失败不阻断安装。

**评价**：🟢 合理的加固，避免用户在登录界面看到/用 jbx-sandbox。`REG_BASE_KEY` 不拼以区分全路径的 API 设计清晰。

---

## 四、关键代码检视

### 4.1 `_win_roundtrip_blocking`（process.py:3058-3091）

```python
header_blob = encode_request(request_type=request_type, payload=payload)
send_frame(stdin_wf, header_blob)
stdin_wf.flush()
if body_bytes:
    send_frame(stdin_wf, body_bytes)
    stdin_wf.flush()
blob = recv_frame(stdout_rf, DAEMON_MAX_RESPONSE_BYTES)
response = json.loads(blob.decode("utf-8"))
body = b""
if want_body and response.get("ok"):
    size = int(response.get("content_size") or 0)
    if size > 0:
        body = recv_frame(stdout_rf, MAX_FILE_BYTES)
return response, body
```

**问题**：
- 🟡 `recv_frame(stdout_rf, ...)` 无超时。若 runner 卡死（不回响应也不关 pipe），executor 线程永久阻塞，per-sandbox Lock 永久持有 → 该 sandbox 所有后续 exec/file-op 全部死锁。基线的 `with` 模式至少 pipe 关闭会抛错；持久化复用后失去了这个自然超时。建议 `stdout_rf` 设 `settimeout` 或用 `loop.run_in_executor` + `asyncio.wait_for` 包外层（当前 `_win_runner_roundtrip` 只对 shutdown 有 wait_for，exec/file-op 没有）。
- 🟡 `json.loads` 若 runner 返回非 JSON 或截断帧会抛 `ValueError`，被外层 except 捕获返回 None——但此时 pipe 状态已损坏（帧未对齐），后续 roundtrip 会读到错位数据。runner pipe 是帧协议，一次解析失败应标记 sandbox 不可用并触发重建，而非吞错继续复用。

### 4.2 `_send_runner_shutdown_blocking`（process.py:3185-3195）

```python
send_frame(runner["stdin_wf"], encode_request(request_type=REQUEST_TYPE_SHUTDOWN))
runner["stdin_wf"].flush()
```

**问题**：🟡 只发 shutdown 帧不等 runner 回 `_send_response({"ok":True})`。`_send_runner_shutdown` 外层 `asyncio.wait_for` 超时后仅 debug 日志，不强制保证 runner 已退出——靠后续 `TerminateProcess` 兜底。可接受但注释"让 runner 优雅退出它内部会停 child"略乐观，实际靠 TerminateProcess+Job。

### 4.3 `_create_process_as_user` 的 `bInheritHandle=True`（win_exec.py:516）

如第三节第 5 点所述，child 继承 runner 全部可继承句柄。`_clear_inherit` 已覆盖 box-server 端和 child stdin 写端，但 `child_out_read`（runner stdout 读端）未关继承。建议补 `_clear_inherit(int(child_out_read.value))`。

### 4.4 `_parse_getace_tuple` c2c3f5f0 版本（win_acl.py:77-99）

如第三节第 4 点所述，c2c3f5f0 版本未处理 `((ace_type, ace_flags), access_mask, sid)` 这一当前 pywin32 实际返回形态，会 `int(tuple)` TypeError。后续 commit 已修。这是本 commit 在真实环境"修复不生效"的根因之一。

### 4.5 WFP filter 数量与卸载范围

如第三节第 8 点所述，大端口范围可能撞 WFP filter 上限；uninstall 默认范围与 install 实际范围可能不匹配导致残留。

---

## 五、优点

1. **Deny-before-Allow 重建**是 NTFS DACL 的硬性安全要求，基线把 Deny 当 Allow 写回是严重权限绕过漏洞，本 commit 分桶重建方向完全正确。
2. **pipe 持久化 + per-sandbox Lock** 正确解决了基线"只能 exec 一次"的硬 bug，串行化单连接 pipe 是标准做法。
3. **`_clear_inherit`** 对标 Linux `close_fds=True`，堵住了 runner/child 继承 box-server pipe 端的隔离泄露，安全收益明显。
4. **Job 双保险内存限制**对齐 cgroup 语义，正确。
5. **WFP 端口范围**从占位单值改为每端口一 filter，真正放行整个范围。
6. **install join 等待**修复了 daemon 线程被提权子进程退出强杀的问题。
7. **隐藏登录界面用户**是合理的加固细节。
8. 测试覆盖了 ACL 解析/重建的核心逻辑（3 个单测），mock 了 Linux 上无法实跑的 pipe fd 转换。

---

## 六、问题与风险

### 🔴 严重

1. **ACL 修复在 c2c3f5f0 真实环境不生效**（win_acl.py:134, 99）：
   - `range(existing_dacl.GetAclSize())` 用字节数当 ACE 个数，`GetAce(3+)` 抛错被外层 except 吞掉 → grant/revoke 静默失败。沙箱 ACL 实际未施加/未撤销。
   - `_parse_getace_tuple` 不处理 `((ace_type,ace_flags),mask,sid)` 子元组形态 → 当前 pywin32 上 TypeError → 同样静默失败。
   - 二者叠加，本 commit 的 ACL 修复在真实大目录 + 当前 pywin32 上**完全不可用**，沙箱写权限隔离形同虚设。后续 commit（工作区当前版本）已修（`GetAceCount` + 子元组分支），但 c2c3f5f0 本身未修。

### 🟡 中等

2. **`child_out_read` 未关继承**（win_exec.py `_handle_exec_request`）：child 继承 runner 的 stdout 读端，pipe 不 EOF，`fh.read()` 可能挂起到 child 退出。功能上可能不致命，但违背最小权限。

3. **roundtrip 无超时**（process.py `_win_roundtrip_blocking`）：runner 卡死时 executor 线程永久阻塞 + per-sandbox Lock 永久持有 → sandbox 死锁。exec/file-op 路径无 `wait_for`。

4. **帧解析失败后 pipe 状态损坏未标记**：`json.loads` 失败被吞，后续 roundtrip 读错位帧。

5. **预装超时降级后永久缺失读权限**（win_setup.py:520-528）：超时仍写 installed 标记，ensure 不重跑，沙箱用户对系统目录读权限永久缺失，且非 workspace grant_ace 能补。

6. **WFP uninstall 范围不匹配**（win_wfp.py:474-477）：uninstall 默认范围，install 用 policy 范围，改配置后卸载残留旧 filter。

### 🟢 轻微

7. **`_close_win_pipe_handles` 双重 CloseHandle 风险**：fdopen(closefd=True) 关 fd 时连带关 HANDLE，再显式 CloseHandle 同一 handle（被 except 吞，无害但生命周期理解有偏差）。

8. **`_handle_exec_request` except 未关 `child_in_write`**：异常路径泄漏 child stdin 写端 handle。

9. **WFP 大端口范围可能撞 filter 数量上限**（默认范围 10 个无影响，极端配置 200+ 有风险）。

---

## 七、改进建议

1. **[必修]** ACL 遍历用 `GetAceCount()` 而非 `GetAclSize()`；`_parse_getace_tuple` 补 `isinstance(first, tuple)` 子元组分支。（注：工作区当前版本已修，确认后续 commit 已合入即可。）
2. **[建议]** `_handle_exec_request` 对 `child_out_read` 调 `_clear_inherit`；except 分支补关 `child_in_write`。
3. **[建议]** `_win_roundtrip_blocking` 给 `stdout_rf` 设超时，或 `_win_runner_roundtrip`/`_with_body` 外层包 `asyncio.wait_for`（参考 shutdown 已有 wait_for）。帧解析失败时标记 sandbox 不可用触发重建。
4. **[建议]** 预装超时不写 installed 标记，或记录未完成路径让 ensure 补做，避免永久缺读权限。
5. **[建议]** `uninstall_wfp_filters` 接收实际端口范围参数，或持久化已装范围；install 前校验范围大小，过大时回退或告警。
6. **[建议]** `_close_win_pipe_handles` 统一 fd/handle 生命周期模型：要么 fdopen(closefd=False) 显式关 fd+handle，要么 fdopen(closefd=True) 后不对同 handle 再 CloseHandle；修正自相矛盾的注释。
7. **[建议]** 测试补一例：`revoke_sandbox_acl` 在 `GetAclSize()` 返回字节数 > ACE 个数时不抛错（回归 c2c3f5f0 的 bug）；补一例 roundtrip 超时/pipe 损坏后 sandbox 标记不可用。

---

## 八、小结

本 commit 是对基线 `5f841f7a` 审查发现的批量修复，**方向全部正确**：Deny-before-Allow ACL 重建、pipe 持久化+串行化、句柄继承隔离、Job 内存双保险、WFP 端口范围、stdin 透传、安装预装 join——每一项都切中基线的真实缺陷，安全意图明确。

但 **c2c3f5f0 本身存在"修复不生效"的严重遗留**：`_rebuild_acl_with_order`/`revoke_sandbox_acl` 用 `GetAclSize()`（字节数）当 ACE 个数遍历，`_parse_getace_tuple` 不兼容当前 pywin32 的子元组形态——二者叠加导致 ACL grant/revoke 在真实环境静默失败，沙箱写权限隔离实际未施加。这是本 commit 最严重的问题（后续 commit 已修，工作区当前版本用 `GetAceCount` + 子元组分支已修正）。

其余为中等及轻微问题：`child_out_read` 未关继承、roundtrip 无超时、预装超时降级后永久缺权限、WFP uninstall 范围不匹配、双重 CloseHandle 等，均不致命但应跟进。

**结论**：修复意图合格，ACL 部分在 c2c3f5f0 实际不可用（需后续 commit 补救），建议确认后续 commit 已修 `GetAclSize`→`GetAceCount` 与子元组解析后再视为 ACL 隔离可用。
