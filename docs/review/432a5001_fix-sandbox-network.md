# Commit 432a5001 代码审查报告

- Commit: `432a50013669f5c1d24e71a78d627ba40ba7da78`
- 标题: `fix:修沙箱内联网，以及其他权限问题`
- 作者: lby, 2026-07-30
- 规模: 7 文件, +417 / -25
- 审查重点: 沙箱内网络访问、权限授予边界、白名单有效性

---

## 一、概述

本次 commit 名义上是"修沙箱内联网"，但实际改动只有 **windows-policy.yaml** 的
egress 白名单条目变更（加 `*.npmmirror.com` 等）。绝大部分代码量（win_exec +198、
win_acl +82）其实是修 **沙箱内子进程跑不起来 / 跑起来后 stdout 卡死 runner / EPERM**
等运行链路问题，与"联网"是两条不同的修复线被合进一个 commit。

整体方向合理：定位到 bash 双引号吞反斜杠、子进程超时被 120s 硬编码误杀、强杀后
孙进程持写端导致 runner 卡死、数据根 traverse ACL 残缺、递归 ACE 继承标志写错等
真实 bug，修复均有日志佐证。但 **win_acl.py 对 `~/.office-claw` 递归授予 Read+Write**
这一改动显著放宽了文件权限模型，是本次审查中最需要关注的安全退化点。

网络白名单侧：WFP Block + Permit loopback:port_range + win_proxy 域名/IP 过滤三层
模型未改动，白名单仍有效；新增 `*.npmmirror.com` 是为 Playwright/ npm 镜像源放行，
范围合理。无任意出站能力引入。DNS 在 win_proxy 内解析后比对 IP（非系统解析后直连），
不存在明显 DNS 泄露路径——但见风险 R3 关于 DNS 解析时机。

---

## 二、变更范围

| 文件 | 增删 | 性质 |
|---|---|---|
| `supervisor/win_exec.py` | +198 | 重点：bash 反斜杠规整、超时预算读 caller、强杀后非阻塞读 stdout、TEMP 重定向、失败落盘 |
| `supervisor/win_acl.py` | +82 | 重点：数据根 traverse ACL + `~/.office-claw` 递归 Read+Write |
| `server/runtime/process.py` | +49 | TEMP 注入、exec roundtrip 读超时按 caller timeout 传导 |
| `server/workspace.py` | +23 | 新增 `OFFICE_CLAW_DATA_ROOT` / `JIUWENCLAW_DATA_DIR_PATH` 常量 |
| `supervisor/win_setup.py` | +63 | install 时对 `~/.office-claw` 递归授 R+W、密码校验改 NETWORK logon |
| `supervisor/win_constants.py` | +9 | 修 `SUB_CONTAINERS_AND_OBJECTS_INHERIT` 由 0x7 改为 0x3 |
| `configs/windows-policy.yaml` | +18 | egress 放行 npmmirror 全域 + Chrome 预装 |

---

## 三、根因与修复分析

### 3.1 沙箱内联网

🔴 **根因不在 WFP/代理，而在 bash 路径解析**
`win_exec.py:1095-1106` 注释清楚记录：caller (openjiuwen) 把命令包成
`["bash","-lc", script]`，script 含 `node "D:\...\cli.js" check-env`。
`_create_process_as_user` 把 argv 重新拼成 Windows cmdline 时，script 因含空格被双引号
包裹；bash 双引号内会吞掉非特殊反斜杠 → `D:\Workspace\...\cli.js` 变成
`D:Workspace...cli.js` → `MODULE_NOT_FOUND` → 子进程根本没起来 → 表象就是"沙箱内
联网失败"（实际是命令都没跑起来）。

修复 (`win_exec.py:1034-1074` `_normalize_bash_script_backslashes`)：对
`["bash"|"sh", "-lc"|"-c", script, ...]` 模式，用 `_DQ_SEGMENT_RE` 只规整双引号段内
的反斜杠为正斜杠，单引号/双引号外不动。🟢 修复方式正确：范围限定严格，只动双引号
内、只对 bash/sh + -c/-lc 生效，不影响 `python -c` / `node -e`。

🟡 **白名单新增 `*.npmmirror.com`** (`windows-policy.yaml:144-149`)
为 Playwright Chromium 下载（`playwright_download_host=npmmirror.com/mirrors/playwright`）
和 npm 镜像源放行。`*.npmmirror.com` 匹配子域 + 主域（见 `win_proxy.EgressFilter._domain_matches`，
`*.example.com` 也匹配 `example.com`）。范围合理但偏宽：npmmirror 是公开 CDN，
任意沙箱进程都能经它拉取任意包/二进制。在 default=deny 模型下可接受，但建议收紧到
具体子域（`cdn.npmmirror.com`, `registry.npmmirror.com`, `npmmirror.com`）减少放行面。

### 3.2 子进程超时误杀 + runner 卡死

🔴 **根因：runner 端 WAIT_BUDGET 硬编码 120s**
`win_exec.py:1160-1177`：旧版 `WAIT_BUDGET_MS = 120000`，caller (box-server) 传
`timeout=600`（playwright install）也被 120s 强杀。修复改为读 `header.get("timeout")`，
缺省 120s，传导到 box-server 端读超时 `max(130, caller_timeout+10)` (`process.py:3364-3377`)。
🟢 修复正确，且 box-server 读超时 > runner wait budget 的关系注释得很清楚，避免
WinError 10053 连接中止。

🔴 **根因：强杀后孙进程持 stdout 写端 → pipe 不 EOF → runner accept 循环卡死**
`win_exec.py:1202-1246`：`TerminateProcess` 只杀直接 child (bash)，但 child 起的
孙进程 (node/npm/下载子进程) 继承了 stdout pipe 写端仍持有 → `fh.read` 永久阻塞 →
后续所有 exec IPC 全 timeout。修复：强杀分支改用 `win32pipe.PeekNamedPipe` 轮询 +
5s 总期限读，超时放弃残缺 stdout 跳出。🟢 修复正确，且显式 `os.close(read_fd)` 避免
osfhandle→fd 映射泄漏。正常退出分支仍用阻塞 `fh.read` 拿完整输出，未引入回退。

### 3.3 EPERM / 数据根 traverse ACL

🔴 **根因：递归 ACE 继承标志写错** (`win_constants.py:204-212`)
旧版 `SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x7`，实际含 `NO_PROPAGATE_INHERIT_ACE=0x4`，
导致 recursive grant 的 ACE 只继承到直接子项、不向下传播到孙目录 →
`workspace\.tmp\playwright-download-*` 子目录没继承合成 SID/jbx-sandbox ACE → child
在子目录里写文件 EPERM。修复改为 `CONTAINER_INHERIT_ACE | OBJECT_INHERIT_ACE` (=0x3)。
🟢 这是一个真实且严重的 bug 修复，影响所有 recursive grant 的传播。修复正确。

🔴 **根因：数据根祖先目录 ACL 残缺** (`win_acl.py:389-428`)
`npx/npm` 解析时逐级 `lstat` 数据子树祖先，`.office-claw` / `.jiuwenclaw` / `jiuwenbox`
三级 ACL 经沙箱 revoke 或创建时未给 jbx-sandbox/合成 SID → 受限 token `lstat` 即
WinError 5。修复：对这三目录施加 **非递归** Allow Read（traverse），且 **不**加入
`applied` 清单（避免 `revoke_sandbox_acl` rglob 递归扫整树误删其他沙箱 ACE，因合成
SID 固定共用）。🟢 修复正确，traverse read 残留无害且 grant 幂等。

### 3.4 TEMP 重定向

🟡 `win_exec.py:763-782` + `process.py:2961-2979`：受限 token 写不了宿主 `%TEMP%`，
把 `TEMP/TMP` 指向 `workspace/.tmp`（box-server 是 owner，合成 SID ACL 允许受限 token 写）。
修复合理。注意 `process.py` 用 `setdefault`（调用方未传才注入），`win_exec.py` 用直接
赋值（`env["TEMP"] = _child_tmp`）覆盖调用方。两处语义不一致但都有注释说明，runner
侧覆盖是因为 runner 离 box-server 经 JSON 序列化可能丢值。🟡 可接受但建议统一语义。

### 3.5 密码校验改 NETWORK logon

🟢 `win_setup.py:1157-1185`：`_verify_or_reset_sandbox_user_password` 由
`LOGON32_LOGON_INTERACTIVE`(2) 改为 `LOGON32_LOGON_NETWORK`(3)，避免每次校验密码都
物理创建 `C:\Users\jbx-sandbox` profile 目录。失败时回退 INTERACTIVE 保校验功能。
注释清楚说明 `two_hop_spawn` 仍用 `LOGON_WITH_PROFILE`（登录链路必需），此处仅是
副源消除。修复合理，profile 创建主源/副源区分清晰。

---

## 四、关键代码检视

### 4.1 win_acl.py:430-464 —— `~/.office-claw` 递归 R+W（🔴 安全退化点）

```python
import pathlib as _pl
_office_claw_root = str(_pl.Path.home() / ".office-claw")
if os.path.isdir(_office_claw_root):
    grant_ace(_office_claw_root, sid,
        rights=const.ALLOW_WRITE_RIGHTS, mode="ALLOW", recursive=True)
    grant_ace(_office_claw_root, sid,
        rights=const.FILE_GENERIC_READ, mode="ALLOW", recursive=True)
    if sandbox_user_sid:
        grant_ace(_office_claw_root, sandbox_user_sid, rights=const.ALLOW_WRITE_RIGHTS, ...)
        grant_ace(_office_claw_root, sandbox_user_sid, rights=const.FILE_GENERIC_READ, ...)
```

问题：
1. **写控制模型从 allow-only 退化为 allow-all-on-data-root**。原模型是"对
   `allow_write` 路径（仅 workspace）授 Write，对 `deny_write`（.git/.env）施加
   Deny"。现在对整个 `~/.office-claw`（含所有沙箱 workspace、isolation_venv、
   .ms-playwright、业务产物）递归授 Write+Read，**`deny_write` 的 `.git/.env`
   Deny ACE 仍在，NTFS Deny 优先于 Allow 故仍挡得住**——但这是靠 NTFS 顺序兜底，
   而非模型本身约束。一旦后续 `grant_ace` 的 Deny-then-Allow 重建逻辑（`_rebuild_acl_with_order`）
   出任何偏差，整树 Write 即刻暴露。
2. **跨沙箱隔离被显式放弃**。注释直言"单用户本地部署, 跨沙箱读 workspace 可接受"。
   这与 `deny_write: ["{{ workspace }}/.git", "{{ workspace }}/.env"]` 表达的"沙箱
   间数据敏感"语义矛盾——如果跨沙箱可读可写，那沙箱 A 能改沙箱 B 的 .env（Deny 只
   挡合成 SID，但真实 SID 也被授 Write，沙箱 A 的 runner 真实 SID 与沙箱 B 同
   jbx-sandbox）。
3. **install 阶段 (`win_setup.py:985-1010`) 与 apply_sandbox_acl 阶段重复授两次**。
   install 时已对 `~/.office-claw` 递归 R+W，apply_sandbox_acl 又授一遍。幂等但冗余，
   且 install 阶段授的 ACL 永不撤销（不在 `applied` 清单），uninstall 时若不整体删
   `~/.office-claw` 则 ACE 残留。

### 4.2 win_exec.py:1210-1246 —— 强杀后 PeekNamedPipe 轮询

🟢 逻辑正确，但 `import win32pipe` 在函数内、强杀分支内才 import。若 pywin32 缺失，
会在强杀路径才报错（正常运行路径不触发）。建议模块顶层做一次可用性探测或在
`_require_windows` 里检查，避免强杀路径首次触发时才发现缺包。

### 4.3 win_exec.py:1275-1291 —— 失败落盘 + EPERM 长 preview

🟢 失败时把完整 stdout 落盘 `workspace/.exec_failed.log` + EPERM 时 preview 取 4000
字符。诊断友好。🟡 但 `.exec_failed.log` 写在 workspace 下，沙箱 child 后续可读它
（child 对 workspace 有 Read）。若 stdout 含敏感路径/凭据，会暴露给沙箱进程。建议
落盘到 box-server 私有目录（不在 allow_read 内）。

### 4.4 win_exec.py:793-801 —— proxy 注入日志

🟢 注入 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY` 后打印一条 INFO 日志，含
`HTTPS_PROXY/NO_PROXY/LOCALAPPDATA/PLAYWRIGHT_BROWSERS_PATH/USERPROFILE/HOME/TEMP`。
诊断友好。🟡 `LOCALAPPDATA/USERPROFILE/HOME` 出现在 proxy 日志里语义不搭，且这些
值可能含用户名（隐私）。建议拆成两条日志或脱敏。

### 4.5 win_constants.py:204-212 —— 继承标志修复

🟢 修复正确且注释充分。但这个 bug 意味着 **修复前所有 recursive grant 都不向下传播**，
即修复前 `deny_write` 的 `.git` Deny ACE 也只到直接子项——孙目录的 .git 子目录
可能未被 Deny。修复后 Deny 才真正向下传播。这是个静默安全退化已被修掉的好事，
但建议在 release notes 标注：旧版部署的沙箱需重新 apply_sandbox_acl 才能拿到正确 ACE。

---

## 五、优点

1. 🟢 **诊断驱动的修复**：每处修复都附日志/实测证据（bash 吞反斜杠、120s 误杀、
   孙进程持写端、lstat EPERM、继承标志 0x7 错误），不是凭空推测。
2. 🟢 **超时预算链路打通**：caller timeout → runner WAIT_BUDGET → box-server
   read_timeout，三层关系注释清楚（`process.py:3364-3377`），避免 10053 噪音。
3. 🟢 **bash 反斜杠规整范围严格**：只动双引号内、只对 bash/sh + -c/-lc，不误伤
   python -c / node -e。
4. 🟢 **traverse ACL 不进 applied 清单**：避免 revoke 跨沙箱误删共用合成 SID 的 ACE。
5. 🟢 **继承标志 bug 修复**：`0x7 → 0x3` 是真实且影响广泛的 bug 修复。
6. 🟢 **NETWORK logon 消除副源 profile**：减少 `C:\Users\jbx-sandbox` 莫名创建。

---

## 六、问题与风险

### R1 🔴 `~/.office-claw` 递归 R+W 过宽（win_acl.py:430-464, win_setup.py:985-1010）

如 §4.1 所述。这是本次 commit 最大的安全退化。原 allow-only 写控制模型被实质
放弃，改为"整数据根可读写，靠 deny_write 兜底"。在单用户本地部署假设下可接受，
但：
- 与 `deny_write` 表达的沙箱间数据敏感语义矛盾；
- 真实 SID 也授 Write，沙箱 A 的 runner 可改沙箱 B 的产物（除 .git/.env 被 Deny）；
- install 阶段授的 ACL 永不撤销，uninstall 不删 `~/.office-claw` 则残留。

建议：若确需整树可写，至少把 **真实 SID 的 Write 收窄到 workspace 子树**，只让
合成 SID（受限 token 才生效）拿整树 Write；或保留原分目录授权，修 install 子进程
env 缺 `JIUWENCLAW_DATA_DIR` 的根因（让 workspace.py 算对路径），而非绕开它。

### R2 🟡 `read_acl_preinstall` 加 Chrome 路径（windows-policy.yaml:108）

`C:\Program Files (x86)\Qoom\Chrome` 硬编码。Program Files 默认 ACL 已给 Users 组
ReadAndExecute，预装是 idempotent 保险。但：
- 路径硬编码到产品名 Qoom，其他部署（用系统 Chrome/Edge）需手改；
- 预装递归 grant 合成 SID Read 到整个 Chrome 目录，沙箱能读 Chrome 全部文件
  （含其他用户 profile 数据若 Chrome 装在共享目录）。风险低但建议改用
  `executablePath` 授单文件 Read 而非整目录。

### R3 🟡 DNS 解析时机与泄露

`win_proxy.EgressFilter.allow` 在握手时 `socket.getaddrinfo(host, ...)` 解析域名比对
IP。这是代理侧解析，不是沙箱进程侧系统解析——沙箱进程的 DNS 查询（53 端口）会被
WFP Block 拦截（只 Permit loopback:port_range），故沙箱进程无法直连 DNS。但：
- 若沙箱进程用 DoH（DNS over HTTPS，443 端口经代理），代理只看到 CONNECT host:443，
  仍会按 host 比对白名单。故 DoH 不绕过。🟢
- 若沙箱进程用 IP 直连（不解析域名），代理的 IP allow 规则只有 `127.0.0.1/32`、
  `::1/128`，default=deny，会被拒。🟢
- 风险点：`allowed_domains` 域名解析后只比对 `allowed_ips`，但 `allowed_ips` 几乎
  为空（只有 loopback）。这意味着域名放行后，解析出的任何 IP 都被允许（因
  `domain_allowed` 命中即放行，不要求 IP 也在 allow）。这是设计如此（域名白名单
  模型），但若 `*.npmmirror.com` 解析到一个被劫持的 IP，沙箱会连到该 IP。建议
  增加 `blocked_ips` 覆盖已知恶意 IP 段。

### R4 🟡 `*.npmmirror.com` 通配符偏宽（windows-policy.yaml:147）

如 §3.1 所述。`*.npmmirror.com` 匹配任意子域，任意沙箱进程都能经它拉取任意 npm
包/二进制。建议收紧到 `cdn.npmmirror.com`、`registry.npmmirror.com`、`npmmirror.com`
三个具体条目。

### R5 🟡 落盘 `.exec_failed.log` 在 workspace 内（win_exec.py:1282-1291）

如 §4.3 所述。建议落盘到 box-server 私有目录。

### R6 🟡 TEMP 语义不一致（process.py vs win_exec.py）

`process.py` 用 `setdefault`，`win_exec.py` 用直接赋值覆盖。建议统一。

### R7 🟢 网络白名单仍有效 / 无任意出站能力

WFP Block + Permit loopback:port_range + win_proxy 域名/IP 过滤三层模型未改动。
沙箱进程所有出站流量必须经 win_proxy（127.0.0.1:port_range），win_proxy 按
`allowed_domains/blocked_domains/allowed_ips/blocked_ips/allowed_ports/blocked_ports`
过滤，default=deny。未引入任意出站能力。🟢

---

## 七、改进建议

1. **R1 优先**：收窄 `~/.office-claw` 递归 Write。至少真实 SID 的 Write 只授 workspace
   子树；或修 install 子进程 env 缺 `JIUWENCLAW_DATA_DIR` 的根因，恢复分目录授权。
2. **R4**：`*.npmmirror.com` 收紧到具体子域。
3. **R5**：`.exec_failed.log` 落盘到 box-server 私有目录（如
   `~/.office-claw/.jiuwenclaw/jiuwenbox/logs/`，不在 allow_read 内）。
4. **R2**：Chrome 预装改用 `executablePath` 单文件 Read，或参数化路径。
5. **R3**：`blocked_ips` 增加已知恶意 IP 段（如 metadata IP 已有，可加内网段）。
6. **R6**：TEMP 注入语义统一（建议 runner 侧也用 setdefault，与 box-server 一致）。
7. **文档**：在 release notes 标注继承标志 bug 修复，旧版部署需重新 apply_sandbox_acl。
8. **commit 粒度**：本次把"联网（实为 bash 路径）"、"超时/卡死"、"EPERM/ACL"、
   "NETWORK logon" 四条独立修复线合进一个 commit，建议后续拆分以便回滚定位。

---

## 八、小结

本次 commit 修了多个真实且严重的运行链路 bug（bash 吞反斜杠、120s 误杀、强杀后
runner 卡死、ACE 继承标志 0x7 错误、数据根 traverse 残缺），诊断与修复质量高。
**网络白名单模型未改动，三层过滤（WFP+proxy+域名/IP）仍有效，无任意出站能力引入**。

主要安全关注点是 **win_acl.py 对 `~/.office-claw` 递归授予 Read+Write**（R1），
它实质把 allow-only 写控制退化为"整树可写 + deny 兜底"，且真实 SID 也拿 Write，
跨沙箱隔离被显式放弃。在单用户本地部署假设下可接受，但与 `deny_write` 语义矛盾，
建议收窄。其余风险均为 🟡 次要（通配符偏宽、落盘位置、TEMP 语义）。

---

## 关键发现（5 条）

1. 🔴 **`~/.office-claw` 递归 Read+Write 过宽**（win_acl.py:430-464, win_setup.py:985-1010）：
   写控制模型从 allow-only 退化为整树可写，真实 SID 也授 Write，跨沙箱隔离被显式
   放弃，是本次最大安全退化点。
2. 🟢 **网络白名单仍有效**：WFP+proxy+域名/IP 三层过滤未改动，`*.npmmirror.com`
   放行范围合理偏宽，无任意出站能力引入，无 DNS 泄露路径。
3. 🟢 **ACE 继承标志 0x7→0x3 修复**（win_constants.py:204-212）：真实且影响广泛
   的 bug，修复前所有 recursive grant 不向下传播，包括 deny_write 的 .git Deny。
4. 🟢 **bash 反斜杠规整 + 超时链路修复**（win_exec.py:1034-1074, 1160-1246）：
   "沙箱内联网"的真根因是 bash 双引号吞反斜杠导致命令没起来，非网络层；配套修了
   120s 硬编码误杀和强杀后 runner 卡死。
5. 🟡 **次要风险**：`*.npmmirror.com` 通配符偏宽、`.exec_failed.log` 落盘在
   workspace 内可被 child 读、TEMP 注入两处语义不一致、Chrome 预装路径硬编码。
