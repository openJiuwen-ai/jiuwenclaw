# 代码审查：ab4932ac fix:修box-server启动报错

- **commit**: `ab4932ac82408c295495655c2a0c19872ee41a4d`
- **作者**: lby，2026-07-27
- **变更**: 4 文件，约 +57 / −15
- **审查人**: 资深代码审查员
- **审查日期**: 2026-08-01

---

## 一、概述

本 commit 名义上是修复 "box-server 启动报错"，但实际修改触及的是
**Windows 沙箱创建链路（`POST /sandboxes` → `_create_windows`）的多处阻塞性 bug**，
并非 box-server 进程本身的启动（进程拉起）问题。综合改动语义看，所谓"启动报错"
应理解为"box-server 跑在 Windows 上、首个沙箱创建/工具执行链路报错"。

修复覆盖四个独立根因，分属不同模块：

1. `windows-policy.yaml`：`filesystem_policy.directories` 里残留的 Linux 路径
   `/home`、`/tmp` 在 Windows 下被 `validate_policy` 判为非绝对路径，导致
   `POST /sandboxes` 返回 HTTP 400，**阻断所有工具**。
2. `process.py::_win_workspace_for`：旧代码用
   `os.path.expandvars(allow_write[0])` 解析 workspace，但配置里写的是
   `{{ workspace }}` Jinja 风格占位，`expandvars` 只认 `%VAR%`，展不开。
3. `process.py::_create_windows`：`allow_write/deny_write/deny_read` 直接传
   配置原值（含占位），未做模板替换，ACE 加到错路径上；且未给真实
   `jbx-sandbox` SID 授权，第一跳 runner 读不了 venv python / DLL。
4. `win_exec.py::_build_runner_command`：runner 用 `sys.executable`，部署环境
   box-server 跑在 uv trampoline launcher 上，jbx-sandbox 对 trampoline 路径
   无读/执行权限，runner 起不来。

四个根因相互独立但都在"首个沙箱创建/首跳 runner 拉起"路径上，故表现为同一类
"box-server 启动报错"。修复方向正确，注释质量高（每处都写清了根因与权衡）。
但存在若干 **本 commit 引入的一致性隐患与已被后续 commit 回退的设计决策**，
详见"问题与风险"。

---

## 二、变更范围

| 文件 | 改动 | 性质 |
|---|---|---|
| `configs/windows-policy.yaml` | 12 行 | 策略配置：Linux `directories` 留空 |
| `server/runtime/process.py` | +32 | workspace 解析 + 占位展开 + 真实 SID 授权 |
| `supervisor/win_acl.py` | +15 | `apply_sandbox_acl` 新增 `sandbox_user_sid` 参数 |
| `supervisor/win_exec.py` | +13 | runner python 改用 venv python |

---

## 三、根因与修复分析

### 3.1 windows-policy.yaml — Linux 路径残留阻断校验 🔴 → 🟢

**根因**（`windows-policy.yaml:14-18` 注释 + `policy_engine.py:152-161`）：

`validate_policy` 对 `filesystem_policy.directories` 中的每个路径调用
`_expand_path`（`policy.py:15-17`）：

```python
def _expand_path(value: str) -> str:
    return str(Path(os.path.expandvars(value)).expanduser())
```

在 Windows 上 `Path("/home")` → `WindowsPath("home")`（无盘符），随后
`_is_absolute_sandbox_path` 判定非绝对路径，抛
`PolicyValidationError("Filesystem policy paths must be absolute sandbox paths")`
（`policy_engine.py:159-161`）。这会直接 400 阻断沙箱创建，所有工具全挂。

**修复**（`windows-policy.yaml:19-23`）：把 `directories` 从
`[{path: "/home", ...}, {path: "/tmp", ...}]` 改为 `[]`，注释说明 Windows
真实可写路径走 `windows.filesystem.allow_write`。

**评价**：
- 🟢 修复正确且最小化：Linux 字段在 Windows 下本就忽略，留空是合理且语义清晰的。
- 🟢 向后兼容：`directories` 是 list，空 list 合法；下游消费处
  （`process.py::_create_windows` 对 `read_write/bind_mounts` 的处理）已对空值
  做了 `or []` 守护。
- 🟢 注释把"为什么留空而非删字段"讲透了（保留字段为策略合并逻辑兼容）。
- 🟡 小瑕疵：注释只解释了 `directories`，但 `read_only/read_write/bind_mounts`
  仍保留为空 list。若用户后续误填 Linux 路径仍会触发同一 400，本 commit 未在
  `validate_policy` 侧加 Windows 平台短路（见改进建议 §7.1）。

### 3.2 process.py — workspace 占位展不开 🔴 → 🟢

**根因**（`process.py` ab4932ac 版 `_win_workspace_for`，diff 删除的旧逻辑）：

旧代码：
```python
allow_write = policy.windows.filesystem.allow_write
if allow_write:
    return str(Path(os.path.expandvars(allow_write[0])))
return str(SANDBOX_WORKSPACE / sandbox_id)
```

配置 `allow_write[0]` 写的是 `"{{ workspace }}"`（`windows-policy.yaml:112`），
`os.path.expandvars` 只认 `%VAR%` 形式，对 `{{ workspace }}` 原样返回，再
`Path("{{ workspace }}")` → 不是有效本机路径。

**修复**（`process.py:2812-2825`）：彻底放弃从 `allow_write[0]` 反推 workspace，
直接返回 `str(SANDBOX_WORKSPACE / sandbox_id)`（即 `~/.jiuwenbox/workspace/<id>`），
注释明确"占位语义留在配置，真实路径代码算"。

**评价**：
- 🟢 修复正确：分离了"配置语义（占位）"与"运行时真实路径"，避免用错误的展开
  机制（`expandvars`）去解析非 env 占位。
- 🟢 workspace 路径走 `SANDBOX_WORKSPACE`（`workspace.py:61`，
  `~/.jiuwenbox/workspace`），是 box-server 进程用户天然 owner 的目录，makedirs
  无权限问题。
- 🟡 **本 commit 引入的注释不一致**：ab4932ac 版 `_win_workspace_for` docstring
  写 `~/.jiuwenbox/workspace/<sandbox_id>`（与 `SANDBOX_WORKSPACE` 定义一致 ✓），
  但当前 HEAD（82001d09）的 docstring 已改成
  `~/.office-claw/.jiuwenclaw/jiuwenbox/<id>`，而 `WIN_SANDBOX_WORKSPACE_ROOT`
  仍等于 `SANDBOX_WORKSPACE = JIUWENBOX_HOME/"workspace"` = `~/.jiuwenbox/workspace`。
  即 HEAD 版 docstring 与代码实现不符 —— 这是 ab4932ac 之后的回归，非本 commit
  的问题，但提示 workspace 路径设计在后续 commit 反复变动，需复核一致性。

### 3.3 process.py — 占位未展开 + 真实 SID 未授权 🔴 → 🟡

**根因**（`process.py:2894-2902` 旧逻辑，diff 删除）：

旧代码直接把 `policy.windows.filesystem.deny_write`、`deny_read` 原值传给
`apply_sandbox_acl`，其中含 `"{{ workspace }}/.git"` 等占位，`apply_sandbox_acl`
内部 `os.path.expandvars` 同样展不开 → ACE 加到名为
`{{ workspace }}/.git` 的不存在路径上，被 `os.path.exists` 守护跳过
（`win_acl.py:308-310`），等于 deny 完全没生效。

**修复**（`process.py:2896-2902`）：

```python
def _expand_ws(path: str) -> str:
    return path.replace("{{ workspace }}", workspace)

allow_read_paths = [_expand_ws(p) for p in (policy.windows.filesystem.allow_read or [])]
allow_write_paths = [_expand_ws(p) for p in (policy.windows.filesystem.allow_write or [])]
deny_write_paths = [_expand_ws(p) for p in (policy.windows.filesystem.deny_write or [])]
deny_read_paths = [_expand_ws(p) for p in (policy.windows.filesystem.deny_read or [])]
```

并在 `apply_sandbox_acl` 调用处传入 `sandbox_user_sid=win_setup.get_sandbox_user_sid()`
（`process.py:2871-2872`）。

**评价**：
- 🟢 占位展开修复正确：四个 list 统一过 `_expand_ws`，deny_write 现在指向真实
  `workspace/.git`、`workspace/.env`，ACE 能落到正确路径。
- 🟢 真实 SID 授权修复正确：第一跳 runner 是 `jbx-sandbox` 真实 SID 且 token 未
  受限（`CreateProcessWithLogonW` 拉起），合成 SID 的 ACE 对它不生效，必须真实
  SID 的 ACE。`win_acl.py:326-332` 给真实 SID grant `ALLOW_WRITE_RIGHTS`，
  `win_acl.py:317-325` 处给真实 SID grant `FILE_GENERIC_READ`，覆盖读+写两类。
- 🟡 **`_expand_ws` 是闭包内嵌函数，作用域限于 `_create_windows`**：如果后续有
  别的方法需要展开 `{{ workspace }}` 占位（如 upload/list 路径处理），会重复实现。
  建议抽成模块级 `_expand_workspace_placeholder(path, workspace)` 工具函数。
- 🟡 **占位只替换 `{{ workspace }}` 一种**：配置里 `allow_read` 还用了
  `%JIUWENBOX_SKILLS_DIR%`（`windows-policy.yaml:120`），走的是 `expandvars`，
  在 `apply_sandbox_acl` 内部统一处理。两条展开路径（`_expand_ws` vs 内部
  `expandvars`）混用，若未来新增 `${var}` 或 `{{ var }}` 风格占位容易漏。建议
  统一占位语法（见 §7.2）。
- 🟡 **`get_sandbox_user_sid` 失败静默**（`win_setup.py:1213-1216`）：从注册表读
  SID，读不到返回 `None`，`apply_sandbox_acl` 里 `if sandbox_user_sid:` 守护跳过
  真实 SID ACE。这意味着如果 install 未完成或注册表损坏，runner 会因缺读权限
  起不来，但 box-server 不会显式报错，只留下"读不了 venv python"的运行时错。
  建议在 `_create_windows` 里对 `sandbox_user_sid is None` 至少 `logger.warning`
  （见 §7.3）。

### 3.4 win_exec.py — runner python 选错 🔴 → 🟡（后被回退）

**根因**（`win_exec.py:266-269` ab4932ac 版）：

旧代码 `py = sys.executable or "python"`。部署环境 box-server 跑在 uv
trampoline launcher 上，`sys.executable` 指向 trampoline；jbx-sandbox 对
trampoline 及其依赖路径无读/执行权限，trampoline 内部 spawn child 时
permission denied（os error 5），runner 起不来。

**修复**（`win_exec.py:266-269` ab4932ac 版）：

```python
venv_dir = (os.environ.get("JIUWENBOX_VENV_DIR") or "").strip()
py = sys.executable or "python"
if venv_dir:
    candidate = os.path.join(venv_dir, "Scripts", "python.exe")
    if os.path.isfile(candidate):
        py = candidate
```

优先用 `JIUWENBOX_VENV_DIR/Scripts/python.exe`（virtualenv 创建的真实解释器，非
trampoline）。

**评价**：
- 🟢 修复方向正确：用 virtualenv 真实 venv python 替代 trampoline，逻辑上能绕开
  jbx-sandbox 对 trampoline 路径无权限的问题。
- 🔴 **本 commit 引入的策略在后续 commit 已被回退/重写**：当前 HEAD
  （82001d09）的 `_build_runner_command`（`win_exec.py:392-437`）注释明确写
  "runner python 用标准 CPython（非 uv trampoline/venv launcher）...
  jbx-sandbox 对 uv 缓存/AppData 无读权限，**任何 uv 体系 venv python
  （`.venv` / isolation_venv / uv 全局）第一跳都报 WinError 5 或 trampoline
  spawn child permission denied**"，并改为优先级
  `JIUWENBOX_RUNNER_PYTHON` env > 默认系统 python 路径。即 ab4932ac 用
  `isolation_venv` python 的方案被实测证伪（uv 体系 venv 仍走 trampoline），
  后续改为"标准 CPython + 显式 `JIUWENBOX_RUNNER_PYTHON`"。
  - 这说明 ab4932ac 的 runner python 修复**实际不 work**（uv 创建的 venv python
    也是 trampoline），属于"先打个补丁、后回退"的中间态。
  - 审查本 commit 时需知：§3.4 的修复在主干上已被替代，不能作为"已解决"看待。
- 🟡 ab4932ac 版未对 `candidate` 不存在的情况记日志：`if os.path.isfile(candidate)`
  失败时静默回退 `sys.executable`，运维难定位为何仍走 trampoline。HEAD 版补了
  `JIUWENBOX_RUNNER_PYTHON` 不存在/非文件的兜底与日志，更稳健。

---

## 四、关键代码检视

### 4.1 win_acl.py — `sandbox_user_sid` 参数新增 🟢

`win_acl.py:266-296` 给 `apply_sandbox_acl` 加 `sandbox_user_sid: str | None = None`
关键字参数，默认 `None` 保持向后兼容。文档串第 6 条（`:288-291`）讲清"第一跳
runner 真实 SID、token 未受限、合成 SID ACE 不生效"的根因。

`win_acl.py:317-325`（写控制）与 `:326-332`（读控制，本 commit diff 新增块）
对称地给真实 SID grant 与合成 SID 相同的 rights：

```python
# win_acl.py:326-332 (本 commit 新增)
if sandbox_user_sid:
    grant_ace(
        expanded, sandbox_user_sid,
        rights=const.FILE_GENERIC_READ,
        mode="ALLOW",
        recursive=recursive,
    )
```

- 🟢 对称设计合理：写控制给 `ALLOW_WRITE_RIGHTS`，读控制给 `FILE_GENERIC_READ`，
  与合成 SID 各自的授权一致。
- 🟢 `recursive=recursive` 透传，与同路径的合成 SID ACE 递归范围一致，避免
  "顶层允许、子树拒绝"的错配。
- 🟡 **未撤销策略缺失**：`apply_sandbox_acl` 返回 `applied` 列表供
  `revoke_sandbox_acl` 按清单撤销（`win_acl.py:293-295` 注释）。新增的真实 SID
  ACE 同样加在 `applied` 里的同一 `expanded` 路径上（`:333`），revoke 时按路径
  扫描应能覆盖。但 `applied` 是按"路径"而非"路径+SID+rights"维度记账的，
  revoke 若只删合成 SID 的 ACE 不会清真实 SID 的 ACE，可能残留 Allow ACE。
  需复核 `revoke_sandbox_acl` 是否对所有授过 ACE 的 SID 都撤销（见 §6 风险 R3）。

### 4.2 process.py — `_expand_ws` 闭包 🟡

`process.py:2896-2902`：

```python
def _expand_ws(path: str) -> str:
    return path.replace("{{ workspace }}", workspace)
```

- 🟢 实现简单、对 None 不敏感（外层 `or []` 已守护）。
- 🟡 `str.replace` 是朴素字符串替换，若 workspace 路径里含 `{{ workspace }}`
  字面量会二次替换（极不可能但非鲁棒）。用一次性替换或正则更稳。
- 🟡 闭包作用域限 `_create_windows`，复用性差（见 §3.3）。

### 4.3 win_exec.py — venv python 探测 🔴（已被回退）

`win_exec.py:266-269` ab4932ac 版见 §3.4。当前 HEAD 已重写为
`JIUWENBOX_RUNNER_PYTHON` 优先 + 标准 CPython 兜底，ab4932ac 的 venv 方案不再
适用。

---

## 五、优点

1. **根因注释质量极高**：每处改动都写了详细的根因说明（含实测错误码
   WinError 5/87/1326、HTTP 400 报文原文），对后续维护与审查极友好。
2. **修复方向正确**（§3.1-3.3）：policy 留空、workspace 代码算、占位统一展开、
   真实 SID 授权，四项均直击根因。
3. **向后兼容**：`apply_sandbox_acl` 新增参数默认 `None`，旧调用方不破；
   `directories: []` 对下游 `or []` 守护友好。
4. **安全权衡写清**（`win_acl.py:317-325` 注释）：真实 SID grant Write 是因为
   runner 是 box-server 自启的可信代理，真正执行用户代码的第二跳仍受合成 SID
   双重 ACL 约束 —— 权衡合理且有文档。

---

## 六、问题与风险

### R1 🔴 runner python 修复实际无效（§3.4）
ab4932ac 用 `isolation_venv` 的 venv python 作 runner，但 uv 体系 venv python
仍是 trampoline，jbx-sandbox 第一跳仍 WinError 5。后续 commit（cafaa1f1
"自动探测python" + 82001d09）已改为标准 CPython + `JIUWENBOX_RUNNER_PYTHON`。
**本 commit 的 win_exec.py 改动属中间态，不应视为该根因的最终解决**。

### R2 🟡 `validate_policy` 未在 Windows 平台短路 Linux 路径（§3.1）
`validate_policy`（`policy_engine.py:136-165`）对 `filesystem_policy.directories`
无差别过 `_expand_path`。本 commit 用"配置留空"规避，但若用户在 Windows 上误填
Linux 路径仍会 400。更稳的修法是在 `validate_policy` 里对 Windows 平台跳过
Linux-only 字段的绝对路径校验（或在 `_expand_path` 里对 Windows 上的 `/foo`
映射到 `C:\foo`）。当前做法把"正确性"绑在配置文件上，脆弱。

### R3 🟡 真实 SID ACE 的撤销路径未在本 commit 验证（§4.1）
`applied` 列表按"路径"维度记账，新增的真实 SID ACE 与合成 SID ACE 共用同一
路径条目。`revoke_sandbox_acl` 是否对"该路径上所有授过的 SID"都撤销，本 commit
未触及也未在注释中说明。若 revoke 只删合成 SID，真实 SID 的 Allow ACE 会残留
在工作区/venv 目录上，构成权限泄漏。需复核 `revoke_sandbox_acl` 实现。

### R4 🟡 `sandbox_user_sid` 缺失静默（§3.3）
`get_sandbox_user_sid` 读注册表失败返回 `None`，`apply_sandbox_acl` 静默跳过真实
SID ACE，runner 后续因读不了 venv python 起不来，box-server 无显式告警。建议
`_create_windows` 对 `sandbox_user_sid is None` 至少 `logger.warning` 并在日志里
标"install 可能未完成"。

### R5 🟡 占位语法不统一（§3.3）
配置同时用 `{{ workspace }}`（Jinja 风格，代码侧 `_expand_ws` 展开）和
`%JIUWENBOX_SKILLS_DIR%`（env 风格，`apply_sandbox_acl` 内 `expandvars` 展开）。
两套展开机制混用，易错。建议统一为一种（推荐 `%VAR%`，原生支持）。

### R6 🟢 无降级/退出策略问题
本 commit 不涉及启动失败时的进程退出；`_create_windows` 各失败点用
`logger.warning` + 跳过，未 `raise`，沙箱创建会以半成品状态继续 —— 这与
`apply_sandbox_acl` 跳过不存在路径的既有行为一致，可接受。但若 `os.makedirs
workspace` 失败（`process.py:2874-2880`），仅 warning 后继续，后续 ACL施加在
不存在路径上会全跳过，沙箱实际无任何写权限。建议 workspace makedirs 失败时
直接抛出，终止该沙箱创建（见 §7.4）。

---

## 七、改进建议

### 7.1 `validate_policy` 加平台短路（对应 R2）
在 `policy_engine.py:validate_policy` 入口加：
```python
import sys
if sys.platform == "win32":
    # Linux-only filesystem_policy 字段在 Windows 下跳过绝对路径校验
    directory_paths = []
    file_paths = []
else:
    directory_paths = [...]
    file_paths = [...]
```
或在 `_expand_path` 里对 Windows 上以 `/` 开头的路径映射到当前盘符根。这样即使用
户误填 Linux 路径也不会 400，鲁棒性优于"配置必须留空"。

### 7.2 统一占位语法（对应 R5）
把 `windows-policy.yaml` 里的 `{{ workspace }}` 改成 `%JIUWENBOX_WORKSPACE%`
等 env 风格，由 `apply_sandbox_acl` 内 `expandvars` 统一展开，删除 `_expand_ws`
闭包。或在 `_expand_ws` 里同时支持 `{{ workspace }}` 与 `%VAR%`。推荐前者
（少一层自定义）。

### 7.3 `sandbox_user_sid` 缺失告警（对应 R4）
`process.py::_create_windows` 在调用 `apply_sandbox_acl` 前：
```python
sandbox_user_sid = win_setup.get_sandbox_user_sid()
if not sandbox_user_sid:
    logger.warning(
        "[SandboxWin] %s jbx-sandbox SID 未注册 (install 未完成?), "
        "runner 第一跳可能因缺读权限起不来", sandbox_id,
    )
```

### 7.4 workspace makedirs 失败应终止（对应 R6）
`process.py:2874-2880` 的 `try/except OSError` 改为 `raise` 或返回错误码，避免
半成品沙箱继续创建。当前仅 warning 会让 ACL 全跳过、沙箱无写权限，错误更难定位。

### 7.5 复核 `revoke_sandbox_acl`（对应 R3）
单独审查 `revoke_sandbox_acl` 是否对真实 SID 的 ACE 也撤销。若否，补撤销逻辑
或在本 commit `applied` 记账里区分"路径+SID+rights"三元组。

### 7.6 runner python 选型（对应 R1）
ab4932ac 的 venv 方案已回退，后续应沿用 HEAD 的"标准 CPython +
`JIUWENBOX_RUNNER_PYTHON`"并补齐打包环境的 `tools/python/python.exe` 自动探测
（cafaa1f1 的 `policy_reader._resolve_tool_paths` 已做 python_dir 自动检测，
应与 runner 选型对齐）。

---

## 八、小结

本 commit 是一组**针对 Windows 沙箱首创建/首跳拉起链路的多根因修复**，方向正确、
注释详尽、向后兼容性好。其中 §3.1-3.3（policy 留空、workspace 代码算、占位展开、
真实 SID 授权）是扎实的修复，应予肯定。

但 §3.4（runner python 选 venv）**实际不 work 并已被后续 commit 回退**，是本
commit 最主要的"伪修复"，审查时需明确标注；同时 R2/R3/R4 暴露了"配置驱动正确性"
与"缺 SID 静默"的脆弱点，建议按 §7.1/7.3/7.5 跟进加固。

**最关键四条发现**：
1. 🔴 `win_exec.py` 的 venv runner 方案在主干已被回退（uv venv 仍是 trampoline），
   本 commit 该项属中间态伪修复。
2. 🟢 `windows-policy.yaml` 把 Linux `directories` 留空，正确消除
   `validate_policy` 在 Windows 上的 HTTP 400 阻断，且向后兼容。
3. 🟢 `process.py` 的 `_expand_ws` + 真实 SID 授权，正确修复了占位未展开与第一跳
   runner 缺读权限两个根因。
4. 🟡 `validate_policy` 未在 Windows 平台短路 Linux 字段校验（R2）、真实 SID ACE
   撤销未验证（R3）、SID 缺失静默（R4），是后续应加固的脆弱点。
