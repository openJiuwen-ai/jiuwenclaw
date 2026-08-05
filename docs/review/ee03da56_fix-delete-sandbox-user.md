# 代码审查：ee03da56 fix:删除sand-box用户

- **commit**: `ee03da56e77b28be99eebd809346e7c2acf7e3ed`
- **作者**: lby，2026-07-28
- **范围**: `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py`，+95 / -2
- **审查日期**: 2026-08-01
- **审查人**: Windows 系统工程代码审查

> 说明：行号引用以 commit `ee03da56` 自身版本的 `win_setup.py` 为准（`git show ee03da56:.../win_setup.py`）。当前工作树文件已在后续 commit 中加 `_rmtree_onerror` / `os.rmdir` 兜底，本报告聚焦 ee03da56 本身的代码。

---

## 1. 概述

本 commit 给 Windows 沙箱卸载链路补上了此前一直缺失的两件事：

1. 在 `uninstall()` 中真正删除沙箱用户 `jbx-sandbox`（旧版注释明确说"保留账户避免残留密码"不删，结果是 reinstall 时密码与注册表密码不一致，最终触发 `CreateProcessWithLogonW` WinError 1326）。
2. 删除用户时同步清理用户的 profile 目录与 `ProfileList` 注册项，并对 `C:\Users\jbx-sandbox.*`（`.000`/`.001`/`DESKTOP-XXX` 备份目录）做兜底 `rmtree`。

实现上是新增一个 `_get_userenv()` 暴露 `DeleteProfileW`，加上 `_delete_profile_by_sid()` 和 `_purge_stale_profile_dirs()` 两个工具函数，再在 `uninstall()` 主体里按"WFP/防火墙卸载 → 删 profile → 删用户 → 清注册表"顺序串起来。

总体上方向正确：先删 profile 再删用户、用 `DeleteProfileW` 而不是裸 `shutil.rmtree`、错误码按 `lmerr.h` 校正。但有几处与运行期进程生命周期、`ignore_errors=False` 的中断行为、以及对称性相关的隐患需要后续收紧。

## 2. 变更范围

| 位置 (ee03da56 行号) | 变更 |
|---|---|
| `win_setup.py:41-42` | 新增模块级 `_userenv` 句柄 |
| `win_setup.py:154-173` | 新增 `_get_userenv()`，绑定 `DeleteProfileW` 的 `argtypes/restype` |
| `win_setup.py:470-490` | 新增 `_delete_profile_by_sid(sid_str)`，按 SID 调 `DeleteProfileW` |
| `win_setup.py:494-521` | 新增 `_purge_stale_profile_dirs()`，前缀匹配 `rmtree` 残留 profile 目录 |
| `win_setup.py:1000-1043` | 重写 `uninstall()`：补 `uninstall_firewall_rule_fallback()`、profile 删除、用户/组删除 |
| `win_setup.py:85-89` | `_get_netapi32()` 中补 `NetUserDel` / `NetLocalGroupDel` 的 `argtypes` 声明（实际声明在前序 commit，本 commit 仅使用） |

## 3. 删除逻辑分析

### 3.1 删除时机 🟢

`uninstall()` 在两个场景被调用（见 `win_setup.py:1024` 的 `install()` 回滚，以及 `_main` 的 `--uninstall` CLI 分支 `win_setup.py` ee03da56 行 1077 附近）：
- **install 失败回滚**：install 主体 `try/except` 捕获致命错误后调 `uninstall()`（`win_setup.py:1024`），此时沙箱进程尚未真正跑起来（install 阶段不启动 runner），删用户安全。
- **CLI 手动卸载**：`python -m jiuwenbox.supervisor.win_setup --uninstall`。

我未在 `server/` 或 `supervisor/` 中找到运行期（沙箱 runner 在跑时）调用 `uninstall()` 的代码路径——`server/app.py:300` 只调 `ensure_windows_setup`（install 路径），`server/runtime/process.py` 也只调 `collect_preinstall_paths` / `ensure_windows_setup` / `get_sandbox_user_*`。**结论：当前调用面下，删用户发生在"卸载/重装"而非"沙箱运行期"，时机正确。** 但需注意 §4.3 的隐患：卸载入口没有任何机制保证 runner 进程已退出。

### 3.2 删除方式 🟢

- 用户：`NetUserDel(None, const.SANDBOX_USER_NAME)`（`win_setup.py:1028`），netapi32 正规 API，不是 `Win32_UserAccount` WMI / `net user /delete` 子进程。
- 组：`NetLocalGroupDel(None, const.SANDBOX_USER_GROUP)`（`win_setup.py:1036`）。
- profile：`DeleteProfileW(sid_str, None, None)`（`win_setup.py:507`，由 `_delete_profile_by_sid` 调用），`lpProfilePath` 传 `None` 让系统自己从 `ProfileList` 解析路径，避免读到错路径。这比裸 `shutil.rmtree("C:\\Users\\jbx-sandbox")` 干净——后者会留下 `HKLM\...\ProfileList\<sid>` 注册项，是下次同名用户登录时 Windows 建 `.000`/`.001` 备份目录的根因（commit message 与 docstring 都点明了这点，属实）。

### 3.3 幂等性 🟢（用户/组/profile 各有处理）

- `NetUserDel` 把 `0`（成功）和 `2221`（`NERR_UserNotFound`）都视为成功（`win_setup.py:1030-1033`），注释还指出旧版误用 `2201`（`NERR_BadPassword`）导致幂等卸载误判 warning，据 `lmerr.h` 改回 `2221`——✓ 正确。
- `NetLocalGroupDel` 同理：`0` + `2220`（`NERR_GroupNotFound`）视为成功（`win_setup.py:1036-1039`）——✓ 正确。
- `DeleteProfileW` 失败时检查 `get_last_error() == 2`（`ERROR_FILE_NOT_FOUND`，无 profile 可删）视为幂等成功（`win_setup.py:512`）——✓ 正确。
- 其他非幂等错误一律降级为 `logger.warning(...)` 并继续，不 raise——✓ 卸载 best-effort，不阻断。

### 3.4 profile/目录/注册表 hive 清理 🟢（DeleteProfileW 部分）/ 🟡（rmtree 部分）

- `DeleteProfileW` 同时删 profile 目录树 + `ProfileList\<sid>` 注册项（`win_setup.py:470-489`）——✓ 正规路径，覆盖到位。
- 兜底 `_purge_stale_profile_dirs()` 按目录名前缀 `== SANDBOX_USER_NAME` 或 `startswith(SANDBOX_USER_NAME + ".")` 严格匹配（`win_setup.py:509-512`），避免误删 `sandbox-other` 之类无关目录——✓ 严格前缀匹配，意识正确。
- 但本 commit ee03da56 版本用的是 `shutil.rmtree(path, ignore_errors=False)`（`win_setup.py:517`），**`ignore_errors=False` 在遇到 profile 内 `WinX` reparse point（系统锁定 ACL，WinError 5）时会让 rmtree 直接抛 `OSError` 中止**，导致"删了一半留半删残留"——这是 ee03da56 版本的一个真实缺陷。后续 commit 已加 `_rmtree_onerror` + `os.rmdir` 兜底修复（见当前工作树 `win_setup.py:540-582`），本 commit 算遗留。**审查此 commit 时点（2026-07-28）此问题存在，需在后续 commit 中确认已修——已确认。**

### 3.5 异常/中途失败时是否遗留用户 🟡

`uninstall()` 整体是 best-effort：
- WFP/防火墙卸载失败：`except Exception: logger.warning(...)` 继续往下（`win_setup.py:1011-1014`）。
- profile 删除失败：`_delete_profile_by_sid` 内 warning 后返回 `False`，不抛。
- `_purge_stale_profile_dirs` 失败：warning 后 continue。
- `NetUserDel` 非 0/2221：warning 继续。
- `NetLocalGroupDel` 非 0/2220：warning 继续。
- 最后 `_reg_set_str(REG_VALUE_INSTALLED, "")`（`win_setup.py:1043`）无 try/except 保护。

**风险点**：如果 profile 正被占用（沙箱 runner 还没退），`DeleteProfileW` 失败但只 warning，紧接着 `NetUserDel` 仍会删用户。结果是用户没了但 profile 目录 + `ProfileList` 注册项残留（commit message 的注释也承认"目录待重启后可手动删"）。这本身不是灾难，但与 commit 目标"reinstall 干净重建"有偏差——下次 install 会发现 profile 注册项还在，`DeleteProfileW` 在 reinstall 路径里没被调（install 不调 `_delete_profile_by_sid`），可能再次触发 `.000` 堆积。建议 uninstall 删用户前若 `DeleteProfileW` 失败，至少 abort 删用户或显式提示需重启。

### 3.6 删除权限要求 🟢

- `_get_userenv` docstring 明确 `DeleteProfileW` 需 admin（`win_setup.py:179`）。
- `uninstall()` 入口 `if not _is_admin(): _elevate_uninstall(); return`（`win_setup.py:1003-1004`）——✓ 非 admin 会 UAC 拉起提权子进程。
- `NetUserDel` / `NetLocalGroupDel` 在 admin 上下文下天然可用。
- 但 `_elevate_uninstall()`（`win_setup.py:1046` 附近）用 `ShellExecuteW("runas", sys.executable, ...)`，**没有像 install 那样的命名 Event 同步等待机制**——uninstall 子进程是否跑完、是否失败，主进程无从得知。这是既有 install/uninstall 不对称（install 有 `--install-done-event` 同步，uninstall 无）。本 commit 未引入但也没恶化此问题，仅记录。

### 3.7 与用户创建逻辑的对称性 🟡

install 侧（前序 commit `3fe33056 fix:修复沙箱用户创建bug` 等）创建链路：
`_generate_password` → `_create_sandbox_user`（NetUserAdd，已存在则跳过不重设密码）→ `_add_user_to_group`（NetLocalGroupAdd + NetLocalGroupAddMembers）→ `_lookup_user_sid` → 写注册表 SID/DPAPI 密码。

uninstall 侧（本 commit）对称链路：
`NetUserDel`（删用户）+ `NetLocalGroupDel`（删组）+ `_delete_profile_by_sid`（删 profile）+ `_reg_set_str(REG_VALUE_INSTALLED, "")`。

**对称缺口**：
- install 写了三个注册表值：`REG_VALUE_SANDBOX_USER_SID`、`REG_VALUE_SANDBOX_USER_PW`、`REG_VALUE_SYNTHETIC_WRITE_SID`、`REG_VALUE_PREINSTALLED_PATHS`、`REG_VALUE_READ_ACL_PROGRESS`、`REG_VALUE_INSTALLED`。uninstall 只清 `REG_VALUE_INSTALLED`（注释 `win_setup.py:1042` 说"其他值保留也无害，reinstall 会覆盖"）。
- 对 reinstall 而言这基本成立（install 会覆盖 SID/PW/PREINSTALLED_PATHS，`_preinstall_read_acl` 会重置 PROGRESS）。**但 `REG_VALUE_SANDBOX_USER_PW` 是 DPAPI 加密的密码 blob，用户删了之后这个值还留**——下次 install 用 `_generate_password()` 固定返回 `"000000"`，覆盖写入新 blob，逻辑上 OK；但调试期一过改回随机密码（docstring `win_setup.py:323` 已预告"后期改为从配置文件读取"），如果新 install 用新随机密码但旧 blob 解密得到旧密码且 install 路径没覆盖到位，会重现 1326。建议 uninstall 顺手把 `REG_VALUE_SANDBOX_USER_SID/PW` 一起清，彻底对称。

## 4. 安全审查

### 4.1 删错系统账户的风险 🟢

`NetUserDel(None, const.SANDBOX_USER_NAME)` 传的是 `const.SANDBOX_USER_NAME = "jbx-sandbox"`（`win_constants.py:25`），硬编码常量，不接外部输入，不会因用户可控字符串删到 `Administrator` / `Guest` / `DefaultAccount` 等系统账户。组名同理 `const.SANDBOX_USER_GROUP = "jbx-sandbox-users"`。✓ 无注入面。

### 4.2 是否按 SID/名称校验 🟡

- 删用户按**名称**（`NetUserDel` 接受 username），这是 netapi32 的标准用法，名称在本地 SAM 内唯一，可接受。
- 删 profile 按**SID 字符串**（`DeleteProfileW(sid_str, None, None)`，`win_setup.py:507`），`sid_str` 来自 `get_sandbox_user_sid()` → `_reg_get_str(REG_VALUE_SANDBOX_USER_SID)`（注册表缓存）。✓ 按 SID 删 profile 比按名称安全（避免同名歧义）。
- **但**：`sid_str` 是 install 时缓存进注册表的，install 失败回滚走到 `uninstall()` 时，如果 install 在写 SID 之前就失败（`_lookup_user_sid` 抛错），注册表里可能存的是**上一次 install 的旧 SID**（同一用户名 reinstall 后 SID 会变，因为删用户再建会分配新 RID）。`get_sandbox_user_sid()` 拿到旧 SID 调 `DeleteProfileW`，要么 `ERROR_FILE_NOT_FOUND`（幂等成功，本次 profile 没删），要么删错——删的是旧 SID 对应的 profile 目录。好在旧 SID 对应的目录本就是"历史残留"，`_purge_stale_profile_dirs` 会兜底按名称前缀删，所以实际影响有限。但语义上"用可能过期的 SID 删 profile"是个味儿，建议 `_delete_profile_by_sid` 调用前用 `LookupAccountName(SANDBOX_USER_NAME)` 实时取当前 SID 再删，注册表缓存只作 fallback。

### 4.3 卸载时是否确保沙箱进程已退出 🔴

这是本 commit 最需要关注的点。`uninstall()` 删用户 + 删 profile 时，**没有先确认沙箱 runner 进程（以 jbx-sandbox 身份跑的 `two_hop_spawn` 子进程）已退出**。

- `win_exec.py:542` 的 `_stop_runner` 用 `TerminateProcess` 兜底杀进程，但这是运行期单沙箱停止逻辑，`uninstall()` 不调它。
- 当前 `uninstall()` 只在 CLI / install 回滚被调，**运行期不会调**（见 §3.1 调用面分析）。所以"沙箱在跑时删用户"这个场景在当前代码路径下不会发生——这是为什么我标 🔴 而非"已发生"：**风险在于调用面扩张时**。一旦未来某个"卸载产品"流程（如 relay-claw 卸载、box-server 优雅退出）直接调 `win_setup.uninstall()`，而那时还有 runner 在 jbx-sandbox 上下文跑：
  1. `DeleteProfileW` 因 profile 被占用失败（warning 继续）。
  2. `NetUserDel` 删用户——**用户被删但 token 仍持有**，运行中进程继续跑直到退出，但下次登录失败；profile 目录残留。
  3. 更糟：WFP filter 已卸载，沙箱 runner 失去网络隔离却仍在 jbx-sandbox token 下跑，可能继续访问网络。

建议 `uninstall()` 入口加一个"确保无 jbx-sandbox 进程"的前置检查（枚举 `CreateProcessWithLogonW` 创建的 job 对象 / 按 SID 查进程），或在 docstring/调用约定里强约束"调用方必须先停所有沙箱"。本 commit 没做这层防护。

### 4.4 防火墙降级规则卸载 🟢

`uninstall()` 新增 `win_wfp.uninstall_firewall_rule_fallback()`（`win_setup.py:1013`），清理 install 走降级路径时 PowerShell 建的两条 `New-NetFirewallRule`。注释点明旧版漏卸降级规则会导致"沙箱用户永久被 Block 出站"。✓ 这是个真实修复（对应 review B2），且放在 `try/except` 里 best-effort。

## 5. 关键代码检视

```python
# win_setup.py:1000-1043 (ee03da56)
def uninstall() -> None:
    """卸载: 删除 WFP filter + profile + 用户 + 注册表标记 (管理员)."""
    _require_windows()
    if not _is_admin():
        _elevate_uninstall()
        return
    try:
        from jiuwenbox.supervisor import win_wfp
        win_wfp.uninstall_wfp_filters()
        win_wfp.uninstall_firewall_rule_fallback()   # 🟢 修了旧版漏卸降级规则
    except Exception:  # noqa: BLE001
        logger.warning("WFP/防火墙卸载失败", exc_info=True)
    # 🟢 顺序正确: 先删 profile 再删用户 (删用户后 LookupAccountName 失效)
    sid_str = get_sandbox_user_sid()
    if sid_str:
        _delete_profile_by_sid(sid_str)            # 🟡 sid 可能是 install 早期失败时的旧缓存
    _purge_stale_profile_dirs()                     # 🟡 ee03da56 版 rmtree ignore_errors=False, 遇 WinX 抛出中止
    netapi32 = _get_netapi32()
    ret = netapi32.NetUserDel(None, const.SANDBOX_USER_NAME)  # 🔴 前面未确保 runner 退出
    if ret not in (0, 2221):                        # 🟢 2221 幂等, 据 lmerr.h 校正
        logger.warning("NetUserDel 返回 %d (继续)", ret)
    ret = netapi32.NetLocalGroupDel(None, const.SANDBOX_USER_GROUP)
    if ret not in (0, 2220):                        # 🟢 2220 幂等
        logger.warning("NetLocalGroupDel 返回 %d (继续)", ret)
    _reg_set_str(const.REG_VALUE_INSTALLED, "")    # 🟡 只清 installed, SID/PW 保留
```

```python
# win_setup.py:507-518 (ee03da56)
ok = userenv.DeleteProfileW(sid_str, None, None)
if ok:
    return True
err = ctypes.get_last_error()
if err == 2:                                        # 🟢 ERROR_FILE_NOT_FOUND 幂等
    return True
logger.warning("DeleteProfileW(sid=%s) 失败 ...", sid_str, err)
return False
```

```python
# win_setup.py:509-519 (ee03da56) — 严格前缀匹配
if name == const.SANDBOX_USER_NAME or name.startswith(
    const.SANDBOX_USER_NAME + "."
):
    path = os.path.join(users_root, name)
    shutil.rmtree(path, ignore_errors=False)        # 🟡 ee03da56 版本遇锁定子项会抛出中止
```

## 6. 优点

1. **彻底删用户，根除 1326**：旧版"保留账户避免残留密码"是个反模式，导致 reinstall 时用户密码（旧）与注册表 DPAPI 密码（新）不一致 → `CreateProcessWithLogonW` 1326。本 commit 改为删用户 + reinstall 干净重建，方向正确。
2. **用 DeleteProfileW 而非裸 rmtree**：同时清 profile 目录 + `ProfileList` 注册项，避免下次同名用户登录建 `.000`/`.001` 备份目录堆积，是 Windows 用户删除的正规做法。`lpProfilePath` 传 None 让系统解析路径也避免了读错路径。
3. **错误码据 lmerr.h 校正**：`2221`（UserNotFound）/ `2220`（GroupNotFound）/ `2`（FILE_NOT_FOUND）幂等处理，还点明旧版误用 `2201`/`2201`，修正扎实。
4. **卸载顺序合理**：WFP/防火墙 → profile → 用户 → 注册表标记。删 profile 在删用户之前（注释 win_setup.py:1016-1021 说清了"删用户后 LookupAccountName 查不到 SID 但 DeleteProfileW 按 SID 仍可工作"），逻辑正确。
5. **兜底清理历史残留目录**：`_purge_stale_profile_dirs` 按 `SANDBOX_USER_NAME` + `.` 严格前缀匹配，意识正确，避免误删。
6. **补卸防火墙降级规则**：`uninstall_firewall_rule_fallback()` 修复旧版漏卸降级规则导致沙箱用户永久 Block 出站的真实 bug。

## 7. 问题与风险

| 编号 | 级别 | 问题 | 位置 |
|---|---|---|---|
| R1 | 🔴 | uninstall 删用户前未确保沙箱 runner 进程已退出。当前调用面（CLI / install 回滚）不触发，但未来"产品卸载"流程一旦直接调 `uninstall()` 而 runner 还在跑，会删用户于运行中、profile 占用删不掉、WFP 已卸但 runner 仍持 token 跑 | `win_setup.py:1028` |
| R2 | 🟡 | `_purge_stale_profile_dirs` 用 `shutil.rmtree(path, ignore_errors=False)`，遇 profile 内 `WinX` reparse point（系统锁定 ACL）会抛 `OSError` 中止，留半删残留。**后续 commit 已加 `_rmtree_onerror` + `os.rmdir` 兜底修复**，本 commit 时点存在 | `win_setup.py:517`（ee03da56 版） |
| R3 | 🟡 | `_delete_profile_by_sid` 用注册表缓存的 SID，install 早期失败回滚时可能是**上一次 install 的旧 SID**，语义上删的是旧 profile。实际影响有限（`_purge_stale_profile_dirs` 兜底），但应实时 `LookupAccountName` 取当前 SID | `win_setup.py:1020-1022` |
| R4 | 🟡 | uninstall 只清 `REG_VALUE_INSTALLED`，`SANDBOX_USER_SID`/`SANDBOX_USER_PW`（DPAPI blob）保留。当前固定密码 `"000000"` 下无害，但 docstring 预告后期改随机密码/配置读取，届时若 install 覆盖不彻底会重现 1326。建议顺手清这两个值以对称 | `win_setup.py:1042` |
| R5 | 🟡 | `DeleteProfileW` 失败只 warning，紧接着仍 `NetUserDel` 删用户 → 用户没了但 profile 目录 + `ProfileList` 注册项残留，与 commit 目标"reinstall 干净"有偏差 | `win_setup.py:1022-1028` |
| R6 | 🟢（记录） | `_elevate_uninstall` 无 install 那样的命名 Event 同步，主进程不知 uninstall 子进程是否跑完/失败。本 commit 未引入也未恶化，仅 install/uninstall 不对称 | `_elevate_uninstall` |

## 8. 改进建议

1. **（对应 R1，必须）** 在 `uninstall()` 入口加前置检查：枚举以 jbx-sandbox SID 持有的进程（`EnumProcesses` + `OpenProcess` + `GetTokenInformation` 比对 SID），若存在则警告并要求调用方先停沙箱；或在 docstring + 调用约定里强约束"调用方必须先停所有沙箱 runner"。
2. **（对应 R3）** `_delete_profile_by_sid` 调用前先 `LookupAccountName(SANDBOX_USER_NAME)` 实时取当前 SID，注册表缓存仅作 fallback。若 Lookup 失败（用户已删）才用缓存 SID。
3. **（对应 R5）** 若 `DeleteProfileW` 失败且 `err != 2`，考虑 abort 删用户或显式 warning 提示"用户将被删但 profile 残留，需重启后手动删"，避免静默留残留。
4. **（对应 R4）** uninstall 顺手清 `REG_VALUE_SANDBOX_USER_SID`、`REG_VALUE_SANDBOX_USER_PW`，与 install 写入完全对称，降低后期改随机密码时的回归面。
5. **（对应 R2，已修复确认）** ee03da56 版本的 `ignore_errors=False` 中止问题，后续 commit 已用 `_rmtree_onerror`（chmod 重试 + warning 跳过）+ `os.rmdir` 兜底修复，无需再动。
6. **（可选）** `_elevate_uninstall` 参照 `_elevate_and_run_install` 加命名 Event 同步，让主进程能确认 uninstall 子进程跑完，与 install 对称。

## 9. 小结

本 commit 方向正确、实现扎实：用 `DeleteProfileW` + `NetUserDel`/`NetLocalGroupDel` 取代旧版"保留账户"的反模式，根因上消解了 reinstall 时的 1326；错误码据 `lmerr.h` 校正，幂等处理到位；卸载顺序（WFP → profile → 用户 → 注册表）合理。主要遗留风险是 **R1（删用户前未确保 runner 退出，当前调用面不触发但未来扩张会踩）** 和 **R2（ee03da56 版 rmtree `ignore_errors=False` 遇锁定子项中止，后续 commit 已修）**。建议优先补 R1 的前置进程检查，其余 R3/R4/R5 属对称性收紧，可一并处理。
