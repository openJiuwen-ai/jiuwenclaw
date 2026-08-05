# Windows 沙箱 Review 问题修复设计

> 依据 `docs/window沙箱.md` 第6章 + `.claude/plans/windows-sandbox.md`，针对 review 发现的 CRITICAL/MAJOR 问题修复。
> 范围：WFP（修结构体 + 保留 PowerShell 降级）、读控制（补 policy 字段 + 实现），以及 SID 一致性、环境块悬垂指针、Job SUSPEND、端口硬编码、force 幂等、proxy 语义、revoke 范围等。

## 一、问题清单与根因

### CRITICAL（阻断沙箱运行）

| # | 问题 | 根因 | 位置 |
|---|------|------|------|
| C1 | 合成 SID 两处生成不一致，写隔离失效 | `win_acl` 按字符串拼 `S-1-5-21-...`（带 `21`）；`win_exec.AllocateAndInitializeSid` 用 `nSubAuthorityCount=3` 产 `S-1-5-...`（缺 `21`）。ACL 授权的 SID 与受限 token 携带的 SID 不是同一个 | `win_acl.py:59-69`, `win_exec.py:443-450` |
| C2 | WFP sublayer/filter key 非 UUID，`install_wfp_filters` 首行抛 `ValueError` | key 是普通字符串（"JiuwenBox-Windows-Sandbox-Sublayer"），`_guid_from_str` 调 `uuid.UUID()` 解析失败 | `win_constants.py:279-285`, `win_wfp.py:64-72` |
| C3 | 环境块悬垂指针，子进程拿释放后内存 | `_build_environment_block` 内 `buf` 是局部对象，返回 `c_void_p` 后 buf 被 GC，`CreateProcessAsUserW` 读已释放内存 | `win_exec.py:546-552` |
| C4 | loopback 字节序错，Permit 放行 1.0.0.127 而非 127.0.0.1 | `LOOPBACK_IPV4_INT=0x0100007F`（网络序），WFP 要求 host order `0x7F000001` | `win_constants.py:292`, `win_wfp.py:314` |
| C5 | **WFP 所有 layer/condition GUID 常量是虚构值**，与 Windows SDK 不符 | 当前值（如 `FWPM_LAYER_ALE_AUTH_CONNECT_V4="C38D57D1-05A0-4E9C-886C-509CF8E61F74"`）与 SDK 真实值（`C38D57D1-05A7-4C33-900F-7FBCEEE60E82`）完全不同；4 个 condition GUID 全错。即使结构体修对，filter 也匹配不到任何字段 | `win_constants.py:240-246` |

### MAJOR

| # | 问题 | 根因 | 位置 |
|---|------|------|------|
| M1 | Job 无 SUSPEND→Assign→Resume，存在 Job 逃逸窗口 | `assign_process*` 直接 Assign，runner 已在跑且可能已 fork child | `win_job.py:184-210`, `process.py:_create_windows` |
| M2 | WFP ctypes 结构体与 SDK 布局不符 | `FWPM_FILTER0` 多了不存在的 `providerDataSize`、缺 `flags`；`FWPM_SUBLAYER0` 同；`FWPM_SESSION0` `displayData` 应内嵌 `FWPM_DISPLAY_DATA0`（16B）而非 `c_void_p`（8B），字段全错位 | `win_wfp.py:179-218` |
| M3 | `FWP_DATA_TYPE` 常量与 SDK 枚举错位 | `FWP_SID=12`（实为 `FWP_CHAR8`），真值 `FWP_SID=13`；`FWP_BYTE_BLOB_TYPE=16`（真值 12）等 | `win_constants.py:257-276` |
| M4 | 读控制 deny-then-allow 未实现 | `apply_sandbox_acl` 只有写控制，无 `deny_read/allow_read` | `win_acl.py`, `WindowsFilesystemPolicy` |
| M5 | `PROTECTED_DACL` 永久切断继承 | `grant_ace` recursive 时总设 PROTECTED 且 revoke 不恢复 | `win_acl.py` |
| M6 | `revoke_sandbox_acl` 只扫 workspace 树，系统路径 ACE 残留 | revoke 根为 workspace，预装 ACE 不在树内 | `win_acl.py` |
| M7 | WFP 安装硬编码默认端口，忽略 policy `port_range` | `win_setup._install` 用 `DEFAULT_PROXY_PORT_RANGE_*`，不取 policy | `win_setup.py:484-488` |
| M8 | 读 ACL 预装进度未持久化，断点续传是空话 | `REG_VALUE_READ_ACL_PROGRESS` 定义但从不读写 | `win_setup.py:374-395` |
| M9 | `force=True` 从非管理员进程静默 no-op | `_elevate_and_run_install` 不转发 `--force`/`--preinstall-paths`，CLI 也不接 | `win_setup.py:350-368, 614+` |
| M10 | proxy allow 语义与 Linux `network.py` 分叉 | IP-allow 与 port-allow 同时存在时做 AND，比 Linux 严 | `win_proxy.py:161-164` |

### NIT（择要）
- `install_wfp_filters` 重复 `import ctypes`、SID 指针未 `LocalFree` 泄漏（`win_wfp.py:404, 410-414`）
- `_add_filter` docstring 说"先删后加"，实际只忽略 ALREADY_EXISTS（`win_wfp.py:361`）
- `_exec_background_windows` 降级为同步（`process.py:2960-2987`）— 已注释为已知限制，保留
- `configure_logging()` 在模块顶层有副作用（多个 win_*.py）

## 二、SDK 权威依据（已查 Windows SDK fwpmtypes.h / fwpmu.h）

### FWP_DATA_TYPE 枚举真值
```
FWP_EMPTY=0, FWP_UINT8=1, FWP_UINT16=2, FWP_UINT32=3, FWP_UINT64=4,
FWP_INT8=5, FWP_INT16=6, FWP_INT32=7, FWP_INT64=8, FWP_FLOAT=9,
FWP_DOUBLE=10, FWP_BYTE_ARRAY16_TYPE=11, FWP_BYTE_BLOB_TYPE=12,
FWP_SID=13, FWP_SECURITY_DESCRIPTOR_TYPE=14, FWP_TOKEN_INFORMATION_TYPE=15,
FWP_TOKEN_ACCESS_INFORMATION_TYPE=16, FWP_UNICODE_STRING_TYPE=17,
FWP_BYTE_ARRAY6_TYPE=18, FWP_SINGLE_DATA_TYPE_MAX=0xff,
FWP_V4_ADDR_MASK=256, FWP_V6_ADDR_MASK=257, FWP_RANGE_TYPE=258
```

### 结构体真值布局（x64）
```
FWPM_DISPLAY_DATA0 { wchar_t* name; wchar_t* description; }   // 16B, 内嵌
FWP_BYTE_BLOB { UINT32 size; UINT8* data; }
FWP_V4_ADDR_AND_MASK { UINT32 addr; UINT32 mask; }            // host byte order
FWP_V6_ADDR_AND_MASK { UINT8 addr[16]; UINT8 prefixLength; }
FWP_VALUE0 { FWP_DATA_TYPE type; union{ uint8..int64, float, double,
           byteArray16*, byteBlob*, sid*, sd*, ..., unicodeString, byteArray6* } }
FWP_CONDITION_VALUE0 { type; union{ ..., v4AddrMask*, v6AddrMask*, rangeValue* } }
FWPM_FILTER_CONDITION0 { GUID fieldKey; FWP_MATCH_TYPE matchType; FWP_CONDITION_VALUE0 conditionValue; }
FWPM_ACTION0 { FWP_ACTION_TYPE type; union{ GUID filterType; GUID calloutKey; } }  // union 占 GUID
FWPM_FILTER0 { GUID filterKey; FWPM_DISPLAY_DATA0 displayData; UINT32 flags;
   GUID* providerKey; FWP_BYTE_BLOB providerData; GUID layerKey; GUID subLayerKey;
   FWP_VALUE0 weight; UINT32 numFilterConditions; FWPM_FILTER_CONDITION0* filterCondition;
   FWPM_ACTION0 action; union{ UINT64 rawContext; GUID providerContextKey; };
   GUID* reserved; UINT64 filterId; FWP_VALUE0 effectiveWeight; }
FWPM_SUBLAYER0 { GUID subLayerKey; FWPM_DISPLAY_DATA0 displayData; UINT32 flags;
   GUID* providerKey; FWP_BYTE_BLOB providerData; UINT16 weight; }   // weight 是 UINT16!
FWPM_SESSION0 { GUID sessionKey; FWPM_DISPLAY_DATA0 displayData; UINT32 flags;
   UINT32 txnWaitTimeoutInMSec; DWORD processId; SID* sid; wchar_t* username; BOOL kernelMode; }
```

### GUID 真值（fwpmu.h DEFINE_GUID）
```
FWPM_LAYER_ALE_AUTH_CONNECT_V4 = C38D57D1-05A7-4C33-900F-7FBCEEE60E82
FWPM_LAYER_ALE_AUTH_CONNECT_V6 = 4A72393B-319F-44BC-84C3-BA54DCB3B6B4
FWPM_CONDITION_ALE_USER_ID     = AF043A0A-B34D-4F86-979C-C90371AF6E66
FWPM_CONDITION_IP_REMOTE_ADDR = B235AE9A-1D64-49B8-A44C-5FF3D9095045
FWPM_CONDITION_IP_REMOTE_PORT= C35A604D-D22B-4E1A-91B4-68F674EE674B
```
当前代码这 5 个全是虚构值，必须替换。

### ALE_USER_ID 匹配机制（关键认知，已决策：实现 SD-based 条件）
`FWPM_CONDITION_ALE_USER_ID` 的值类型是 **`FWP_SECURITY_DESCRIPTOR_TYPE`**（自相关 SD + DACL 授 `FWP_ACTRL_MATCH_FILTER` 给目标用户），**不是裸 `FWP_SID`**。SDK 示例（Permitting and Blocking Applications and Users）用 `BuildSecurityDescriptorW` 构造。
→ **决策：实现 SD-based 条件**。用 `win32security` 构造：`BuildExplicitAccessWithName(jbx-sandbox, FWP_ACTRL_MATCH_FILTER, GRANT_ACCESS)` → `SetEntriesInAcl` → `SetSecurityDescriptorDacl` → `MakeSelfRelativeSD`，得到自相关 SD 字节，包进 `FWP_BYTE_BLOB`，condition value.type=`FWP_SECURITY_DESCRIPTOR_TYPE`、value.sd=&blob。SD/blob 生命周期存活到 `FwpmFilterAdd0` 返回。
→ PowerShell `New-NetFirewallRule -LocalUser` 降级保留（WFP ctypes 失败时兜底）。

## 三、修复方案（按模块）

### 1. `win_constants.py`（修 C2/C4/C5/M3）
- **C5**：5 个 layer/condition GUID 全部替换为 SDK 真值（见上）。
- **C2**：sublayer/filter key 改为**合法 UUID 字符串**（保留语义名 + 固定 UUID），如：
  - `JBX_SUBLAYER_KEY = "8F2A1B3C-4D5E-6F70-8190-123456789ABC"`（新生成固定 UUID）
  - 4 个 filter key 同理各定一个固定 UUID。`install/uninstall` 仍按 key 幂等。
- **C4**：`LOOPBACK_IPV4_INT = 0x7F000001`（host order 127.0.0.1），更新注释。
- **M3**：`FWP_DATA_TYPE` 枚举按 SDK 真值重排：`FWP_BYTE_ARRAY16_TYPE=11, FWP_BYTE_BLOB_TYPE=12, FWP_SID=13, FWP_SECURITY_DESCRIPTOR_TYPE=14, ...`；新增 `FWP_V4_ADDR_MASK=256`、`FWP_V6_ADDR_MASK=257`（替换错位的别名）。删掉 `FWP_V4_ADDR_MASK_TYPE` 别名或改为指向 256。
- 补 `FWPM_SESSION_FLAG_DYNAMIC=0x1`（session 用）。

### 2. `win_wfp.py`（修 C2/C4/M2 + SD 条件）
- **M2** 结构体重写对照 SDK：
  - 新增 `FWPM_DISPLAY_DATA0`（内嵌，两 `wchar_t*`）。
  - `FWPM_FILTER0` 按真值布局（去 `providerDataSize`、加 `flags`、`reserved` 改 `GUID*`、补 `filterId`/`effectiveWeight`）。
  - `FWPM_SUBLAYER0`：去 `providerDataSize`，`weight` 改 `UINT16`。
  - `FWPM_SESSION0`：`displayData` 内嵌，去重复 `txnWait*` 字段，补 `processId/sid/username/kernelMode`（即使不设也要占位保尺寸）。
  - `FWP_VALUE0`/`FWP_CONDITION_VALUE0` union 补 `sd`(FWP_BYTE_BLOB*)、`v4AddrMask`(指针)、`v6AddrMask`(指针)。
- **C2** `_guid_from_str`：保留 `uuid.UUID(s).bytes_le` 解析逻辑（key 改合法 UUID 后即可正常）。
- **C4** `_build_loopback_v4_condition`：`addr` 用 `const.LOOPBACK_IPV4_INT`(0x7F000001)。**条件值用 `FWP_V4_ADDR_MASK` 类型，value 设 `v4AddrMask` 指针**（指向一个本地 `FWP_V4_ADDR_AND_MASK` 实例，需保持其生命周期到 FwpmFilterAdd0 返回）。
- **ALE_USER_ID 条件**：值类型改 `FWP_SECURITY_DESCRIPTOR_TYPE`，value.sd 指向一个含 DACL（授 `FWP_ACTRL_MATCH_FILTER` 给 jbx-sandbox 用户 SID）的自相关 SD 的 `FWP_BYTE_BLOB`。用 `win32security` 构造 SD（`BuildExplicitAccessWithName` + `SetEntriesInAcl` + `SetSecurityDescriptorDacl` + `MakeSelfRelativeSD`）。失败则该 filter 不装（降级路径兜底）。
- **生命周期**：所有 condition 引用的 SID/SD/blob 指针，必须存活到 `FwpmFilterAdd0` 返回；用局部变量持有引用。
- **LocalFree 泄漏**：`ConvertStringSidToSidW` 返回的 SID 指针在 filter 安装完成后 `LocalFree`（用 `kernel32`/`LocalFree`，或 `win32api.LocalFree`）。
- **幂等**：`_add_filter` docstring 改为"忽略 ALREADY_EXISTS"（不删后加），避免误导。
- 保留 `install_firewall_rule_fallback`（PowerShell）不变。

### 3. `win_exec.py`（修 C1/C3）
- **C1**：`_get_synthetic_write_sid_ptr` 改为 `nSubAuthorityCount=4`，第一个 sub-authority 传 `21`：
  ```
  AllocateAndInitializeSid(SID_AUTH_NT, 4,
      21,  # sub0 = 21 (使 SID = S-1-5-21-...)
      SUBAUTHS[0], SUBAUTHS[1], RID,
      0,0,0,0,0, &sid)
  ```
  产出的 SID 与 `win_acl.get_synthetic_write_sid()` 字符串版（`S-1-5-21-...`）一致。删掉错误注释。
  - 另加一个跨模块一致性单测：`ConvertSidToStringSid` 把 runner 产出的 SID 转字符串，断言等于 `win_acl.get_synthetic_write_sid()`（mock 路径下用常量直接比对，不跑真 API）。
- **C3**：`_build_environment_block` 不再返回悬垂指针。改为返回 `(c_void_p, buf_obj)` 或把 buf 生命周期绑到 `FWP_BYTE_BLOB`/调用方持有；最简做法：让 `_create_process_as_user` 内联构造 env block 并持有局部 `buf` 引用直到 `CreateProcessAsUserW` 返回。

### 4. `win_job.py`（修 M1）
- `create_job` 仍返回 handle。
- `assign_process_by_pid`/`assign_process`：保持直接 assign（runner 已起）。**但对 runner 第一跳改用 `CREATE_SUSPENDED`**（见 `process.py` 改动），assign 后 `ResumeThread`。新增 `resume_process(process_handle)` 调 `ResumeThread`（kernel32，对主线程句柄）。
  - 因 `CreateProcessWithLogonW` 拿不到 thread handle（PROCESS_INFORMATION.hThread 在 broker 侧关闭了），需保留 `pi.hThread` 到 assign 完成。改 `two_hop_spawn` 返回 `(pid, stdin_w, stdout_r, proc_handle, thread_handle)`，assign/resume 后 CloseHandle(thread)。

### 5. `win_acl.py`（修 C1 配合 + M4/M5/M6）
- **M4 读控制（已决策：含 workspace 默认）**：`apply_sandbox_acl` 增 `allow_read`/`deny_read` 参数。语义接近设计 6.7 全局 deny-then-allow：
  - 对 `deny_read` 路径施加 Deny Read ACE（合成 SID）。
  - 对 `allow_read` 路径施加 Allow Read ACE（合成 SID）覆盖 deny。
  - **workspace 默认**：若 policy 未显式列 `allow_read`，对 workspace 根施加 Allow Read ACE（合成 SID），使沙箱至少能读自己工作区；其余路径默认不可读（独立用户 + 预装 ACL 控制）。
  - 新增 `grant_read_ace`（Allow Read）/`deny_read_ace`（Deny Read）。
- **M5 继承**：`grant_ace` 不再默认设 PROTECTED_DACL；只在显式需要时（隔离工作区）才设，且 `revoke_sandbox_acl` 恢复继承。
- **M6 revoke 范围**：`apply_sandbox_acl` 返回施加 ACE 的全部路径清单（含 workspace 默认 + policy 列出）；`revoke_sandbox_acl` 接清单遍历撤销。`_create_windows` 存清单到 `_win_acl_paths`（dict → 含 paths list）。

### 6. `models/policy.py`（M4 配合）
- `WindowsFilesystemPolicy` 增字段：
  ```
  allow_read: list[str] = Field(default_factory=list)
  deny_read: list[str] = Field(default_factory=list)
  ```
  走与 `allow_write`/`deny_write` 相同的 `expand_path_lists` validator。
- `WindowsResourcePolicy` 不动。

### 7. `win_setup.py`（修 M7/M8/M9）
- **M7 端口**：`_install` 接收 `proxy_port_start/end` 参数（从 policy 读），传给 `install_wfp_filters` 与降级；`ensure_windows_setup(preinstall_paths, proxy_port_start, proxy_port_end)` 透传。`app.py` lifespan 调用时传 root policy 的 `windows.proxy.port_range_*`。
- **M8 进度**：`_preinstall_read_acl` 每完成一个路径写 `REG_VALUE_READ_ACL_PROGRESS`（已完成列表）；下次 install 先读进度跳过已完成。`_preinstall_read_acl_async` join 后写最终完成。
- **M9 force/CLI**：`_elevate_and_run_install` 转发 `--force`/`--preinstall-paths`（JSON 编码进参数）；`_main` CLI 接 `--force`、`--preinstall-paths`、`--proxy-port-start/end`。

### 8. `win_proxy.py`（修 M10）
- `EgressFilter.allow()`：去掉 IP-allow 与 port-allow 的隐式 AND；改为「任一维度命中即放行」（domain/IP 任一在 allow 即放行，port 单独校验但与 IP 不做 AND），对齐 Linux iptables 独立 ACCEPT 语义。或更保守：保留各自独立判定，只在两者都为空时按 default。
- 补单测覆盖 `allowed_ips + allowed_ports` 不做 AND。

### 9. `process.py`（C1 配合 + M1 配合 + M4 配合 + M6 配合）
- `_create_windows`：
  - 传 `allow_read`/`deny_read` 给 `apply_sandbox_acl`。
  - Job：runner 用 `CREATE_SUSPENDED` 起（`win_exec.two_hop_spawn` 新增返回 thread_handle），`assign_process` 后 `win_job.resume_process(thread_handle)` 再 `CloseHandle`。
  - apply 返回施加路径清单存 `_win_acl_paths[sandbox_id]`（dict → 含 paths list）。
- `_stop_windows`：`revoke_sandbox_acl` 传清单。

### 10. `app.py`（M7 配合）
- lifespan 调 `ensure_windows_setup(preinstall_paths=_preinstall, proxy_port_start=root_policy.windows.proxy.port_range_start, proxy_port_end=...)`。

## 四、测试补充

`test_server_api_windows.py` 增补：
- `test_wfp_layer_and_condition_guids_match_sdk`：断言 5 个 GUID 等于 SDK 真值。
- `test_wfp_sublayer_and_filter_keys_are_uuid`：`uuid.UUID(key)` 不抛错。
- `test_loopback_ipv4_is_host_order`：`LOOPBACK_IPV4_INT` 经 `socket.inet_ntoa(struct.pack('<I', x))` == '127.0.0.1'。
- `test_fwp_data_type_enum_matches_sdk`：`FWP_SID==13, FWP_BYTE_BLOB_TYPE==12, FWP_V4_ADDR_MASK==256`。
- `test_fwpm_filter0_size_matches_layout`：`ctypes.sizeof(FWPM_FILTER0)` 与按 SDK 字段累加值一致（粗校验布局对齐）。
- `test_synthetic_write_sid_string_matches_allocateandinitialsid`：跨模块一致性（mock 侧用常量推导，不跑真 API）。
- `test_build_environment_block_keeps_buffer_alive`：构造 env block，断言指针可读且非悬垂（mock CreateProcessAsUserW，验证调用时 buf 引用仍在）。
- `test_windows_policy_supports_read_acl_fields`：`allow_read`/`deny_read` 解析 + `extra=forbid`。
- `test_egress_allow_no_implicit_and`：IP+port allow 不做 AND。

## 五、不交付项
- 不改 `supervisor/` 下任何 Linux 模块。
- 不改现有 `configs/*.yaml`（`windows-policy.yaml` 可加 read 字段示例）。
- WFP ctypes 实现按 SDK 对齐，但**不强制启用**为生产路径（PowerShell 降级仍为主）；Windows 实跑验证由用户执行。
- `_exec_background_windows` 降级为同步保持现状（已知限制）。
