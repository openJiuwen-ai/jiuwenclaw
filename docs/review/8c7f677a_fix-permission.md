# Commit 8c7f677a 代码审查报告

**Commit**: `8c7f677a2ee14423a90823a4b891c92cd8b785f1`
**信息**: fix:修权限... (作者 lby, 2026-07-28)
**审查范围**: Windows 沙箱权限体系与执行链路
**审查重点**: 受限 token 创建与禁用 SID、Mandatory Label、文件系统 ACL、runner 与 process 的权限传递链、daemon_ipc 兼容、policy 模型向后兼容、是否存在权限提升或降权绕过

## 概述

本 commit 约 880 增 43 删, 10 文件。主要改动集中在 `win_exec.py`(+301)、`process.py`(+262)、`win_setup.py`(+174) 三个文件, 围绕 Windows 沙箱的"权限收紧后的可用性恢复"展开: 让受限 token 下的子进程能跑起来 (env block / PATH / profile 变量补全)、让 runner 早期异常能回传定位 (日志订阅长连 + 本地落盘)、让 install 阶段与运行时阶段的 ACL 预装边界清晰 (tool_paths 预装 + 增量检测)、让密码一致性自愈 (LogonUserW 探测 + 重设)。

修复质量在工程层面较高: 多处死锁/静默退出/WinError 定位精确, 注释详尽还原根因。但从**安全模型**角度, 本 commit 暴露了一个核心退化 — **受限 token 实际未用于 exec child, 写控制双重检查被绕过**, 以及若干权限过宽与边界遗漏问题 (见"问题与风险")。这些问题的根因不全是本 commit 引入, 但本 commit 在"修权限"语义下未纠正它们, 部分还加重了。

## 变更范围

| 文件 | 性质 | 关键点 |
|------|------|--------|
| `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py` (+301) | 执行链路 | 日志订阅长连 `_push_log`/`_local_log`、env block 始终构造 + 代理 env 注入、exec wait 改超时循环 + drain 线程防死锁、单连接异常隔离不杀 runner |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` (+262) | 权限传递 | Windows workspace 根改到 `~/.office-claw/.jiuwenclaw/jiuwenbox`、tool_paths 拼进 PATH (不改 ACL)、read_write/bind_mounts 加进 allow_write、日志读取后台线程、SIGCHLD/WNOHANG 平台守卫、exec 读超时放宽到 130s |
| `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py` (+174) | 安装/ACL | `_load_policy_preinstall_paths` 从 yaml 读 tool_paths 合并预装、`REG_VALUE_PREINSTALLED_PATHS` 增量检测、`_verify_or_reset_sandbox_user_password` LogonUserW 探测+重设、install 日志落盘 |
| `jiuwenclaw/agentserver/jiuwenbox_runner.py` (+103) | 端口清理 | `_cleanup_stale_win_proxy_ports` PowerShell kill 占用 60080-60089 的残留 python 进程、pump 日志级别 DEBUG→INFO |
| `jiuwenbox/src/jiuwenbox/models/policy.py` (+33) | 模型 | 新增 `WindowsToolPaths` (git_dir/node_dir/python_dir/bash_path), `WindowsFilesystemPolicy.tool_paths` 字段 |
| `jiuwenbox/src/jiuwenbox/server/workspace.py` (+19) | 路径根 | Windows `JIUWENBOX_HOME` 改到 `JIUWENCLAW_DATA_DIR/jiuwenbox`, 新增 `WIN_SANDBOX_WORKSPACE_ROOT` / `OFFICE_CLAW_DATA_ROOT` |
| `jiuwenbox/src/jiuwenbox/supervisor/daemon_ipc.py` (+13) | IPC | 新增 `REQUEST_TYPE_SUBSCRIBE_LOG` + `LOG_FRAME_TYPE` + log 字段常量 |
| `jiuwenbox/src/jiuwenbox/supervisor/win_constants.py` (+7) | 常量 | `CREATE_UNICODE_ENVIRONMENT`、`REG_VALUE_PREINSTALLED_PATHS` |
| `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` (+10) | 配置 | `tool_paths` 默认值 (D:\Files\Git 等) |
| `jiuwenclaw/resources/config.yaml` (+1) | 配置 | 末尾空行 |

## 权限模型分析

本沙箱的权限隔离设计为三层: (1) 独立用户 `jbx-sandbox` (独立 SID/独立 profile); (2) Write-Restricted Token (合成 SID `JHXSandboxWrite` 的双重 ACL 写检查); (3) NTFS DACL (deny-then-allow 读 + allow-only 写) + WFP 网络隔离。本 commit 的改动触及每一层, 以下逐项标注。

### 🔴 受限 token 实际未用于 exec child — 写控制双重检查被绕过 (安全退化, 核心问题)

`win_exec.py:75` `RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT  # 临时去掉 WRITE_RESTRICTED(0x8) 定位 0xC0000142`:
- 注释明确: 为了定位 child 启动即 `0xC0000142 (STATUS_DLL_INIT_FAILED)`, **临时去掉了 `WRITE_RESTRICTED` 标志**。
- `WRITE_RESTRICTED(0x8)` 是合成 SID 双重写检查的关键 — 去掉后, 受限 token 的写操作不再做"token 必须携带合成 SID"的第二重检查, 只剩 NTFS DACL 的 allow-only 写控制。

更严重的是 `win_exec.py:1301-1310` `_handle_exec_request`:
```python
# 方案1: 不用受限 token (它让 child 启动即 0xC0000142), 改用 runner 自身
# primary token (未受限). 见 _get_runner_primary_token 注释.
_self_token = _get_runner_primary_token()
...
pid, proc_handle = _create_process_as_user(
    _self_token, list(command), ...
```
- **exec child 用的是 runner 自身的未受限 primary token, 不是 `_create_restricted_token()` 创建的受限 token**。`restricted_token` 变量在 `runner_main` 创建后, 仅在 `runner_main:1074` 处被持有, **从未传给 `_handle_exec_request`**, 也没用于任何 `CreateProcessAsUserW`。
- 这意味着: 用户代码跑在 **jbx-sandbox 用户身份 + runner 未受限 token** 下。合成 SID 的 ACE 对它**完全不生效** (token 不携带合成 SID)。写控制只剩"runner 是 jbx-sandbox 用户, DACL 上是否 grant 了 jbx-sandbox 真实 SID"这一重。

`win_acl.py:317-332` 的 grant 逻辑证实了这一点: `apply_sandbox_acl` 对 allow_write 路径, 除了给合成 SID grant `ALLOW_WRITE_RIGHTS`, **还给 `sandbox_user_sid` (jbx-sandbox 真实 SID) grant 同样的 `ALLOW_WRITE_RIGHTS`**。注释说"runner 是 box-server 自己起的可信代理进程, 它的写操作就是 upload/文件操作, 给它写权限符合预期。真正执行用户代码的是 runner 用受限 token 起的 child (第二跳)"。

**但这个"第二跳受限 token"在本 commit 里不存在**。用户代码 child 与 runner 用的是同一个未受限 token。因此:
- 任何被 `sandbox_user_sid` grant 写权限的路径, 用户代码都能直接写。
- 合成 SID 的 deny_write ACE 对用户代码 child **无效** (token 不含合成 SID, NTFS 检查时合成 SID 的 Deny ACE 根本不评估)。
- 文档 §6.5/§6.7 描述的"双重写检查"在本实现中**名存实亡**。

**风险**: 若 allow_write 配置过宽 (见下条 `~/.office-claw` 整树递归 grant 写权限), 用户代码可写整个数据根; deny_write 的 `.git`/`.env` 封锁也只对合成 SID 生效, 对 jbx-sandbox 真实 SID 无 Deny ACE → **deny_write 被绕过**。

这是本 commit 最严重的安全发现。注释多处提及"方案1""临时""安全降一重", 说明开发者已知, 但未在 commit 信息或代码中显式标记为已知降级, 也未在 `runner_main` 处关闭"创建受限 token 但不用"的死代码。

### 🔴 `~/.office-claw` 整树递归 grant Read+Write 给 jbx-sandbox 真实 SID — 权限过宽

`win_acl.py:430-464`:
```python
_office_claw_root = str(_pl.Path.home() / ".office-claw")
if os.path.isdir(_office_claw_root):
    grant_ace(_office_claw_root, sid, rights=ALLOW_WRITE_RIGHTS, mode="ALLOW", recursive=True)
    grant_ace(_office_claw_root, sid, rights=FILE_GENERIC_READ, mode="ALLOW", recursive=True)
    if sandbox_user_sid:
        grant_ace(_office_claw_root, sandbox_user_sid, rights=ALLOW_WRITE_RIGHTS, ...)
        grant_ace(_office_claw_root, sandbox_user_sid, rights=FILE_GENERIC_READ, ...)
```
- 每次创建沙箱都对 `~/.office-claw` 整树**递归** grant Read+Write (合成 SID + 真实 SID)。
- 注释承认"递归授权会让各沙箱能互相读 workspace 内容 — 但本产品是单用户本地部署, 跨沙箱隔离非安全目标"。
- 问题在于: (1) 结合上条"受限 token 未用", 真实 SID 的 Write grant 让用户代码可写整个数据根下任意沙箱的 workspace, 包括其他沙箱的 `.git`/`.env` (deny_write 对真实 SID 无 ACE); (2) `win_setup.py:984-996` install 阶段也对同一根做了同样的递归 grant, 两次叠加 (虽然 grant 幂等, 但语义上 install 时就该一次性, 运行时再 grant 是冗余且扩大暴露面)。

### 🟡 `deny_write` 仅施加给合成 SID, 未给真实 SID — deny 被绕过

`win_acl.py:334-345`: `deny_write` 路径只对 `sid` (合成 SID) 施加 `Deny Write`, **没有对 `sandbox_user_sid` (真实 SID) 施加 Deny**。
- 在"受限 token 未用"的前提下, 用户代码 child 用真实 SID, 合成 SID 的 Deny ACE 不评估 → `deny_write` 的 `.git`/`.env` 封锁对用户代码无效。
- 即使受限 token 恢复使用, 真实 SID 的 runner 仍能写 `.git`/`.env` (runner 负责 upload), 这在设计内; 但若 runner 被 RCE, `.git`/`.env` 不受保护。
- 建议: 对 `deny_write` 路径同时给真实 SID 施加 Deny (至少对 `.env` 这类敏感文件), 或在恢复受限 token 后重新评估。

### 🟡 exec child 的 env 注入 `HTTP_PROXY=127.0.0.1:<port>` — 代理绕过风险

`win_exec.py:921-928`:
```python
proxy_url = f"http://127.0.0.1:{_proxy_port_start}"
env.setdefault("HTTP_PROXY", proxy_url)
env.setdefault("HTTPS_PROXY", proxy_url)
env.setdefault("ALL_PROXY", proxy_url)
```
- 用 `setdefault`: 若调用方已传 `HTTP_PROXY` 则不覆盖, 合理。
- 但 `_proxy_port_start` 是端口范围**起点** (默认 60080), 不是 win_proxy 实际监听的端口。win_proxy 监听 60080-60089 整段, 把代理指向 60080 可能撞到未监听的端口 (若 win_proxy 从 60081 起监)。需确认 win_proxy 的实际监听端口与 `_proxy_port_start` 一致。
- `ALL_PROXY=http://...` 对不识别 HTTP 代理的工具 (如某些 SOCKS 客户端) 可能无效; 但 WFP 兜底 Block 所有出站, 非 loopback 的出站被拦, 风险可控。
- 真正风险: 若用户代码显式 `unset HTTP_PROXY` 后直接 connect 外网, WFP Block 兜底拦, 但若 WFP 未装成功 (降级到防火墙规则), 防火墙规则是否也 Block 所有出站? 需确认 `install_firewall_rule_fallback` 的语义。

### 🟡 `jiuwenbox_runner.py:107` 进程名白名单过宽 — 误杀风险

`_cleanup_stale_win_proxy_ports` 用 `proc_name.lower() != "python"` 判断是否 kill。但:
- uv venv 的 python 进程名可能是 `python3.13.exe` / `pythonw.exe`, 不匹配 `"python"` → 被当非 python 跳过, 残留进程清不掉 → win_proxy bind 仍 WinError 10048。
- 反向: 若第三方 python 进程 (非 jiuwenbox) 恰好监听 60080-60089, 会被误杀。
- 建议: 改为 `proc_name.lower().startswith("python")` + 校验命令行含 `jiuwenbox` / `win_proxy` 关键字。

### 🟢 `two_hop_spawn` env block + CREATE_UNICODE_ENVIRONMENT (正确)

`win_exec.py:500-513`: 传 env block 给 `CreateProcessWithLogonW` 时正确设置 `CREATE_UNICODE_ENVIRONMENT`, 且 `env_block_buf` 持有引用直到 API 返回 (防悬垂指针)。`_build_env_block` 用 `\0` 分隔 + 双 `\0` 终止, 符合 Win32 规范。🟢

### 🟢 `_create_process_as_user` env=None 回退 + 代理注入 + profile 变量补全 (正确但偏宽)

`win_exec.py:855-968`: env=None 回退 `os.environ` 而非传 NULL, 避免 child 空 env 无 PATH。自动注入 `SystemRoot/windir/PATHEXT/COMSPEC` 防 `STATUS_DLL_INIT_FAILED`。profile 变量 (USERPROFILE/LOCALAPPDATA/APPDATA/TEMP) 用 `get_sandbox_profile_dir()` API 拿真实路径 (处理 .000 后缀), 每沙箱隔离 TEMP 子目录。逻辑正确。🟢

唯一偏宽: `TEMP` 指向 `<profile>\AppData\Local\Temp\jiuwenbox\<sandbox_id>`, 该路径在 jbx-sandbox profile 下, 用户代码可写 — 但若用户代码填充此目录撑爆磁盘, 无 quota 限制 (Job Object 资源限制已禁用, `process.py:3068-3110` 注释)。

### 🟢 日志订阅长连 + 单连接异常隔离 (正确)

`win_exec.py:1122-1138` subscribe_log 握手 + `1143-1172` 单连接 try/except 不杀 runner: 修复了旧版"单连接 OSError 冒泡到 accept 循环 → runner 退出 → 后续全 409"的级联失败。`process.py:3504-3601` 后台线程带 50 次重试 connect, stop 时 set + join 2s。设计合理。🟢

### 🟢 exec wait 改超时循环 + drain 线程 (正确)

`win_exec.py:1331-1406`: 起 `_drain_thread` 持续读 stdout pipe 防 child 写满 64KB pipe 死锁, 主线程 `WaitForSingleObject` 500ms 轮询 + 120s 超时强杀。修复了旧版"先 wait 再 read → child 写满 pipe 阻塞 → runner 死等"的致命死锁。🟢

### 🟢 tool_paths 预装边界清晰化 (正确)

`win_setup.py:585-618` `_load_policy_preinstall_paths` + `collect_preinstall_paths` + `REG_VALUE_PREINSTALLED_PATHS` 增量检测: 把"owner=Administrators 的外部工具目录"的 ACL 预装严格限制在 install 阶段 (管理员), 运行时只拼 PATH 不改 ACL。`process.py:2860` 用 `collect_preinstall_paths` 算同一集合, 保证两处一致。🟢

### 🟢 `_verify_or_reset_sandbox_user_password` 用 NETWORK logon (正确)

`win_setup.py:1169-1187`: 优先 `LOGON32_LOGON_NETWORK(3)` 而非 INTERACTIVE(2), 避免每次校验都物理创建 profile 目录。失败回退 INTERACTIVE 保校验功能。设计合理。🟢

### 🟢 policy.py `WindowsToolPaths` 向后兼容 (正确)

`policy.py:768-793`: `tool_paths` 字段默认 `WindowsToolPaths()` (全空字符串), `extra="forbid"`。旧 yaml 无此字段时用默认值, 不会校验失败。`expand_paths` validator 用 `_expand_path`。向后兼容。🟢

### 🟢 daemon_ipc.py 新增常量向后兼容 (正确)

`daemon_ipc.py:64-76`: 新增 `REQUEST_TYPE_SUBSCRIBE_LOG` / `LOG_FRAME_TYPE` 等常量, 不影响已有 `REQUEST_TYPE_EXEC` 等旧常量。Linux 路径不发送 subscribe_log, runner 侧也只在 win32 注册 handler。兼容。🟢

## 关键代码检视

### `win_exec.py:760-785` `_get_runner_primary_token` — 死代码与安全隐患

```python
def _get_runner_primary_token() -> int:
    """...方案1: 受限 token (CreateRestrictedToken) 会让任何 child 进程启动即
    0xC0000142 (DllMain 失败...). 故 exec 改用 runner 自身的未受限 token
    起 child...代价: 失去 Write-Restricted 双重写检查...安全降一重..."""
```
- 此函数返回 runner 自身 primary token (未受限), 用于 exec child。
- `_create_restricted_token` 创建的 `restricted_token` 在 `runner_main:1074` 创建后**从未使用**, 仅在 finally CloseHandle。是死代码。
- 注释承认"安全降一重", 但 commit 信息"fix:修权限"未体现这一降级, 容易让 reviewer 误以为权限收紧了。
- **建议**: 要么恢复受限 token (需解决 0xC0000142, 可能是 child env 缺 SystemRoot 或 token 完整性级别问题), 要么显式删除 `restricted_token` 死代码 + 在 commit 信息/文档中标注"已知安全降级: 写控制单重"。

### `win_exec.py:144` `_push_log` — 长连回传的可靠性

`_push_log` 往所有订阅连接 best-effort push, 失败移除。`_local_log` 落盘 `C:\Users\jbx-sandbox\jiuwenbox-logs\<id>\runner.log`。双保险设计合理。但:
- `_local_log_file` 用 `open(path, "a", encoding="utf-8", buffering=1)` (行缓冲), 多线程写有 `_local_log_lock` 保护。正确。
- 但 `_init_local_log` 在 `runner_main:1063` 调用, 若 `get_sandbox_profile_dir` 失败, fallback `C:\Users\jbx-sandbox` 可能不存在 (首次登录 profile 未建) → `makedirs` 失败 → `_local_log_file=None` → 只回传不落盘。可接受降级。

### `process.py:3181-3186` `revoke_sandbox_acl` — 撤销不彻底

`_stop_windows` 调 `revoke_sandbox_acl(acl_paths)`, 但 `acl_paths` 只含 `apply_sandbox_acl` 返回的 `applied` 清单 (workspace + allow/deny 各项), **不含 `~/.office-claw` 整树递归 grant 的 ACE** (注释明确"不进 revoke 清单")。这意味着:
- 沙箱 stop 后, `~/.office-claw` 上 jbx-sandbox 真实 SID 的 Read+Write ACE **残留**, 下次建沙箱再 grant (幂等)。
- 单用户本地部署下, 残留无害 (jbx-sandbox 用户始终存在); 但若 uninstall 删了 jbx-sandbox 用户, 这些 ACE 变成"孤儿 SID" ACE, 需 `revoke_sandbox_acl` 扫数据根清理。`uninstall()` 未调 `revoke_sandbox_acl(~/.office-claw)`, 残留 ACE 不会被清。

### `win_setup.py:320-327` 密码固定 `"000000"` — 已知弱密码

```python
def _generate_password() -> str:
    return "000000"
```
- 注释承认"调试阶段固定"。但 jbx-sandbox 是本地用户, 密码 `000000` + `UF_PASSWD_CANT_CHANGE` 意味着任何能本地登录的进程都能以 jbx-sandbox 身份登录 (密码已知)。
- `CreateProcessWithLogonW` 用此密码; `_verify_or_reset_sandbox_user_password` 也用此密码。
- 风险: 若 jbx-sandbox 用户被 grant 了敏感路径 ACL (如 `~/.office-claw` 整树 Read+Write), 任何本机进程都能以 jbx-sandbox 身份 `RunAs` 访问这些路径, 绕过 box-server 的沙箱管控。
- 此问题非本 commit 引入, 但本 commit 大量 grant jbx-sandbox 真实 SID 权限, 放大了弱密码的影响面。

## 优点

1. **死锁修复扎实**: exec wait 改超时循环 + drain 线程, 彻底解决 pipe 写满死锁; 单连接异常隔离不杀 runner, 解决级联 409。
2. **可观测性大幅提升**: 日志订阅长连 + 本地落盘 + install 日志落盘, 覆盖了 CREATE_NO_WINDOW 下 stderr 无落盘的盲区。
3. **ACL 预装边界清晰**: tool_paths 预装严格限 install 阶段, 运行时只拼 PATH; `REG_VALUE_PREINSTALLED_PATHS` 增量检测 + 自动弹 UAC 补预装, 用户体验好。
4. **env 补全周到**: SystemRoot/PATHEXT/profile 变量/TEMP 隔离, 解决 0xC0000142 的 env 侧根因。
5. **密码一致性自愈**: LogonUserW 探测 + NetUserSetInfo 重设, 避免反复 1326。
6. **平台守卫**: SIGCHLD/WNOHANG `hasattr` 守卫, 避免 Windows AttributeError。

## 问题与风险

### 🔴 P0: 受限 token 未用于 exec child, 写控制双重检查被绕过
- 位置: `win_exec.py:75` (RESTRICTED_TOKEN_FLAGS 去掉 WRITE_RESTRICTED)、`win_exec.py:1301-1310` (用 `_get_runner_primary_token` 而非 `restricted_token`)、`win_exec.py:1074` (restricted_token 创建后未用)。
- 影响: 用户代码 child 跑在 jbx-sandbox 未受限 token 下, 合成 SID ACE 不评估, `deny_write` 对真实 SID 无 ACE → deny_write 被绕过, 写控制只剩"真实 SID 是否被 grant 写"的单重检查。
- 修复方向: 恢复 `WRITE_RESTRICTED` flag + 让 `_handle_exec_request` 用 `restricted_token` 起 child; 0xC0000142 的根因 (可能是 child env 缺 SystemRoot 或 token 完整性级别) 需单独定位。若暂不恢复, 需在 commit 信息/文档显式标注降级, 并对 `deny_write` 路径同时给真实 SID 施加 Deny。

### 🔴 P0: `~/.office-claw` 整树递归 grant Read+Write 给真实 SID + deny_write 对真实 SID 无 Deny
- 位置: `win_acl.py:430-464` (整树 grant)、`win_acl.py:334-345` (deny_write 只给合成 SID)。
- 影响: 结合 P0-1, 用户代码可写整个数据根下任意沙箱 workspace, 包括其他沙箱的 `.git`/`.env`。
- 修复方向: (1) 对 `deny_write` 路径同时给真实 SID 施加 Deny Write ACE; (2) 收缩 `~/.office-claw` 递归 grant 范围, 改为只 grant 当前沙箱 workspace 子树 (而非整树); (3) install 阶段已 grant 的, 运行时不再重复 grant。

### 🟡 P1: 密码固定 `"000000"` + jbx-sandbox 可被任意本机进程 RunAs
- 位置: `win_setup.py:327`。
- 影响: 弱密码 + 大量真实 SID grant → 本机任意进程可 `RunAs /user:jbx-sandbox` 绕过 box-server 访问 grant 过的路径。
- 修复方向: 改为 `secrets.token_hex(32)` 随机密码, install 时生成 + DPAPI 加密存注册表, 不在代码硬编码。

### 🟡 P1: `revoke_sandbox_acl` 不清理 `~/.office-claw` 整树 ACE, uninstall 残留孤儿 SID ACE
- 位置: `win_acl.py:404-405` (注释明确不进 revoke 清单)、`win_setup.py:1244-1288` `uninstall` 未调 revoke 数据根。
- 影响: uninstall 删 jbx-sandbox 用户后, `~/.office-claw` 上残留 S-1-5-21-... (合成 SID) + jbx-sandbox SID 的 ACE, 成为孤儿 ACE, 污染 DACL。
- 修复方向: `uninstall` 末尾对 `~/.office-claw` 调 `revoke_sandbox_acl` 递归清理 (合成 SID 固定, 可安全扫)。

### 🟡 P2: 进程名白名单 `"python"` 过窄, 漏杀 uv venv python
- 位置: `jiuwenbox_runner.py:107`。
- 影响: 残留的 uv venv python 进程 (名 `python3.13.exe`) 清不掉 → win_proxy bind WinError 10048。
- 修复方向: `proc_name.lower().startswith("python")` + 命令行含 `jiuwenbox`/`win_proxy` 校验。

### 🟡 P2: `_proxy_port_start` 可能不等于 win_proxy 实际监听端口
- 位置: `win_exec.py:923`。
- 影响: 代理 env 指向未监听端口 → 子进程 HTTP 请求失败 (WFP 兜底仍拦, 但功能不可用)。
- 修复方向: 确认 win_proxy 从 `_proxy_port_start` 起监听, 或 env 指向 win_proxy 实际监听的端口。

### 🟢 P3: `_handle_write_file_request` / `_handle_read_file_request` 无路径校验
- 位置: `win_exec.py:1491-1534`。
- 现状: `write_file` 的 `path` 直接 `open(path, "wb")`, 无 workspace 边界校验。`read_file` 同理。
- 风险: 若 box-server 被 RCE 发恶意 path (如 `\\?\C:\Users\liubuyu\.ssh\id_rsa`), runner (jbx-sandbox + 被 grant 的真实 SID) 能写/读任意 grant 过的路径。
- 缓解: runner 的 ACL 已 grant 了 `~/.office-claw` 整树, 但宿主其他路径 (如 `C:\Users\liubuyu\.ssh`) jbx-sandbox 默认无权限 (profile 隔离), 故实际风险有限。但设计上应校验 path 在 workspace 子树内。
- 注: 这是 pre-existing 设计, 非 commit 引入。

## 改进建议

1. **恢复受限 token 用于 exec child** (P0): 重新引入 `WRITE_RESTRICTED` + 用 `restricted_token` 起 child。0xC0000142 的根因可能是: (a) child env 缺 `SystemRoot` (已在本 commit 补); (b) 受限 token 的默认 DACL 缺某些 SID 导致 DLL 加载失败 (需 `SetTokenInformation(TokenDefaultDacl)` 补全); (c) token 完整性级别 (Mandatory Integrity Level) 过低 — 需检查是否误设 Low Integrity。建议单独 commit 定位。

2. **deny_write 同时给真实 SID 施加 Deny** (P0): 在 `win_acl.py:334` 循环里, 对每个 deny_write 路径同时 `grant_ace(path, sandbox_user_sid, rights=FILE_GENERIC_WRITE, mode="DENY")`。这样即使受限 token 未用, 用户代码 child (真实 SID) 也被 deny。

3. **收缩 `~/.office-claw` 递归 grant 范围** (P0): 改为只 grant 当前沙箱 workspace 子树 (`workspace` 路径 + 其 allow_write 子项), 而非整树。install 阶段的整树 grant 也应评估是否可收缩为只 grant 数据根的 traverse (非递归) + 各沙箱 workspace 子树 (递归)。

4. **密码改随机** (P1): `_generate_password` 用 `secrets.token_hex(32)`, install 时生成 + DPAPI 存注册表, `get_sandbox_user_password` 解密。

5. **uninstall 清理数据根 ACE** (P1): `uninstall` 末尾对 `~/.office-claw` 调 `revoke_sandbox_acl` 递归清理合成 SID + jbx-sandbox SID 的 ACE。

6. **进程名白名单放宽** (P2): `proc_name.lower().startswith("python")` + 命令行校验。

7. **write_file/read_file 路径校验** (P3): 校验 `path` 在 `workspace` 子树内 (resolve 后 startswith workspace), 否则拒绝。

8. **删除 `restricted_token` 死代码或恢复使用** (P0): 当前 `runner_main:1074` 创建 `restricted_token` 后只在 finally CloseHandle, 是死代码。要么恢复用于 exec child, 要么删除以免误导。

## 小结

本 commit 在"修权限"语义下, 实际做了两件事: (1) 修复了权限收紧后子进程起不来的可用性问题 (env/PATH/profile/drain 死锁/日志回传), 质量高; (2) 在此过程中暴露并加重了一个安全退化 — 受限 token 未用于 exec child, 写控制双重检查名存实亡, 且 `~/.office-claw` 整树递归 grant 真实 SID 写权限 + deny_write 仅给合成 SID, 导致 deny_write 被绕过、跨沙箱 workspace 可互写。

开发者注释多处提及"方案1""临时""安全降一重", 说明已知降级, 但未在 commit 信息或代码中显式标记, 且未对 deny_write/真实 SID grant 做相应加固, 是审查中的核心风险项。建议在合并前至少完成 P0 (deny_write 给真实 SID 施加 Deny + 收缩整树 grant 范围 + 标注降级), 并在后续 commit 恢复受限 token。

---
审查人: Claude Code 资深 Windows 安全工程审查员
审查日期: 2026-08-01
