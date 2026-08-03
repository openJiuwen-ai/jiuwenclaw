# 代码审查：cafaa1f1 fix:自动探测python

- **Commit**: `cafaa1f1633801f440dbedb173438a5ee51f980b`
- **作者**: lby，2026-07-31
- **变更**: +88 / -8，2 文件
  - `jiuwenbox/src/jiuwenbox/server/policy_reader.py` (+81)
  - `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` (+15)
- **审查重点**: Windows 沙箱 Python 解释器自动探测逻辑、兜底、安全可信度

---

## 概述

本 commit 解决一个真实且重要的工程问题：随 wheel 打包的基底 `windows-policy.yaml` 不能再写死开发机路径（`D:\Files\python313` / `D:\Files\Git` 等），否则换一台机器沙箱就指向不存在的解释器。新方案在 `policy_reader.load_policy()` 内存合并之后、返回之前，插入一个 `_resolve_tool_paths()` 钩子，对空字段做运行时探测填充：用 `sys.executable` 反推出 OfficeAce 包内 `tools/python` 目录，再向上找 `tools/node`；`git_dir`/`bash_path` 从 `PATH` 上的 `git.exe` 反推安装根。

整体方向正确、注释充分、只填空字段不覆盖显式配置、不落盘，符合 `load_policy` 既有的"内存合并不生成文件"机制。但实现上存在若干值得收紧的点：node_dir 的向上遍历无边界、install 提权子进程绕过本探测导致 ACL 预装与运行时填充不一致、探测路径未做规范化/可信校验。

---

## 变更范围

1. **`policy_reader.py`** 新增 `_resolve_tool_paths(policy)` 函数（约 66 行），并在 `load_policy()` 的三个返回点（基底直用、副本读失败、副本合并后）统一包一层调用。
2. **`windows-policy.yaml`** 把 `tool_paths` 四个字段从写死的开发机路径改为空串，并补了一段说明自动探测语义的注释。

调用链：`app.py` lifespan 与 `SandboxManager.__init__` 调 `policy_reader.load_policy()` → `_resolve_tool_paths()` 填充 → `collect_preinstall_paths()`（`app.py:291`）展开预装集 → `ensure_windows_setup()`。运行时 `_create_windows`（`process.py:2939`）从 `self.policy.windows.filesystem.tool_paths` 取目录拼子进程 PATH。

---

## 探测逻辑分析

### python_dir：`sys.executable` 反推 🟢

`policy_reader.py:52-58`。取 `sys.executable` 的父目录，并校验同目录存在 `python.exe` 才写入。这是最稳的一条：agent-server 进程就是用 OfficeAce 预制 python 跑的，`sys.executable` 直接是 `OfficeAce/tools/python/python.exe`，反推准确，且用 `is_file()` 守护避免误填。开发环境 venv 场景下 `sys.executable` 指向 `.venv\Scripts\python.exe`，注释（`windows-policy.yaml:136`）也说明了这一点——venv 下 python_dir 会指向 venv 的 Scripts 目录，符合"agent-server 自带解释器"的设计意图。

### node_dir：向上遍历找 `tools/node` 🟡

`policy_reader.py:61-68`。逻辑是 `py_dir.parent` + `py_dir.parents` 全部祖先，找 `ancestor/node/node.exe`。

- 对 OfficeAce 结构 `<root>/tools/python` → `<root>/tools/node`，正确（parent 即 `tools`，命中即 break）。
- **问题**：`(py_dir.parent, *py_dir.parents)` 会一直遍历到文件系统根（`C:\`、`D:\`）。理论上若某层祖先恰好有个名为 `node` 且含 `node.exe` 的目录，会被误命中。OfficeAce 结构下第一跳就 break，实际风险低，但缺乏"只看 parent（即 `tools` 同级）"的收紧，遍历范围过大。建议限定为只检查 `py_dir.parent`（即 `tools/python` 的父目录 `tools`），或最多回溯 1-2 层。

### git_dir / bash_path：从 PATH 上的 git.exe 反推 🟡

`policy_reader.py:71-82`。用 `shutil.which("git")` 拿到 `git.exe` 全路径，再向上找含 `usr/bin/bash.exe` 的祖先作为 `git_dir`，同时填 `bash_path`。

- 逻辑合理：Git for Windows 的 `git.exe` 多在 `<root>/cmd` 或 `<root>/bin`，`git_dir` 期望是含 `usr/bin/bash.exe` 的安装根，用 `usr/bin/bash.exe` 存在性判定根，正确。
- **风险点**：`shutil.which("git")` 受**进程 PATH** 控制。box-server 进程的 PATH 若被篡改（例如用户改了环境变量、或父进程注入），探测到的 git 可能指向非标准位置甚至被替换的 `git.exe`。这是路径可信度问题（见安全节）。`git_dir` 一旦写入，会被加进子进程 PATH 前缀（`process.py:2942`、`win_exec.py:90`）和读 ACL 预装（`collect_preinstall_paths:1051`），影响面不小。
- 兜底：检测不到 `git.exe` 或祖先链无 `bash.exe` 时留空，注释明确"OfficeAce 包未必带 git，需用户装"，合理。

### 探测失败兜底 🟢

每个字段独立 try/守护，失败（`OSError`、`which` 返回 None、文件不存在）则该字段不填，留空。留空后下游依赖系统 PATH + 默认预装目录兜底（`windows-policy.yaml:128-129` 注释）。`_resolve_tool_paths` 整体在 `sys.platform != "win32"` 或 `AttributeError` 时直接返回原 policy，非 Windows 平台零影响。整体兜底策略合理，不会因探测失败阻断启动。

### 探测结果如何写入策略 🟢

`policy_reader.py:87-94`。用 pydantic `model_copy(update=...)` 逐层不可变拷贝（`tp → fs → windows → policy`），不修改原对象，符合 pydantic 不可变约定。填充字典为空时直接返回原 policy（`policy_reader.py:84-85`），无副作用。只对空字段填（每处都有 `if not (tp.xxx or "").strip()` 守护），显式配置不被覆盖——这是正确的"基底+副本显式优先于自动探测"语义。

### 跨用户/权限下探测可靠性 🟡

- `python_dir`/`node_dir` 基于 `sys.executable`，与运行账户无关，跨用户一致。✓
- `git_dir` 基于 `shutil.which("git")`，依赖**当前进程 PATH**。若 box-server 以服务账户（如 `jbx-sandbox` 或 LocalSystem）运行，其 PATH 可能与交互登录用户不同 → 探测到的 git 可能与用户预期不一致。这只影响 git 工具可用性（git 非沙箱核心，OfficeAce 包默认不带），可接受，但应在日志中体现来源。
- 运行时 box-server 是普通用户进程，无权改外部目录 ACL，所以 `tool_paths` 目录的读 ACL 必须由 install 阶段（管理员）预装。见下方"问题与风险"第 1 条的预装不一致问题。

### windows-policy.yaml +15 行 🟢

注释清晰说明了自动探测的触发时机（`load_policy` 时）、探测来源（`sys.executable` 反推 + PATH 检测）、不覆盖显式值、开发环境 venv 行为。四个字段改空串合理。注释与 `policy_reader.py` 实现一致。

### 性能与缓存 🟡

- 性能：`_resolve_tool_paths` 每次调用做几次 `is_file()`/`which()`，开销极小（毫秒级文件 stat），可接受。
- **缓存**：`load_policy()` 本身**无缓存**，每次调用都重新读 YAML + 合并 + 探测。当前调用方各自缓存结果（`SandboxManager.__init__:228` 存 `self.policy`、`app.py` lifespan 调两次）。这与 MEMORY 记录的"box-server root policy load-once（策略启动时缓存）"一致——改策略需重启 box-server。`_resolve_tool_paths` 的探测只在 `load_policy()` 被调用时发生，而 `load_policy()` 主要在启动时调，故探测实质上也是"启动时一次性"。可接受，无需额外缓存。但 `app.py` lifespan 里调了**两次** `load_policy()`（`:285` 和 `:319`），各探测一遍，轻微冗余但无害。

---

## 关键代码检视

```python
# policy_reader.py:52-58  python_dir 探测 — 带存在性校验, 合理
if not (tp.python_dir or "").strip():
    try:
        py_dir = str(Path(sys.executable).parent)
        if Path(py_dir, "python.exe").is_file():
            filled["python_dir"] = py_dir
    except OSError:
        pass
```

```python
# policy_reader.py:61-68  node_dir 向上遍历 — 遍历到根, 范围过大
if not (tp.node_dir or "").strip() and filled.get("python_dir"):
    py_dir = Path(filled["python_dir"])
    for ancestor in (py_dir.parent, *py_dir.parents):   # <- 到文件系统根
        cand = ancestor / "node"
        if (cand / "node.exe").is_file():
            filled["node_dir"] = str(cand)
            break
```

```python
# policy_reader.py:71-82  git_dir 从 PATH 反推 — 受进程 PATH 影响
if not (tp.git_dir or "").strip():
    git_exe = shutil.which("git")          # <- 依赖进程 PATH
    if git_exe:
        git_path = Path(git_exe)
        for ancestor in (git_path.parent, *git_path.parents):
            if (ancestor / "usr" / "bin" / "bash.exe").is_file():
                filled["git_dir"] = str(ancestor)
                if not (tp.bash_path or "").strip():
                    filled["bash_path"] = str(ancestor / "usr" / "bin" / "bash.exe")
                break
```

```python
# policy_reader.py:182-202  三个返回点统一包一层 — 调用点完整, 无遗漏
return _resolve_tool_paths(base_policy)              # 基底直用
...
return _resolve_tool_paths(base_policy)              # 副本读失败
...
return _resolve_tool_paths(                          # 副本合并后
    self.policy_engine.merge_policy(base_policy, override_data)
)
```

---

## 优点

1. **解决真问题**：基底 YAML 不再写死开发机路径，wheel 跨机器可用，是打包发布的必要修复。
2. **只填空字段、不覆盖显式配置**：`if not (tp.xxx or "").strip()` 守护到位，用户显式配置优先，自动探测是纯补充。
3. **不落盘**：用 `model_copy` 内存填充，符合 `load_policy` 不生成合并文件的既有机制，不污染基底/副本文件。
4. **兜底完整**：每字段独立守护，失败留空，下游依赖系统 PATH 兜底；非 win32 直接返回，零副作用。
5. **注释质量高**：函数 docstring + yaml 注释把探测来源、OfficeAce 结构假设、开发环境 venv 行为都说清楚了，可维护性好。
6. **三个返回点全覆盖**：`load_policy` 的所有返回路径都包了 `_resolve_tool_paths`，无遗漏。

---

## 问题与风险

### 🔴 P1 — install 提权子进程绕过探测，导致 ACL 预装与运行时填充不一致

`win_setup.py:866` 的 `--force --policy-path <yaml>` 重装路径调 `_load_policy_preinstall_paths(policy_path)`（`win_setup.py:585`），该函数**直接 `yaml.safe_load` 读原始 YAML**，不经 `policy_reader`，故读到 `tool_paths` 全是空串 → 预装集不含任何工具目录。

而 `app.py:285-291` lifespan 的首次安装路径走的是 `policy_reader.load_policy()`（经 `_resolve_tool_paths` 填充）→ `collect_preinstall_paths()` 算出填充后的预装集。**两条路径不一致**：

- 首次 lifespan 安装：预装了 `python_dir`/`node_dir` 对应目录的读 ACL（因为走填充后的 policy）。✓
- 手动 `--force --policy-path` 重装：`_load_policy_preinstall_paths` 读原始 YAML 空 `tool_paths` → 预装集丢失工具目录 → 重装后受限 token 读不了 OfficeAce `tools/python` → `CreateProcessAsUserW` WinError 2/5。

用户按 `win_setup.py:862` 注释提示"改 tool_paths 后 --force 重装"时，恰好命中此坑（虽然本 commit 后 tool_paths 默认空、靠自动探测，但用户若显式填了路径再重装，就会丢失）。

**建议**：`_load_policy_preinstall_paths` 应复用 `PolicyReader.load_policy()`（或抽一个共享的"加载并解析"函数），保证 install 与 runtime 读到同一份填充后的 tool_paths。

### 🟡 P2 — node_dir 向上遍历到文件系统根，范围过大

`policy_reader.py:64` `for ancestor in (py_dir.parent, *py_dir.parents)` 会遍历到盘符根。OfficeAce 结构下第一跳即命中并 break，实际误命中概率低；但理论上若 `python_dir` 不在标准 `tools/python` 下（如 venv 场景 `.../.venv/Scripts`），遍历会一路向上，任何祖先下碰巧有个 `node/node.exe` 都会被填为 `node_dir`，可能指向意外的 Node 安装。

**建议**：限定只查 `py_dir.parent`（即 `tools/python` 的父目录 `tools`），或最多回溯 1 层。OfficeAce 结构就是 `tools/python` + `tools/node` 同级，不需要深层回溯。

### 🟡 P3 — 探测路径未做规范化与可信校验

`filled` 里写入的路径直接取自 `str(Path(...))` 或 `shutil.which` 返回值，未做：
- **规范化**：未 `.resolve()`，可能含符号链接/相对成分/混合分隔符。下游 `collect_preinstall_paths` 和 `process.py` 对这些路径直接 `os.path.join` 拼 PATH、`os.path.dirname` 取父目录，未规范化可能导致比对去重（`collect_preinstall_paths` 用字符串去重）失真、ACL 预装路径与运行时路径字符串不一致而误判"新增"。
- **可信校验**：`shutil.which("git")` 返回的路径完全由进程 PATH 决定。若 box-server 进程 PATH 被篡改（环境变量注入、父进程构造），`git_dir`/`bash_path` 会指向攻击者控制的目录，进而被加进沙箱子进程 PATH 前缀（`process.py:2942`）和读 ACL 预装集。虽 `git` 非沙箱核心且可被显式配置覆盖，但属于路径可信度隐患。

**建议**：
- 对 `filled` 里的路径统一 `os.path.realpath`/`Path.resolve()` 规范化后再写入。
- 对 `git_dir` 这类来自 PATH 的路径，至少在日志中标出来源（`via PATH`），便于审计；或限制只接受 ProgramFiles/SystemRoot 下的 git 作为可信来源。

### 🟡 P4 — 无路径注入风险，但无显式防御

四个字段都是路径字符串，拼进子进程 PATH（`win_exec.py:90`、`process.py:2942`）和作为 `os.path.join` 参数。路径来自 `sys.executable`/`shutil.which`（系统 API，不含用户可控输入），**当前无注入风险**。但若未来副本 YAML 允许用户填 `python_dir`，`_expand_path`（`policy.py:15-17`）虽 expandvars/expanduser，但未做 CRLF/null/控制字符/路径穿越校验（`policy.py:20-37` 有这些 helper 但**未在 tool_paths 字段上调用**）。属于潜在防御缺口，非本 commit 引入，提请注意。

### 🟢 P5 — lifespan 调两次 load_policy，轻微冗余

`app.py:285` 和 `:319` 各调一次，各触发一遍探测。开销极小（几次 stat），但可合并为一次取变量。非问题，仅整洁性。

---

## 改进建议（按优先级）

1. **[高]** 修 P1：让 `win_setup._load_policy_preinstall_paths` 改走 `PolicyReader` 加载（或抽公共函数），使 install 提权子进程与 runtime 用同一份"探测后"的 tool_paths，避免 `--force` 重装丢失工具目录预装。
2. **[中]** 修 P2：`node_dir` 遍历限定为只查 `py_dir.parent`（`tools` 同级），不要遍历到根。
3. **[中]** 修 P3：`filled` 写入前对路径 `resolve()` 规范化；`git_dir` 日志标注 `via PATH` 来源。
4. **[低]** 修 P5：`app.py` lifespan 合并两次 `load_policy()` 为一次。
5. **[低]** 修 P4（防御性）：考虑在 `WindowsToolPaths.expand_paths` validator 里加 `_contains_crlf_or_null` 校验，防御未来用户副本 YAML 注入。

---

## 小结

本 commit 是 Windows 沙箱跨机器可用性的必要修复，方向正确、实现克制（只填空字段、不落盘、兜底完整）、注释优秀。三个返回点统一包裹、pydantic 不可变拷贝、非 win32 短路，工程素养在线。主要问题集中在 **install 与 runtime 探测路径不一致（P1，🔴）**——这是会导致 `--force` 重装后沙箱工具不可用的实际功能缺陷，应优先修复。其余为遍历范围（P2）、路径规范化与可信校验（P3）的收紧建议，风险可控。整体可合入，P1 建议作为后续 follow-up 立即修。
