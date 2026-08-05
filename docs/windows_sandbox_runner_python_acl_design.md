# Windows 沙箱 runner python + ACL 修复设计

> 接续 [[windows-sandbox-install-state]] 的 4 个待办。日期 2026-07-27。
> 相关: `docs/windows_sandbox_exec_socket_design.md`(exec socket 改造,已完成)。
>      `docs/windows_sandbox_bk_unify_design.md`(env 驱动→yaml 驱动,已完成)。

## 背景

exec socket 改造完成(改 TCP loopback,control_port 走命令行)后,runner 仍起不来。
捕获 runner stderr 实测:

```
error: uv trampoline failed to spawn Python child process
  Caused by: permission denied (os error 5)
```

四个根因串成一条因果链:

1. **`{{ workspace }}` 占位不展开**(配置层 bug)
   `_win_workspace_for` 读 `policy.windows.filesystem.allow_write[0]`,yaml 里写的是字面量
   `{{ workspace }}`。它用 `os.path.expandvars()` 展开——而 `expandvars` 只认 `%VAR%` 不认
   Jinja 风格 `{{ }}` → workspace 变成字面量字符串 `{{ workspace }}`(不存在)。

2. **ACL 整批跳过**(链 #1 的直接后果)
   `apply_sandbox_acl` 对每条路径先 `os.path.exists` 检查,字面量 `{{ workspace }}` 不存在
   → 全部 `logger.warning("路径不存在, 跳过 ACL")` → jbx-sandbox 啥读权限都没拿到。

3. **runner python 是 uv trampoline**(runner 拉起 bug)
   `_build_runner_command` 用 `sys.executable`。部署环境 box-server 跑在 uv trampoline
   `python.exe`(`D:\.venv\Scripts\python.exe` 等)上,jbx-sandbox 对 trampoline 及其依赖路径
   无读/执行权限 → trampoline 内部 spawn 真正 python child 时 `permission denied (os error 5)`
   → runner 退出,listener 没起来 → 所有 exec 报 `runner IPC 失败`。

4. **合成 SID ACE 对第一跳 runner 无效**(安全模型 bug)
   `apply_sandbox_acl` 只给**合成 SID** `JHXSandboxWrite` grant ACE。第一跳 runner 进程是
   jbx-sandbox **真实 SID**,token 未受限(`CreateProcessWithLogonW` 拉起,未受限 token)。
   合成 SID 的 ACE 对真实 SID token 不生效 → 即使路径替换对了,runner 仍读不了 venv python。

## 关键发现

### env 注入链路是对的(不迁就 yaml)

`JIUWENBOX_BUNDLED_PYTHON` / `JIUWENBOX_VENV_DIR` 是 agent-server **运行时算出的本机路径**
(`agent_ws_server.py:375-378` 调 `ensure_runtime_venv()` / `resolve_base_python()`),
每台机不同(`~/.office-claw/...`),不能写死进打包进仓库的 yaml。**保持 env 注入不动。**

`{{ workspace }}` 是**静态语义占位**("沙箱工作区根"),不是本机路径 → 代码展开,不靠 yaml 写死。
yaml 继续写占位,代码替换成真实路径 `SANDBOX_WORKSPACE / sandbox_id`。

### runner python 换 isolation_venv 真实 venv python

`pip_env.resolve_base_python()`(`pip_env.py:154`)直接返回 `sys.executable` —— 若 box-server
跑在 uv trampoline 上,注入的 `JIUWENBOX_BUNDLED_PYTHON` 就是 trampoline。

但 `get_runtime_python()`(`pip_env.py:63`)返回 `isolation_venv/Scripts/python.exe` ——
**virtualenv 创建的真实 venv python,非 trampoline**,jbx-sandbox 只要 ACL 配好 isolation_venv
目录就能直接执行。`agent_ws_server` 已注入 `JIUWENBOX_VENV_DIR` = isolation_venv 目录。

→ runner python 改读 `JIUWENBOX_VENV_DIR/Scripts/python.exe`,弃用 `sys.executable`。

### jbx-sandbox 真实 SID 已有现成取法

`win_setup.get_sandbox_user_sid()` 从注册表读 jbx-sandbox 真实 SID 字符串(install 阶段
`LookupAccountName` 取 + 写注册表,`win_wfp` 已复用同 SID 建 ALE filter)。#4 直接复用。

`apply_sandbox_acl` 的 `grant_ace` 已用 pywin32,`sid` 参数既能传字符串也能传 SID 对象 →
对真实 SID 再 grant 一份 `Allow Read+Execute` 即可,无需改 grant_ace 签名。

## 修改方案(4 处,跨 3 文件 + 1 配置)

### A. `{{ workspace }}` 占位展开 — `process.py`

**`_win_workspace_for`**:不再读 `allow_write[0]`(那是占位不是真路径),直接返回
`SANDBOX_WORKSPACE / sandbox_id`。

**`_create_windows`**:对 `allow_write`/`deny_write`/`allow_read`/`deny_read` 四个列表
统一做 `{{ workspace }}` → 真实 workspace 字符串替换,再传 `apply_sandbox_acl`。
policy yaml 的 `{{ workspace }}` 占位保留(配置层语义,代码展开)。

### B. runner python 换掉 trampoline — `win_exec.py`

**`_build_runner_command`**:`py` 不用 `sys.executable`,改用
`os.path.join(JIUWENBOX_VENV_DIR, "Scripts", "python.exe")`(isolation_venv 真实 venv python)。
缺 env 时兜底回 `sys.executable`(非 Windows 部署/单元测试不崩)。

### C. jbx-sandbox 真实 SID 也授 ACE — `win_acl.py` + `process.py`

**`apply_sandbox_acl`**:新增可选参数 `sandbox_user_sid: str | None`(默认 None,行为不变)。
对 `allow_read` 路径(含 bundled_python / venv_dir)在给合成 SID grant 的**同时**,
也给 jbx-sandbox 真实 SID grant `Allow Read+Execute`(第一跳 runner 是真实 SID token,
必须真实 SID 的 ACE 才生效)。

合成 SID 逻辑(allow_write / deny_write / deny_read / allow_read 的合成 SID grant)不动。

**`process.py` 调用处**:`win_setup.get_sandbox_user_sid()` 取真实 SID 传入。

### D. ACL 覆盖 venv python 依赖(隐含在 C)

`allow_read_paths` 已含 `bundled_python`(base python 目录)。C 项给真实 SID 授
Read+Execute 后,jbx-sandbox 能读 base python 目录的 DLL → 兜底 virtualenv venv python
靠 `pyvenv.cfg` 的 `home` 指向 base python 找 DLL 的依赖回退。

## 风险 / 边界

- **不保证 venv python 一定能独立跑**:virtualenv 的 venv python.exe 可能靠 `pyvenv.cfg`
  的 `home` 指向 base python 找 DLL。D 项给 base python 目录授真实 SID Read+Execute 正为
  兜这个。若仍失败,下一步才是 embeddable python(需额外打包,本次不做)。
- **铁律一**:只动这 4 处,不重构 `grant_ace` 签名、不改 `daemon_ipc`、不碰 Linux 路径。
- **铁律三**:Linux 路径完全不走 `_create_windows`/`apply_sandbox_acl` 新参数
  (默认 None,行为不变)。
- 待 Windows 端到端实测(runner 起来 + exec 一次)。

## 2026-07-27 解包 OfficeAce 安装包发现 runner python 正确选法

用户指出: "server 启动用哪个 python, box 就用哪个。server 能启动, jiuwenbox 也一定能启动。"
解包 `OfficeAce-0.4.5-windows-x64-setup.exe` (NSIS + payload.7z) 看打包结构:

```
payload/
  tools/python/                       ← embeddable CPython (无 uv/launcher/base 依赖)
    python.exe                       ← 真实 CPython, 自带 python313.dll
    python313.dll, python313.zip
    python313._pth                    ← embeddable 标志: sys.path 含 ../../vendor/jiuwenclaw
    Lib/site-packages/               ← 含 jiuwenclaw-agentserver.exe 等 console_scripts
    Scripts/jiuwenclaw-agentserver.exe  ← agent-server 入口
  vendor/jiuwenclaw/                  ← jiuwenclaw + jiuwenbox 源码 (无 .venv!)
  ...
```

**关键**: 打包版 agent-server **不**用 `.venv` (uv trampoline), 而用
`tools\python\python.exe` (embeddable). `python313._pth` 把 `../../vendor/jiuwenclaw`
加进 sys.path, 所以 embeddable python 能直接 import jiuwenclaw/jiuwenbox 源码 (无需装包).

### 与 dev 环境对比 (之前错配根源)

- **打包**: `tools\python\python.exe` (embeddable, 自包含, jbx-sandbox grant RX 即可跑)
- **dev (用户实测)**: 只有 `D:\Workspace\community\jiuwenclaw\.venv\Scripts\python.exe`
  (uv trampoline, base python = `AppData\Roaming\uv\python\cpython-3.13-...`, 也是 launcher,
  jbx-sandbox 读不了 uv 缓存 → permission denied os error 5)

实测 jbx-sandbox 跑 `.venv\Scripts\python.exe`:
```
error: uv trampoline failed to spawn Python child process
  Caused by: permission denied (os error 5)
```
与 isolation_venv 同样症状 (两者 base python 都是 uv launcher)。

**根因纠正**: 不是 "isolation_venv python 选错", 是 "runner 不该用任何 uv 体系 venv python"。
agent-server 能跑 `.venv` 是因为它以 liubuyu 身份跑 (liubuyu 对 uv 缓存有权限);
runner 以 jbx-sandbox 身份跑, 读不了 uv 缓存 → 任何 uv venv python 都不行。

### 正确修法方向

runner python 改用 **embeddable python** (`tools\python\python.exe` 等价物):
- 打包环境: 已有 `tools\python\python.exe`, jbx-sandbox grant RX 到该目录即可跑
  (`JIUWENBOX_BUNDLED_PYTHON` env 注入应指向 `tools\python` 而非 `.venv\Scripts`)
- dev 环境: 需要一份 embeddable python (从 python.org 下
  `python-3.13-embed-amd64.zip` 解压到 jiuwenclaw 仓库 `tools\python\`, 或复用 relay-claw 的)

embeddable python 自带 python313.dll, 无 base python 依赖, 是 jbx-sandbox 唯一能跑的 python 形态。
对应 bk Linux 沙箱用系统 `python3` (标准 CPython 无依赖) — Windows 无系统 CPython, embeddable 补位。



实测用 isolation_venv python 跑 runner, 捕获 runner stderr (`run_runner_capture.py`):

```
password: '000000'
cmd: ...\isolation_venv\Scripts\python.exe -m jiuwenbox.supervisor.win_exec runner ...
CreateProcessWithLogonW: OK pid=22936   ← 第一跳成功(改动 #2 生效)
RUNNER STDERR:
did not find executable at 'C:\Users\liubuyu\AppData\Roaming\uv\python\cpython-3.13-windows-x86_64-none\python.exe'
runner exit_code: 103
```

### 新发现 1: allow_write 路径漏授真实 SID (要补的 bug)

`_create_windows` 把 `venv_dir` 加进 `allow_write_paths`(非 allow_read),
而改动 #1 的 `apply_sandbox_acl` 只对 **allow_read** 路径给真实 SID grant Read。
→ venv_dir (含 runner python.exe) 只授了合成 SID, jbx-sandbox 真实 SID token 读不了
venv python.exe → `CreateProcessWithLogonW` 第一跳 WinError 5(实测 phase=error)。

手动 `icacls isolation_venv /grant *<真实SID>:(OI)(CI)RX` 后第一跳成功(phase=ready)。
→ **修法 E(补 bug)**: `apply_sandbox_acl` 的 allow_write 循环里, 合成 SID grant
Write+Execute+Delete 之后, 也给真实 SID grant `Allow Read+Execute`(FILE_GENERIC_READ)。
Write 仍只给合成 SID(受限 token 第二跳才写), 真实 SID 能读能执行但不能写, 不破坏
写控制。

### 新发现 2: uv launcher base python 走不通 (本 bug 修复之外的阻塞)

isolation_venv 的 `pyvenv.cfg` base python 指向 uv 装的 launcher
`C:\Users\liubuyu\AppData\Roaming\uv\python\cpython-3.13-...\python.exe`。
手动 grant 该目录 RX 给 jbx-sandbox 后**仍报** `did not find executable at '...python.exe'`,
exit 103 —— uv launcher 依赖 jbx-sandbox 读不了的 uv 缓存 / LocalAppData 机制
(记忆 [[windows-sandbox-install-state]] 早已记录这条路走不通)。

系统里**无标准 CPython 安装**(无 C:\Python313 / ProgramFiles\Python / py.exe),
全是 uv launcher 形态。补完 bug E 后 venv python.exe 本身能被读, 但 base python launcher
问题不解决, runner 仍 exit 103。**下一步**: 打包 embeddable python(见风险段第一行)。

## 开发步骤

1. `win_acl.py`:`apply_sandbox_acl` 加 `sandbox_user_sid` 参数,grant Read+Execute 给
   真实 SID(仅 allow_read 路径,合成 SID 逻辑不动)。
2. `win_exec.py`:`_build_runner_command` 的 `py` 改读 `JIUWENBOX_VENV_DIR/Scripts/python.exe`,
   兜底 `sys.executable`。
3. `process.py`:`_win_workspace_for` 返回真实路径 + `_create_windows` 展开 `{{ workspace }}`
   模板 + 调 `apply_sandbox_acl` 传 `sandbox_user_sid`。
4. 写本设计文档。
5. 待 Windows 端到端实测验证。
