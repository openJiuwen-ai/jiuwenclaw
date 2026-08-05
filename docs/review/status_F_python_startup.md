# 状态核对 F：Python 探测与启动流程

> 核对基线：HEAD `82001d09` 工作区文件
> 核对范围：6 份检视报告（ab4932ac / d15fcf8e / cafaa1f1 / fa85c987 / 7fe80192 / 82001d09）提及的 14 条 Python 探测/启动流程/policy 类问题
> 图例：✅ 已解决 / ❌ 仍存在 / ⚠️ 部分解决 / 🔄 以其他方式绕过

## 状态总表

| # | 问题 | 报告出处 | 当前状态 | 证据 file:line | 说明 |
|---|---|---|---|---|---|
| 1 | runner python 选 venv 是中间态伪修复，应回退为标准 CPython + JIUWENBOX_RUNNER_PYTHON env | ab4932ac §3.4 / R1 | ✅ 已解决 | `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:392-437`（`_build_runner_command`） | docstring 明确"runner python 用标准 CPython（非 uv trampoline/venv launcher）"；`win_exec.py:417` `py = os.environ.get("JIUWENBOX_RUNNER_PYTHON")`；`:418-419` 兜底 `sys.executable or "python"`。venv 方案（ab4932ac 版的 `JIUWENBOX_VENV_DIR/Scripts/python.exe`）已彻底删除，runner 走标准 CPython。`JIUWENBOX_RUNNER_PYTHON` env 在用。 |
| 2 | CPython 探测路径硬编码 dev 机（D:\Files\python313\python.exe） | d15fcf8e §4.3 / 6.5 | ❌ 仍存在 | `jiuwenclaw/agentserver/agent_ws_server.py:357-366` | 候选列表首项 `r"D:\Files\python313\python.exe"  # dev 实测机` 仍在源码中。生产用户常见安装路径（`%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`、Store 版、注册表注册路径）仍未覆盖，未做 glob/注册表查询，未校验非 trampoline。仅将"硬编码"从 policy_reader 迁到 agent_ws_server 探测注入路径，本质未改。 |
| 3 | `_load_policy_preinstall_paths` 在 --force --policy-path 重装时直接读原始 YAML 不经 _resolve_tool_paths → tool_paths 全空 → 预装集丢失 | cafaa1f1 P1 | ❌ 仍存在 | `jiuwenbox/src/jiuwenbox/supervisor/win_setup.py:585-609`（`_load_policy_preinstall_paths`） | 函数仍直接 `yaml.safe_load` 读原始 YAML，读 `win_fs.get("tool_paths")`（`:600`），未经 `policy_reader._resolve_tool_paths` 填充。基底 windows-policy.yaml 的 tool_paths 四字段均为空串（`windows-policy.yaml:138-141`），--force --policy-path 重装时读到全空 → 预装集不含任何工具目录。`install(force=True, policy_path=...)` → `_elevate_and_run_install` → 提权子进程走同函数，两条路径不一致未修复。 |
| 4 | node_dir 向上遍历到文件系统根（`for ancestor in (py_dir.parent, *py_dir.parents)`） | cafaa1f1 P2 | ❌ 仍存在 | `jiuwenbox/src/jiuwenbox/server/policy_reader.py:64` | 仍为 `for ancestor in (py_dir.parent, *py_dir.parents)`，会遍历到盘符根。未限定只查 `py_dir.parent` 或最多回溯 1-2 层。OfficeAce 标准结构下第一跳即 break，实际误命中概率低，但范围未收紧。 |
| 5 | 探测路径未 `.resolve()` 规范化、未做可信校验；shutil.which("git") 完全受进程 PATH 控制 | cafaa1f1 P3 | ❌ 仍存在 | `jiuwenbox/src/jiuwenbox/server/policy_reader.py:54,76,90-93` | python_dir 写入用 `str(Path(sys.executable).parent)`（`:54`），未 `.resolve()`；git_dir 来自 `shutil.which("git")`（`:72`），路径直接 `str(ancestor)` 写入（`:79`），未规范化；`:90-93` 的日志 `logger.info("tool_paths 自动检测填充: %s", ...)` 只列字段值，未标 `via PATH` 或 `via sys.executable` 来源。可信校验（限制 ProgramFiles/SystemRoot）未加。 |
| 6 | validate_policy 未在 Windows 平台短路 Linux 字段校验，正确性依赖配置留空 | ab4932ac §3.1 / R2 | 🔄 以其他方式绕过 | `jiuwenbox/src/jiuwenbox/server/policy_engine.py:104-114`（`_is_absolute_sandbox_path`）；`policy_engine.py:136-165`（`validate_policy`） | **未按报告建议在 validate_policy 里加 Windows 平台短路**（仍无 `sys.platform == "win32"` 分支，policy_engine.py 全文仅 `:107` 注释提到"不依赖 box-server 自身运行平台"）。但根因被另一种方式绕过：`_is_absolute_sandbox_path`（`:111-114`）改为同时接受 `PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()`，即 `/home` 这种 POSIX 绝对路径在 Windows 上也判为绝对 → 不再 400。配置留空（`windows-policy.yaml:19-23` `directories: []`）+ 路径判定放宽，共同消除了 400 阻断，但"用户误填 Linux 路径仍会过校验进入下游"的隐患仍在（只是不再 400，下游消费处是否正确处理未验证）。报告"短路"诉求未实现，但阻断症状已消除。 |
| 7 | `_win_workspace_for` docstring 写 ~/.office-claw/.jiuwenclaw/jiuwenbox/<id>，但实现 WIN_SANDBOX_WORKSPACE_ROOT 实际是 ~/.jiuwenbox/workspace（docstring 与实现不符） | ab4932ac §3.2 🟡 回归 | ❌ 仍存在 | `jiuwenbox/src/jiuwenbox/server/runtime/process.py:2813-2825`（docstring）；`jiuwenbox/src/jiuwenbox/server/workspace.py:43-62`（实现） | process.py:2813 docstring 写"沙箱 workspace 真实路径 (Windows): ~/.office-claw/.jiuwenclaw/jiuwenbox/<id>"，`:2825` 返回 `WIN_SANDBOX_WORKSPACE_ROOT / sandbox_id`。但 workspace.py:62 `WIN_SANDBOX_WORKSPACE_ROOT = SANDBOX_WORKSPACE = JIUWENBOX_HOME / "workspace"`，而 `:50` Windows 下 `JIUWENBOX_HOME = JIUWENCLAW_DATA_DIR_PATH / "jiuwenbox"`，`:44-49` `JIUWENCLAW_DATA_DIR_PATH` 默认 `_effective_user_home() / ".jiuwenclaw"`（**不是 .office-claw**），仅当 `JIUWENCLAW_DATA_DIR` env 设了才用 env 值。`OFFICE_CLAW_DATA_ROOT`（`:52-56`）是另一独立变量（`~/.office-claw`），与 `JIUWENBOX_HOME` 无关。故 docstring 的 `~/.office-claw/.jiuwenclaw/jiuwenbox/<id>` 路径与代码实际根 `~/.jiuwenclaw/jiuwenbox/workspace/<id>` 不符。docstring 还少了 `workspace` 这一段。 |
| 8 | bootstrap 触发条件不再看 sandbox.enabled（只要 startup_mode: internal 就 spawn，即使 enabled:false） | fa85c987 §4.1 / R1 | ❌ 仍存在 | `jiuwenclaw/agentserver/agent_ws_server.py:245-287`（`_bootstrap_internal_jiuwenbox`） | `:273` `explicit_mode = get_sandbox_startup_mode_explicit()`；`:274-280` mode is None 则 return；`:281-287` mode != "internal" 则 return。全程未读 `sandbox.enabled`。docstring（`:248-251`）明言"不单独依赖 sandbox.enabled: 只要 startup_mode=internal 就拉"。enabled 门控未恢复。 |
| 9 | `_normalize_sandbox_startup_mode` 非法值静默回落 internal（与写入路径严格校验不一致） | fa85c987 §4.4 / R2 | ❌ 仍存在 | `jiuwenclaw/config.py:1245-1250`（读取归一化）；`config.py:1352-1358`（写入校验） | `:1247-1250` `text = str(value or "").strip().lower(); if text not in _VALID: return _DEFAULT`（静默回落，不抛错）。`update_sandbox_startup_mode:1355-1358` 写入路径 `if str(mode).strip().lower() not in _VALID: raise ValueError(...)`。读取路径对"非空但非法"仍不抛错，读写校验仍不一致。用户 yaml 拼错值（如 `iternal`）启动时无反馈。 |
| 10 | bootstrap 在 ws listen 之后，初始化竞态 | fa85c987 §4.2 / R3 | ❌ 仍存在 | `jiuwenclaw/agentserver/agent_ws_server.py:467-472` | `:467-470` 先 `logger.info("已启动: ws://%s:%s"...)`（已 listen），`:471-472` 再 `await self._bootstrap_internal_jiuwenbox()`。bootstrap 仍在 listen 之后，无 asyncio.Event gate，Gateway 抢先连入时 sandbox 未 ready。 |
| 11 | 存量用户迁移坑（env-var 迁 yaml 必须显式写 startup_mode: internal） | fa85c987 §4.1 / R10 | ❌ 仍存在 | `jiuwenclaw/agentserver/agent_ws_server.py:273-287`；`jiuwenclaw/config.py:1330-1349`（`get_sandbox_startup_mode_explicit`） | `get_sandbox_startup_mode_explicit` 只看 yaml `sandbox.startup_mode`，不读 `JIUWENCLAW_SANDBOX_STARTUP_MODE` env；`_bootstrap_internal_jiuwenbox` 对 env-only 存量用户返回 None → 不 spawn。无迁移告警、无 env 透传兜底（`_sandbox_yaml_to_env_overlay` 是反向 yaml→env，不能帮 env→yaml 迁移）。 |
| 12 | 稀疏副本+内存合并根治"副本固化基底挡升级"；disable_all "只压不删"；端口工具外移 | 7fe80192 §1-3 | ✅ 已解决 | `jiuwenbox/src/jiuwenbox/server/policy_reader.py:154-202`；`jiuwenbox/src/jiuwenbox/bundled_configs.py:24-32`；`jiuwenbox/src/jiuwenbox/models/policy.py:847-851`（disable_all 字段）；`jiuwenclaw/agentserver/sandbox/port_util.py`（外移） | 仍是该机制：`load_policy` 读基底+副本内存合并（`:167-202`），不生成合并文件；`base_policy_path()`（bundled_configs.py:24-32）按平台返回基底；`WindowsNetworkPolicy.disable_all: bool = False`（policy.py:850）；端口工具在 `sandbox/port_util.py`。机制完整。 |
| 13 | interface_deep.py 用 except Exception 回退 local（沙箱静默降级 local，无"已降级"标记，未按 policy 决定是否允许降级） | 7fe80192 interface_deep §🟡；82001d09 未单独提 | ❌ 仍存在 | `jiuwenclaw/agentserver/deep_agent/interface_deep.py:2683-2700` | `:2683` `except Exception as exc:` 捕获所有异常；`:2685-2689` 若原本要走沙箱则 `logger.warning("...fallback to LOCAL mode")`；`:2690-2694` 试 `create_local_sysop_card` 重试。仅 warning 日志，返回的 sysop 无"已降级"标志，调用方拿不到信号；无 `policy.allow_sandbox_fallback` 开关。三处 fallback（sysop_card None `:2613-2629`、add 失败 `:2660-2681`、整体异常 `:2683-2700`）均未标记降级。 |
| 14 | 新增本地落盘日志（C:\Users\jbx-sandbox\jiuwenbox-logs\<sandbox_id>\runner.log），best-effort 不阻断 | 82001d09 §3.1(a) | ✅ 已解决 | `jiuwenbox/src/jiuwenbox/supervisor/win_exec.py:81-187`（`_init_local_log`/`_local_log`/`_push_log`） | `:85-87` 模块级 `_local_log_path/_local_log_file/_local_log_lock`；`:90-124` `_init_local_log` 在 `home/jiuwenbox-logs/<sandbox_id>/runner.log` 开追加模式文件（`:117-121`），失败降级不抛（`:122-124`）；`:127-141` `_local_log` 行缓冲 `flush()`；`:186-187` `_push_log` 末尾调 `_local_log` 落盘。多路兜底 profile 根（env USERPROFILE → `get_sandbox_profile_dir()` API → `C:\Users\jbx-sandbox`，`:104-114`）。best-effort 不阻断主流程，符合预期。 |

## 汇总

- 本组共核对 **14** 条
- ✅ 已解决 **4** 条：#1、#12、#14，以及 #6（以"路径判定放宽"绕过而非按报告建议短路，标 🔄）
- ❌ 仍存在 **9** 条：#2、#3、#4、#5、#7、#8、#9、#10、#11、#13（实为 10 条仍存在，#6 单独计为绕过）

更正计数：
- ✅ 已解决：3 条（#1、#12、#14）
- 🔄 以其他方式绕过：1 条（#6）
- ❌ 仍存在：10 条（#2、#3、#4、#5、#7、#8、#9、#10、#11、#13）

## 仍存在/部分解决清单（按优先级）

### 🔴 高优先级（功能缺陷）

1. **#3 `_load_policy_preinstall_paths` 绕过 `_resolve_tool_paths`**（`win_setup.py:585-609`）
   - --force --policy-path 重装时 tool_paths 全空 → 预装集丢失 → 重装后受限 token 读不了 OfficeAce tools/python → WinError 2/5。
   - 修法：让 `_load_policy_preinstall_paths` 复用 `PolicyReader.load_policy()`（或抽公共"加载并解析+探测"函数），保证 install 与 runtime 用同一份填充后的 tool_paths。

2. **#2 CPython 探测硬编码 dev 机路径**（`agent_ws_server.py:357-366`）
   - 生产用户常见 Python 安装路径未覆盖，探测失败回退 sys.executable 可能仍是 uv trampoline → runner 起不来。
   - 修法：候选列表加 `%LOCALAPPDATA%\Programs\Python\Python3*\python.exe`（glob）+ 注册表 `HKCU/HKLM\Software\Python\PythonCore\*\InstallPath` 查询 + PATH 里 `python.exe`（校验非 trampoline）；删 `D:\Files\python313` dev 机硬编码。

### 🟡 中优先级（健壮性/一致性）

3. **#7 `_win_workspace_for` docstring 与实现不符**（`process.py:2813` vs `workspace.py:43-62`）
   - docstring 写 `~/.office-claw/.jiuwenclaw/jiuwenbox/<id>`，实际根是 `~/.jiuwenclaw/jiuwenbox/workspace/<id>`（或 JIUWENCLAW_DATA_DIR env 指定值）。docstring 还漏 `workspace` 段。误导维护者。
   - 修法：docstring 改为与 `WIN_SANDBOX_WORKSPACE_ROOT` 一致的真实路径，或注明跟随 env。

4. **#8 bootstrap 不看 sandbox.enabled**（`agent_ws_server.py:273-287`）
   - `enabled:false + startup_mode:internal` 会意外拉起 jiuwenbox。对"只想配置 sandbox 字段但暂不启用"的用户意外多进程。
   - 修法：恢复 `enabled && startup_mode==internal(显式)` 才 spawn，或在 docstring/升级文档显式说明语义变化。

5. **#9 `_normalize_sandbox_startup_mode` 读取路径静默回落**（`config.py:1245-1250`）
   - 用户 yaml 拼错值（`iternal`）无反馈，读写路径校验不一致。
   - 修法：读取路径对"非空但非法"抛 `ValueError`，仅 None/空回落默认。

6. **#10 bootstrap 在 ws listen 之后**（`agent_ws_server.py:467-472`）
   - 初始化顺序窗口，Gateway 抢先连入时 sandbox 未 ready。
   - 修法：bootstrap 移到 listen 前，或在 ws handler 对 sandbox 请求加 `asyncio.Event` gate。

7. **#13 沙箱静默降级 local 无标记**（`interface_deep.py:2683-2700`）
   - 用户可能误以为在沙箱里跑，实则在 local，安全隔离失效无感。
   - 修法：返回值/日志透出"已降级"标志，或加 `policy.allow_sandbox_fallback` 开关（默认关闭）。

### 🟡 低优先级（收紧/防御）

8. **#4 node_dir 向上遍历到根**（`policy_reader.py:64`）
   - 限定只查 `py_dir.parent` 或最多回溯 1-2 层。

9. **#5 探测路径未 `.resolve()`、未标来源**（`policy_reader.py:54,76,90-93`）
   - 对 filled 路径统一 `resolve()`；`git_dir` 日志标 `via PATH`。

10. **#11 存量用户 env→yaml 迁移坑**（`agent_ws_server.py:273-287`）
    - 无迁移指引/兜底。修法：启动时若 yaml 无显式 startup_mode 但 env 有 `JIUWENCLAW_SANDBOX_STARTUP_MODE=internal`，log 迁移提示或透传触发。

### 🔄 已绕过（非报告建议的修法，但症状消除）

11. **#6 validate_policy 未在 Windows 短路 Linux 字段**（`policy_engine.py:104-114`）
    - 报告建议在 validate_policy 加 `sys.platform == "win32"` 短路，未实现。但 `_is_absolute_sandbox_path` 改为同时接受 Windows+POSIX 绝对路径，`/home` 不再被判非绝对 → 不再 400。配置留空 + 路径判定放宽共同消除阻断。隐患：用户误填 Linux 路径会过校验进下游，下游处理未验证。
