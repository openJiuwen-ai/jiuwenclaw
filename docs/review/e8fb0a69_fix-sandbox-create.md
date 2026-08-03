# 代码审查报告：e8fb0a69 fix:修复沙箱创建问题

- **Commit**：`e8fb0a690a6e0551a19239d73f17536801b5583c`
- **作者/日期**：lby，2026-07-27
- **变更规模**：9 文件，+553 / -175（净 +378）
- **审查重点**：WFP 网络过滤、沙箱创建流程、ctypes 结构体对齐、资源释放
- **审查人**：资深 Windows 系统/安全工程审查员
- **审查日期**：2026-08-01

---

## 一、概述

本次 commit 是一次典型的"实跑定位 + 修复"型提交。作者在一次完整链路调通过程中，围绕"沙箱创建"链路上的多处真实 bug 做了批量修复，主要分布在两个子系统：

1. **WFP 网络过滤**（`win_wfp.py`，+351）：修正 ctypes 结构体布局错位、条件构造、错误码翻译、幂等键派生与降级路径。
2. **沙箱用户/组/ACL/受限 Token**（`win_setup.py` +214、`win_constants.py`、`win_exec.py`、`win_acl.py`）：修正 netapi level 误用、netapi 错误码误用、CreateRestrictedToken 数组越界、Winnt 标志位错值、回滚机制。

整体修复质量较高：根因分析翔实、注释把"为什么"讲清楚、诊断日志充足、致命/非致命路径分层清晰。但也存在若干**临时性妥协**（全端口放行、WRITE_RESTRICTED 被临时去掉）和**潜在资源/安全风险**，下文逐条展开。

---

## 二、变更范围

| 文件 | 变动 | 关键内容 |
|------|------|----------|
| `jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py` | +351 | WFP 错误码表、结构体 union 对齐修正、SD 构造、filter 字段名修正、V4/V6 分支修正、GUID 派生、降级路径返回值 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py` | +214 | install 主体 try/except + 回滚、_add_user_to_group 改 level 3、netapi 错误码修正、UAC Event 同步 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_acl.py` | +39 | 新增 grant_read_ace / deny_read_ace |
| `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` | +40 | CreateRestrictedToken 动态 entries 数组、runner 异常落盘 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_proxy.py` | +11 | allow 判定从 AND 改为 OR 语义 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_constants.py` | -50/+调整 | SANDBOX_INERT、UF_DONT_EXPIRE_PASSWD、FWPM_LAYER GUID 修正 |
| `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` | +16 | 新增 nodejs/skills 预装路径、allow_read 段 |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | 2 行 | `_win_acl_paths` 类型 str→list[str] |
| `tests/integration/test_server_api_windows.py` | +5 | （本次未深入审查测试） |

---

## 三、根因与修复分析

### 3.1 WFP 过滤（win_wfp.py）

#### 🟢 [根因 1] FWPM_FILTER0 内嵌 union 尺寸错误（win_wfp.py:338-346, 362）

- **原 bug**：`rawContext` 字段定义为 `c_uint64`（8B），而 SDK `FWPM_FILTER0` 末尾是 `union { UINT64 rawContext; GUID providerContextKey; }`，GUID 16B，union 取 max=16B。旧 `sizeof(flt)=192B` 比 SDK 实际（200B）少 8B。
- **后果**：BFE 经 RPC 收到错误大小的结构体 → `RPC_X_BAD_STUB_DATA`（hr=0x6F7）。
- **修复**：引入 `_FWPM_FILTER0_UNION(ctypes.Union)`（rawContext c_uint64 + providerContextKey GUID），替换裸字段。`FWPM_ACTION0` 同理引入 `_FWPM_ACTION0_UNION`。
- **评价**：✅ 正确。union 尺寸取 max 是 ctypes 惯例，注释的偏移标注（16@0…16@184，sizeof=200）与 SDK 一致。这是本次 commit 最关键的修复之一。

#### 🟢 [根因 2] filterCondition 字段名拼写错误（win_wfp.py:668-674）

- **原 bug**：写成 `flt.filterConditions`（带 s），ctypes 把它当新增实例属性，结构体内部 `filterCondition` 指针保持 NULL；`numFilterConditions=1` 但指针 NULL → BFE 报 `RPC_X_BAD_STUB_DATA`。
- **修复**：改回 `flt.filterCondition`。
- **评价**：✅ 正确。此类 ctypes 字段名拼写错误极隐蔽（不报错，静默 NULL），作者靠诊断日志定位，修复干净。

#### 🟢 [根因 3] FWPM_LAYER_ALE_AUTH_CONNECT_V4 GUID 错值（win_constants.py:260-262）

- **原 bug**：第 4 段写成 `900F`，SDK 实际为 `904F`（`C38D57D1-05A7-4C33-904F-7FBCEEE60E82`）。BFE 报 `FWP_E_LAYER_NOT_FOUND`（0x80320004）。
- **修复**：据 `fwpmu.h` `DEFINE_GUID` 改回 `904F`。
- **评价**：✅ 正确。已据公开 SDK 资料交叉确认 V4 GUID 第 2 段为 `05A7`、第 3 段 `4C33`、第 4 段 `904F`，与修复后一致。

#### 🟢 [根因 4] V4/V6 分支误用字符串包含判断（win_wfp.py:769-774）

- **原 bug**：`if "V4" in base_key:` —— `base_key` 是纯 hex GUID 串（如 `"BC5D4E3F-7081-9203-1234-56789ABCDEF0"`），不含 `V4`/`V6` 子串，导致两路都走 else → **V4 层装了 IPv6 ::1 条件**（`FWP_V6_ADDR_AND_MASK`=257），condition 类型与 layer 不匹配 → BFE 返回 0x80320027（未登记码，注释准确标注需对照 fwperr.h）。
- **修复**：改用 `is_v4 = (layer == const.FWPM_LAYER_ALE_AUTH_CONNECT_V4)`，按 layer GUID 显式判断。
- **评价**：✅ 正确且必要。这是"用魔法字符串子串判断枚举值"的典型反例，修复方式严谨。

#### 🟢 [根因 5] per-port filter key 非法 UUID（win_wfp.py:149-165）

- **原 bug**：`port_key = f"{base_key}-{port}"` 不是合法 UUID（含端口后缀），`_guid_from_str` 内 `uuid.UUID(s)` 抛 `ValueError`，install/uninstall 全崩。
- **修复**：引入固定 namespace GUID，用 `uuid.uuid5(ns, f"{base_key}:{port}")` 派生确定性合法 GUID，install/uninstall 用相同 (base, port) 派生同 GUID → 幂等。
- **评价**：✅ 正确。uuid5 派生是确定性、无随机、跨进程一致的，满足幂等删。namespace 用任意合法 UUID 即可。

#### 🟢 [根因 6] FWP_V6_ADDR_MASK 枚举值错误（win_wfp.py:512）

- **原 bug**：`cond.conditionValue.type = const.FWP_V6_ADDR_MASK`（旧值命名），实际 `win_constants` 里 `FWP_V6_ADDR_MASK` 已修正为 `FWP_V6_ADDR_AND_MASK`（0x101=257）。修复把 type 指向正确的 `FWP_V6_ADDR_AND_MASK`。
- **评价**：✅ 正确。`FWP_V6_ADDR_MASK_TYPE` 别名保留向后兼容，但 V6 条件构造显式用 `FWP_V6_ADDR_AND_MASK`，与 SDK 枚举名一致。

#### 🟢 [根因 7] displayData.name 不能为 NULL（win_wfp.py:474-477, 660-662）

- **原 bug**：sublayer/filter 的 `displayData.name` 未设（NULL），BFE 报 `FWP_E_INVALID_AUTH_VALUE`（0x80320023）。
- **修复**：显式设 `name`/`description` 为非空字符串。
- **评价**：✅ 正确。这是 BFE 的硬性校验，SDK 示例也均设 name。

#### 🟢 [根因 8] ALE_USER_ID 条件的 SD 构造（win_wfp.py:560-611）

- **原 bug 链**：
  1. 旧版用 `win32security.TRUSTEE()` + `BuildTrusteeWithSid`，但 pywin32 未暴露这些 → 改为 EXPLICIT_ACCESS dict + 内嵌 Trustee dict（`TrusteeForm=TRUSTEE_IS_SID`，`Identifier` 放 PySID 对象）。
  2. SD 缺 owner/group → `SetSecurityDescriptorDacl` 产出不一致的自相关 SD → BFE marshal 校验失败 → 显式 `Initialize` + `SetSecurityDescriptorOwner/Group`（用 jbx-sandbox 自己 SID）。
  3. SD bytes 来源用 `from_buffer_copy` → 改 `create_string_buffer`（malloc 分配、8B 对齐、RPC 友好）。
  4. `MakeSelfRelativeSD` pywin32 无此函数 → 用 `bytes(sd)`（PySECURITY_DESCRIPTOR 内部即 self-relative）。
- **评价**：✅ 多层叠加修复，每层都有"实跑日志 + 根因 + 对齐 SDK 示例"的论证。SD owner=group=jbx-sandbox 自身保证 SD 自洽，blob.data 指向 malloc 内存避免 ctypes buffer 对齐问题。诊断日志（SD 长度、self_relative 位）合理。

#### 🟡 [修复 9] uninstall 删除 not-found 错误码误用（win_wfp.py:853-872）

- **原 bug**：`_delete_filter_by_key` 把 `0x800700B7`（`ERROR_ALREADY_EXISTS`，**add 路径才出现**）当 delete 的 not-found 忽略；实际 delete 的 not-found 是 `FWP_E_KEY_NOT_FOUND=0x80320031` / `FWP_E_FILTER_NOT_FOUND=0x80320003`。
- **修复**：定义 `_DELETE_NOT_FOUND = {0x80320031, 0x80320003}`，命中则静默，其余用 `_wfp_error`。
- **评价**：✅ 正确。但 🟡 **隐患**：`_WFP_ERROR_NAMES` 字典里登记了 `0x80320032: "FWP_E_FILTER_NOT_FOUND (delete)"`（win_wfp.py:86），这个码值疑似错误——SDK 中 `FWP_E_FILTER_NOT_FOUND` 是 `0x80320003`，`0x80320032` 在 SDK 中并不存在对应常量。虽然实际 not-found 集合用的是正确的 `{0x80320031, 0x80320003}`，字典里这条死条目不影响逻辑，但会误导后续维护者。**建议删除字典中 `0x80320032` 这条或更正注释**。

#### 🟢 [修复 10] _wfp_error 替代 ctypes.WinError（win_wfp.py:91-114）

- **根因**：`ctypes.WinError(hr)` 对高位为 1 的 HRESULT（0x80320xxx）会因内部转 signed long 溢出抛 `OverflowError`，掩盖真实错误码。
- **修复**：`_wfp_error(hr, where)` 显式格式化为 `0xXXXXXXXX + 已知名`，兼容 `HRESULT_FROM_WIN32` 段（0x80070000）和未登记的 FWP_E_* 段（标注 `check fwperr.h`）。
- **评价**：✅ 实用且健壮。未登记码不臆测写死，只标注段名提示对照 SDK，态度严谨。

#### 🟡 [临时妥协 11] Permit filter 全端口放行（win_wfp.py:756-791）

- **改动**：Permit filter 条件从 `user + loopback + port_eq` 改为 `user + loopback`（去掉 port 条件），filter key 用固定 `base_key`（不再 per-port 派生）。
- **理由（注释）**：pptx-craft 的 render server 用 `getPort()` 随机选端口（如 6298），不在固定 60080-60089 范围 → chromium 访问 render server 被 Block → `ERR_NETWORK_ACCESS_DENIED`。作者明确标注"此为验证根因的临时全放开; 定稿方案待定"。
- **评价**：🟡 **安全降级**。这等于把"沙箱出网唯一出口是 win_proxy"的设计目标（win_wfp.py:18-19）打破：jbx-sandbox 现在可以访问 127.0.0.1 **任意端口**的本地服务。若机器上还跑着其他本地服务（数据库、调试器、其他租户的 proxy），沙箱用户可直连它们绕过 win_proxy 的域名/IP/端口过滤。
  - 风险面：本地多租户/多服务场景下存在横向访问面。
  - 缓解：仅放行 loopback，不含外部 IP；Block filter 仍挡外网。
  - **建议**：定稿方案应让 render server 用固定端口（或从 policy 端口范围内取），恢复 per-port Permit；或在 win_proxy 侧动态注册 render server 端口后回写 WFP。**当前临时方案不宜进生产**。
  - uninstall 路径（win_wfp.py:823-835）已兼容：既删旧 per-port 残留，又删当前固定 key，幂等性 OK。

#### 🟢 [修复 12] 降级路径返回值 + SDDL/Protocol 修正（win_wfp.py:875-953）

- **原 bug 链**：
  1. `-ErrorAction SilentlyContinue` 吞 stderr → 失败只 warning，循环外无条件打"安装完成" → **假成功**。
  2. `-LocalUser` 裸传 `S-1-5-21-...`（含连字符）被拒，微软文档要求 SDDL 串 `D:(A;;CC;;;<SID>)`。
  3. `-RemotePort` 配 `-RemoteAddress` 缺 `-Protocol` → 协议不匹配（0x80070057）。
- **修复**：去 `SilentlyContinue`、捕 stderr 明文打印、构造 SDDL、显式 `-Protocol TCP`、返回 `bool`，调用方据返回值决定是否致命 raise。
- **评价**：✅ 全面。`install` 主路径据此正确 raise `RuntimeError` 触发回滚（win_setup.py:953-960），不再假成功。

### 3.2 沙箱创建（win_setup.py / win_constants.py / win_exec.py）

#### 🟢 [根因 13] NetLocalGroupAddMembers 用错 level（win_setup.py:396-441）

- **原 bug**：用 level 0（`LOCALGROUP_MEMBERS_INFO_0`，字段是 `PSID`）却塞用户名字符串 → netapi 把字符串当 PSID 解析失败 → 返回 1337（注释说 `ERROR_INVALID_PASSWORD`，实际 1337 是 `ERROR_INVALID_PASSWORD` 或 `NERR_InvalidDatabase` 视上下文，但实跑确实失败）。
- **修复**：改 level 3（`LOCALGROUP_MEMBERS_INFO_3`，`lgrpi3_domainandname` 接受 `"DOMAIN\\user"` 名字串）。
- **评价**：✅ 正确。level 3 显式接受名字串，netapi 内部解析账户，本地账户用裸名即可。

#### 🟢 [根因 14] netapi 错误码误用（win_setup.py:1272-1285）

- **原 bug**：`NetUserDel` 的 not-found 用 `2201`（实为 `NERR_BadPassword`），`NetLocalGroupDel` 用 `2201`（实为 `NERR_BadPassword`）。
- **修复**：据 `lmerr.h` 改为 `2221`（`NERR_UserNotFound`）和 `2220`（`NERR_GroupNotFound`）。
- **评价**：✅ 正确。`NERR_UserNotFound=2221`、`NERR_GroupNotFound=2220` 是 `lmerr.h` 标准值。旧版误用导致幂等卸载时落 warning 分支（实跑日志"NetUserDel 返回 2221"即被错判）。

#### 🟢 [根因 15] SANDBOX_INERT 标志值错误（win_constants.py:59-75）

- **原 bug**：`SANDBOX_INERT = 0x4`（实为 `LUA_TOKEN` 的值），`RESTRICTED_TOKEN_FLAGS` 实际组合出 `DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED`，与文档 6.5 要求的 SANDBOX_INERT 语义不符。
- **修复**：据 `winnt.h` 改回 `0x2`。
- **评价**：✅ 正确。`winnt.h` 中 `SANDBOX_INERT=0x2`、`LUA_TOKEN=0x4`，旧值确为错值。
- 🟡 **但**：`RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT`（win_constants.py:75）**临时去掉了 `WRITE_RESTRICTED`(0x8)**，注释说"定定位 0xC0000142"。这是**安全降级**：去掉 WRITE_RESTRICTED 后，受限 token 不再对写操作做 Restricted SID 双重 ACL 检查，写控制只剩合成 SID 的 ACL（allow-only 仍挡越权写，但失去第二重防线）。`win_exec.py:654` 的 docstring 仍写"Flags = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT | WRITE_RESTRICTED"，与实际不符。**建议**：定位完 0xC0000142 后恢复 WRITE_RESTRICTED，并同步 docstring。

#### 🟢 [根因 16] UF_DONT_EXPIRE_PASSWD 错值（win_constants.py:114-142）

- **原 bug**：`UF_DONT_EXPIRE_PASSWD = 0x0200`（实为 `UF_NORMAL_ACCOUNT` 的值），且旧注释声称"二者历史上同占 0x0200 但语义域不同故不冲突"——与 `lmaccess.h` 不符。
- **修复**：据 `lmaccess.h` 改为 `0x10000`，并因改后与 `UF_NORMAL_ACCOUNT(0x0200)` 不再重叠，`SANDBOX_USER_FLAGS` 显式 OR `UF_NORMAL_ACCOUNT`。
- **评价**：✅ 正确。`UF_DONT_EXPIRE_PASSWD=0x10000`、`UF_NORMAL_ACCOUNT=0x0200` 是 `lmaccess.h` 标准值。旧值导致沙箱用户密码会按本地账户策略过期。显式 OR UF_NORMAL_ACCOUNT 的补救正确。

#### 🟢 [根因 17] install 主体 try/except + 回滚（win_setup.py:888-1027）

- **原 bug**：旧版各步 best-effort 只 warning，最后无条件写 `installed=1` → 假成功（实跑日志证实：组成员失败/WFP 失败/预装失败后仍打"安装完成"）。
- **修复**：install 主体包 try/except，致命步骤（用户+组创建 S8、网络隔离 S9/S10）失败时调 `uninstall()` 回滚并 re-raise，不写 installed=1。非致命步骤（隐藏登录界面、DPAPI、合成 SID 缓存、读 ACL 预装）仍 best-effort。
- **评价**：✅ 设计正确。致命/非致命分层清晰，回滚 best-effort 不掩盖原异常（`except Exception: ... raise`）。
- 🟡 **小隐患**：回滚调 `uninstall()`，而 `uninstall` 内部会 `_purge_stale_profile_dirs()` + `NetUserDel`，若 install 失败时用户正被占用（如 LogonUser 已建 profile），回滚可能不彻底——但作者已在 `_verify_or_reset_sandbox_user_password` 兜底（win_setup.py:1146-1210），可接受。

#### 🟢 [修复 18] CreateRestrictedToken 动态 entries（win_exec.py:711-757）

- **原 bug**：`restricting = (_SID_AND_ATTRIBUTES * 3)(...)` 固定 3 个元素，若 `logon_sid_val is None`（count==0 或无 LOGON_ID 组）仍硬塞 NULL → `CreateRestrictedToken` 返回 WinError 87。
- **修复**：先建 `entries = [Everyone]`，`if logon_sid_val is not None: entries.append(...)`，再 append write_sid_ptr，最后 `restricting = (_SID_AND_ATTRIBUTES * len(entries))(*entries)`。并加 logger.info 诊断。
- **评价**：✅ 正确。动态数组大小避免 NULL SID 传入，`AllocateAndInitializeSid` 失败也 raise 不悬垂。
- 🟡 **小问题**：runner 侧 `_create_restricted_token` 失败现在 try/except 落盘后 return 1（win_exec.py:677-690），但**未关闭已打开的 h_token**？实际 finally 会 `CloseHandle(h_token)`，OK。但 `write_sid_ptr` 若 `AllocateAndInitializeSid` 成功后 `CreateRestrictedToken` 失败，`write_sid_ptr` 未 `FreeSid`（leak）。单次 runner 进程内 leak 可忽略，但建议补 `FreeSid`。

#### 🟢 [修复 19] win_proxy allow 语义 AND→OR（win_proxy.py:104-116）

- **改动**：allow 判定从"IP+port 同时存在时做 AND"改为"按维度独立判定 OR"。
- **理由**：对齐 Linux iptables 的独立 ACCEPT 链。
- **评价**：✅ 语义对齐 Linux 侧，合理。但需注意：OR 语义下，若只配 `allowed_ports=[60080]`，则 60080 端口的**任意 IP**都被放行（包括外网 IP，若 Block filter 未挡）。当前 WFP Block filter 挡了 jbx-sandbox 所有出站，loopback Permit 放行 127.0.0.1，故 win_proxy 的 OR 放行实际只作用于已通过 WFP 到达 proxy 的流量，影响可控。

#### 🟢 [修复 20] process.py 类型对齐（process.py:645）

- `self._win_acl_paths: dict[str, str]` → `dict[str, list[str]]`，与 `apply_sandbox_acl` 返回 `list[str]` 一致。
- **评价**：✅ 类型修正，避免后续 `revoke_sandbox_acl` 按清单撤销时把 str 当 list 迭代。

---

## 四、关键代码检视

### 4.1 WFP filter 安装流程（install_wfp_filters, win_wfp.py:714-802）

```python
engine = _open_engine()
try:
    fwpu.FwpmTransactionBegin0(engine, 0)
    try:
        sublayer_key = _add_sublayer(engine, const.JBX_SUBLAYER_KEY, weight=100)
        # Block filters (V4 + V6)
        for layer, fkey in (...):
            block_cond, ka = _build_ale_user_condition(sandbox_user_sid)
            keeps.append(ka)
            _add_filter(...)
        # Permit filters (V4 + V6) — 临时全端口放行
        for layer, base_key in (...):
            is_v4 = (layer == const.FWPM_LAYER_ALE_AUTH_CONNECT_V4)
            user_cond, user_ka = _build_ale_user_condition(sandbox_user_sid)
            keeps.append(user_ka)
            if is_v4: lb_cond, lb_ka = _build_loopback_v4_condition()
            else:     lb_cond, lb_ka = _build_loopback_v6_condition()
            keeps.append(lb_ka)
            _add_filter(engine, base_key, layer, sublayer_key,
                        [user_cond, lb_cond], const.FWP_ACTION_PERMIT, ...)
        fwpu.FwpmTransactionCommit0(engine)
    except Exception:
        fwpu.FwpmTransactionAbort0(engine)
        raise
finally:
    fwpu.FwpmEngineClose0(engine)
```

- 🟢 事务包裹（Begin/Commit/Abort）保证多 filter 原子安装，失败 Abort 回滚。
- 🟢 `keeps` 列表持有 `_build_ale_user_condition` / `_build_loopback_*` 返回的 keep-alive 引用（SD blob、v4/v6 addr_mask），直到全部 `FwpmFilterAdd0` 返回才允许 GC。`_KeepAlive` 类（win_wfp.py:614-617）简单有效。
- 🟢 `finally: FwpmEngineClose0(engine)` 保证 engine 句柄释放。
- 🟡 **session 未设 `FWPM_SESSION_FLAG_DYNAMIC`**（win_wfp.py:453 用 `FWP_SESSION_FLAG_NONE`=0）。非 DYNAMIC session 下，通过 `FwpmFilterAdd0` 添加的 filter 会持久化进 BFE 数据库（重启后仍在）。这其实是**期望行为**（沙箱安装一次长期有效），但 sublayer/filter 的 `flags` 字段也未设 `FWPM_SUBLAYER_FLAG_PERSISTENT`/`FWPM_FILTER_FLAG_PERSISTENT`——非 DYNAMIC session 下 Add 的对象默认即持久，故不影响。但**建议显式设 PERSISTENT flag** 以明示意图，避免未来误改成 DYNAMIC session 后 filter 随 session 关闭消失。

### 4.2 ALE_USER_ID 条件 SD 构造（_build_ale_user_condition, win_wfp.py:527-611）

- 🟢 SD 构造：Initialize → SetOwner/SetGroup（jbx-sandbox 自身 SID）→ SetDacl（GRANT FWP_ACTRL_MATCH_FILTER）→ `bytes(sd)` 取 self-relative 原始字节。
- 🟢 blob.data 用 `create_string_buffer`（malloc、8B 对齐、RPC 友好），替代 `from_buffer_copy`。
- 🟢 `_KeepAlive(blob=blob, buf=buf, sd_bytes=sd_bytes)` 持有引用防 GC。
- 🟡 **owner=group=jbx-sandbox 自身**：SD 自洽但 owner 即被授权主体，从安全描述符语义看略怪（通常 owner 应是管理员/系统）。但 FWP 只校验 DACL 的 MATCH_FILTER 访问权，不关心 owner，功能上 OK。

### 4.3 install 回滚（win_setup.py:1019-1027）

```python
except Exception:
    logger.error("install 失败, 执行回滚", exc_info=True)
    try:
        uninstall()
    except Exception:
        logger.error("install 失败后回滚也失败", exc_info=True)
    raise
```

- 🟢 回滚 best-effort，不掩盖原异常（re-raise 原 exception）。
- 🟢 `_main` 的 `finally: _notify()` 保证 install 失败也 SetEvent 通知主进程解除 INFINITE 阻塞（win_setup.py:1431-1445），避免主进程死等。

### 4.4 CreateRestrictedToken entries 动态构造（win_exec.py:711-737）

- 🟢 `entries` 列表动态 append，数组大小 `len(entries)`，避免 NULL SID 传入。
- 🟡 `write_sid_ptr` leak：`AllocateAndInitializeSid` 成功后若后续 `CreateRestrictedToken` 失败，未 `FreeSid`。单进程内可忽略，但建议补。

---

## 五、优点

1. **根因分析翔实**：几乎每处修复都附"实跑日志 + 根因 + 对齐 SDK"的注释链，可追溯、可复核。这在 ctypes 调试中极有价值（错误码常被 OverflowError 掩盖）。
2. **诊断日志充足**：`FwpmFilterAdd0` 失败时打结构体布局、指针字段值、cond_types、flt_hex_first32（win_wfp.py:684-710），`CreateRestrictedToken` 打 restricting_sids 数 + flags（win_exec.py:739-742），便于实跑定位。
3. **致命/非致命分层清晰**：install 主体明确标注哪些步骤致命（用户+组、网络隔离）、哪些非致命（隐藏用户、DPAPI、预装），失败行为分层。
4. **幂等性贯穿**：filter key 用固定合法 UUID / uuid5 派生，sublayer 用固定 key，install/uninstall 跨次一致；netapi 错误码 not-found 静默。
5. **结构体对齐修正到位**：FWPM_FILTER0 的 union 16B、FWPM_ACTION0 的 union 16B、displayData 内嵌 16B，均据 SDK repr(C) 偏移标注，是本次最硬核的修复。
6. **降级路径不再假成功**：返回 bool + 调用方据返回值 raise，SDDL/Protocol 修正到位。

---

## 六、问题与风险

### 🔴 高危

1. **[临时全端口放行]（win_wfp.py:756-791）**：Permit filter 去掉 port 条件，jbx-sandbox 可访问 127.0.0.1 任意端口。打破"沙箱出网唯一出口是 win_proxy"的设计目标。本地多服务/多租户场景下存在横向访问面（可直连他人 proxy/DB/调试器）。注释自承"临时验证; 定稿方案待定"。**不宜进生产**。

### 🟡 中危

2. **[WRITE_RESTRICTED 被临时去掉]（win_constants.py:75）**：`RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT`，注释"临时去掉 WRITE_RESTRICTED(0x8) 定位 0xC0000142"。失去写操作的第二重 ACL 检查，写控制只剩合成 SID 的 allow-only ACL。`win_exec.py:654` docstring 与实际不符。建议定位后恢复。

3. **[WFP 诊断字典死条目]（win_wfp.py:86）**：`0x80320032: "FWP_E_FILTER_NOT_FOUND (delete)"` 疑似错误码值（SDK `FWP_E_FILTER_NOT_FOUND=0x80320003`，`0x80320032` 无对应常量）。实际 not-found 集合用对了（`{0x80320031, 0x80320003}`），但字典死条目误导维护者。建议删除或更正。

4. **[write_sid_ptr leak]（win_exec.py:723-735）**：`AllocateAndInitializeSid` 成功后若 `CreateRestrictedToken` 失败，未 `FreeSid`。单进程内可忽略，多次失败累积。建议 finally 补 `FreeSid`。

5. **[降级路径 loopback 放行有限]（win_wfp.py:914-916）**：注释自承"Windows Firewall 对 loopback 目标过滤支持有限，-RemoteAddress 127.0.0.1 在部分版本不生效"。降级路径主要靠 Block 规则，Permit 规则可能失效 → 沙箱用户连 127.0.0.1:proxy 也被 Block。但当前主路径 WFP 已工作，降级是兜底，可接受。

### 🟢 低危/建议

6. **[未显式设 PERSISTENT flag]（win_wfp.py:477, 661）**：sublayer/filter flags=0。非 DYNAMIC session 下 Add 的对象默认持久，功能 OK。建议显式设 `FWPM_SUBLAYER_FLAG_PERSISTENT`/`FWPM_FILTER_FLAG_PERSISTENT` 明意图。

7. **[SD owner=group=jbx-sandbox]（win_wfp.py:581-582）**：SD 自洽但 owner 即被授权主体，语义略怪。功能上 FWP 只校验 DACL，不影响。可接受。

8. **[密码固定 "000000"]（win_setup.py:327）**：`_generate_password` 固定返回 "000000"，注释"调试阶段"。生产前需改随机/配置读取。

9. **[preinstall 默认空]（win_setup.py:969-970）**：`preinstall_paths` 为空时 `paths_to_preinstall = []`，但随后又对 `~/.office-claw` 整树递归 grant（win_setup.py:982-998）。逻辑上 OK（office-claw 是数据根），但 install 注释（win_setup.py:962-964）说"默认不 preinstall 系统目录"，与 `windows-policy.yaml` 的 `read_acl_preinstall` 仍列系统目录存在认知不一致——实际 install 时 preinstall_paths 由调用方传入（collect_preinstall_paths），install 内部不回退默认系统目录，OK。

---

## 七、改进建议

1. **恢复 per-port Permit**：定稿 render server 端口策略（固定端口 or policy 范围内取 or 动态注册后回写 WFP），恢复 `_build_port_eq_condition` + per-port uuid5 key，去掉全端口放行。
2. **恢复 WRITE_RESTRICTED**：定位完 0xC0000142 后恢复 `RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT | WRITE_RESTRICTED`，同步 `win_exec.py:654` docstring。
3. **清理 WFP 错误码字典**：删除 `0x80320032` 死条目或更正为 `0x80320003`。
4. **补 FreeSid**：`_create_restricted_token` finally 中 `if write_sid_ptr: advapi32.FreeSid(write_sid_ptr)`。
5. **显式 PERSISTENT flag**：sublayer/filter 显式设持久 flag，明意图。
6. **密码改随机**：`_generate_password` 改 `secrets.token_urlsafe(32)` 或从配置读，去掉固定 "000000"。
7. **WFP 单元测试**：对 ctypes 结构体 `sizeof` 加断言（`assert ctypes.sizeof(FWPM_FILTER0) == 200`），防未来回归（x64）。Linux 下可 import 但不能调 API，`sizeof` 断言可在 CI 跑。

---

## 八、小结

本次 commit 是一次高质量的"实跑定位 + 修复"提交，集中解决了 WFP ctypes 封装中一系列隐蔽的结构体对齐/字段名/枚举值/GUID 错值问题，以及沙箱用户/组创建中 netapi level/错误码误用问题。根因分析翔实、诊断日志充足、致命/非致命分层清晰、幂等性贯穿、回滚机制健全。

主要遗留风险是**两处临时性安全降级**（全端口放行、去掉 WRITE_RESTRICTED），作者已在注释中明确标注"临时; 定稿待定"，但**不宜直接进生产**。其余为字典死条目、FreeSid leak、密码固定等低危问题。

**总体评价**：修复方向正确、执行扎实，遗留项有明确跟进路径。建议在合并前/合并后立即处理两条 🔴/🟡 临时降级，其余可作为后续 issue 跟进。

---

**审查涉及的文件（绝对路径）**：
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_wfp.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_setup.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_constants.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_exec.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_acl.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_proxy.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/server/runtime/process.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml`
