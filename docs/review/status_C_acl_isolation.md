# 状态核对 C：ACL 与文件隔离

核对基准：当前工作区 = HEAD `82001d09`（branch `enterprise_dev_windowbox`）。

证据文件（绝对路径）：
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_acl.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_constants.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/supervisor/win_setup.py`
- `d:/Workspace/community/jiuwenclaw/jiuwenbox/src/jiuwenbox/server/runtime/process.py`

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|------|----------|----------|----------------|------|
| 1 | `_rebuild_acl_with_order`/`revoke_sandbox_acl` 用 `GetAclSize()`（ACL 字节数）当 ACE 个数遍历，`GetAce(3+)` 抛错被 except 吞；`_parse_getace_tuple` 不兼容 pywin32 `((ace_type,ace_flags),mask,sid)` 子元组形态 → grant/revoke 静默失败 | c2c3f5f0 §六.1 / §四.4 | ✅ 已解决 | win_acl.py:134 `for i in range(existing_dacl.GetAceCount())`；win_acl.py:528 revoke 同样用 `GetAceCount()`；win_acl.py:99-103 `if isinstance(first, tuple): ace_type, ace_flags = first` | c2c3f5f0 本身的 `GetAclSize` bug 已在工作区被后续 commit 修正为 `GetAceCount()`（两处一致），子元组分支也已补上（`isinstance(first, tuple)`），3/4/5 元组 + 子元组形态全覆盖。报告预期已修，核对一致。 |
| 2 | 预装超时降级后永久缺失读权限：预装超时仍写 installed 标记，`ensure_windows_setup` 已 installed 直接 return 不重跑 | c2c3f5f0 §三.9 / §六.5 | ✅ 已解决（以其他方式绕过） | win_setup.py:999-1005 超时仅 warning，不再阻塞；win_setup.py:1008 `_reg_set_str(REG_VALUE_INSTALLED, "1")` 仍写在超时后；win_setup.py:971-996 install 阶段对 `~/.office-claw` 整树递归 grant Read+Write（一劳永逸） | 仍存在"超时即写 installed=1"的写法（win_setup.py:1008 在 try 内，超时不是致命步骤，会继续到 installed 标记写入）。但**根因被绕过**：install 与 apply_sandbox_acl 都改为对 `~/.office-claw` 整树递归 grant Read+Write（win_setup.py:985-996 + win_acl.py:439-460），预装系统目录不再是沙箱可读的唯一来源；预装超时缺失读权限的路径，被整树 grant 覆盖。同时 `ensure_windows_setup` 增量检测新增预装路径会自动弹 UAC 补预装（win_setup.py:1110-1125）。报告担心的"永久缺失读权限"已不成立。 |
| 3 | `KEY_READ_WRITE` 权限位 `0x20019→0x2001F`；`NetLocalGroupAdd` argtypes 错位 | 3fe33056 §原 bug 分析 | ✅ 已解决 | win_setup.py:204 `KEY_READ_WRITE = 0x2001F`，win_setup.py:203 `KEY_READ_ONLY = 0x20019`；win_setup.py:69-72 `NetLocalGroupAdd.argtypes = [LPCWSTR, DWORD, c_void_p, c_void_p]` | 两处均按报告修正，且拆分读写权限位常量。`_reg_get_str` 用 `RegOpenKeyExW` + `KEY_READ_ONLY`（win_setup.py:245-247），`_reg_open_create` 用 `KEY_READ_WRITE`（win_setup.py:215）。 |
| 4 | `~/.office-claw` 整树递归 Write+Delete 授权过宽（给合成 SID + jbx-sandbox 真实 SID 整树递归 ALLOW_WRITE_RIGHTS） | 3fe33056 §问题与风险 / §4.1 | ❌ 仍存在 | win_acl.py:439-460（apply_sandbox_acl 末尾对 `_office_claw_root` 递归 grant ALLOW_WRITE_RIGHTS + FILE_GENERIC_READ 给 sid 与 sandbox_user_sid）；win_setup.py:984-996（install 阶段同样整树递归 grant） | 当前仍对整个 `~/.office-claw`（含所有沙箱 workspace、isolation_venv、.ms-playwright、业务产物）**整树递归** grant `ALLOW_WRITE_RIGHTS`（Write+Execute+Delete）给合成 SID **和**真实 SID。注释（win_acl.py:436 "单用户本地部署, 跨沙箱读 workspace 可接受"）显式承认放弃跨沙箱隔离。报告 R1 的收窄建议（"真实 SID 的 Write 只授 workspace 子树"）未采纳。 |
| 5 | ACE 继承标志 `0x7→0x3` 修复（修复前所有 recursive grant 不向下传播到孙目录） | 432a5001 §三.3 / §4.5 | ✅ 已解决 | win_constants.py:212 `SUB_CONTAINERS_AND_OBJECTS_INHERIT = CONTAINER_INHERIT_ACE \| OBJECT_INHERIT_ACE` (=0x3)；win_constants.py:207-211 注释明确纠正旧版 0x7 误用 | 修复正确，且注释充分。`RECURSIVE_ACE_FLAGS`（win_constants.py:217-221）由 0x3 组合而成，递归 ACE 现在能向下传播到孙目录。 |
| 6 | `~/.office-claw` 递归 Read+Write 过宽，真实 SID 也授 Write，跨沙箱隔离被放弃，与 deny_write 的 .git/.env 矛盾 | 432a5001 §4.1 / R1 | ❌ 仍存在 | win_acl.py:439-460（整树 grant Read+Write 给真实 SID）；win_acl.py:334-345（deny_write 仅给合成 SID `sid`，无 `sandbox_user_sid` 分支） | 同 #4，未收窄。deny_write 路径（.git/.env）仍只对合成 SID 施加 Deny（win_acl.py:339-344），对真实 SID 无 Deny ACE。结合受限 token 实际未用于 exec child（见 #7），真实 SID 的 Deny 缺失意味着 deny_write 对用户代码 child 无效。 |
| 7 | `~/.office-claw` 整树递归 grant Read+Write 给真实 SID + deny_write 仅给合成 SID → deny_write 被绕过，child 用真实 SID 可写整个数据根；deny_write 的 .git/.env 对真实 SID 无 Deny ACE → 跨沙箱 workspace 互写 | 8c7f677a §🔴 P0 | ❌ 仍存在 | win_acl.py:334-345 deny_write 循环只对 `sid`（合成 SID）施加 `mode="DENY"`，**无 `if sandbox_user_sid:` 分支给真实 SID 施加 Deny**；win_acl.py:430-464 整树 grant Write 给真实 SID 未收窄 | 报告 P0 两项修复方向（deny_write 对真实 SID 施加 Deny + 整树 grant 收窄到 workspace 子树）**均未采纳**。deny_write 仍只挡合成 SID，整树 grant 仍给真实 SID Write。跨沙箱 workspace 互写风险依旧。 |
| 8 | `revoke_sandbox_acl` 不清理 `~/.office-claw` 整树 ACE，uninstall 残留孤儿 SID ACE（注释明确整树 grant "不进 revoke 清单"） | 8c7f677a §🔴 P0-2 / §关键代码 process.py:3181 | ❌ 仍存在 | win_acl.py:430-434 + 460-464 注释"不进 applied 清单（避免 revoke 跨沙箱误删）"；win_acl.py:482-556 revoke 只按 `applied` 清单逐路径撤销合成 SID ACE；win_setup.py:1244-1288 uninstall 未调 `revoke_sandbox_acl(~/.office-claw)` | 整树 grant 的 ACE 仍明确"不进 applied 清单"，`revoke_sandbox_acl` 只清 `applied` 路径上的合成 SID ACE。`uninstall()`（win_setup.py:1244-1288）删 WFP/profile/用户/组/注册表标记，但**不调 revoke 清理 `~/.office-claw` 整树 ACE**。uninstall 删 jbx-sandbox 用户后，`~/.office-claw` 上残留合成 SID + jbx-sandbox SID 的 ACE 变孤儿。 |
| 9 | 新增真实 SID ACE 的撤销路径未验证，applied 按"路径"而非"路径+SID+rights"记账，revoke 可能漏撤真实 SID 的 Allow ACE | ab4932ac §六.R3 / §四.4.1 | ❌ 仍存在 | win_acl.py:302 `applied: list[str] = []`（仅路径字符串）；win_acl.py:333/345/359/387 `applied.append(expanded)`（只记路径）；win_acl.py:482-556 `revoke_sandbox_acl` 只按 `target_sid`（合成 SID，win_acl.py:494）过滤 ACE，**不撤销真实 SID 的 ACE** | `applied` 仍是 `list[str]`（路径维度），revoke 内部 `target_sid = _resolve_sid(get_synthetic_write_sid())`（win_acl.py:494-495）只匹配合成 SID，真实 SID 的 Allow ACE 不在撤销范围。apply 时对真实 SID grant 的 ACE（win_acl.py:327-332, 380-386, 417-423, 450-460）在 revoke 时**全部残留**。 |
| 10 | `get_sandbox_user_sid` 读注册表失败返回 None 后静默跳过真实 SID 授权，install 未完成时 runner 起不来但无显式告警 | ab4932ac §六.R4 / §改进建议 7.3 | ❌ 仍存在 | process.py:3009 `sandbox_user_sid = win_setup.get_sandbox_user_sid()`（无 None 检查）；win_acl.py:326/380/417/450 `if sandbox_user_sid:` 静默跳过；process.py:3019-3027 logger.info 不含 SID 缺失告警 | `_create_windows` 拿到 `sandbox_user_sid=None` 时无 `logger.warning`，apply_sandbox_acl 用 `if sandbox_user_sid:` 静默跳过真实 SID ACE。报告 7.3 的告警建议未采纳。runner 会因缺读权限起不来，但 box-server 只留运行时错，无显式"install 未完成"告警。 |
| 11 | bb1afca0 win_acl.py 大改（ACL 读控制 deny-then-allow、去掉 PROTECTED_DACL、revoke 按施加清单撤销）在当前是否解决 | bb1afca0 §三.9 / §三.10 / §三.11 | ⚠️ 部分解决 | win_acl.py:347-387（deny_read 先施加 Deny → allow_read 后施加 Allow，顺序正确）；win_acl.py:210/546 不设 PROTECTED_DACL（注释明确）；win_acl.py:302+482 revoke 按施加清单撤销（✅）；但 win_acl.py:283 注释"allow 覆盖 deny"与 NTFS Deny 优先语义不符（bb1afca0 §六.6 未修）；revoke 只清合成 SID 不清真实 SID（见 #9） | bb1afca0 的三项核心修复（读控制补全、保留继承、按清单撤销）已合入当前工作区。但遗留：① deny_read+allow_read 同路径语义注释错误（win_acl.py:283 "allow 覆盖 deny" 与 NTFS 实际 Deny 优先矛盾，bb1afca0 §六.6 未修）；② revoke 只撤销合成 SID，真实 SID ACE 残留（见 #9）。 |

## 汇总

本组共核对 **11** 条。

- ✅ 已解决 **4** 条：#1、#2（以其他方式绕过）、#3、#5
- ❌ 仍存在 **6** 条：#4、#6、#7、#8、#9、#10
- ⚠️ 部分解决 **1** 条：#11（bb1afca0 核心三修复已合入，但 deny_read/allow_read 语义注释错误 + revoke 不清真实 SID 仍存）

仍存在/部分解决清单：

1. **#4/#6/#7（同根因，安全核心）**：`~/.office-claw` 整树递归 grant Read+Write 给合成 SID + 真实 SID 未收窄；deny_write 仅对合成 SID 施加 Deny，真实 SID 无 Deny ACE → 跨沙箱 workspace 互写、deny_write 被绕过。证据 win_acl.py:334-345（deny_write 无真实 SID 分支）、win_acl.py:430-464（整树 grant Write 给真实 SID）。
2. **#8**：uninstall 不清理 `~/.office-claw` 整树 ACE，整树 grant 明确"不进 applied 清单"，卸载后残留孤儿 SID ACE。证据 win_acl.py:430-434 注释、win_setup.py:1244-1288 uninstall 无 revoke 调用。
3. **#9**：applied 按"路径"记账（`list[str]`），revoke 只匹配合成 SID（`get_synthetic_write_sid()`），真实 SID 的 Allow ACE 在 revoke 时全部残留。证据 win_acl.py:302/494-495。
4. **#10**：`get_sandbox_user_sid` 返回 None 时无显式告警，apply_sandbox_acl 静默跳过真实 SID ACE。证据 process.py:3009、win_acl.py:326 `if sandbox_user_sid:`。
6. **#11（部分）**：bb1afca0 核心修复（读控制、保留继承、按清单撤销）已合入，但 deny_read+allow_read 同路径语义注释错误（win_acl.py:283 "allow 覆盖 deny" 与 NTFS Deny 优先矛盾）+ revoke 不清真实 SID（见 #9）。

## 核对方法说明

- 所有证据均对照当前工作区 HEAD `82001d09` 实读源文件，行号为实读结果。
- "以其他方式绕过"指：报告指出的 bug 本身代码仍存在（如超时仍写 installed 标记），但通过其他设计变更（整树 grant 覆盖预装路径）使其危害不再成立。
- 未读 win_exec.py 的受限 token 部分（8c7f677a P0-1），因本组聚焦"ACL 与文件隔离"；但 #7 的判定依赖"受限 token 未用于 exec child"这一前提（8c7f677a §🔴 P0 已述，win_constants.py:75 `RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT` 临时去掉 WRITE_RESTRICTED 仍在工作区），故真实 SID 的 Deny 缺失影响成立。
