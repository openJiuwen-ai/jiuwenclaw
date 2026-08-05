# Windows 沙箱 Review 问题修复步骤

> 依据 `docs/windows_sandbox_review_fix_design.md`。铁律：只改 review 相关，Linux 路径不动，新功能不影响旧功能。

## 步骤

### 步骤 1：`win_constants.py` 修常量（C2/C4/C5/M3）
- 替换 5 个 GUID 为 SDK 真值：
  - `FWPM_LAYER_ALE_AUTH_CONNECT_V4`/`V6`、`FWPM_CONDITION_ALE_USER_ID`/`IP_REMOTE_ADDRESS`/`IP_REMOTE_PORT`。
- sublayer/filter 4 个 key 改为合法固定 UUID 字符串（`JBX_SUBLAYER_KEY` + 4 filter key）。
- `LOOPBACK_IPV4_INT = 0x7F000001`（host order）+ 注释。
- `FWP_DATA_TYPE` 枚举按 SDK 真值重排（`FWP_BYTE_ARRAY16_TYPE=11, FWP_BYTE_BLOB_TYPE=12, FWP_SID=13, FWP_SECURITY_DESCRIPTOR_TYPE=14, ...`，`FWP_V4_ADDR_MASK=256, FWP_V6_ADDR_MASK=257, FWP_RANGE_TYPE=258`），删/改别名。
- 补 `FWPM_SESSION_FLAG_DYNAMIC=0x1`、`FWPM_SUBLAYER_FLAG_PERSISTENT=0x1`、`FWPM_FILTER_FLAG_PERSISTENT=0x1`、`FWP_ACTRL_MATCH_FILTER=0x1`。
- 单测：`test_wfp_*_guids_match_sdk`、`test_keys_are_uuid`、`test_loopback_ipv4_is_host_order`、`test_fwp_data_type_enum_matches_sdk`。

### 步骤 2：`win_wfp.py` 修结构体 + 条件 + 生命周期（C2/C4/M2 + SD）
- 结构体重写对照 SDK：`FWPM_DISPLAY_DATA0`（内嵌）、`FWPM_FILTER0`（去 providerDataSize、加 flags、reserved→GUID*、补 filterId/effectiveWeight）、`FWPM_SUBLAYER0`（去 providerDataSize、weight→UINT16）、`FWPM_SESSION0`（displayData 内嵌、去重复字段、补 processId/sid/username/kernelMode）、`FWP_VALUE0`/`FWP_CONDITION_VALUE0` union 补 sd/v4AddrMask*/v6AddrMask*。
- `_build_loopback_v4_condition`：addr=0x7F000001，值类型 `FWP_V4_ADDR_MASK`，value 设 v4AddrMask 指针（局部实例，存活到 FwpmFilterAdd0 返回）。
- `_build_ale_user_condition`：值类型 `FWP_SECURITY_DESCRIPTOR_TYPE`，用 win32security 构造自相关 SD（DACL 授 jbx-sandbox `FWP_ACTRL_MATCH_FILTER`），包 FWP_BYTE_BLOB，value.sd=&blob。构造失败 → 该 filter 不装（降级兜底）。
- SID 指针 `ConvertStringSidToSidW` 后在 filter 装完 `LocalFree`。
- `_add_filter` docstring 改"忽略 ALREADY_EXISTS"。
- 单测：`test_fwpm_filter0_size`、`test_build_loopback_v4_condition_host_order`、`test_build_ale_user_condition_uses_security_descriptor`（mock win32security）。

### 步骤 3：`win_exec.py` 修 SID 一致性 + 环境块（C1/C3 + M1 thread）
- `_get_synthetic_write_sid_ptr`：`nSubAuthorityCount=4`，首 sub=`21`，产出 `S-1-5-21-...`，与 `win_acl.get_synthetic_write_sid()` 一致。删错注释。
- `_build_environment_block` + `_create_process_as_user`：env block 持有 buf 引用到 `CreateProcessAsUserW` 返回（内联或返回元组保留引用）。
- `two_hop_spawn`：返回新增 `thread_handle`（保留 `pi.hThread` 不早关），供 Job SUSPEND/resume。creation_flags 加 `CREATE_SUSPENDED`。
- 单测：`test_synthetic_write_sid_matches_acl_string`、`test_build_environment_block_keeps_buffer_alive`（mock）、`test_two_hop_spawn_returns_thread_handle`。

### 步骤 4：`win_job.py` 修 SUSPEND/resume（M1）
- 新增 `resume_process(thread_handle)`：`ResumeThread`（kernel32）。
- `assign_process`/`assign_process_by_pid` 不变（runner suspended 时 assign 无竞态）。
- 单测：`test_resume_process_calls_resume_thread`。

### 步骤 5：`models/policy.py` + `win_acl.py` 修读控制（M4）
- `WindowsFilesystemPolicy` 增 `allow_read`/`deny_read`（list[str]，走 expand_path_lists）。
- `win_acl`：
  - `apply_sandbox_acl(..., allow_read, deny_read)`：deny_read 施 Deny Read ACE；allow_read 施 Allow Read ACE；workspace 默认若 allow_read 空则对 workspace 根施 Allow Read ACE。
  - 新增 `grant_read_ace`/`deny_read_ace`。
  - `apply` 返回施加路径清单；`revoke_sandbox_acl(paths)` 接清单。
  - `grant_ace` 不再默认 PROTECTED_DACL；revoke 不切断继承。
- 单测：`test_windows_policy_read_fields`、`test_apply_acl_workspace_default_read`、`test_revoke_acl_walks_all_applied_paths`。

### 步骤 6：`win_setup.py` 修端口/进度/force（M7/M8/M9）
- `_install(..., proxy_port_start, proxy_port_end)`：传给 `install_wfp_filters` 与降级。
- `_preinstall_read_acl`：每路径完成写 `REG_VALUE_READ_ACL_PROGRESS`（JSON 已完成列表）；install 先读跳过。
- `_elevate_and_run_install`：转发 `--force`、`--preinstall-paths`(JSON)、`--proxy-port-start/end`。
- `_main` CLI 接这些参数。
- `ensure_windows_setup(preinstall_paths, proxy_port_start, proxy_port_end, force)` 透传。
- 单测：`test_install_uses_policy_ports`、`test_preinstall_progress_persisted`、`test_force_elevation_forwards_flag`。

### 步骤 7：`win_proxy.py` 修 allow 语义（M10）
- `EgressFilter.allow()`：去掉 IP-allow 与 port-allow 隐式 AND；改为各维度独立判定（任一 allow 命中即放行），对齐 Linux iptables 独立 ACCEPT。
- 单测：`test_egress_allow_no_implicit_and`、`test_egress_block_default_still_works`。

### 步骤 8：`process.py` + `app.py` 接线（M1/M4/M6/M7）
- `_create_windows`：
  - 传 `allow_read`/`deny_read` 给 apply；apply 返回清单存 `_win_acl_paths[sandbox_id]`。
  - Job：runner thread_handle assign 后 `win_job.resume_process`，CloseHandle thread。
- `_stop_windows`：`revoke_sandbox_acl` 传清单。
- `app.py` lifespan：`ensure_windows_setup(preinstall_paths=_preinstall, proxy_port_start=..., proxy_port_end=...)`。
- 单测：`test_create_windows_applies_read_acl_and_resumes`、`test_lifespan_passes_policy_ports`。

### 步骤 9：回归
- `PYTHONPATH=src python3 -c "import jiuwenbox.supervisor.win_*"` 七模块仍干净导入。
- 现有 mock 单测全绿；新增单测全绿。
- Linux 路径未触（`test_server_api_default.py` 概念上不变，本次不跑 docker）。

## 影响评估
- 全部改动限定在 `win_*.py` / `process.py`(win 分支) / `app.py`(win 分支) / `models/policy.py`(新增字段) / 测试。Linux 分支逻辑一行不改。
- `models/policy.py` 新增字段默认空，旧 policy YAML 零回归。
- WFP ctypes 修对后仍保留 PowerShell 降级为生产兜底，不强制启用。

## 验证分工
- WSL：跑全部 mock 单测 + import 安全 + 结构体尺寸校验。
- Windows 实跑（用户）：WFP filter 真实安装、网络拦截、文件/进程隔离端到端 — 由用户执行，代码侧提供可验证路径。
