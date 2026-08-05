# Commit 3fe33056 代码审查报告

**Commit**: `3fe33056acb1087a6ddf87b583adbfa34019a9eb`
**信息**: fix:修复沙箱用户创建bug (作者 lby, 2026-07-25)
**审查范围**: 沙箱 Windows 用户创建 / ACL / IPC 链路
**审查重点**: 用户创建正确性、密码与账户类型安全、用户清理完整性、IPC 协议兼容性、ACL 权限赋予粒度、是否遗留测试账户

## 概述

本 commit 修复 Windows 沙箱首次安装/重装链路上多个串联阻塞 bug。约 136 增 44 删, 5 文件。核心是让 `win_setup.install()` 在真实 Windows 11 + Python 3.13 + pywin32 环境下能走通用户创建→密码持久化→UAC 提权→IPC 复用整条路径。

修复质量较高, 多处 bug 定位精确(注册表 SAM 权限位、NERR 错误码、ctypes 类型映射、pywin32 ACE 元组形态), 注释详尽还原根因。但存在若干安全/健壮性遗留项(见末段), 主要集中在密码强度与 IPC 协议向后兼容。

## 变更范围

| 文件 | 性质 | 关键点 |
|------|------|--------|
| `jiuwenbox/src/jiuwenbox/server/policy_engine.py` (+13) | 跨平台路径识别 | `_is_absolute_sandbox_path` 兼容 Windows 绝对路径 |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` (+7) | 异步修正 | 去掉对同步函数 `ensure_windows_setup` 的 `await` |
| `jiuwenbox/src/jiuwenbox/supervisor/daemon_ipc.py` (+33 调整) | IPC 协议泛化 | `recv_exact`/`send_frame` 兼容 socket 与 anonymous pipe |
| `jiuwenbox/src/jiuwenbox/supervisor/win_acl.py` (+65 调整) | ACL 重建修正 | `GetAce` 元组解析、`GetAceCount`、`AddAccess{Denied,Allowed}AceEx` 签名、`SE_FILE_OBJECT` 归属 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py` (+62 调整) | 用户创建修正 | `NetLocalGroupAdd` argtypes、注册表 KEY_READ_WRITE、`NetUserAdd` LPWSTR 赋值、NERR_UserExists、`_reg_get_str` 两阶段读 |

## 原 bug 与修复分析

### 🔴 `win_setup.py:204` KEY_READ_WRITE 权限位写错 (致命)

原 `KEY_READ_WRITE = 0x20019`, 实为 `KEY_READ` (只读)。`_reg_set_str` 用只读 hkey 调 `RegSetValueExW` → 即使 admin 也 WinError 5。修复为 `KEY_READ_WRITE = 0x2001F` (KEY_READ|KEY_WRITE), 并拆出 `KEY_READ_ONLY = 0x20019` 给读路径用。

- 根因定位准确: Win32 SAM 权限位 `KEY_READ=0x20019` / `KEY_WRITE=0x20006` / `KEY_READ|KEY_WRITE=0x2001F`。
- 拆分合理: 写用 `KEY_READ_WRITE`, 读用 `KEY_READ_ONLY`。
- 影响链: 此 bug 把后续 NERR/ctypes bug 全部掩盖 (注册表第一步幂等检查就抛, install 内 UAC 分支走不到), 修后才暴露下游。

### 🔴 `win_setup.py:69` `NetLocalGroupAdd` argtypes 错位 (致命)

原 argtypes `[LPCWSTR, LPCWSTR, DWORD, c_void_p]` 把 `level:DWORD` 标成 `LPCWSTR`, `buf:LPBYTE` 标成 `DWORD`。调用 `NetLocalGroupAdd(None, 0, buf, None)` 时 int 0 喂给 `c_wchar_p` → `ctypes.ArgumentError`。修正为 `[LPCWSTR, DWORD, c_void_p, c_void_p]` (servername, level, buf, parm_err)。

- 对照 Win32 SDK 签名 `NetLocalGroupAdd(servername, level, buf, parm_err)` 正确。
- 注释解释清晰。

### 🔴 `win_setup.py:236-274` `_reg_get_str` 两处缺陷 (致命)

1. 复用 `_reg_open_create()` 的 `RegCreateKeyExW + KEY_READ_WRITE`: 普通用户对 HKLM 无写权限, 即使 key 已存在也被拒 (WinError 5), 导致 `ensure_windows_setup` 第一步幂等检查就抛, install UAC 分支永远走不到。修复为 `RegOpenKeyEx + KEY_READ_ONLY`, key 不存在/无权限均返回 None 让上层走 install 提权路径。
2. 固定 buffer 512 chars (1024 bytes): DPAPI 密码 blob 的 hex 串远超此长度, `RegQueryValueExW` 返回 `ERROR_MORE_DATA=234`, 原代码误当"未读到"返回 None → `get_sandbox_user_password` 拿不到密码 → `CreateProcessWithLogonW` 1326。修复为两阶段读 (先空 buffer 查 size, 再按 size 分配), char 数按 `size//2+1` 计算。

- 两处修复都正确。两阶段读是 Win32 注册表读取的标准范式。
- `char_count = max(1, size.value // 2 + 1)` 对 size=0 (空值) 兜底为 1, 防御合理。

### 🟡 `win_setup.py:338` `NetUserAdd` LPWSTR 赋值方式 (兼容性)

原 `info.usri1_name = ctypes.create_unicode_buffer(const.SANDBOX_USER_NAME)`, Python 3.13 ctypes 严格类型检查拒绝 `c_wchar_Array_N → c_wchar_p` 赋值 → TypeError。修复为直接赋 str (`info.usri1_name = const.SANDBOX_USER_NAME`), ctypes 自动转 NUL 结尾宽字符串指针并绑定到 info 生命周期。

- 修复正确: `_USER_INFO_1.usri1_name` 字段类型是 `LPWSTR` (`c_wchar_p`), 直接赋 str 是 ctypes 推荐做法。
- 同步修了 `_add_user_to_group` 的 `lgrpi0_name` / `lgrmi0_name` (但 diff 显示 commit 后续版本又改为 level 3 + `_LOCALGROUP_MEMBERS_INFO_3`, 见 win_constants 注释 S8)。
- 生命周期: info 是局部变量且贯穿 `NetUserAdd` 调用, ctypes 保持对 str 的引用 (内部 buffer), 调用期间不会 GC。安全。

### 🟡 `win_setup.py:355` NERR_UserExists 错误码 (致命, 幂等性)

原 `elif ret == 2236`, 实际 `NERR_UserExists = 2224` (lmerr.h)。重装时遇到已存在用户被当失败 raise, install 回滚删用户, 下次又全新建, 永远幂等不了。修正为 `2224`。

- 错误码定位准确: 2224 = `NERR_UserExists`, 2236 是 `NERR_PasswordTooShort` (恰好是密码复杂度相关, 易混淆)。
- uninstall 的 `NetUserDel` 错误码 (2221 = NERR_UserNotFound) 也在同 commit 注释里纠正了旧版误用的 2201, 但 2201 修正未出现在本 commit diff 内 (win_setup.py:1276 注释提到"旧版误用 2201", 但实际代码已是 2221, 应为前序 commit 已修)。

### 🟢 `win_acl.py:77-114` `_parse_getace_tuple` 兼容当前 pywin32 ACE 元组形态

实测 pywin32 (Python 3.13) `GetAce` 对普通目录返回 3 元组 `((ace_type, ace_flags), access_mask, sid)`, 即首元素是 header 子元组。原实现假设 3 元组为 `(access_mask, ace_flags, sid)`, 把 header 子元组当 access_mask → `int(tuple)` TypeError。修复: 3 元组分支先判首元素是否 tuple, 是则拆 header, 否则按旧版 `(access_mask, ace_flags, sid)` 处理。

- 修复正确且向后兼容 (覆盖 3/4/5 元组 + header 子元组形态)。
- else 分支 (其他元数) 兜底 `sid=None`, 后续 `EqualSid(None, ...)` 会在 revoke 路径抛, 但仅在畸形 ACE 出现时, 实际不会触发。

### 🟢 `win_acl.py:134` `GetAclSize` → `GetAceCount` (致命, ACL 重建)

原 `for i in range(existing_dacl.GetAclSize())`。`GetAclSize()` 返回 ACL 字节数 (非 ACE 个数), 88 字节 ACL 实际 3 个 ACE, `range(88) → GetAce(3)` 抛 `pywintypes.error (87, 'GetAce', '参数错误')`。修复为 `GetAceCount()`。同步修了 `revoke_sandbox_acl` (win_acl.py:528)。

- 两处都修了, 一致。
- `GetAceCount` 是 pywin32 `PyACL` 的标准方法, 正确。

### 🟢 `win_acl.py:152,154` `AddAccess{Denied,Allowed}AceEx` 签名 (致命, ACL 写回)

新版 pywin32 要 `(revision, flags, mask, sid)`, 旧版只收 `(flags, mask, sid)`。原代码传 3 参在新版 pywin32 抛 TypeError。修复加首位 `revision=2` (`ACL_REVISION=2`, 普通文件 ACE)。

- 修复正确。`ACL_REVISION_SD` 等版本号对文件对象用 2 即可, 仅对象特定 ACE 才需 3/4。
- 同步修了 `revoke_sandbox_acl` (win_acl.py:543,545), 一致。

### 🟢 `win_acl.py:194,214,518,549` `SE_FILE_OBJECT` 归属修正

原 `win32con.SE_FILE_OBJECT`, 实际 `SE_FILE_OBJECT` 在 `win32security` (非 `win32con`)。pywin32 新版 `win32con` 已无此常量 → AttributeError。改为 `win32security.SE_FILE_OBJECT`。共 4 处 (`grant_ace` 读/写 + `revoke_sandbox_acl` 读/写)。

- 修复正确且一致。`win_constants.py:230` 也定义了 `SE_FILE_OBJECT = 1`, 但 win_acl.py 用的是 `win32security.SE_FILE_OBJECT` (pywin32 自带), 二者值一致。
- `_ensure_pywin32` 仍 import `win32con` (win_acl.py:48), 但实际不再使用其常量 — 可清理但无害。

### 🟢 `process.py:2782` 去掉对同步函数的 `await` (致命, 流程阻塞)

`ensure_windows_setup` 是同步 `def` (内部读注册表 + 阻塞跑 UAC 子进程), 原代码 `await win_setup.ensure_windows_setup(...)` → `await None` → `TypeError: object NoneType can't be used in 'await' expression`。此 bug 之前被注册表 WinError 5 掩在 `ensure_windows_setup` 内部, 修注册表后才暴露。修复去掉 `await`, 直接同步调用。

- 修复正确。`ProcessRuntime._create_windows` 是 async 方法, 内部调同步函数是常见模式 (UAC 阻塞弹窗本身就不该 async)。
- 阻塞调用期间事件循环会卡住, 但 UAC 弹窗 + install 是低频一次性操作, 可接受。

### 🟢 `daemon_ipc.py:88,103` IPC 协议兼容 socket 与 anonymous pipe (功能性)

原 `recv_exact`/`send_frame` 硬调 `sock.recv`/`sock.sendall`, Windows 沙箱 runner roundtrip 用 `os.fdopen` 打开的 pipe file object (BufferedReader/BufferedWriter), 无 `recv`/`sendall` → AttributeError。修复: `recv_exact` 用 `getattr(sock, "recv", None) or sock.read`, `send_frame` 用 `getattr(sock, "sendall", None)` 判定走 `sendall` 还是 `write+flush`。

- 修复正确, socket 与 pipe 双路径都覆盖。
- `recv_exact` 用 `or sock.read` 而非 `else sock.read`: 若 `getattr` 返回 `None` (socket 无 recv 属性 — 理论不会) 则 fallback read, 防御性。

### 🟢 `policy_engine.py:111` `_is_absolute_sandbox_path` 兼容 Windows 绝对路径 (功能性)

原 `PurePosixPath(path).is_absolute()` 把 Windows `C:\\...` 判成非绝对 → HTTP 400 "must be absolute"。修复为 `PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()`。

- 修复正确。`PureWindowsPath("C:\\foo").is_absolute()` = True, `PurePosixPath("/foo").is_absolute()` = True, 二者覆盖 Windows + POSIX。
- 边界: `PureWindowsPath("\\\\server\\share")` (UNC) 也判 True, 符合预期。
- 注意: box-server 自身运行平台不影响判断 (PureWindowsPath 在 Linux 也能解析 Windows 路径字符串), 注释已说明。

## 关键代码检视

### 用户创建 (`win_setup.py:330-367`)

```python
def _create_sandbox_user(password: str) -> None:
    info = _USER_INFO_1()
    info.usri1_name = const.SANDBOX_USER_NAME
    info.usri1_password = password
    info.usri1_priv = USER_PRIV_USER          # USER_PRIV_USER=1 (普通用户, 非 admin/guest)
    info.usri1_flags = const.SANDBOX_USER_FLAGS
    ...
    ret = netapi32.NetUserAdd(None, const.USER_INFO_1_LEVEL, ctypes.byref(info), ctypes.byref(err))
    if ret == 0: ...
    elif ret == 2224: ...                      # NERR_UserExists, 幂等跳过
```

- 账户类型 `USER_PRIV_USER=1` 正确 (非 guest=0, 非 admin=2)。
- `SANDBOX_USER_FLAGS` = `UF_SCRIPT | UF_PASSWD_CANT_CHANGE | UF_DONT_EXPIRE_PASSWD | UF_NORMAL_ACCOUNT` (win_constants.py:140), 不含 `UF_ACCOUNTDISABLE`, 账户启用, 可登录。
- 幂等: 已存在用户不重设密码, 注释解释"密码固定 000000, 每次 install 生成的密码一致"。这与 `_generate_password` 固定返回 `"000000"` (win_setup.py:327) 配套。

### 密码持久化与读取 (`win_setup.py:913-918, 1219-1235`)

- 写: `win32crypt.CryptProtectData(password.encode("utf-8"), "jbx-sandbox-pw", None, None, None, 0)` (DPAPI 机器范围), hex 存注册表 `sandbox_user_pw_encrypted`。
- 读: `win32crypt.CryptUnprotectData(enc, None, None, None, 0)`。
- 两阶段注册表读 (`_reg_get_str`) 保证长 hex 串能完整读出。
- pywin32 缺失时降级 (不加密存储 / 无法解密), 仅开发环境, 注释已说明。

### 用户清理 (`win_setup.py:1244-1288`)

- `uninstall()` 调 `NetUserDel` + `NetLocalGroupDel`, 错误码 2221/2220 (NotFound) 幂等。
- `DeleteProfileW` 按 SID 删 profile 目录 + ProfileList 注册项 (正规方式)。
- `_purge_stale_profile_dirs()` 兜底 rmtree `C:\Users\jbx-sandbox*` 历史 .000/.001 备份目录 (反复 reinstall 堆积根因), `onerror` 回调 chmod 重试 + 跳过 WinX 等系统锁定项。
- install 失败回滚调 `uninstall()` (win_setup.py:1024), 不写 installed=1。
- 顺序正确: 先 `DeleteProfileW` (按注册表缓存 SID) → 再 `_purge_stale_profile_dirs` → 再 `NetUserDel` (删用户后 SID 仍可用于 DeleteProfileW, 但这里已先删 profile)。

### IPC 协议 (`daemon_ipc.py:88-120`)

- `recv_exact(sock, n)`: `recv = getattr(sock, "recv", None) or sock.read`, 循环读到 n 字节, 短读重试, EOF 抛 `ConnectionError`。
- `send_frame(sock, payload)`: `sendall = getattr(sock, "sendall", None)`, 有则 `sendall(length_prefix) + sendall(payload)`, 无则 `write(length_prefix) + write(payload) + flush()`。
- `recv_frame(sock, max_size)`: 读 4 字节 length, 校验 `max_size`, 再 `recv_exact` 读 payload。
- 协议不变: 仍是 4 字节 big-endian length prefix + payload, `PROTOCOL_VERSION=1` 未变, 向后兼容。

### ACL 赋予 (`win_acl.py:266-479`)

- 写控制: `allow_write` 施加 Allow `ALLOW_WRITE_RIGHTS` (Write+Execute+Delete) 给合成 SID; `deny_write` 施加 Deny `FILE_GENERIC_WRITE`。
- 读控制: `deny_read` 施加 Deny `FILE_GENERIC_READ`; `allow_read` 施加 Allow `FILE_GENERIC_READ`, 覆盖 deny。
- 第一跳 runner (jbx-sandbox 真实 SID, token 未受限) 同步授一份 (合成 SID ACE 对未受限 token 不生效)。
- 数据根 traverse: 对 `OFFICE_CLAW_DATA_ROOT / JIUWENCLAW_DATA_DIR_PATH / JIUWENBOX_HOME` 非递归 Allow Read, 让受限 token 能 lstat 整条链。
- `~/.office-claw` 整树递归 Read+Write (一劳永逸, 单用户本地部署可接受跨沙箱读)。
- revoke: 按 `apply_sandbox_acl` 返回的路径清单逐路径递归撤销合成 SID 的 ACE, 保留继承不切断 (`PROTECTED_DACL` 不设)。

## 优点

1. **根因定位精确**: 每个 bug 修复都附详尽注释还原实测现象 + Win32 SDK 依据 (NERR 错误码、SAM 权限位、ctypes 类型映射、pywin32 ACE 元组形态), 后续维护者能快速理解。
2. **幂等性贯穿**: 用户已存在 (2224)、组已存在 (2237)、成员已在组 (1377)、用户不存在 (2221)、组不存在 (2220)、DeleteProfileW 无 profile (2) 均幂等, 重装/卸载干净。
3. **回滚完整**: install 致命步骤失败调 `uninstall()` 回滚 + re-raise, 不写假 installed=1。
4. **profile 清理正规**: `DeleteProfileW` (删目录 + ProfileList 注册项) + `_purge_stale_profile_dirs` 兜底, 解决反复 reinstall 导致 `C:\Users\jbx-sandbox.*` 堆积。
5. **IPC 协议向后兼容**: socket/pipe 双路径覆盖, 协议版本未变, 老客户端兼容。
6. **ACL 顺序正确**: Deny 在前 Allow 在后 (NTFS 显式 Deny 优先), 不切断继承链。

## 问题与风险

### 🟡 密码强度弱 (安全)

`_generate_password()` (win_setup.py:320-327) 固定返回 `"000000"`。注释承认"调试阶段固定", 但:

- 6 位纯数字密码极易被本地暴力破解 (Windows 本地账户无锁定阈值时尤其)。
- `SANDBOX_USER_PASSWORD_LENGTH = 64` (win_constants.py:36) 定义了 64 位密码长度常量却未使用, `_generate_password` 应改用 `secrets.token_urlsafe(64)` 或至少 `secrets.token_hex(32)`。
- `UF_PASSWD_CANT_CHANGE` 标志阻止用户改密, 但不阻止其他 admin/进程 `NetUserSetInfo` 重设, 弱密码仍是攻击面。
- 若机器密码复杂度策略启用, `NetUserAdd` 用 "000000" 会直接失败 (ret=2243 `NERR_PasswordTooShort` 或类似)。当前靠"密码固定 + 已存在不重设"绕开, 但首次创建仍可能撞策略。注释也提到 `_set_user_password` 重设 "000000" 撞复杂度策略 → ret=87。

**建议**: 改用强随机密码 (`secrets.token_urlsafe(32)`), 注册表 DPAPI 加密存储已具备, 无需固定。`_verify_or_reset_sandbox_user_password` 兜底重设时也用强密码。

### 🟡 IPC `send_frame` pipe 路径无短写重试 (健壮性)

socket `sendall` 内部已处理短写 (循环发送直到全部发出), 但 pipe 路径 `sock.write(...)` 对 BufferedWriter 是 buffered write, 单次 `write` 可能不立即全部刷出, 靠 `flush()` 兜底。`BufferedWriter.write` 通常返回写入字节数, 但这里忽略返回值。若 `write` 返回少于入参字节数 (极端情况, 如 pipe buffer 满 + 非阻塞), 会静默丢字节 → 对端 `recv_exact` 拿到不完整 length prefix → 协议错乱。

**建议**: pipe 路径也循环写, 或确认 `BufferedWriter.write` 保证全量写入 (实际上 CPython 的 `BufferedWriter.write` 会循环直到全部写入或抛错, 风险较低, 但显式循环更稳妥)。

### 🟡 `recv_exact` 用 `or sock.read` 隐患 (健壮性)

`recv = getattr(sock, "recv", None) or sock.read`: 若 socket 的 `recv` 属性存在但为 `None` (不正常, 但理论上), 会 fallback `sock.read`。更严重: 若 `sock` 既无 `recv` 也无 `read` (传入错误类型对象), `getattr` 返回 `None`, `None or sock.read` 抛 `AttributeError`, 错误信息不清晰 ("'xxx' object has no attribute 'read'")。

**建议**: 显式判 `if hasattr(sock, "recv"): recv = sock.recv elif hasattr(sock, "read"): recv = sock.read else: raise TypeError(...)`, 错误更清晰。

### 🟡 `~/.office-claw` 整树递归 Read+Write 过宽 (安全)

`apply_sandbox_acl` (win_acl.py:437-464) + `install` (win_setup.py:982-996) 对 `Path.home()/.office-claw` 整树递归 grant `ALLOW_WRITE_RIGHTS` (Write+Execute+Delete) 给合成 SID + jbx-sandbox 真实 SID。注释承认"单用户本地部署, 跨沙箱读 workspace 可接受"。

- 风险: 若该机器多用户或多沙箱实例, 一个沙箱的受限进程能写另一个沙箱的 workspace (跨沙箱写入泄露)。
- `~/.office-claw` 含 `.ms-playwright` (浏览器二进制)、`isolation_venv`、各 sandbox workspace, 全部 Write+Delete 权限给合成 SID 意味着任一沙箱 child 进程 (携带合成 SID 的受限 token) 能删/改其他沙箱产物。
- 单用户本地部署确实可接受 (数据本就是当前用户的), 但若产品未来上多租户/服务器模式, 此处是横向移动入口。

**建议**: 至少在文档/注释中明确标注"单用户本地部署前提", 并考虑用 per-sandbox 子目录 ACL 替代整树授权 (性能权衡)。

### 🟢 `win32con` 仍被 import 但未使用 (清理)

`_ensure_pywin32` (win_acl.py:48) 仍 `import win32con`, 但 `SE_FILE_OBJECT` 已改用 `win32security.SE_FILE_OBJECT`, `win32con` 实际不再被引用。无害, 但可清理。

### 🟢 `_reg_get_str` 返回 None 时 `ensure_windows_setup` 静默走 install (设计)

`_reg_get_str` 对 `ERROR_FILE_NOT_FOUND (2)` 和 `ERROR_ACCESS_DENIED (5)` 均返回 None。`ensure_windows_setup` 见 None 走 install 提权路径。设计合理 (普通用户读 HKLM 失败就走提权), 但若 key 存在但值损坏 (非预期形态), 也走 install, install 内部不校验旧值形态直接覆盖, 行为正确但隐式。

## 改进建议

1. **密码强度 (高优)**: `_generate_password` 改用 `secrets.token_urlsafe(32)` 或 `secrets.token_hex(SANDBOX_USER_PASSWORD_LENGTH)`, 删除固定 "000000"。注册表 DPAPI 加密已就绪, 无需固定密码调试。
2. **IPC pipe 写循环 (中优)**: `send_frame` pipe 路径显式循环写或确认 `BufferedWriter.write` 全量语义, 加测试覆盖 pipe roundtrip。
3. **`recv_exact` 类型判定 (低优)**: 显式 `hasattr` 判定 + 清晰 TypeError, 优于 `getattr(...) or sock.read`。
4. **`~/.office-claw` 整树授权标注 (低优)**: 在 `apply_sandbox_acl` / `install` 注释中明确"单用户本地部署前提", 供未来多租户改造参考。
5. **`win32con` import 清理 (低优)**: 删除 `_ensure_pywin32` 中未使用的 `import win32con`。
6. **NERR 错误码集中定义 (低优)**: `2224/2237/2221/2220/1377` 等 NERR 码散落在 win_setup.py, 建议在 win_constants.py 集中定义具名常量 (`NERR_UserExists=2224` 等), 避免魔法数字 + 防止再次写错。

## 小结

本 commit 是一次高质量的串联 bug 修复。修复了从注册表 SAM 权限位 → ctypes 类型映射 → pywin32 ACE 元组形态 → NERR 错误码 → IPC 协议泛化 → 异步/同步误用 共 6 类真实环境暴露的 bug, 每处都附详尽根因注释。用户创建、密码持久化、ACL 重建、profile 清理的核心逻辑均正确且幂等。主要遗留风险是密码强度弱 ("000000" 固定) 与 `~/.office-claw` 整树 Write+Delete 授权过宽, 二者均不影响功能正确性但影响安全姿态, 建议在调试期结束后优先处理密码强度。

**3-5 条最重要发现**:
1. 密码固定 `"000000"` (win_setup.py:327), 未用已定义的 `SANDBOX_USER_PASSWORD_LENGTH=64`, 弱密码 + 撞复杂度策略风险。
2. `KEY_READ_WRITE` 权限位 (0x20019→0x2001F) 与 `NetLocalGroupAdd` argtypes 错位是两个串联致命 bug, 修后才暴露下游 NERR/ctypes 问题。
3. `_reg_get_str` 两阶段读修复了 DPAPI 密码 hex 串被截断 (固定 512 chars buffer) 导致 `get_sandbox_user_password` 拿不到密码 → 1326 的链路。
4. `~/.office-claw` 整树递归 Write+Delete 授权给合成 SID + 真实 SID, 单用户本地可接受但多租户/服务器模式是横向移动入口。
5. IPC `send_frame` pipe 路径 `write+flush` 未循环写, 依赖 `BufferedWriter.write` 全量语义, 极端短写场景可能丢字节。
