# Commit 检视报告：bb1afca0 review2

## 一、概述

- Commit：bb1afca03d05c41f8fdcd69109b83dd3542d0b48
- 日期：2026-07-22
- 作者：lby
- 说明：第二轮 review 修复。
- 定位：基线 `5f841f7a`（feat:window 沙箱）+ 首轮修复 `c2c3f5f0`（fix:review）之后的第二轮批量修复，集中纠正 WFP ctypes 结构体布局、SID 一致性、loopback 字节序、ACL 读控制缺失、Job SUSPEND→Assign→Resume 竞态、环境块悬垂指针、CLI 参数透传、代理端口范围配置化等十余项缺陷。
- 规模：11 文件，+925 / -270。

基线 `5f841f7a` 引入 Windows 沙箱，首轮 `c2c3f5f0` 修复了 pipe 复用、ACL Deny 顺序、Job 内存双保险等，但仍遗留若干"会编译过、测试自洽但真实 Windows 上不工作"的硬伤（WFP 结构体错位、GUID 非法、SID 不匹配、loopback 字节序反、ACL 读控制缺失、Job 逃逸窗口、env 块悬垂、CLI force/端口不透传）。本 commit 是对这些根因的集中修复，方向整体正确、修复深度到位，WFP 结构体重写是本次最大亮点。仍有若干遗留风险（卸载端口范围不匹配、resume 失败后句柄泄漏、`_add_filter` 的 keeps_alive 形参未使用、win_proxy docstring 与实现矛盾等），详见第六节。

---

## 二、变更范围

| 文件 | 增/删 | 性质 | 修复主题 |
|------|------|------|---------|
| `models/policy.py` | +8/-1 | 改动 | `WindowsFilesystemPolicy` 增 `allow_read`/`deny_read` 字段（读控制，MAJOR #4） |
| `server/app.py` | +18/-4 | 改动 | lifespan 把根 policy 的 `proxy.port_range_*` 透传给 `ensure_windows_setup`（MAJOR #7） |
| `server/runtime/process.py` | +66/-20 | 改动 | `_create_windows` 接 5 元组 two_hop_spawn；SUSPEND→Assign→Resume→CloseHandle；apply/revoke ACL 按路径清单（MAJOR #1/#6）；setup 传 proxy 端口 |
| `supervisor/win_acl.py` | +88/-21 | 改动 | `apply_sandbox_acl` 增读控制 deny-then-allow，返回施加路径清单；`revoke_sandbox_acl` 按清单撤销；去掉 `PROTECTED_DACL`（MAJOR #4/#5/#6） |
| `supervisor/win_constants.py` | +72/-32 | 改动 | WFP layer/condition GUID 对齐 SDK 真值；`JBX_*` key 改为合法 UUID；`FWP_DATA_TYPE` 枚举对齐（CRITICAL #2/#5，MAJOR #3）；`LOOPBACK_IPV4_INT` 改 host order（CRITICAL #4）；新增 `FWP_ACTRL_MATCH_FILTER`/`FWPM_*_FLAG` |
| `supervisor/win_exec.py` | +43/-25 | 改动 | `AllocateAndInitializeSid` nSubAuthorityCount=3→4（CRITICAL #1）；`_create_process_as_user` env 块内联持有（CRITICAL #3）；two_hop_spawn 用 `CREATE_SUSPENDED` 并返回 thread handle（MAJOR #1） |
| `supervisor/win_job.py` | +19/-0 | 新增 | `resume_process`（ResumeThread），配合 SUSPEND→Assign→Resume（MAJOR #1） |
| `supervisor/win_proxy.py` | +14/-18 | 改动 | `EgressFilter.allow` IP-allow 与 port-allow 不再隐式 AND，改 OR（MAJOR #10） |
| `supervisor/win_setup.py` | +131/-38 | 改动 | `install`/`ensure_windows_setup`/`_elevate_and_run_install` 接收并透传 `force`/`proxy_port_*`/`preinstall_paths`（MAJOR #7/#9）；预装断点续传（MAJOR #8）；CLI argparse 化 |
| `supervisor/win_wfp.py` | +243/-100 | 改动 | 结构体布局全面对齐 SDK（FWPM_FILTER0/SUBLAYER0/SESSION0/DISPLAY_DATA0、FWP_VALUE0/CONDITION_VALUE0、FWP_V4/V6_ADDR_MASK、FWP_RANGE0）；ALE_USER_ID 改用 SD（FWP_SECURITY_DESCRIPTOR_TYPE + FWP_ACTRL_MATCH_FILTER）；v4/v6 loopback 条件改返回 keep-alive；`_add_filter` 幂等语义修正（CRITICAL #5，MAJOR #2/#6） |
| `tests/integration/test_server_api_windows.py` | +170/-0 | 新增 | WFP 常量/结构体自洽性、SID 一致性、policy read 字段、proxy OR 语义、resume/apply mock 断言 |

---

## 三、修复与改动逐项分析

### 1. WFP 结构体布局全面重写（win_wfp.py，最大改动）

**原问题**（首轮遗留 + 基线遗留）：
- `FWPM_FILTER0` 多了不存在的 `providerDataSize` 字段、缺 `flags`、`reserved` 误为 `c_uint64`，导致 `FwpmFilterAdd0` 收到的结构体字段全部错位，filter 安装在真实 Windows 上必失败。
- `FWPM_SUBLAYER0.weight` 误为 `c_uint32`，SDK 是 `UINT16`，导致 `FwpmSubLayerAdd0` 读到错位 weight。
- `FWPM_SESSION0.displayData` 误为 `c_void_p`（8B）而非内嵌 `FWPM_DISPLAY_DATA0`（16B），使后续 `txnWait*` 字段错位；且有重复 `txnWaitDurationInMSec`/`txnWaitDurationInMsec` 两字段。
- `FWP_VALUE0`/`FWP_CONDITION_VALUE0` 联合体成员不全（缺 `sd`/`unicodeString`/`byteArray6`/`rangeValue`），`v4AddrMask`/`v6AddrMask` 误为内嵌结构体而非指针，尺寸与 SDK 不符。

**修复方式**：
- 新增 `FWPM_DISPLAY_DATA0`（内嵌两 `wchar_t*`，x64 16B），`FWPM_FILTER0`/`SUBLAYER0`/`SESSION0` 均改为内嵌而非 `c_void_p`，对齐 SDK 字段序列与类型（`flags` UINT32、`weight` UINT16、`reserved` GUID*、`filterId` UINT64、`effectiveWeight` FWP_VALUE0、`txnWaitTimeoutInMSec`/`processId`/`sid`/`username`/`kernelMode`）。
- `FWP_VALUE0`/`FWP_CONDITION_VALUE0` 联合体补全所有指针成员；`v4AddrMask`/`v6AddrMask`/`rangeValue` 改为 `c_void_p`（指针，8B），构造时用 `ctypes.cast(pointer(local), c_void_p)`。
- 新增 `FWP_RANGE0`、`FWP_BYTE_ARRAY16`。

**评价**：🟢 这是本 commit 最关键的修复。基线/首轮的 ctypes 结构体错位是 WFP 在真实 Windows 上"从未成功安装"的根因之一（除 GUID 非法、loopback 字节序反之外）。重写后布局与 `fwpmtypes.h` 一致，`FwpmFilterAdd0`/`FwpmSubLayerAdd0` 能正确读到字段。`FWPM_DISPLAY_DATA0` 内嵌改正是尤其关键的细节——`c_void_p` (8B) 与内嵌结构体 (16B) 的差异会让后续所有字段偏移，是隐蔽而致命的 bug。

🟡 **轻微**：`_add_filter` 新增 `keeps_alive: list[object] | None = None` 形参，但函数体内**从未引用**该参数（win_wfp.py:497-527）。keep-alive 引用实际由调用方 `install_wfp_filters` 持有 `keeps` 列表，`_add_filter` 只是把同一引用传进来又不用。该形参是 dead parameter，仅起文档提示作用，不影响正确性（引用存活靠调用方栈帧），但易误导维护者以为 `_add_filter` 内部负责保活。建议删除该形参，或改为真正在 `_add_filter` 末尾 `keeps_alive.clear()` 之类（但语义反了，不能清，因为同一 keeps 跨多个 filter 复用）——实际应直接删除该形参。

### 2. ALE_USER_ID 条件改用 SECURITY_DESCRIPTOR（win_wfp.py）

**原问题**：基线/首轮 `_build_ale_user_condition` 把裸 SID 指针写入 `conditionValue.value.sid`（`FWP_SID` 类型），但 SDK 的 `FWPM_CONDITION_ALE_USER_ID` 条件值类型是 `FWP_SECURITY_DESCRIPTOR_TYPE`（`FWP_BYTE_BLOB*` 指向自相关 SD 字节），BFE 评估时检查 SD 的 DACL 是否对发起连接用户授予 `FWP_ACTRL_MATCH_FILTER`。裸 SID 条件类型不匹配，Block filter 实际不命中用户 → 沙箱出站未被拦截。

**修复方式**：
- `_build_ale_user_condition(sandbox_user_sid: str)` 改用 `win32security` 构造自相关 SD：`ConvertStringSidToSid` → `TRUSTEE`（`BuildTrusteeWithSid`）→ `SetEntriesInAcl([{AccessPermissions: FWP_ACTRL_MATCH_FILTER, AccessMode: GRANT_ACCESS, Inheritance: 0, Trustee}])` → `SECURITY_DESCRIPTOR.SetSecurityDescriptorDacl(1, dacl, 0)` → `MakeSelfRelativeSD` 得自相关 SD bytes。
- `FWP_BYTE_BLOB` 持 SD bytes（`from_buffer_copy` 保活），`conditionValue.type = FWP_SECURITY_DESCRIPTOR_TYPE`，`conditionValue.value.sd` 指向 blob。
- 返回 `(cond, _KeepAlive(blob=..., buf=..., sd_bytes=...))`，调用方保活到 `FwpmFilterAdd0` 返回。

**评价**：🟢 方向正确，对齐 SDK 示例（"Permitting and Blocking Applications and Users"）。这是 WFP user-keyed 过滤的标准做法。`_KeepAlive` 持 `sd_bytes`/`buf`/`blob` 引用防 GC 释放指针，是 ctypes 与 GC 共存的正确模式。

🟡 **潜在风险**：`dacl.SetEntriesInAcl([explicit])` 在 pywin32 不同版本语义不一（部分版本是 in-place 修改并返回 None，部分返回新 ACL）。当前代码取返回值忽略、用原 `dacl`——若某版本返回新 ACL 而原 dacl 未变，SD 的 DACL 为空，BFE 评估时无 ACE 授 `FWP_ACTRL_MATCH_FILTER`，所有沙箱用户连接被 Block（过度封锁，但不被绕过，安全侧无害）。建议显式 `dacl = dacl.SetEntriesInAcl([explicit]) or dacl` 或用模块级 `win32security.SetEntriesInAcl([explicit])` 取返回值。需在真实 Windows + 当前 pywin32 验证。

🟡 **SD 覆盖语义**：`sd.SetSecurityDescriptorDacl(1, dacl, 0)` 后 `MakeSelfRelativeSD(sd)` 返回自相关 SD。但绝对 SD 的 `DACL` 由 `dacl` 对象持有，`MakeSelfRelativeSD` 复制 DACL 字节到新 buffer，故 `dacl` 生命周期只需到 `MakeSelfRelativeSD` 返回——当前 `sd_bytes` 已是独立 bytes，后续 `dacl` 不再需要，保活正确。

### 3. loopback 条件返回 keep-alive + 字节序修正（win_wfp.py + win_constants.py）

**原问题**（CRITICAL #4）：`LOOPBACK_IPV4_INT = 0x0100007F` 是**网络字节序**，但 WFP `FWP_V4_ADDR_AND_MASK.addr` 要求 **host byte order**，旧值会让 Permit filter 匹配 `1.0.0.127` 而非 `127.0.0.1`，沙箱代理端口（127.0.0.1:60080-60089）流量被 Block 拦截，代理路径彻底不通。

**修复方式**：
- `win_constants.py`：`LOOPBACK_IPV4_INT = 0x7F000001`（host order，`127.0.0.1`）。
- `win_wfp.py`：`_build_loopback_v4_condition`/`_build_loopback_v6_condition` 返回 `(cond, addr_mask)`，`addr_mask` 作为 keep-alive 由调用方持有；`v4AddrMask`/`v6AddrMask` 字段改为 `c_void_p` 指针，`ctypes.cast(pointer(addr_mask), c_void_p)`。

**评价**：🟢 CRITICAL 修复正确。`socket.inet_ntoa(struct.pack("!I", 0x7F000001)) == "127.0.0.1"` 的测试断言是验证字节序的正确方式。keep-alive 返回模式与 ALE_USER_ID 一致，统一了条件构造的内存生命周期模型。

### 4. WFP GUID 与 key 对齐 SDK / 合法化（win_constants.py）

**原问题**（CRITICAL #2/#5）：
- `JBX_SUBLAYER_KEY = "JiuwenBox-Windows-Sandbox-Sublayer"` 是描述性字符串，**非合法 UUID**，`_guid_from_str` 首行 `uuid.UUID(s)` 即 `ValueError`，WFP 安装从未执行。
- `JBX_FILTER_*_KEY` 同样是描述性字符串。
- `FWPM_LAYER_ALE_AUTH_CONNECT_V4/V6`、`FWPM_CONDITION_ALE_USER_ID/IP_REMOTE_ADDRESS/IP_REMOTE_PORT` 是虚构值，与 SDK `fwpmu.h DEFINE_GUID` 不符，filter 装在错误的 layer/condition 上。

**修复方式**：
- `JBX_SUBLAYER_KEY`/`JBX_FILTER_*` 改为合法 UUID 字符串。
- 5 个 layer/condition GUID 改为对齐 `fwpmu.h` 真值（注释明确标注取值来源）。

**评价**：🟢 关键修复。GUID 非法是 WFP 在真实 Windows 上"从未执行"的另一根因。改为合法 UUID 后 `_guid_from_str` 不再抛错，filter 能装到正确的 layer/condition。新增的 `test_sublayer_and_filter_keys_are_valid_uuids` / `test_layer_and_condition_guids_match_sdk` 测试覆盖了这一回归。

🟡 **测试局限**：`test_layer_and_condition_guids_match_sdk` 只断言常量==常量（自洽），并未独立交叉验证与 SDK 头文件一致。若常量本身错，测试不会发现。建议在真实 Windows 上用 `FwpmLayerGetByKey0` 反查 layer GUID 做端到端验证（或对照 SDK 头文件静态断言）。当前测试能防"GUID 字符串又变回描述性"的回归，但不能防"GUID 值错"。

### 5. FWP_DATA_TYPE 枚举对齐（win_constants.py，MAJOR #3）

**原问题**：`FWP_SID = 12`（实为 `FWP_CHAR8`）、`FWP_BYTE_BLOB_TYPE = 16`（实为 `FWP_TOKEN_ACCESS_INFORMATION_TYPE`）、`FWP_BYTE_ARRAY_TYPE = 11`（应为 `FWP_BYTE_ARRAY16_TYPE`）、`FWP_V4_ADDR_MASK = 17`/`FWP_V6_ADDR_AND_MASK = 18`（应为 256/257，>0xFF 扩展类型）。

**修复方式**：全量对齐 `fwptypes.h FWP_DATA_TYPE` 枚举：`FWP_BYTE_ARRAY16_TYPE=11`、`FWP_BYTE_BLOB_TYPE=12`、`FWP_SID=13`、`FWP_SECURITY_DESCRIPTOR_TYPE=14`、…、`FWP_V4_ADDR_MASK=0x100`、`FWP_V6_ADDR_AND_MASK=0x101`、`FWP_RANGE_TYPE=0x102`。

**评价**：🟢 修复正确。`conditionValue.type` 取错值会让 BFE 按 wrong union 成员解析 condition 值，filter 永不命中。`test_fwp_data_type_enum_matches_sdk` 覆盖了关键枚举值。

### 6. AllocateAndInitializeSid nSubAuthorityCount 修正（win_exec.py，CRITICAL #1）

**原问题**：`AllocateAndInitializeSid(auth, 3, sub0, sub1, RID, ...)` 产 `S-1-5-<sub0>-<sub1>-<RID>`（3 个 sub authority，缺 `21`），而 `win_acl.get_synthetic_write_sid()` 字符串版是 `S-1-5-21-<sub0>-<sub1>-<RID>`。两个"合成写 SID"不是同一个 SID → 受限 token 第二重 ACL 检查（WRITE_RESTRICTED）永远找不到 DACL 里授的 Allow ACE，沙箱写白名单路径全部失败。

**修复方式**：`nSubAuthorityCount = 4`，sub list = `[21, sub0, sub1, RID]`，产 `S-1-5-21-<sub0>-<sub1>-<RID>`，与字符串版一致。

**评价**：🟢 CRITICAL 修复正确。SID 表示法 `S-R-I-S1-S2-...` 中 `I` 是 identifier authority（5=NT），其每段是 sub-authority，`21` 是第一个 sub-authority 而非 authority 的一部分。`AllocateAndInitializeSid` 的 `nSubAuthorityCount` 必须含 `21`。`test_synthetic_write_sid_string_matches_allocate_layout` 通过常量编排推导出 SID 前缀必须 `S-1-5-21-...` 且 7 段，覆盖了回归。

🟢 **轻微**：`AllocateAndInitializeSid.argtypes` 第 3 个参数定义为 `ctypes.c_void_p`，而 sub-authority 应是 `DWORD`（win_exec.py:163-167）。调用传 `21`（int），ctypes 把 int 当 c_void_p（指针大小无符号）。在 Windows x64 调用约定下，DWORD 与 c_void_p 都占 8 字节栈槽，低 4 字节为 21，`AllocateAndInitializeSid` 读 DWORD 得 21，功能正确。但类型不严谨，建议改为 `wintypes.DWORD` 以语义自洽（防未来 x86 或其他 ABI 下出问题）。

### 7. 环境块悬垂指针修复（win_exec.py，CRITICAL #3）

**原问题**：`_build_environment_block` 返回 `c_void_p`，但 `ctypes.create_unicode_buffer(block)` 的 buf 是函数局部变量，返回后 buf 被 GC 回收，`c_void_p` 指向悬垂内存，`CreateProcessAsUserW` 读到的是已释放的环境块，子进程环境错乱或崩溃。

**修复方式**：删除 `_build_environment_block`，在 `_create_process_as_user` 内联构造：`env_block_buf = ctypes.create_unicode_buffer(block)`，`env_block_ptr = ctypes.cast(env_block_buf, c_void_p)`，buf 作为局部变量存活到 `CreateProcessAsUserW` 返回。

**评价**：🟢 CRITICAL 修复正确。这是 ctypes + GC 的经典陷阱：`create_unicode_buffer` 返回的对象必须由调用方持引用直到 API 读完成。内联持有是正确做法。注释也明确标注了"必须存活到 CreateProcessAsUserW 返回"。

### 8. CREATE_SUSPENDED + SUSPEND→Assign→Resume（win_exec.py + win_job.py + process.py，MAJOR #1）

**原问题**：基线/首轮 `two_hop_spawn` 直接启动 runner（非挂起），`assign_process_by_pid` 与 runner 开始执行之间存在竞态窗口：runner 可能在 assign 前已 fork 出不受 Job 限制的 child，逃逸 Job 资源限制（内存/CPU/进程数）与 KILL_ON_JOB_CLOSE 清理。

**修复方式**：
- `win_exec.two_hop_spawn`：`creation_flags |= const.CREATE_SUSPENDED`，runner 主线程挂起启动；不再 `CloseHandle(pi.hThread)`，返回 5 元组含 `thread_handle`。
- `win_job.resume_process(thread_handle)`：`ResumeThread` 恢复挂起线程，`prev == 0xFFFFFFFF` 抛错。
- `process._create_windows`：assign 成功后 `resume_process`；assign 失败（Job 异常）也 `resume_process`（否则 runner 永久挂起）；无 Job 限制时也 `resume_process`；resume 后 `CloseHandle(thread_handle)` 并从 runner dict pop。

**评价**：🟢 修复正确，对齐设计 6.8（SUSPEND→Assign→Resume）。这是消除 Job 逃逸窗口的标准模式（Windows 推荐做法）。三路 resume 覆盖（assign 成功/Job 失败/无 Job）确保 runner 不会因任何分支永久挂起。`CloseHandle` 后 pop 避免句柄泄漏与重复关闭。

🔴 **遗留风险（resume 失败路径）**：若 `assign_process_by_pid` 成功但 `resume_process` 抛异常（process.py:2855-2859），进入内层 except 仅 warning，**外层 try 不会进 except**（resume 异常被内层 except 吞），随后 `CloseHandle(thread_handle)` 关闭线程句柄。此时 runner 仍挂起，且线程句柄已关闭——**没有任何路径能再 resume 它**。`_stop_windows` 的 `TerminateProcess` 仍可清理挂起进程（TerminateProcess 不要求线程可 resume），故不致死锁，但 runner 从未执行任何业务逻辑，沙箱静默不可用且无显式错误。建议：resume 失败应 `raise`（让外层 Job except 也走 resume 兜底，或直接 fail create），或记录 sandbox 不可用状态供 `_is_running_windows` 探测。

🟡 **轻微**：`win_job.resume_process` 用 `kernel32.ResumeThread`，但 `_get_kernel32` 在 `win_job` 模块内独立加载 kernel32（与 `win_exec._get_kernel32` 是不同 `_kernel32` 缓存）。两个模块各持一份 kernel32 WinDLL 实例，功能无碍（同一 DLL 句柄），但略冗余。

### 9. ACL 读控制 deny-then-allow + workspace 默认 Allow Read（win_acl.py + policy.py，MAJOR #4）

**原问题**：基线/首轮 ACL 只控制写（allow_write/deny_write），读控制完全缺失。Windows 独立用户（jbx-sandbox）默认读不了用户 profile 等系统目录，靠 install 预装补；但 workspace 本身也无显式 Allow Read，沙箱用户连自己工作区都读不了。

**修复方式**：
- `policy.py`：`WindowsFilesystemPolicy` 增 `allow_read`/`deny_read` 字段，进 `expand_path_lists` 校验器。
- `win_acl.apply_sandbox_acl`：先施 `deny_read`（Deny Read ACE），再施 `allow_read`（Allow Read ACE 覆盖 deny）；`allow_read` 为空时对 workspace 根施 Allow Read（默认让沙箱能读自己工作区）。
- 返回施加过 ACE 的顶层路径清单（含 workspace + allow/deny 各项，去重保序）。

**评价**：🟢 读控制补全合理，deny-then-allow 顺序对齐 NTFS 显式 Deny 优先语义。workspace 默认 Allow Read 是必要的——独立用户身份默认读不了 profile，workspace 是沙箱唯一可写可读的家目录。`model_config = ConfigDict(extra="forbid")` + 新字段的 `test_read_fields_extra_forbid` 测试保证了向后兼容（旧 policy 不带 allow_read/deny_read 时默认空 list，不报错）。

🟡 **`deny_read` + `allow_read` 同路径的语义**：若同一路径同时出现在 `deny_read` 和 `allow_read`，`deny_read` 先施 Deny Read ACE，`allow_read` 再施 Allow Read ACE。NTFS 显式 Deny 优先于显式 Allow，故同路径上 deny 实际生效（allow 无效）。注释说"allow 覆盖 deny"——这与 NTFS 评估顺序相反。实际语义是：`allow_read` 列表用于"在 deny_read 覆盖范围内精细化放行"时**无效**（因 Deny 优先）。要真正"覆盖 deny"需用 PROTECTED_DACL 切断继承或删除 deny ACE。当前实现下，同路径 deny_read + allow_read 等价于只 deny_read。建议文档澄清，或 `allow_read` 实际语义改为"仅施加 Allow Read，不保证覆盖 deny"。

### 10. 去掉 PROTECTED_DACL（win_acl.py，MAJOR #5）

**原问题**：基线/首轮 `grant_ace` 在 recursive 时设 `PROTECTED_DACL_SECURITY_INFORMATION`，切断工作区从父目录的继承链，且 `revoke` 时也不恢复，导致用户自己的继承读写权限永久丢失（工作区脱离继承链后，原属主的继承 Allow 权限消失）。

**修复方式**：`grant_ace` 与 `revoke_sandbox_acl` 都只设 `DACL_SECURITY_INFORMATION`，不设 `PROTECTED_DACL`，保留继承。

**评价**：🟢 修复正确。`PROTECTED_DACL` 是"切断继承"的强操作，仅在确需隔离时用；沙箱只需在 DACL 上增删显式 ACE，继承链应保留以避免属主权限丢失。注释也明确"revoke 时恢复继承"（实际是"从未切断"）。

### 11. revoke 按施加路径清单撤销（win_acl.py + process.py，MAJOR #6）

**原问题**：首轮 `revoke_sandbox_acl(workspace)` 只以 workspace 为根 `rglob` 扫描，漏掉系统路径（如 `%USERPROFILE%`）上预装的合成 SID ACE，stop 时这些 ACE 不被撤销，长期累积。

**修复方式**：`apply_sandbox_acl` 返回施加路径清单，`revoke_sandbox_acl(paths: list[str])` 按清单逐路径递归撤销；兼容旧调用（传单字符串退化为只扫该路径树）。

**评价**：🟢 修复正确。按施加清单撤销是幂等且完整的做法，不依赖"扫描 workspace 树"的隐式假设。兼容旧签名的 `isinstance(paths, str)` 分支降低了迁移风险。`process._stop_windows` 改用 `acl_paths`（apply 返回值）撤销，闭环。

### 12. win_proxy IP-allow 与 port-allow 不再 AND（win_proxy.py，MAJOR #10）

**原问题**：首轮 `EgressFilter.allow` 在同时有 IP allow 和 port allow 规则时做 AND（必须 IP 和端口都命中才放行），比 Linux iptables 的独立 ACCEPT 链严，`{allowed_ips:[10/8], allowed_ports:[443]}` 会错杀 `10.1.2.3:8443`（IP 命中但端口不在 allow）。

**修复方式**：改为 OR——IP allow 与 port allow 是两条独立 ACCEPT 规则，任一命中即放行。

**评价**：🟢 语义对齐 Linux iptables 独立 ACCEPT 链，修复了过度封锁。`test_ip_allow_and_port_allow_not_anded` 覆盖了 `10.1.2.3:8443` 在 `{allowed_ips:[10/8], allowed_ports:[443]}` 下应放行的回归。

🔴 **docstring 与实现矛盾**：`EgressFilter.allow` 的 docstring（win_proxy.py:106-113）仍写"若同时有 allowed_ips 和 allowed_ports: 必须两者都命中才放行 (AND)"，与改后的 OR 实现完全相反。`EgressFilter.__init__` 的类 docstring 也仍说"非空且未命中 -> 拒绝"（暗含 AND）。docstring 是安全语义的核心文档，与实现矛盾会误导后续维护者回退到 AND。**必须更新 docstring 为 OR 语义**。

🟡 **OR 语义的安全考量**：OR 比 AND 宽松。`{allowed_ips:[10/8], allowed_ports:[443]}` 下 `10.1.2.3:8443` 被放行（因 IP 命中）。若运营意图是"只放行 10/8 网段的 443 端口"，OR 会过度放行（10/8 的任意端口都通）。这是对齐 Linux iptables 的有意选择（Linux 也是独立链），但应在 policy 文档明确"allowed_ips 与 allowed_ports 是独立规则，任一命中即放行"，避免运营误配。

### 13. install/ensure/CLI 参数透传（win_setup.py，MAJOR #7/#9）

**原问题**：
- `install`/`ensure_windows_setup` 硬编码 `DEFAULT_PROXY_PORT_RANGE_*`，忽略 policy 的 `proxy.port_range_*`，WFP Permit 放行的端口与 win_proxy 实际监听端口不一致，代理路径被 Block 拦截（MAJOR #7）。
- `_elevate_and_run_install` 只传 `--install`，`force=True`/端口/preinstall 从非管理员进程调用时静默 no-op（MAJOR #9）。
- `_main` 只识别 `argv[0] == const.INSTALL_SUBCOMMAND`，不接 `--force`/端口/preinstall 参数。

**修复方式**：
- `install`/`ensure_windows_setup` 增 `proxy_port_start`/`proxy_port_end` 参数，透传到 `win_wfp.install_wfp_filters`。
- `_elevate_and_run_install` 构造完整命令行：`--force`（可选）、`--proxy-port-start N`、`--proxy-port-end N`、`--preinstall-paths JSON`，用 `_quote_arg` 处理含空格参数。
- `_main` 改用 `argparse` 子命令，规整 `--install`/`--uninstall` 前缀，`--preinstall-paths` 用 JSON 解码。
- `app.py` lifespan 与 `process._create_windows` 都从根 policy 读 `proxy.port_range_*` 透传给 `ensure_windows_setup`。

**评价**：🟢 修复正确且完整。端口范围配置化是代理路径能通的前提；CLI argparse 化让 UAC 提权子进程能接收全部参数；JSON 编码 preinstall-paths 避免路径含空格/引号的转义问题。`_quote_arg` 的双引号包裹规则符合 Windows 命令行约定。

🟡 **`_elevate_and_run_install` 不等提权子进程**：`ShellExecuteW("runas", ...)` 是异步的，返回后父进程立即 `return 0`，不等 install 子进程完成。`ensure_windows_setup` 调用方（lifespan/process）以为安装已发起但不知是否完成，若 install 子进程仍在跑 WFP 安装，ensure 调用方已继续启动沙箱 → WFP filter 可能未就绪。首轮报告已提"daemon 线程被强杀"，本 commit 的 join 等待只在提权子进程内部（install 函数里 join 预装线程），但父进程不等提权子进程。建议 ensure 在非管理员路径下 `ShellExecuteW` 后轮询注册表 `REG_VALUE_INSTALLED` 或等子进程 PID 退出。

### 14. 预装断点续传（win_setup.py，MAJOR #8）

**原问题**：首轮预装线程被 install 子进程退出强杀后，下次 install 从头再来，已完成的系统目录重复施加 ACL。

**修复方式**：`_preinstall_read_acl` 读 `REG_VALUE_READ_ACL_PROGRESS`（JSON 已完成路径集合），跳过已完成路径；每完成一个路径写一次进度；install 全部成功后清进度标记。

**评价**：🟢 修复合理。断点续传避免重复施加 ACL（幂等但费时）。每路径写进度是合理的持久化粒度。

🟡 **进度标记残留风险**：若 install 在"写 installed 标记"前崩溃（预装中途异常），`REG_VALUE_READ_ACL_PROGRESS` 残留，下次 install 会跳过这些路径——但若上次施加的 ACE 不完整（部分子项失败），跳过会导致这些子项永久缺 ACE。建议进度标记在每路径 ACL 施加**完全成功**后才记（当前是路径级 grant_ace 返回即记，grant_ace 内部 best-effort，单子项失败被外层 grant_ace 的 except 吞——实际上 grant_ace 对单路径要么成功要么抛，子项继承靠 ACE flags，不存在"部分子项失败"，故风险低）。

### 15. 测试新增（test_server_api_windows.py）

**评价**：🟢 测试覆盖显著增强（+170 行）：
- `TestWinWfpConstantsAndLayout`：GUID 合法性、loopback 字节序、`FWP_DATA_TYPE` 枚举、结构体非退化、`FWPM_SUBLAYER0.weight` 类型断言。这些是 WFP 能否在真实 Windows 工作的前置自洽性检查。
- `TestWinExecSidAndEnvBlock`：SID 字符串结构与常量编排推导、`_build_runner_command` 不变。
- `TestWindowsPolicyReadFields`：`allow_read`/`deny_read` 解析、默认空、`extra="forbid"`。
- `TestWinProxyEgressSemantics`：IP+port 不 AND、port-only、blocked 覆盖 allow。
- `TestProcessRuntimeWindowsBranch`：apply 返回路径清单、two_hop_spawn 5 元组、resume 调用断言。

🟡 **测试局限**：
- `test_build_ale_user_condition_uses_security_descriptor` 是 `@pytest.mark.skipif(True)` 占位（因需 win32security），ALE_USER_ID 的 SD 构造在 WSL 上**完全未测**。这是 WFP 最复杂、最易错的部分（pywin32 SD 构造 + 自相关 SD + FWP_BYTE_BLOB 保活），无任何单测覆盖，仅靠"win32 实跑"口头保证。建议至少 mock `win32security` 验证 `SetEntriesInAcl` 调用参数与 `MakeSelfRelativeSD` 返回的 blob 被正确写入 `conditionValue.value.sd`。
- `test_fwpm_struct_layout_is_nontrivial` 只断言 `sizeof > 0`，不验证精确尺寸/字段偏移。Linux 上 `wintypes.DWORD=8B` 与 Windows `4B` 不同，故无法做精确尺寸断言，但可断言字段偏移（`offsetof(FWPM_FILTER0, "flags")` 等）在 Linux/Windows 一致（偏移与平台无关，只取决于字段类型尺寸——而 `wintypes` 差异会影响偏移）。建议至少断言关键字段存在（已隐含在可构造性里）。
- `test_layer_and_condition_guids_match_sdk` 自洽而非交叉验证（见第 4 点）。

---

## 四、关键代码检视

### 4.1 `install_wfp_filters` 的 keeps 列表与 `_add_filter` 形参（win_wfp.py:540-602）

```python
keeps: list[object] = []
...
for layer, fkey in (...):
    block_cond, ka = _build_ale_user_condition(sandbox_user_sid)
    keeps.append(ka)
    _add_filter(engine, fkey, layer, sublayer_key, [block_cond],
                const.FWP_ACTION_BLOCK, const.FWP_WEIGHT_BLOCK,
                f"JiuwenBox-Block-{fkey}", keeps_alive=keeps)
...
for port in range(permit_port_start, permit_port_end + 1):
    user_cond, user_ka = _build_ale_user_condition(sandbox_user_sid)
    keeps.append(user_ka)
    if "V4" in base_key:
        lb_cond, lb_ka = _build_loopback_v4_condition()
    ...
    keeps.append(lb_ka)
    _add_filter(..., keeps_alive=keeps)
```

- 🟢 `keeps` 在 `install_wfp_filters` 栈帧持有所有 keep-alive 引用到 `FwpmTransactionCommit0` 返回，生命周期正确。
- 🟡 `_add_filter` 的 `keeps_alive` 形参（win_wfp.py:497）**函数体内从未引用**（dead parameter）。keep-alive 靠调用方 `keeps` 栈帧，不靠该形参。建议删除该形参，避免误导。

### 4.2 `_create_windows` 的 resume 三路与句柄关闭（process.py:2844-2892）

```python
if not resource.is_empty():
    try:
        job = win_job.create_job(...)
        win_job.assign_process_by_pid(job, runner_pid)
        try:
            win_job.resume_process(thread_handle)
        except Exception:  # 内层 except
            logger.warning(...)
        self._win_job_handles[sandbox_id] = job
    except Exception:  # 外层 except (Job 失败)
        ...
        try:
            win_job.resume_process(thread_handle)
        except Exception: ...
else:
    try:
        win_job.resume_process(thread_handle)
    except Exception: ...
try:
    kernel32.CloseHandle(wintypes.HANDLE(thread_handle))
except Exception: ...
self._win_runners[sandbox_id].pop("thread_handle", None)
```

- 🔴 **resume 失败 + CloseHandle 路径**（process.py:2855-2859, 2886）：`assign` 成功后 `resume` 抛异常 → 内层 except 吞 → 外层 try 未进 except（因 assign 成功）→ 直接到 `CloseHandle(thread_handle)` 关闭线程句柄。runner 永久挂起，无路径再 resume。`TerminateProcess` 仍可清理（挂起进程可被 Terminate），但沙箱静默不可用且无显式错误。建议 resume 失败时 `raise` 让外层 Job except 兜底 resume，或标记 sandbox 不可用。

### 4.3 `_build_ale_user_condition` 的 SD 构造（win_wfp.py:418-465）

```python
dacl = win32security.ACL()
dacl.SetEntriesInAcl([explicit])
sd = win32security.SECURITY_DESCRIPTOR()
sd.SetSecurityDescriptorDacl(1, dacl, 0)
sd_bytes = win32security.MakeSelfRelativeSD(sd)
blob = FWP_BYTE_BLOB()
buf = (ctypes.c_uint8 * len(sd_bytes)).from_buffer_copy(sd_bytes)
blob.size = len(sd_bytes)
blob.data = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8))
cond.conditionValue.value.sd = ctypes.cast(ctypes.pointer(blob), ctypes.c_void_p).value
return cond, _KeepAlive(blob=blob, buf=buf, sd_bytes=sd_bytes)
```

- 🟢 SD 构造链对齐 SDK 示例。`MakeSelfRelativeSD` 得自相关 SD bytes，`from_buffer_copy` 复制到 ctypes 数组保活，`FWP_BYTE_BLOB` 持 size+data 指针，`_KeepAlive` 持全部引用。
- 🟡 `dacl.SetEntriesInAcl([explicit])` 返回值被忽略（见第三节第 2 点）。pywin32 版本差异下可能 DACL 未生效。建议显式取返回值或用模块级函数。

### 4.4 `_elevate_and_run_install` 不等子进程（win_setup.py:366-392）

```python
result = shell32.ShellExecuteW(None, "runas", py, params, None, SW_SHOWNORMAL)
if result <= 32:
    raise RuntimeError(...)
logger.info("已通过 UAC 提权运行 install 子进程 (force=%s)", force)
return 0
```

- 🟡 `ShellExecuteW` 异步返回，父进程立即 `return 0`，不等提权子进程完成（见第三节第 13 点）。ensure 调用方不知 install 是否完成。

### 4.5 `EgressFilter.allow` docstring 与实现矛盾（win_proxy.py:106-113）

```python
def allow(self, host, port):
    """...
    2. 若同时有 allowed_ips 和 allowed_ports: 必须两者都命中才放行 (AND).  # ← 旧 AND 语义
    3. 若只有 allowed_ports (无 allowed_ips/domains): 放行该端口所有 IP.
    4. 若只有 allowed_ips/domains (无 allowed_ports): 放行这些 IP/域名的
       所有端口.
    ..."""
    ...
    # 4a. allow 规则按维度独立判定 (OR) ...  # ← 新 OR 实现
    if has_ip_rules or has_port_rules:
        reasons = []
        if domain_allowed: reasons.append("domain")
        if ip_allowed: reasons.append("ip")
        if port_in_allow: reasons.append("port")
        if reasons: return True, ...
```

- 🔴 docstring 第 2 条仍是旧 AND 语义，与实现（OR）完全相反。必须更新。

### 4.6 `uninstall_wfp_filters` 默认范围与 install 实际范围不匹配（win_wfp.py:611-634）

```python
def uninstall_wfp_filters(
    permit_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    permit_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
) -> None:
    ...
    for base_key in (const.JBX_FILTER_PERMIT_KEY_V4, const.JBX_FILTER_PERMIT_KEY_V6):
        for port in range(permit_port_start, permit_port_end + 1):
            _delete_filter_by_key(fwpu, engine, f"{base_key}-{port}")
```

- 🟡 `uninstall()` 调 `uninstall_wfp_filters()` 用默认范围。若 install 时 policy 端口范围非默认（如 60080-60099），卸载只删 60080-60089，60090-60099 的 Permit filter 残留。首轮报告已提，本 commit 未修。建议 uninstall 接收实际范围或从注册表读已装范围。

---

## 五、优点

1. **WFP ctypes 结构体全面重写**是本 commit 最大亮点，修正了 `FWPM_FILTER0`/`SUBLAYER0`/`SESSION0` 字段错位、`FWP_VALUE0`/`CONDITION_VALUE0` 联合体成员缺失、`v4AddrMask` 内嵌 vs 指针等十余处布局 bug，是 WFP 在真实 Windows 上能工作的前提。
2. **ALE_USER_ID 改用 SECURITY_DESCRIPTOR + FWP_ACTRL_MATCH_FILTER** 对齐 SDK 标准做法，是 user-keyed 出站拦截的正确实现。
3. **CRITICAL #1 SID 一致性修复**（nSubAuthorityCount 3→4）解决了 ACL 授权的 SID 与受限 token 携带的 SID 不是同一个的硬伤，写权限隔离才能真正生效。
4. **CRITICAL #3 env 块悬垂指针修复**消除了子进程环境错乱/崩溃的隐蔽 bug。
5. **CRITICAL #4 loopback 字节序修正**（网络序→host 序）让 Permit filter 真正匹配 127.0.0.1，代理路径能通。
6. **CRITICAL #2 GUID/key 合法化**让 WFP 安装不再首行 ValueError。
7. **CREATE_SUSPENDED + SUSPEND→Assign→Resume** 消除 Job 逃逸窗口，对齐设计 6.8。
8. **ACL 读控制补全 + workspace 默认 Allow Read** 补齐了读权限缺失。
9. **revoke 按施加清单撤销** 闭环了 ACL 生命周期，不漏系统路径 ACE。
10. **install/CLI 参数透传 + 预装断点续传** 让非管理员路径下 force/端口/preinstall 可用，预装可恢复。
11. **测试覆盖显著增强**：WFP 常量/结构体自洽性、SID 一致性、policy read 字段、proxy OR 语义、resume/apply mock 断言，覆盖了大部分修复点的回归。

---

## 六、问题与风险

### 🔴 严重

1. **`EgressFilter.allow` docstring 与实现矛盾**（win_proxy.py:106-113）：docstring 仍写 AND 语义，实现已改 OR。安全语义文档与代码相反，误导后续维护者可能回退到 AND。**必须更新 docstring**。

2. **resume 失败后 runner 永久挂起 + 句柄泄漏**（process.py:2855-2859, 2886）：`assign` 成功后 `resume` 抛异常被内层 except 吞，外层不进 except，直接 `CloseHandle(thread_handle)`。runner 永久挂起，线程句柄已关无法再 resume，沙箱静默不可用。`TerminateProcess` 可清理但无显式错误。建议 resume 失败 `raise` 或标记不可用。

### 🟡 中等

3. **`dacl.SetEntriesInAcl` 返回值忽略**（win_wfp.py:445）：pywin32 版本差异下 DACL 可能未生效，BFE 评估时无 ACE 授 `FWP_ACTRL_MATCH_FILTER`，沙箱用户连接全被 Block（过度封锁，安全侧无害但不符预期）。需真实 Windows + 当前 pywin32 验证，或显式取返回值。

4. **`uninstall_wfp_filters` 默认范围与 install 实际范围不匹配**（win_wfp.py:611-634）：policy 改端口范围后卸载残留旧 Permit filter。首轮已提，本 commit 未修。建议 uninstall 接收实际范围或持久化已装范围。

5. **`_elevate_and_run_install` 不等提权子进程**（win_setup.py:366-392）：`ShellExecuteW` 异步返回，ensure 调用方不知 install 是否完成，WFP filter 可能未就绪就启动沙箱。建议轮询 `REG_VALUE_INSTALLED` 或等子进程退出。

6. **`deny_read` + `allow_read` 同路径语义与注释不符**（win_acl.py:apply_sandbox_acl）：注释说"allow 覆盖 deny"，但 NTFS 显式 Deny 优先于显式 Allow，同路径上 allow 实际无效。建议文档澄清或改语义。

7. **ALE_USER_ID SD 构造无单测覆盖**（win_wfp.py `_build_ale_user_condition`）：`test_build_ale_user_condition_uses_security_descriptor` 是 `skipif(True)` 占位，WFP 最复杂的 SD 构造 + pywin32 调用 + 自相关 SD + blob 保活完全无测。建议 mock `win32security` 验证调用参数与 blob 写入。

### 🟢 轻微

8. **`_add_filter` 的 `keeps_alive` 形参未使用**（win_wfp.py:497）：dead parameter，keep-alive 靠调用方栈帧。建议删除。

9. **`AllocateAndInitializeSid.argtypes` 第 3 参数误为 `c_void_p`**（win_exec.py:163-167）：应为 `wintypes.DWORD`。x64 下功能正确（栈槽同尺寸），类型不严谨。

10. **`AllocateAndInitializeSid` argtypes 数量（11）少于调用参数（13）**：ctypes varargs 默认处理多出参数为 c_int，功能无碍但类型不严谨。

11. **`win_job` 与 `win_exec` 各持一份 kernel32 WinDLL 缓存**：冗余，功能无碍。

12. **进度标记残留风险**（win_setup.py `_preinstall_read_acl`）：install 崩溃后进度残留，下次跳过已完成路径。grant_ace 对单路径要么成功要么抛，风险低。

---

## 七、改进建议

1. **[必修]** 更新 `EgressFilter.allow` 的 docstring（win_proxy.py:106-113）为 OR 语义，与实现一致；同步 `EgressFilter.__init__` 类 docstring。
2. **[必修]** `process._create_windows` 的 resume 失败路径：内层 except 改为 `raise`（让外层 Job except 也走 resume 兜底，或直接 fail create 并标记 sandbox 不可用），避免 runner 永久挂起 + 句柄泄漏。
3. **[建议]** `win_wfp._build_ale_user_condition` 显式取 `dacl.SetEntriesInAcl` 返回值（`dacl = dacl.SetEntriesInAcl([explicit]) or dacl`），或改用模块级 `win32security.SetEntriesInAcl`，防 pywin32 版本差异。
4. **[建议]** `uninstall_wfp_filters`/`uninstall` 接收实际端口范围参数，或 install 时把已装范围持久化到注册表，卸载时读取。
5. **[建议]** `ensure_windows_setup` 在非管理员路径下 `ShellExecuteW` 后轮询 `REG_VALUE_INSTALLED` 或等子进程 PID 退出，确保 install 完成才返回。
6. **[建议]** `win_acl.apply_sandbox_acl` 文档澄清 `deny_read` + `allow_read` 同路径的 NTFS 评估顺序（Deny 优先，allow 不覆盖）。
7. **[建议]** 为 `_build_ale_user_condition` 补 mock `win32security` 的单测，验证 `SetEntriesInAcl` 调用参数、`MakeSelfRelativeSD` 返回 blob 被正确写入 `conditionValue.value.sd`、`_KeepAlive` 持引用。
8. **[建议]** 删除 `_add_filter` 的 `keeps_alive` dead parameter；`AllocateAndInitializeSid.argtypes` 第 3 参数改 `wintypes.DWORD`。
9. **[建议]** `test_layer_and_condition_guids_match_sdk` 补一例：在真实 Windows 上用 `FwpmLayerGetByKey0` 反查 layer GUID 做端到端交叉验证（标记 `@pytest.mark.skipif(sys.platform != "win32")`）。
10. **[建议]** 测试补一例：resume 失败后 sandbox 标记不可用（回归第 2 点）；`uninstall` 在 install 用非默认端口范围后无残留 filter（回归第 4 点）。

---

## 八、与前两 commit 的演进对比

| 维度 | `5f841f7a`（基线 feat） | `c2c3f5f0`（首轮 review） | `bb1afca0`（二轮 review2） |
|------|------------------------|------------------------|--------------------------|
| WFP 结构体 | ctypes 字段错位（多 providerDataSize、缺 flags、weight 误 uint32、displayData 误 c_void_p） | 未触及 | ✅ 全面对齐 SDK（内嵌 DISPLAY_DATA、weight uint16、补 flags/reserved/filterId/effectiveWeight） |
| WFP GUID/key | 描述性字符串（非法 UUID）+ 虚构 layer/condition GUID | 未触及 | ✅ key 改合法 UUID、layer/condition GUID 对齐 fwpmu.h |
| FWP_DATA_TYPE 枚举 | FWP_SID=12 等错位 | 未触及 | ✅ 全量对齐 fwptypes.h |
| loopback 字节序 | 网络序 0x0100007F | 未触及 | ✅ host 序 0x7F000001 |
| ALE_USER_ID | 裸 SID（FWP_SID） | 未触及 | ✅ 改用 SD（FWP_SECURITY_DESCRIPTOR_TYPE + FWP_ACTRL_MATCH_FILTER） |
| SID 一致性 | nSubAuthorityCount=3（缺 21） | 未触及 | ✅ nSubAuthorityCount=4 |
| env 块 | 悬垂指针（buf 被回收） | 未触及 | ✅ 内联持有 buf |
| Job 逃逸窗口 | 直接启动（非 SUSPEND） | 未触及 | ✅ CREATE_SUSPENDED + Resume |
| ACL 读控制 | 缺失 | 未触及 | ✅ allow_read/deny_read + workspace 默认 Allow Read |
| ACL revoke 范围 | 只扫 workspace | 只扫 workspace | ✅ 按施加清单撤销 |
| PROTECTED_DACL | 切断继承 | 切断继承 | ✅ 保留继承 |
| proxy 端口范围 | 硬编码默认 | 硬编码默认 | ✅ policy 透传 |
| CLI 参数 | 不接 force/端口/preinstall | 不接 | ✅ argparse + 全参数透传 |
| 预装断点续传 | 无 | 无（daemon 线程被强杀） | ✅ 进度标记 + 跳过已完成 |
| proxy OR 语义 | AND | AND | ✅ OR（对齐 iptables） |
| 测试 | 基础 mock | +3 ACL 单测 | +170 行（WFP 自洽性、SID、policy、proxy OR、resume） |

**演进判断**：基线 `5f841f7a` 是功能骨架，存在大量"能 import 但真实 Windows 不工作"的硬伤；首轮 `c2c3f5f0` 修了 IPC/ACL 顺序/Job 内存等运行时逻辑，但**未触及 WFP ctypes 与 GUID 这一层**（首轮报告也指出 ACL 修复在真实环境因 `GetAclSize`/`_parse_getace_tuple` 不生效）；本 commit `bb1afca0` 集中攻 WFP ctypes 结构体 + GUID + SID + 字节序 + env + SUSPEND + 读控制 + CLI，是让 Windows 沙箱"真正能跑起来"的关键一轮。演进方向合理，修复深度逐层深入（运行时逻辑 → ctypes/SDK 对齐 → 安全语义补全）。

---

## 九、小结

本 commit 是 Windows 沙箱适配的关键修复轮，WFP ctypes 结构体重写、GUID 合法化、SID 一致性、loopback 字节序、ALE_USER_ID 改用 SD、env 块保活、SUSPEND→Assign→Resume、ACL 读控制、CLI 参数透传等十余项修复**方向全部正确，深度到位**，是让沙箱在真实 Windows 上从"import 自洽"走向"功能可用"的必要修复。测试覆盖也显著增强。

遗留风险集中在：① `win_proxy.allow` docstring 与 OR 实现矛盾（必须修）；② resume 失败后 runner 永久挂起 + 句柄泄漏（必须修）；③ `dacl.SetEntriesInAcl` 返回值忽略（需真实环境验证）；④ uninstall 端口范围不匹配（首轮遗留未修）；⑤ `_elevate_and_run_install` 不等子进程；⑥ ALE_USER_ID SD 构造无单测。建议优先修①②，其余按真实 Windows 验证结果决定。

整体评价：**通过，但建议修①②后合入**。WFP 层修复质量高，是本 commit 的核心价值；运行时层（process.py resume 路径）与文档层（win_proxy docstring）各有一处需补丁。
