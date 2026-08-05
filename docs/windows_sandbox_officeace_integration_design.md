# Windows 沙箱接进 officeAce 产品 — 设计文档

> 创建：2026-07-22
> 状态：G1（拉起层）+ G0/G2/G3（执行层 ACL/PATH/venv）已实现（2026-07-24，开发态语法自检过 + review 修正）；待 Windows 机器端到端实测 + ACL 粒度收窄
>
> **2026-07-24 review 修正**：
> - 关停顺序：`finally` 段改为先 `shutdown_jiuwenbox_sandboxes`（DELETE，box-server 活着）再 `runner.stop()`（停子进程）。原"先停子进程再清缓存"是反的（停了子进程 DELETE 全失败）。见 §4.2 动作 3。
> - 端口分配：新增 `_allocate_jiuwenbox_port(host, preferred)`，preferred 被占则 bind(0) 选随机空闲端口。runner 自身不做端口分配（develop 那套未移植），在调用点补。见 §4.2 动作 2。
> - 留 Windows 实测：`proc.terminate()` 在 Windows 是 TerminateProcess（即时强杀，不给 uvicorn 跑 lifespan shutdown），有活 sandbox 时可能成孤儿——属 §8.1 Q4 / 实测收窄。
> 关联：`docs/windows_sandbox_review_fix_design.md`（Windows 沙箱 review 修复，已落地）
> 关联 commit：`04b3d3a97 feat:window 沙箱`（2026-07-21，Windows 分支诞生）
> 方案 B 依据来源：`jiuwenclaw_bk`（develop 分支）`jiuwenswarm/server/sandbox/jiuwenbox_runner.py`（现成、自包含、Windows 兼容，见 §1.7）
>
> **G1 实现清单（2026-07-24）**：
> - `jiuwenclaw/config.py`：`_VALID_SANDBOX_STARTUP_MODES` 加 `"internal"`；默认改 `"internal"`；`_normalize_sandbox_startup_mode` 接受 `internal`（逻辑未改，集合已含）；注释从"不实现"→"agent-server spawn 实现"。
> - `pyproject.toml`：照搬 develop 三件套（`package-dir` + `where=[".","jiuwenbox/src"]` + `include=["jiuwenclaw*","jiuwenbox*"]`）+ `jiuwenbox`/`jiuwenbox-server` console script + `package-data` jiuwenbox configs。**已实测**：`pip wheel` 产物含 jiuwenbox 全部 6 顶层子包 + 5 configs yaml + entry_points.txt。
> - `jiuwenclaw/agentserver/jiuwenbox_runner.py`（新增，移植）：整文件移植自 develop；`_sync_terminate` 加 `if sys.platform=="win32"` 分支（`proc.terminate()`→`proc.kill()`，§8.1 Q4）；新增 `_resolve_jiuwenbox_configs_dir` + `JiuwenBoxRunner.resolve_policy_path` classmethod（policy 路径解析，仓库内/site-packages 双兜底）。
> - `jiuwenclaw/app_agentserver.py`：新增 `_ensure_jiuwenbox_internal()` helper（`startup_mode==internal` 且 `enabled` 时调 `ensure_running` + url 回写 `JIUWENCLAW_SANDBOX_URL`）；`_run` 启动链在 `server.start()` 后调之；`finally` 段在 `shutdown_jiuwenbox_sandboxes()` 之前加 `await JiuwenBoxRunner.instance().stop()`。
>
> **G0/G2/G3 实现清单（2026-07-24，Windows-only，待实测）**：
> - G0 shell ACL：**无需改 yaml**——`windows-policy.yaml::read_acl_preinstall` 已含 `%SystemRoot%`/`%ProgramFiles%`，覆盖 `System32`/`Git`（安装期预装读 ACL）。
> - G0 PATH 注入：`sandbox_manager.py` 新增 `_build_windows_exec_env()`，`exec_in_sandbox` Windows 分支调之，给子进程 env 补 PATH（`<venv>\Scripts` + `Git\bin` + `System32` + `WindowsPowerShell\v1.0` + 打包 python 目录），使裸名 `bash`/`python`/`cmd` 可解析。
> - G2 ACL：`process.py:_create_windows` 从 `os.environ` 读 `JIUWENBOX_BUNDLED_PYTHON`（加 `allow_read`）+ `JIUWENBOX_VENV_DIR`（加 `allow_write`），动态注入 `apply_sandbox_acl`；`_ensure_jiuwenbox_internal` 在 `ensure_running` 前注入这两个 env（用 `pip_env.ensure_runtime_venv()`/`resolve_base_python()`）。
> - G3 命令改写：**决策为不做内层命令改写，靠 G0 PATH 前置 `<venv>\Scripts` 让 `python`/`pip` 裸名解析到 venv python**。理由：agent-core 在 Windows 送的 command 形态是 `["cmd","/c",inner]`/`["powershell","-Command",inner]`（`command[0]` 是 `cmd`/`powershell` 不是 `python`），改写 `command[0]` 无意义，改写内层 inner 字符串里的 `python` 需解析 shell 语法（正则易误伤）。PATH 前置 venv\Scripts 在 cmd/powershell/bash 下都成立且稳。与 §4.3 "改写后 command[0] 是绝对路径"的描述有出入，记录此决策；实测若 PATH 解析有歧义再回退到内层改写。

## 0. 背景：为什么需要这份文档

officeAce.exe 是一个 **Windows 桌面产品**，由三个仓库共同构成：
- `./jiuwenclaw` — 桌面编排 + AgentServer/Gateway + runtime venv/pip 机制
- `./agent-core`（`openjiuwen`）— LLM agent 框架 + sys_operation（local/sandbox 双模式）
- `../relay-claw` — 前端 + Windows 安装器打包脚本（`build-windows-installer.mjs`）

历史上 officeAce **没有沙箱能力**：`jiuwenbox` 只有 Linux 路径（bwrap/landlock/seccomp），而 officeAce 跑在 Windows，Linux 沙箱用不上。officeAce 的"隔离"仅靠 `jiuwenclaw/runtime/pip_env.py` 提供的 **LOCAL 模式 isolation_venv**（在用户工作区建一个 virtualenv，把 LLM 的 `pip install`/`python` 命令改写到 venv 里跑），这是进程级软隔离，不是操作系统级沙箱。

2026-07-21 的 commit `04b3d3a97 feat:window 沙箱` 一次性给 `jiuwenbox` 新增了完整的 Windows 沙箱执行层（`win_exec/win_setup/win_acl/win_job/win_proxy/win_wfp` + `process.py:_create_windows` + `app.py` win32 lifespan 分支），共 +4575 行。经全链路核查，**box-server 内部的 Windows 沙箱执行逻辑是完整的**（`app.py` win32 lifespan 能起、`ProcessRuntime.create` 第一行就分流 `_create_windows`、七个 `win_*` 模块齐全），**缺的不是执行层，而是"把 box-server 进程本身接进 officeAce 的拉起层"**——当前 jiuwenclaw 把 `startup_mode` schema 收窄到只允许 `external`（无人 spawn box-server），所以这套 Windows 执行能力没机会被触发。本文档定义把拉起层接通所需的工作。

## 1. 现状核查（基于真实代码，非推测）

### 1.1 Windows 沙箱是刚加的、纯新增

```
$ git log --diff-filter=A --oneline -- jiuwenbox/src/jiuwenbox/supervisor/win_exec.py
04b3d3a97 feat:window 沙箱   (2026-07-21 14:09, lby, +4575 行/14 文件)
```

`win_*` 全部 7 个模块 + `windows-policy.yaml` + `process.py` 的 `_create_windows` 分支 + `app.py` win32 lifespan 分支，**全部诞生于这一个 commit**。其后两个 commit（`f22c9bb74 fix:review`、`a8a7f9fc8 review2`）仅做 review 修复。

### 1.2 commit 之前 jiuwenbox 是纯 Linux 件（铁证）

旧版 `process.py`（commit 父版本）唯一的平台判断：
```python
if not sys.platform.startswith("linux"):
    logger.debug("PR_SET_CHILD_SUBREAPER unavailable on %s", sys.platform)
```
非 Linux 即 no-op，**不存在 Windows 执行路径**。旧版 `app.py` 零 Windows 代码。因此 officeAce 历史上技术上不可能用上 jiuwenbox。

### 1.3 box-server 的拉起层级（officeAce → jiuwenclaw → agent-server）

**正确层级**：officeAce.exe 只负责拉起 jiuwenclaw；box-server 由 **jiuwenclaw 的 agent-server 负责（该）拉起**，不是 officeAce 主进程。

- `jiuwenclaw/app.py:29-35`：officeAce 入口用 `sys.executable -m jiuwenclaw.app_agentserver` / `-m jiuwenclaw.app_gateway` 拉起 jiuwenclaw 的两个进程。**officeAce 不碰 box-server**。
- `desktop_app.start_services()`（`jiuwenclaw/desktop_app.py:429`）只拉 `app` + `web`，同样不碰 box-server。
- box-server 的 spawn 责任在 **agent-server 侧**（`app_agentserver.py` / `interface_deep.py` 的 sysop 组装链）。`interface_deep.py:2299-2344` 的 `_create_sys_operation` 是 agent-server 消费 sandbox endpoint 的真实点：读 `get_sandbox_endpoint()` 的 `url`/`type`，造 `sysop_card`。`startup_mode` 当前只在日志出现（`:2311`），**没被消费做 spawn**——印证 internal 未实现。

**`internal` 模式不是要新增的，是 jiuwenclaw config 主动禁掉的**：
- `jiuwenclaw/config.py:1171` `_VALID_SANDBOX_STARTUP_MODES = ("external",)`，`_DEFAULT_SANDBOX_STARTUP_MODE = "external"`。
- `config.py:1302-1313` `_normalize_sandbox_startup_mode` 注释明文："显式拒绝 `internal`……`internal`（agent-server 自动拉起 jiuwenbox 子进程）在本工程内不实现，留下名字徒增歧义，故 schema 收窄"。即：**`internal` 语义在 jiuwenbox README 里已定义**（"agent-server 启动时自动 spawn `jiuwenbox-server` 子进程并落盘最终生效的 `url`"，`jiuwenbox/README_CN.md:560`），但 jiuwenclaw 这一层把 schema 收窄到只允许 `external`，所以当前不 spawn。
- `external` 模式（当前唯一允许）：box-server 由外部独立托管（K8s Deployment/sidecar），agent-server 只通过 `JIUWENCLAW_SANDBOX_URL` 健康检查 + HTTP 调用，不 spawn。

agent-core 侧 `jiuwenbox.py` provider 也只读 `endpoint.base_url` 用 HTTP 连一个**已存在**的 box-server（`agent-core/.../providers/jiuwenbox.py:558-561`），不负责启动。

**对设计的影响**：R1 的正确动作不是"officeAce 新增 internal 模式拉起 box-server"，而是"放开 jiuwenclaw config 对 `internal` 的拒绝 + 在 agent-server 侧移植一个现成的 box-server 子进程管理器（见 §4.2）"。spawn 主体是 agent-server，officeAce 仍只拉 jiuwenclaw。

### 1.7 现成的拉起实现已在 develop 的 jiuwenswarm 里（方案 B 的依据）

核查 `jiuwenclaw_bk`（develop 分支）发现其 `jiuwenswarm/server/sandbox/jiuwenbox_runner.py` 已实现完整的 agent-server 内部拉起 box-server 逻辑——单例 `JiuwenBoxRunner`，`ensure_running(host, port, startup_mode, policy_path)` 在 `internal` 模式下 `asyncio.create_subprocess_exec(sys.executable, "-m", "uvicorn", "jiuwenbox.server.app:app", ...)` 拉起 box-server 子进程，配套：`PR_SET_PDEATHSIG` 父死子退、`JIUWENBOX_POLICY_PATH` 注入、仓库内源码 `PYTHONPATH` 注入（免 `pip install -e`）、`/health` 轮询就绪、stderr 滚动留尾、atexit 兜底终止、policy 路径变更自动重启。触发点是其 `agent_ws_server._handle_sandbox_enable`（`/sandbox enable` 命令）。

**方案 B = 移植这一个文件**。可行性已逐项验证：

| 验证项 | 结论 |
|---|---|
| `jiuwenbox_runner.py` 自包含度 | **真自包含**：仅 stdlib（asyncio/atexit/contextlib/logging/os/signal/sys/time/pathlib/typing）+ `httpx`，**零 jiuwenswarm 自引用**，可整文件拷到 `jiuwenclaw/agentserver/` |
| box-server 侧接口对接 | 依赖的契约（`GET /health`、`JIUWENBOX_POLICY_PATH` env、`jiuwenbox.server.app:app` 模块路径、lifespan shutdown 调 `shutdown_all_sandboxes`）**当前仓库 `jiuwenbox/server/app.py` 全部具备**（`/health` 在 `app.py:549`，lifespan 在 `:249`，shutdown 调用 `:423`），移植后客户端直接对接得上 |
| 当前仓库 jiuwenbox-server 能否在 Windows 跑 | **能**：`app.py` lifespan 有 `if sys.platform=="win32"` 分支调 `win_setup.ensure_windows_setup` + 起 `win_proxy` task；`ProcessRuntime.create`（`process.py:1733`）第一行 `if sys.platform=="win32": return await self._create_windows(...)`；`_create_windows`（`process.py:2766`）完整存在；`win_acl/win_constants/win_exec/win_job/win_proxy/win_setup/win_wfp` 七模块齐全 |
| runner 的 Linux-only 点 | `preexec_fn`/`prctl`/`ctypes.CDLL("libc.so.6")` 全部用 `if sys.platform.startswith("linux")` 守住（`jiuwenbox_runner.py:59,301`），Windows 上 no-op，不炸 |
| runner 挂载点 | `app_agentserver.py:165-191` 的 `finally` 段已调 `shutdown_jiuwenbox_sandboxes()`（删远端 sandbox 实例），旁边加 `JiuwenBoxRunner.instance().stop()` 即可；`atexit` 已注册（`:76`）与 runner 自身 atexit 兜底并存不冲突 |
| `startup_mode` 放开影响面 | 仅 `config.py:1410 get_sandbox_endpoint()` 一处读，下游 `interface_deep.py` 用于配指纹；放开成 `("internal","external")` 不炸别的 |

**移植要修的一个明确小点**：runner 的 `_sync_terminate`（atexit 兜底，`jiuwenbox_runner.py:414/433`）用 `os.kill(pid, signal.SIGTERM/SIGKILL)`，Windows 上 `SIGTERM` 不被识别（等同强杀、且不给 uvicorn 跑 lifespan shutdown 的机会）。主关停路径 `stop()` 用的是 `proc.terminate()/proc.kill()`（asyncio subprocess 跨平台 API），这条没问题；`_sync_terminate` 需在 Windows 上改成 `proc.terminate()` 风格或 `os.kill(pid, signal.CTRL_BREAK_EVENT)`。属单函数内 ~10 行平台分支，不构成本质障碍。

**触发点差异（移植要补的部分）**：当前仓库**没有** `/sandbox enable` 命令（jiuwenswarm 有整套 `/sandbox enable/disable/status/files`），sandbox 启用纯靠 env/yaml（`interface_deep.py:2304` 读 `runtime.enabled` + `endpoint.url/type`）。移植后 `ensure_running` 的触发点放在 AgentServer 启动时（或首次需要沙箱的 sysop 组装阶段），不照搬 jiuwenswarm 的 `/sandbox enable` 命令路径——后者是一套额外的交互命令，超出本次接入范围。

### 1.4 Windows 沙箱执行命令用的 python 解释器

`win_exec.py:238` runner 命令构造：
```python
py = sys.executable or "python"
parts = [py, "-m", "jiuwenbox.supervisor.win_exec", "runner", ...]
```
`win_setup.py:363/671` 安装子进程同理用 `sys.executable`。**用的是 box-server 进程自己的 python，不是 officeAce 打包的那个**——因为 box-server 不在 officeAce 进程树里。

用户子命令（`win_exec._handle_exec_request:684` → `CreateProcessAsUserW(command)`）原样透传 `command`，`command[0]` 是 `python`/`node` 裸名还是绝对路径取决于上层（agent-core）拼的命令。

### 1.5 officeAce 已有的"自带 python + venv + pip 改写"资产（LOCAL 模式）

`jiuwenclaw/runtime/pip_env.py` 是一套**完整且可用**的机制，目前在 LOCAL 模式（`command_tools.py:293-294` 调用）下工作：

| 能力 | 函数 | 说明 |
|---|---|---|
| 打包 python 发现 | `_discover_bundled_python()` | 查 `<安装根>/tools/python/python.exe`（Windows）/ `tools/python/bin/python3`（Linux） |
| 基座 python 解析 | `resolve_base_python()` | 优先级：`JIUWENCLAW_BASE_PYTHON` env → `sys.executable` → 打包 python → 系统 python |
| venv 创建 | `ensure_runtime_venv()` → `_create_runtime_venv_dir()` | 用 `virtualenv.run.cli_run` 建 `isolation_venv`，**重新 seed pip**（embeddable python 自带无 pip） |
| 命令改写 | `rewrite_shell_command()` / `_rewrite_pip_segment()` | 把 LLM 的 `pip install` / `python xxx` / `uv pip` 改写指向 venv 内 python |
| 子进程环境 | `runtime_subprocess_env()` | 注入 `VIRTUAL_ENV` / `PATH`(prepend venv Scripts) / `PYTHONPATH`(prepend venv site-packages) |
| 版本冲突预警 | `check_command_install_warnings()` | 同一 venv 多 agent 共享，提示版本覆盖风险 |

打包侧：officeAce 用 **Python embeddable runtime**（`relay-claw/scripts/build-windows-installer.mjs:193` 下载 `python-3.x-embed-amd64.zip`，解压到 `tools/python/python.exe`，`:1126/1179/1221` 多处引用）。**安装后目录形态**：`D:\Files\OfficeAce\tools\python\` 是 embeddable python 根，jiuwenclaw 与 openjiuwen 都以 **wheel** 形式（`pip install *.whl --no-deps`）装进 `tools\python\Lib\site-packages\` ——不是 PyInstaller frozen exe。relay-claw 的 `scripts/build-jiuwenclaw-wheel.mjs` 把 jiuwenclaw 源码打成 `jiuwenclaw-*.whl`，`scripts/install-python-wheelhouse.ps1` 按 `packaging/windows/python-runtime-wheelhouse.json` manifest 把各 wheel 装进 embeddable python 的 site-packages。virtualenv 库同理随 wheel 进 site-packages，为 `_create_runtime_venv_dir` 服务。

**关键结论：officeAce 侧的"自带 python + venv + pip 改写"链路是完整的，但只在 LOCAL 模式跑；jiuwenbox Windows 沙箱侧完全没接上这套资产。**

### 1.6 openjiuwen 与 jiuwenbox 的真实关系（解"没 jiuwenbox 为何没失败"）

先回答一个看似矛盾的问题：officeAce 依赖 openjiuwen（`pyproject.toml` 从 git 装 `openjiuwen`），openjiuwen 里有 jiuwenbox provider，但 officeAce 打包产物里没有 jiuwenbox 包——为什么没失败？核查三个事实后矛盾消解：

**事实 A：openjiuwen 的 jiuwenbox provider 是纯 HTTP 客户端，不 import jiuwenbox 包本体。**
`agent-core/openjiuwen/extensions/sys_operation/sandbox/providers/jiuwenbox.py`：`import httpx`（`:18`），`_JiuwenBoxClient` 全用 httpx 发 HTTP（`:302-310`），错误处理用 `httpx.HTTPStatusError`（`:223-237, :484`）。**全文零 `import jiuwenbox`/`from jiuwenbox`**。它只是个连 box-server 的 HTTP 客户端，名字叫 jiuwenbox 纯属历史命名，对 jiuwenbox 包无任何进程内依赖。openjiuwen 打进 dist（spec 已 `copy_metadata("openjiuwen")`）即够了。

**事实 B：openjiuwen 默认 `sandbox_type="mock"`，jiuwenbox provider 注册但不必然使用。**
`agent-core/openjiuwen/core/sys_operation/config.py:55` `sandbox_type: str = Field(default="mock", ...)`。`gateway.py:78` 在 gateway 初始化时无条件 `import ...providers.jiuwenbox`（`# noqa: F401`）触发 `@SandboxRegistry.provider` 装饰器**注册**它，但注册 ≠ 使用——`sandbox_registry.py:62-71` 的 `create_provider(sandbox_type=...)` 只在显式传 `sandbox_type="jiuwenbox"` 时才实例化该 provider。默认 mock 时它闲置。

**事实 C：officeAce 的 jiuwenclaw 层把默认改成 jiuwenbox，但"用 jiuwenbox"只意味着"连 box-server"，不是"import jiuwenbox 包"。**
`jiuwenclaw/config.py:1180` `_DEFAULT_SANDBOX_TYPE = "jiuwenbox"`。但这只是让 openjiuwen 选 HTTP 客户端去连一个 box-server 进程，**不是进程内 import jiuwenbox 包**。而 §1.3 已证：officeAce 进程树从不启动 box-server（`startup_mode` 只接受 `external`，无人在 8321 起 box-server）。所以要么 provider 闲置（沙箱任务没触发），要么 HTTP 连不上失败——**那是任务级失败，不是 officeAce 启动/打包失败**，主进程照常跑。

**矛盾的真正解释（"该缺却没缺"不成立）**：

| 层次 | 需不需要 jiuwenbox 包 | 原因 |
|---|---|---|
| openjiuwen 连 box-server | **不需要** | jiuwenbox provider 是纯 httpx 客户端，随 openjiuwen wheel 进 site-packages |
| box-server 进程本体 | **需要** | 跑 box-server = `python -m uvicorn jiuwenbox.server.app:app`，要 import jiuwenbox 包 |
| officeAce 历史为何没失败 | — | 从不跑 box-server 进程（external + 无人起 box-server），所以从不 import jiuwenbox 包，自然不缺 |

**当前 jiuwenclaw 打 wheel 为何不含 jiuwenbox**：`jiuwenclaw/pyproject.toml` 的 `[tool.setuptools.packages.find]` 是 `where=["."]` + `include=["jiuwenclaw*"]`——只收扁平布局的 `jiuwenclaw` 顶包，而 jiuwenbox 是 **src-layout**（包根在 `jiuwenbox/src/jiuwenbox/`，独立 `jiuwenbox/pyproject.toml`），三件套（`package-dir` 重映射 / `where` 加 `jiuwenbox/src` / `include` 加 `jiuwenbox*`）全缺，所以打 wheel 收不进 jiuwenbox。develop（`jiuwenclaw_bk`）的 pyproject 正是靠这三件套让 jiuwenbox 随 jiuwenswarm wheel 一起出去（见 §8.1 Q1 决策），照搬即可。

结论：不是"该缺却没缺"，而是"**之前用不到所以没缺，现在要用了才开始缺**"。只有当我们新增 `internal` 模式让 officeAce 自己拉起 box-server 子进程时，才第一次需要让 jiuwenbox 随 jiuwenclaw wheel 装进 site-packages。这是 §8 Q1 的真正由来。

## 2. 四个 Gap（要接通的事）

> 顺序即依赖前置：G0 不通，G2/G3 都无从谈起——命令字符串连"被解析"这一步都过不了。

| # | Gap | 现状 | 影响 |
|---|---|---|---|
| **G0** | **沙箱缺 shell（最前置）** | agent-core `jiuwenbox.py:1827` 把命令包成 `["bash","-lc",command]` 送沙箱执行；但 Windows 沙箱无 bash（`win_*.py`/`windows-policy.yaml` grep 全空，`jbx-sandbox` 空 PATH）；cmd/powershell 也未 ACL 授权 | **任何** LLM 命令字符串都跑不了（`bash` 找不到，`CreateProcessAsUserW` 失败），G1/G2/G3 全失去前提 |
| G1 | box-server 桌面部署模型缺失 | 只有 `external` 模式，officeAce 桌面单机场景没有"自动拉起 box-server"的编排 | 桌面用户开沙箱任务会因连不上 8321 而失败 |
| G2 | 沙箱 python 运行时未对接打包 python | `win_exec.py:238` 裸 `sys.executable`（= box-server 的 python，打包态是 `tools/python/python.exe`），用户命令里的 `python`/`node` 靠 `jbx-sandbox` 低权用户 PATH 解析（通常没有）+ DACL 拦截 | LLM 生成 `python xxx` / `pip install` 在沙箱里必然失败（找不到解释器或无权执行） |
| G3 | `pip install` 无落点 | 无 venv、无可写 site-packages；即便 python 能跑，`pip install` 也写不进任何可写区 | 装包任务失败 |

## 3. 需求

### 3.1 功能需求

- **R0（G0，最前置）**：沙箱执行 LLM 命令字符串所需的 shell（cmd / powershell / bash）必须**复用宿主机同一批二进制**（与 LOCAL 模式 `command_tools.py` 用 `shutil.which` 找到的同一份），通过 NTFS ACL 授权 `jbx-sandbox` 读+执行 + 给子进程 PATH 注入 shell 目录，使 agent-core 的 `["bash","-lc",command]`（或 cmd/powershell 等价形态）在沙箱内能解析并执行。不打包额外 shell。
- **R1（G1）**：放开 jiuwenclaw config 对 `startup_mode: internal` 的拒绝（`_VALID_SANDBOX_STARTUP_MODES` 加回 `"internal"`），并在 **agent-server 侧移植现成的 box-server 子进程管理器**（§1.7 已核实 `jiuwenclaw_bk` 的 `jiuwenswarm/server/sandbox/jiuwenbox_runner.py`，方案 B）：把该文件整文件移植到 `jiuwenclaw/agentserver/`，agent-server 启动时若 `startup_mode==internal` 调 `JiuwenBoxRunner.instance().ensure_running(...)` 拉起 box-server 子进程，spawn 后把实际生效的 `url`（端口被占自动换）落盘/env 回写，供 `get_sandbox_endpoint` 与 agent-core provider 使用；进程退出时在 `app_agentserver.py` 的 `finally` 段调 `stop()`。officeAce 仍只负责拉起 jiuwenclaw，不碰 box-server。K8s/企业 `external` 部署形态（外部独立托管 box-server）保持不变。
- **R2（G2）**：沙箱内执行命令时，python 解释器来源 = officeAce 打包的 embeddable python（`tools/python/python.exe`），不依赖用户是否安装系统 python。改写后命令带绝对路径，runner 不依赖 `jbx-sandbox` PATH 解析 `python`。
- **R3（G3）**：LLM 在宿主机侧生成的命令（如 `python -m pip install xxx`、`python xxx.py`）送入沙箱执行时，能成功落在一个**宿主机持久、跨任务/跨沙箱复用**的 venv 里（命令在进沙箱前改写为指向该 venv 的绝对路径）。venv 放宿主机 officeAce 工作区，**首次创建后所有后续任务复用**，避免每次任务重建；装包/跑码不污染宿主系统 site-packages、不串台其他沙箱（串台风险靠 `pip_env.check_command_install_warnings()` 预警）。
- **R4**：复用 `jiuwenclaw/runtime/pip_env.py` 已有的命令改写 + venv 创建资产，不在沙箱侧重造轮子。
- **R5（硬约束）**：**不修改 Linux 沙箱的任何实现**。Linux 沙箱现在是什么样，就保持什么样——`process.py` 的 Linux 分支、`bwrap.py`、`landlock*.py`、`seccomp.py`、`network.py`、`cgroup.py`、`daemon_ipc.py`、`sandbox_daemon.py`、Linux 侧 policy 模板（`default-policy.yaml`/`code-agent-policy.yaml` 等），以及 `app.py` 中非 win32 的 lifespan 分支，一律不动一行、不改逻辑、不"顺手优化"。所有新增能力只能在 Windows 分支与 officeAce/box-server 的编排层落地，禁止以任何理由触碰 Linux 代码路径。

### 3.2 非功能需求

- 桌面单机场景：box-server 用 UDS 或 127.0.0.1 loopback，不暴露到外网。
- box-server 子进程随 officeAce 主进程退出而退出（对齐 Linux 的 `--die-with-parent` 语义，Windows 用 Job Object `KILL_ON_JOB_CLOSE`）。
- 性能：venv 创建是重操作（virtualenv seed pip 数秒级），放宿主机**首次创建后跨任务/跨沙箱复用**（`ensure_runtime_venv` 检 `pyvenv.cfg` 存在即跳过），避免每次任务重建。

## 4. 技术方案

### 4.0 G0：沙箱 shell 来源（最前置，对齐 LOCAL 模式）

**问题根因**：LLM 在宿主机生成的是**命令字符串**（`python -m pip install xxx`、`cd foo && python y.py`），含 `&&`/`|`/`$VAR`/引号等 shell 语法。agent-core `jiuwenbox.py:1827` 不自己解析，直接包成 `["bash","-lc",command]` 送进沙箱，**指望沙箱里有 `bash` 解析并 exec**。当前 Windows 沙箱既无 bash 二进制、也未给 `jbx-sandbox` 授权读任何 shell、PATH 为空——`bash` 裸名无处解析，命令在"被解析"这一步就失败，G2/G3 无从谈起。

**对齐原则**：LOCAL 模式 officeAce **不打包任何 shell**，全靠 `shutil.which()` 在宿主机 PATH 里找：
- powershell：`pwsh`→`powershell`→`powershell.exe`（Windows 系统自带）
- cmd：`subprocess(shell=True)` 走 `cmd.exe`（Windows 系统自带）
- bash：`shutil.which("bash")`（**用户装了 Git for Windows 才有**，`C:\Program Files\Git\bin\bash.exe`）

沙箱模式完全沿用这套——**不引入打包 shell**，只让 `jbx-sandbox` 能"访问到"宿主机同一批 shell 二进制。

**方案：两件事（ACL 授权 + PATH 注入）**

① **NTFS ACL 授权 shell 目录**（`win_acl.apply_sandbox_acl`，在 `_create_windows`）：

| shell | 宿主典型路径 | 授权 | 说明 |
|---|---|---|---|
| powershell / cmd / 系统 dll | `C:\Windows\System32`（及 `WindowsPowerShell\v1.0`） | `allow_read` 递归 | cmd/powershell + 其加载的系统 dll 都在此；递归授权避免逐个 dll 缺漏 |
| bash（若装了 Git） | `C:\Program Files\Git` 整树 | `allow_read` 递归 | bash.exe 依赖 mingw/msys dll（`Git\usr\bin\...`、`Git\mingw64\...`），**必须整树授权**，否则 dll 加载失败 |

两目录纳入 `windows-policy.yaml` 的 `read_acl_preinstall`（安装期预装读 ACL），避免运行时才发现无权读。

② **给 `jbx-sandbox` 子进程 PATH 注入**（`win_exec._create_process_as_user`，env block `:540-544`）：

runner 用 `CreateProcessWithLogonW(LOGON_WITH_PROFILE)` 起，`jbx-sandbox` 拿到自己的 profile 环境（PATH 默认空）。agent-core 送的 `command[0]` 是裸名 `bash`，靠子进程 PATH 解析。故 box-server 须在每个用户子进程 env 里**注入等价 PATH**：
```
PATH = <venv>\Scripts
     ; C:\Program Files\Git\bin     # bash 裸名解析
     ; C:\Windows\System32           # cmd/powershell 裸名解析
     ; <打包 python 目录>            # python 裸名兜底（改写已带绝对路径，此为保险）
```
注入逻辑复用 LOCAL 模式 `pip_env.runtime_subprocess_env()`（已做 venv Scripts prepend PATH），沙箱入口在它基础上补 shell 目录。等价于 LOCAL 模式 `shutil.which` 靠宿主 PATH 找 shell——沙箱没有 `shutil.which`，由 box-server 把"等价 PATH"塞进每个子进程 env。

**与 LOCAL 模式的对齐关系**：

| 维度 | LOCAL 模式 | 沙箱模式（对齐后） |
|---|---|---|
| shell 二进制来源 | 宿主机（Git for Windows / 系统 powershell/cmd） | **同一份**宿主机二进制 |
| 如何找到 shell | `shutil.which` 走 box-server 进程 PATH | `jbx-sandbox` PATH（box-server 注入）+ ACL 授权读 |
| 命令执行 | `subprocess.run([bash,"-lc",cmd])` 宿主进程 | runner `CreateProcessAsUserW([bash,"-lc",cmd])` 在 `jbx-sandbox` 受限 token 下 |
| python 来源 | `pip_env` venv 内 python（宿主工作区） | 同一 venv（ACL 授权 `jbx-sandbox` 读写） |
| 隔离 | 无（进程直接宿主跑） | Restricted Token + Job + ACL |

差异只在"执行身份和隔离"，shell/python 二进制**完全复用宿主机同一份**。

**bash 强依赖 Git for Windows（决策：未装 Git 走 cmd/powershell）**：
- LOCAL 模式 bash 本就依赖用户装了 Git for Windows；沙箱沿用同一前提——装了 Git 时优先用 bash。
- **未装 Git 的机器**：按 `sys.platform` 退化为 cmd/powershell（与 LOCAL 模式 `command_tools._resolve_execution_plan` 的 `auto` 逻辑一致：命令含 powershell 语法 → powershell，否则 cmd）。即 agent-core `jiuwenbox.py` provider 在 Windows 上须按命令语法选 `cmd /c` 或 `powershell -Command` 替代 `bash -lc`。
- **决策落地范围**：这是 agent-core `jiuwenbox.py` provider 的改动（按 `sys.platform=="win32"` 选 shell），属跨仓库。本设计文档定方向、不动 agent-core 代码；实现时按此决策在 agent-core 侧落地。
- **语法差异风险（已接受）**：LLM 生成的 bash 语法（`&&`/`||`/`$VAR`/here-doc）在 cmd 下不通用——cmd 的复合用 `&`/`&&` 部分兼容，但 `$VAR`→`%VAR%`、here-doc 不支持。powershell 语法更接近但仍有差异。本决策接受该风险：装了 Git 走 bash（语法一致，推荐）；未装 Git 走 cmd/powershell（语法差异由 LLM 命令生成侧或任务约束承担，不在沙箱层兜底）。

> **与 R5 的关系**：G0 全部改动落在 Windows 分支（`_create_windows` ACL + `win_exec` env）与 `windows-policy.yaml`，不碰 Linux。

### 4.1 方案选型

核心决策点：**沙箱内的 venv 放在哪一层建、用哪套 python 基座。**

| 选项 | venv 建在哪 | python 基座 | 评价 |
|---|---|---|---|
| A. box-server 内建 per-sandbox venv | box-server 在每个沙箱可写区建 venv，runner 起 python 指向它 | 打包 python | 隔离最强，但 box-server 要发现打包 python + 复制 venv 资产，跨进程耦合重 |
| B. officeAce 侧建 venv，路径透传沙箱 | 复用 `pip_env` 在 officeAce 侧建 venv，把 venv python 路径 + 可写区 bind/授权进沙箱 | 打包 python | 复用现成资产，但 venv 跨 officeAce↔jbx-sandbox 用户可见性需 ACL 放行 |
| C. 沙箱内复用 officeAce venv | 沙箱直接用 officeAce 已建的 `isolation_venv` | 打包 python | 最省事，但破坏"沙箱私有、销毁即清"语义，多沙箱串台 |

**推荐 B**：venv 由 officeAce 侧建（复用 `pip_env`，基座 = 打包 embeddable python），通过 box-server policy 把 venv 目录 + 打包 python 目录以 `allow_read`（python 执行）+ `allow_write`（venv site-packages 写入）授权给 `jbx-sandbox`，并把命令改写发生在送命令进沙箱**之前**（agent-core/box-server 边界）。

理由：(1) 最大复用 `pip_env` 现成 200+ 行逻辑，不重造；(2) venv 物理上在 officeAce 用户工作区，但通过 NTFS ACL 精确授权给 `jbx-sandbox` 只读写必要子树，隔离性由 ACL 保证而非物理分离；(3) 打包 python 目录只读授权，venv 目录读写授权，符合最小权限。

> 备选 A 留作后续硬化方向（若 B 的 ACL 跨用户授权在生产环境出现权限边界问题，再下沉到 box-server 内建）。

### 4.2 G1：放开 internal 模式 + 移植 JiuwenBoxRunner（方案 B）

**核心认知（§1.3/§1.7 已核实）**：`internal` 模式**不是新增**——jiuwenbox README 已定义其语义（"agent-server 启动时自动 spawn `jiuwenbox-server` 子进程并落盘最终生效的 `url`"），是 jiuwenclaw config 主动把 schema 收窄到 `("external",)` 才禁掉的。且 spawn 责任在 **agent-server**，不在 officeAce 主进程。更关键的是：**现成的 spawn 实现已经在 develop 分支的 jiuwenswarm 里写好了**（`jiuwenclaw_bk/jiuwenswarm/server/sandbox/jiuwenbox_runner.py`），且经验证自包含、接口对得上、Windows 兼容（§1.7）。故方案 B = 移植这一个文件，**不自己重写 spawn 逻辑**。

**三步动作**：

**动作 1 — 放开 config 拒绝**（`jiuwenclaw/config.py`）：
- `_VALID_SANDBOX_STARTUP_MODES = ("external", "internal")`（加回 `internal`）。
- `_normalize_sandbox_startup_mode` 接受 `internal` 不再抛 `ValueError`；注释从"本工程内不实现"改为指向 agent-server 移植 runner 实现。
- `_DEFAULT_SANDBOX_STARTUP_MODE`：桌面/单机默认 `internal`，K8s/企业部署默认 `external`（按部署形态区分，不按 `sys.platform`）。

**动作 2 — 移植 `jiuwenbox_runner.py` 到 `jiuwenclaw/agentserver/`**：
- 整文件拷贝 `jiuwenclaw_bk/jiuwenswarm/server/sandbox/jiuwenbox_runner.py` → `jiuwenclaw/agentserver/jiuwenbox_runner.py`（自包含，零 jiuwenswarm 依赖，拷过来即可用，仅做下述 Windows 小修）。
- 该 runner 暴露单例 `JiuwenBoxRunner.instance()`，核心方法 `ensure_running(host, port, *, startup_mode, policy_path)`（拉起/复用/重启 box-server 子进程 + `/health` 健康检查）、`stop()`（优雅关停，给 uvicorn 60s grace 跑 lifespan shutdown）、`is_owned_listener`/`get_owned_endpoint`（端口归属判断）、`fetch_health`。
- spawn 命令是 `python -m uvicorn jiuwenbox.server.app:app --host <host> --port <port>`（runner 内已构造，`sys.executable` 起），policy 经 `JIUWENBOX_POLICY_PATH` 注入，仓库内 `jiuwenbox/src` 自动注入 `PYTHONPATH`（免 `pip install -e`）。
- **Windows 小修（移植时必做）**：runner 的 `_sync_terminate`（atexit 兜底，原文件 `:414/433`）用 `os.kill(pid, signal.SIGTERM/SIGKILL)`，Windows 上 `SIGTERM` 不被识别。主关停路径 `stop()` 用 `proc.terminate()/proc.kill()`（跨平台 asyncio API，没问题）；`_sync_terminate` 加 `if sys.platform == "win32"` 分支改用 `proc.terminate()` 风格。其余 `preexec_fn`/`prctl`/`libc.so.6` 已被 `sys.platform.startswith("linux")` 守住，Windows no-op，无需改。
- **触发点（移植时补）**：当前仓库无 `/sandbox enable` 命令（jiuwenswarm 有整套交互命令，超出本次范围），sandbox 启用靠 env/yaml。故在 AgentServer 启动链（`app_agentserver.py` 的 `_run` 启动阶段，或首次 sysop 组装前）按 `startup_mode==internal` 调 `ensure_running`；不照搬 jiuwenswarm 的 `/sandbox enable` 命令路径。
- spawn 用的 `python` = agent-server 的 `sys.executable`（officeAce 打包态 = `tools/python/python.exe`，site-packages 已含 jiuwenbox（§8.1 Q1）可 `import jiuwenbox`；开发态用 uv 装的 jiuwenbox 子包，runner 的 `PYTHONPATH` 注入兜底）。
- **端口分配（移植时补，2026-07-24 review）**：runner 的 `ensure_running` 把 `port` 原样传给 uvicorn `--port`，**自身不做端口分配**（develop 那套 `_allocate_internal_jiuwenbox_port` 在 `agent_ws_server`，未移植）。故在调用点 `_ensure_jiuwenbox_internal` 补 `_allocate_jiuwenbox_port(host, preferred)`：socket bind 探测 preferred（8321 或 url 里的），被占则 bind(0) 让 OS 选随机空闲端口。存在 TOCTOU race（测完到 uvicorn 起之间被占），best-effort —— 真撞上 runner 内部 uvicorn 会失败，已有 warning 兜底。
- spawn 后 `ensure_running` 内部已做 `/health` 轮询就绪；url 落盘/env 回写（`JIUWENCLAW_SANDBOX_URL`）在调用点用 `runner.base_url`（含分配后实际端口）经 `set_local_config` 写 `ENV_CONFIG_DICT`，使 `get_sandbox_endpoint()`（同进程经 `get_local_config` 读）与 agent-core provider 拿到一致 url。runner 不直接落盘 jiuwenclaw config。
- `external` 模式：runner 的 `ensure_running` 只做健康检查不 spawn（runner 已内置该分支），行为与现状一致。

**动作 3 — 关停挂载**（`jiuwenclaw/app_agentserver.py`）：
- 在现有 `finally` 段（`app_agentserver.py:165-191`，已调 `shutdown_jiuwenbox_sandboxes()` 删远端 sandbox 实例）**之后**加 `await JiuwenBoxRunner.instance().stop()`。**关停顺序（重要，2026-07-24 review 修正）**：先 `shutdown_jiuwenbox_sandboxes`（HTTP DELETE 清本进程 provider 缓存里的 sandbox_id，**必须 box-server 还活着才能响应**），再 `runner.stop()` 停 box-server 子进程。两步顺序：缓存清理（DELETE）在先，子进程关停在后。若反过来先停子进程，DELETE 会全失败（被 warning 吞不崩，但沙箱没正常清理）；box-server 进程退出时其 lifespan shutdown 会兜底调 `shutdown_all_sandboxes` 清漏网的沙箱。external 模式下 `runner.stop()` no-op，顺序对两种模式都成立。
- Windows graceful shutdown 限制（留实测）：Windows 上 `proc.terminate()`=`TerminateProcess`（即时强杀），不给 uvicorn 跑 lifespan shutdown 的机会（Linux `terminate()`=SIGTERM 才 graceful）。有活 sandbox 时沙箱进程可能成孤儿，留 Windows 实测时定（属 §8.1 Q4 / 实测收窄性质）。
- runner 自带 `atexit.register(_sync_terminate)` 兜底，与 `app_agentserver.py:76` 已有的 atexit 并存不冲突。

**生命周期绑定**：box-server 子进程是 agent-server 的子进程（runner 用 `asyncio.create_subprocess_exec` 起，父进程是 agent-server）。Linux 上靠 runner 的 `PR_SET_PDEATHSIG=SIGTERM`（父死子退）；Windows 上靠 atexit 兜底 + 动作 3 的 `stop()` 显式关停。officeAce 退出时 `app.py:_terminate_all` 终止 agent-server，agent-server 的 `finally` 段再 `stop()` 终止 box-server（两层级联，officeAce 不直接管 box-server）。对齐 Linux `--die-with-parent` 语义。

**与 §8.1 Q1 的关系**：agent-server spawn `python -m uvicorn jiuwenbox.server.app:app`，要求那个 python 能 `import jiuwenbox`。已决策（§8.1 Q1）让 jiuwenbox 随 jiuwenclaw wheel 一起装进 embeddable python 的 `site-packages/jiuwenbox/`（改 `jiuwenclaw/pyproject.toml` 三件套），故打包态 officeAce 的 `tools/python/python.exe` 可 `import jiuwenbox`。开发态靠 runner 的 `PYTHONPATH` 注入让 uv 装的 jiuwenbox 子包直接可用，可先跳过打包验证联调。

**与 R5 的关系**：放开 `internal` 是改 jiuwenclaw config（Linux/Windows 共享 schema），但 Linux 上 agent-server 是否实际走 internal 由部署决定；Linux 沙箱实现（`process.py` Linux 分支等）一行不改。移植来的 `jiuwenbox_runner.py` 是 agent-server 侧的进程管理，不碰 Linux 沙箱执行层。Windows 分支的 ACL/PATH 改动同样不碰 Linux。

> **不引入** "officeAce 进程内 import box-server 跑 uvicorn" 的内嵌方案：box-server 用了 `prctl`/`bwrap` 等 Linux 原语概念、且 Windows 侧有 Job Object 句柄管理，进程隔离更稳，符合现有架构。runner 维持"子进程 spawn"形态，与 jiuwenswarm 现有实现一致。

### 4.3 G2：沙箱 python 运行时对接打包 python

**问题**：runner 用 `sys.executable`（box-server 自己的 python），用户命令里的 `python` 裸名靠 `jbx-sandbox` PATH（没有）。

**方案**：两层对接。

**层 1 — runner 基座 python**：box-server 桌面模式下，由 officeAce 拉起时把打包 python 路径通过 env 传入：
- `app.py` 拉起 box-server 时 `env["JIUWENBOX_BUNDLED_PYTHON"] = <tools/python/python.exe 绝对路径>`（用 `pip_env.resolve_base_python()` 解析，已含打包 python 发现）。
- `win_exec.py:238` 改为：`py = os.environ.get("JIUWENBOX_BUNDLED_PYTHON") or sys.executable or "python"`。
  - 注意：runner 本体（`python -m jiuwenbox.supervisor.win_exec runner`）仍需能 import jiuwenbox 包，所以 runner 基座**不能换成 officeAce 的 embeddable python**（embeddable 无 site、无 jiuwenbox 包）。runner 继续用 box-server 的 `sys.executable`（即 officeAce 的 `tools/python/python.exe`——其 site-packages 已含 jiuwenbox（§8.1 Q1），或开发态 box-server 安装 python），**只把打包 python 暴露给沙箱用户命令**。

**层 2 — 用户命令里的 python/pip 改写**：在命令送进沙箱**之前**改写，复用 `pip_env`：
- 改写点：box-server 的 `exec_in_sandbox`（`sandbox_manager.py:687`）收到 `request.command` 后，对 Windows 分支调 `pip_env.rewrite_shell_command()`（或其沙箱适配版），把 `python`/`pip install` 改写成 venv 内 python 的绝对路径。
- 改写后 `command[0]` 是绝对路径（如 `<工作区>/isolation_venv/Scripts/python.exe`），runner `CreateProcessAsUserW` 直接起，不依赖 `jbx-sandbox` PATH。

**ACL 授权**（`win_acl.apply_sandbox_acl`，在 `_create_windows:2793`）：
- 打包 python 目录 `tools/python/` → `allow_read`（含 Execute，ACE 给合成 SID）。
- venv 目录 `<工作区>/isolation_venv/` → `allow_write`（pip 写 site-packages）。
- shell 目录（`System32`/`Git\`）→ `allow_read`（**见 §4.0 G0**，shell 是 python 命令能被解析的前提）。
- 以上目录均加入 `read_acl_preinstall`（安装期预装读 ACL），避免运行时才发现无权读。
- PATH 注入（`win_exec._create_process_as_user` env）补 venv Scripts + shell 目录（见 §4.0），使改写后绝对路径之外的裸名也能兜底解析。

### 4.4 G3：venv + pip 落点（宿主机持久，跨任务复用）

**venv 归属与生命周期**：venv **放在宿主机 officeAce 工作区**（`<用户工作区>/isolation_venv`，与 LOCAL 模式 `pip_env` 同一目录、同一实例），**不在沙箱内、不随沙箱销毁**。理由：
- 命令由 LLM 在宿主机侧生成，沙箱只是执行器；venv 作为"装包落点"属于 officeAce 资产，逻辑上属于命令生成侧，不属于沙箱执行侧。
- virtualenv seed pip 是秒~十秒级重操作，per-task/per-sandbox 建不可接受；放宿主机**首次创建后跨所有后续任务/沙箱复用**，零重建开销。
- 直接复用 `pip_env.ensure_runtime_venv()`（`pip_env.py:210`）：检查 `pyvenv.cfg` 存在即跳过重建，只在首次建一次，进程内 `_venv_ready` 缓存——沙箱模式与 LOCAL 模式共用同一 venv，行为一致。

**pip 命令落点**：LLM 在宿主机生成 `python -m pip install xxx` → 进沙箱前在 `exec_in_sandbox` 改写为 `<venv>/Scripts/python.exe -m pip install xxx` → runner `CreateProcessAsUserW` 以受限 token 跑 → pip 写进宿主机 `<venv>/Lib/site-packages`（已 `allow_write` 授权给 `jbx-sandbox`）→ **不碰宿主系统 site-packages**。

**隔离性保证**（venv 虽在宿主机，但不破坏沙箱隔离）：
- 文件隔离：`jbx-sandbox` 对 venv 目录的访问完全由 NTFS ACL 控制——只授权 venv 子树读写 + 打包 python 目录只读，其余宿主路径 deny。沙箱内进程够不到 venv 之外的宿主文件。
- 进程隔离：runner + 用户子进程仍受 Restricted Token + Job Object 约束，与 venv 物理位置无关。
- 串台预警：多任务/多沙箱共享同一 venv 的版本冲突，靠 `pip_env.check_command_install_warnings()` 在装包前预警（已有机制，复用）。

**与 Linux 沙箱的语义差异**（明确记录，非缺陷，不改 Linux）：
- Linux 沙箱：宿主 python + site-packages 全 ro bind 进沙箱，`pip install` 默认写不进（ro fs），即"不可变共享"。
- Windows 沙箱：打包 python ro 授权 + 宿主机 venv rw 授权给 `jbx-sandbox`，`pip install` 写进宿主机 venv，即"可写复用区 + 不可变基座"。两者都是"基座不可变 + 用户包进隔离区"，Windows 因无 bwrap ro-bind 改用 ACL 表达，且 venv 在宿主机以支持跨任务复用。

## 5. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| **shell 来源** | **复用宿主机 shell（cmd/powershell/bash），不打包** | 与 LOCAL 模式 `shutil.which` 同源；靠 ACL 授权 + PATH 注入让 `jbx-sandbox` 访问到；不增产品体积 |
| **bash 依赖 Git** | 装了 Git 优先 bash；未装 Git 按 `sys.platform` 退化为 cmd/powershell（改 agent-core `jiuwenbox.py` provider 按 `sys.platform=="win32"` + 命令语法选 shell） | 不强制运维装 Git；对齐 LOCAL 模式 `auto` 选 shell 逻辑；agent-core 侧落地（跨仓库，本设计定方向不动代码） |
| venv 建在哪层 | officeAce 侧（方案 B） | 复用 `pip_env` 200+ 行现成逻辑，不重造 |
| venv 粒度与生命周期 | 宿主机全局单 venv，跨任务/跨沙箱复用，不随沙箱销毁 | virtualenv 创建重，不能 per-task 建；命令由宿主机 LLM 生成，venv 属命令生成侧资产；隔离靠 ACL 不靠 venv 物理分离；与 LOCAL 模式同一 venv 实例 |
| python 基座 | 打包 embeddable python（`tools/python/python.exe`） | officeAce 产品自带，用户无需装系统 python |
| runner 基座 | box-server 的 `sys.executable`（不变） | embeddable 无 site，runner 需 import jiuwenbox 包 |
| box-server 部署 | **放开** jiuwenclaw config 对 `internal` 的拒绝（非新增），**移植** `jiuwenbox_runner.py`（方案 B，develop 已有现成实现） | `internal` 语义 README 已定义，config 主动禁了；develop 的 jiuwenswarm 已写好 spawn 逻辑且自包含、Windows 兼容，移植比自己重写风险低；spawn 责任在 agent-server 不在 officeAce；K8s `external` 不变 |
| box-server 监听 | TCP 127.0.0.1（runner 默认 8321，被占自动换随机端口） | runner 内置端口分配 + 健康检查；UDS 留作后续硬化方向（见 §8.1 Q2），非本次范围 |
| 命令改写位置 | box-server `exec_in_sandbox` 入口（送进沙箱前） | 改写后命令带绝对路径，runner 不依赖沙箱内 PATH（裸名兜底靠 PATH 注入） |
| 生命周期绑定 | Job Object `KILL_ON_JOB_CLOSE`，agent-server→box-server 两层级联 | 对齐 Linux `--die-with-parent`；officeAce 退→agent-server 退→box-server 退，officeAce 不直接管 box-server |

## 6. 影响面（铁律一：只动相关的事）

### 6.1 必改文件

| 文件 | 改动 | 大小 |
|---|---|---|
| `jiuwenclaw/config.py` | `_VALID_SANDBOX_STARTUP_MODES` 加 `"internal"`；`_normalize_sandbox_startup_mode` 接受 `internal`；桌面默认 `internal`、K8s 默认 `external`；改注释 | ~15 行 |
| `jiuwenclaw/agentserver/jiuwenbox_runner.py` | **新增文件（移植）**：整文件拷自 `jiuwenclaw_bk/jiuwenswarm/server/sandbox/jiuwenbox_runner.py`（自包含，仅依赖 stdlib+httpx）。唯一改动：`_sync_terminate` 加 `if sys.platform=="win32"` 分支（`os.kill(SIGTERM)`→`proc.terminate()` 风格） | ~500 行（移植+~10 行 Windows 修） |
| `jiuwenclaw/app_agentserver.py` | 新增 `_ensure_jiuwenbox_internal()` helper（`startup_mode==internal` 且 `enabled` 时调 `ensure_running` + `_allocate_jiuwenbox_port` 端口分配 + url 回写 `JIUWENCLAW_SANDBOX_URL` + 注入 `JIUWENBOX_BUNDLED_PYTHON`/`JIUWENBOX_VENV_DIR` env）；`_run` 启动链在 `server.start()` 后调之；`finally` 段顺序：先 `shutdown_jiuwenbox_sandboxes()`（DELETE，box-server 活着）再 `await JiuwenBoxRunner.instance().stop()`（停子进程） | ~40 行 |
| `jiuwenclaw/pyproject.toml` | **打包（§8.1 Q1 已决策）**：照搬 `jiuwenclaw_bk` 三件套让 jiuwenbox 随 jiuwenclaw wheel 一起装进 site-packages——`[tool.setuptools]` 加 `package-dir = {"jiuwenbox" = "jiuwenbox/src/jiuwenbox"}`；`[tool.setuptools.packages.find]` 改 `where=[".","jiuwenbox/src"]` + `include=["jiuwenclaw*","jiuwenbox*"]`；`[project.scripts]` 加 `jiuwenbox`/`jiuwenbox-server` 两个入口。relay-claw 的 `build-jiuwenclaw-wheel.mjs` 无需改（`pip wheel` 自动按改后 pyproject 收 jiuwenbox） | ~8 行 |
| `jiuwenbox/server/sandbox_manager.py` | **仅** `exec_in_sandbox` 内新增 `if sys.platform == "win32"` 分支调命令改写（复用 `pip_env`）；Linux 分支原样保留，一行不动 | ~15 行 |
| `jiuwenbox/server/runtime/process.py` | **仅** `_create_windows` 的 `apply_sandbox_acl` 增加打包 python + venv + **shell 目录**授权（`System32`/`Git\`）；`create`/`stop`/`is_running` 的 Linux 主体与 `if sys.platform=="win32"` 分流点之外不加任何代码 | ~25 行 |
| `jiuwenbox/supervisor/win_exec.py` | **仅** `_create_process_as_user` 的 env block 注入 PATH（venv Scripts + `Git\bin` + `System32` + 打包 python），使裸名 `bash`/`python` 可解析；两跳 spawn/Job/Token 核心**不动** | ~15 行 |
| `jiuwenbox/src/jiuwenbox/configs/windows-policy.yaml` | `allow_read`/`allow_write`/`read_acl_preinstall` 默认纳入 python+venv+shell 路径；**仅此 Windows policy 文件**，Linux policy 模板不动 | ~12 行 |

### 6.2 不改（显式声明，防扩散 —— 对应 R5 硬约束）

- **Linux 沙箱全部实现，一行不改**（R5 硬约束）：`process.py` 的 Linux 分支（`create`/`stop`/`is_running`/`_build_sandbox_bwrap_args`/`_wait_daemon_ready`/`_reap_zombies` 等）、`bwrap.py`、`landlock.py`/`landlock_launcher.py`、`seccomp.py`、`network.py`、`cgroup.py`、`daemon_ipc.py`、`sandbox_daemon.py`、Linux 侧 policy 模板（`default-policy.yaml`/`code-agent-policy.yaml`/`enterprise-policy.yaml`/`inference-policy.yaml`）、`app.py` 中非 win32 的 lifespan 分支。现在什么样就什么样，不修改、不重构、不优化、不顺手清理。
- `win_exec.py` 的两跳 spawn / Job / Restricted Token / pipe IPC 核心逻辑 —— 已 review 修复过，不动。
- `pip_env.py` 的 venv 创建 / 命令改写 / 版本预警核心逻辑 —— 复用，不改（仅在沙箱入口调它）。
- agent-core `jiuwenbox.py` provider —— 只读 `base_url`，`internal` 模式下 url 指向本地，provider 无感。
- K8s/企业 `external` 部署路径 —— 不动。

### 6.3 风险

| 风险 | 缓解 |
|---|---|
| ACL 跨用户授权（officeAce 用户 → `jbx-sandbox`）边界复杂 | 先用方案 B 落地，若生产出权限问题再切方案 A（box-server 内建 venv） |
| embeddable python 缺标准库完整子集，某些包装不上 | `pip_env` 已用 virtualenv（非 venv 模块），seed 更完整；若仍缺，打包时补 `tools/python` 的 stdlib |
| 共享 venv 多沙箱串台 | 复用 `check_command_install_warnings` 预警；后续可演进 per-sandbox venv（懒建+缓存） |
| box-server UDS 在 Windows 的兼容性 | launcher 已支持 `unix://`；Windows 10+ 支持 AF_UNIX，老系统降级 TCP 127.0.0.1 |

## 7. 开发步骤（拆解，每步可独立验证）

> 遵循 CLAUDE.md：先文档（本文档）→ 再步骤拆解（本节）→ 确认后写代码。
> 步骤 0（shell）最前置——它不通，后续 python/venv 都无从验证（命令字符串解析不了）。

### 步骤 0：沙箱 ACL 授权 shell + PATH 注入（G0，最前置）
- `process.py:_create_windows`：`apply_sandbox_acl` 增 `System32` + `Git\`（若存在）`allow_read` 递归。
- `win_exec.py:_create_process_as_user`：env block 注入 PATH（venv Scripts + `Git\bin` + `System32` + 打包 python）。
- `windows-policy.yaml`：`read_acl_preinstall`/`allow_read` 纳入 shell 目录。
- 验证：沙箱内 `jbx-sandbox` 执行 `bash -lc "echo ok"`（装了 Git）或 `cmd /c echo ok`（未装 Git，agent-core 已按 sys.platform 退化）成功，证明 shell 可达。

### 步骤 1：config 放开 `internal` 模式
- `jiuwenclaw/config.py`：`_VALID_SANDBOX_STARTUP_MODES` 加 `"internal"`；`_normalize_sandbox_startup_mode` 接受 `internal` 不再抛错；桌面默认 `internal`、K8s 默认 `external`；改注释（从"不实现"→"agent-server spawn 实现"）。
- 验证：单测覆盖 `internal`/`external` 两种模式 normalize 不抛错、默认值按部署形态正确。

### 步骤 2：移植 JiuwenBoxRunner + agent-server 接入
- **前置（§8.1 Q1 已决策）**：`jiuwenclaw/pyproject.toml` 照搬 `jiuwenclaw_bk` 三件套（`package-dir` + `where=[".","jiuwenbox/src"]` + `include=["jiuwenclaw*","jiuwenbox*"]` + 两个 console script）——让 jiuwenbox 源码随 jiuwenclaw wheel 一起装进 site-packages，否则打包态 runner spawn 的子进程 `import jiuwenbox` 会 `ModuleNotFoundError`。开发态（uv 装了 jiuwenbox 子包 + runner 的 `PYTHONPATH` 注入兜底）可跳过此步先联调。relay-claw 侧 `build-jiuwenclaw-wheel.mjs` 无需改。
- `jiuwenclaw/agentserver/jiuwenbox_runner.py`：整文件移植自 `jiuwenclaw_bk/jiuwenswarm/server/sandbox/jiuwenbox_runner.py`；唯一改动 `_sync_terminate` 加 Windows 分支（§8.1 Q4）。
- `jiuwenclaw/app_agentserver.py`：启动链按 `startup_mode==internal` 调 `JiuwenBoxRunner.instance().ensure_running(host, port, startup_mode="internal", policy_path=...)`，把 runner 返回的实际 host:port 落盘/env 回写 `JIUWENCLAW_SANDBOX_URL`；`finally` 段在 `shutdown_jiuwenbox_sandboxes()` 之前加 `await JiuwenBoxRunner.instance().stop()`。
- spawn 用的 `python` = agent-server 的 `sys.executable`（打包态 = `tools/python/python.exe`，site-packages 已含 jiuwenbox）。env 注入 `JIUWENBOX_BUNDLED_PYTHON`（用 `pip_env.resolve_base_python()`，供 runner 暴露打包 python 给沙箱）。
- 验证：开发态起 agent-server，`box-server` 子进程存在，`/health` 200，`JIUWENCLAW_SANDBOX_URL` 落盘正确；agent-server 退 box-server 也退（runner `stop()` + atexit 兜底）；officeAce 退→agent-server 退→box-server 退级联生效。打包态（dist）同样验证一次。

### 步骤 3：沙箱 ACL 授权打包 python + venv 目录
- `process.py:_create_windows`：`apply_sandbox_acl` 调用增加打包 python 目录 `allow_read` + venv 目录 `allow_write`。
- `windows-policy.yaml`：默认值纳入这两类路径。
- 验证：沙箱内 `jbx-sandbox` 能读执行 `<venv>/Scripts/python.exe`，能写 `<venv>/Lib/site-packages`。

### 步骤 4：exec 命令改写接入沙箱入口
- `sandbox_manager.py:exec_in_sandbox`：Windows 分支调 `pip_env.rewrite_shell_command(command)`（送进沙箱前改写）。
- 验证：LLM 生成 `pip install xxx`，沙箱内实际跑的是 `<venv>/Scripts/python.exe -m pip install xxx`，装包成功。

### 步骤 5：端到端联调 + 回归
- 端到端：officeAce 桌面 → 起沙箱 → LLM `python -m pip install requests && python -c "import requests"` 全程成功。
- 回归：Linux 沙箱路径全测（R5：一行不改，仅验证不回归）、LOCAL 模式 isolation_venv 全测、`external` K8s 模式配置解析测，均不回归。

## 8. 待确认问题

> 2026-07-24 复核：方案 B 定下 + 用户拍板后，原 7 条问题已逐条收敛。**真正阻塞写代码的前置项已清零**——其余为"实测收窄项"或"agent-core 侧落地项"，可在对应步骤边测边定。

### 8.1 已闭环 / 已决策（不再阻塞）

1. **box-server 在 officeAce 打包产物里的形态** —— **已决策：jiuwenbox 随 jiuwenclaw wheel 一起装进 site-packages（与 jiuwenclaw 同等存在），靠改 jiuwenclaw 的 pyproject 实现**。
   - 核实结论（保留备查）：officeAce 安装后是 embeddable python + wheelhouse 形态——`D:\Files\OfficeAce\tools\python\python.exe` 是 embeddable python，jiuwenclaw 与 openjiuwen 都以 wheel 装进 `tools\python\Lib\site-packages\`（`relay-claw/scripts/build-jiuwenclaw-wheel.mjs` 打 wheel，`install-python-wheelhouse.ps1` 装 wheel）。当前 jiuwenclaw wheel **不含 jiuwenbox**：`jiuwenclaw/pyproject.toml` 的 `[tool.setuptools.packages.find]` 是 `where=["."]` + `include=["jiuwenclaw*"]`，只收扁平布局的 jiuwenclaw 顶包；jiuwenbox 是 src-layout（`jiuwenbox/src/jiuwenbox/`，独立 `jiuwenbox/pyproject.toml`），三件套全缺。历史上没缺是因为 openjiuwen 的 jiuwenbox provider 是纯 httpx 客户端 + officeAce 从不启动 box-server（external + 无人起）。
   - **决策**：jiuwenbox 不单独打 wheel、不打包成 exe，作为 Python 源码**随 jiuwenclaw wheel 一起进 site-packages**——即在 `jiuwenclaw/pyproject.toml` 照搬 `jiuwenclaw_bk`（develop）的三件套配置：
     ```toml
     [tool.setuptools]
     package-dir = {"jiuwenbox" = "jiuwenbox/src/jiuwenbox"}        # 重映射 jiuwenbox 顶包到 src-layout 路径
     [tool.setuptools.packages.find]
     where = [".", "jiuwenbox/src"]                                 # find 在 . 和 jiuwenbox/src 两目录下找
     include = ["jiuwenclaw*", "jiuwenbox*"]                        # 把 jiuwenbox 纳入收包范围
     ```
     外加把 `jiuwenbox` / `jiuwenbox-server` 两个 console script 挂到 jiuwenclaw 的 `[project.scripts]`（照搬 `jiuwenclaw_bk` 行 122-123）。已验证（`find_packages(where='jiuwenbox/src', include=['jiuwenbox*'])`）：照搬后 jiuwenbox 的 8 个子包（`jiuwenbox`/`cli`/`models`/`proxy`/`server`/`server.routes`/`server.runtime`/`supervisor`）被完整收进 jiuwenclaw wheel，装进 `site-packages/jiuwenbox/` 后 embeddable python 可 `import jiuwenbox`，runner spawn 的 `python -m uvicorn jiuwenbox.server.app:app` 子进程直接可用。
   - 与 (B) 独立 `jiuwenbox-server.exe`、(C) embeddable python + 独立 site-packages **均不采纳**——用户不要单独 exe、不要额外 python 基座、不要单独打 jiuwenbox wheel。
   - 落地：§6.1 必改文件表 `jiuwenclaw/pyproject.toml` 行已记此改动；开发态（uv 装了 jiuwenbox 子包 + runner `PYTHONPATH` 注入兜底）可先跳过打包验证联调。relay-claw 侧无需新增打 wheel 脚本——`build-jiuwenclaw-wheel.mjs` 的 `pip wheel <source-dir>` 会自动按改后的 pyproject 把 jiuwenbox 一起打进去。

2. **UDS vs TCP 127.0.0.1** —— **已由方案 B 决定：TCP 127.0.0.1**。`jiuwenbox_runner.py` 内部用 `--host/--port` TCP（默认 8321，被占自动换随机端口），不走 UDS。不再待确认；UDS 可作后续硬化方向（若要省端口/不暴露 TCP 监听），非本次范围。

3. **bash 强依赖 Git for Windows** —— **已决策**：装 Git 优先 bash；未装按 `sys.platform=="win32"` + 命令语法退化 cmd/powershell（改 agent-core `jiuwenbox.py` provider 选 shell）。语法差异风险（bash `&&`/`$VAR`/here-doc 在 cmd 不通用）已接受。**落地在 agent-core 侧（跨仓库），不阻塞 G1/G2/G3**——本设计定方向不动 agent-core 代码。

4. **runner `_sync_terminate` Windows 适配** —— **已定解、实现时落地**（方案 B 引入的小修）：runner 的 `_sync_terminate`（atexit 兜底）用 `os.kill(pid, signal.SIGTERM/SIGKILL)`，Windows 上 `SIGTERM` 不被识别。主关停路径 `stop()` 用 `proc.terminate()/proc.kill()`（跨平台 asyncio API，没问题）；`_sync_terminate` 加 `if sys.platform == "win32"` 分支改用 `proc.terminate()` 风格。属单函数内 ~10 行平台分支，已写进 §4.2 动作 2 与 §6.1 必改文件表。

### 8.2 实测收窄项（不前置阻塞，在 G0/步骤 0/3 落地时边测边定）

> 这些是 ACL 授权粒度的收窄问题，默认建议已给（见 §4.0/§4.3）；按默认建议先落，实测若过宽/有遗漏再收窄。**不阻塞 G1，也不阻塞写代码开始**。

5. **venv 目录跨用户可见性**：officeAce 用户工作区路径，`jbx-sandbox` 默认无权访问。默认建议（§4.3）：`<工作区>/isolation_venv/` 整子树（含 Scripts/Lib/site-packages）`allow_write` 给 `jbx-sandbox`。实测验证覆盖完整即收口。

6. **打包 python 目录授权范围**：默认建议（§4.3）：`tools/python/` 整目录 `allow_read`（含 Execute）。收窄方向：仅 `python.exe` + `_pth` + stdlib 子树——实测启动所需文件清单后按最小权限收窄。

7. **shell 目录 ACL 授权粒度**：`System32` 整树 `allow_read` 默认建议（§4.0）是否过宽（暴露系统 dll 给 `jbx-sandbox`）。收窄方向：`WindowsPowerShell\v1.0` + `cmd.exe` 必需 dll 子集——需实测 powershell/cmd 启动所需 dll 清单。

8. **`Git\` 整树授权范围**：bash 依赖 mingw/msys dll 分散在 `Git\usr\bin`/`Git\mingw64`。默认建议（§4.0）：整树 `allow_read`（最稳但范围大）。收窄方向：列 bash 启动最小依赖集——实测后收窄。

### 8.3 结论

- **写代码前置项全部清零**：Q1 选型已定（源码随 jiuwenclaw 同等存在），Q2/Q3 由方案 B 决定，Q4 有定解。
- §8.2 四条是 ACL 授权粒度收窄，按默认建议先落、实测后收窄，不阻塞开工。
- §8.1 Q3（bash）属 agent-core 侧落地，不阻塞本仓库工作。

---

## 附录 A：officeAce 进程树（目标态）

```
officeAce.exe (frozen 入口) ── 只拉起 jiuwenclaw，不碰 box-server
  ├─ app_agentserver (sys.executable -m jiuwenclaw.app_agentserver)  ← agent-server
  │     └─ [startup_mode=internal] JiuwenBoxRunner.ensure_running spawn box-server 子进程:
  │           box-server (python -m uvicorn jiuwenbox.server.app:app --host 127.0.0.1 --port <8321或随机空闲>)
  │             ├─ asyncio: win_proxy (127.0.0.1:<port_range>, 共享)
  │             └─ 每沙箱: runner (CreateProcessWithLogonW jbx-sandbox, CREATE_SUSPENDED→Job→Resume)
  │                  └─ runner_main: 建 Restricted Token → 读 pipe 帧 → CreateProcessAsUserW(改写后的命令)
  │                       └─ agent-core 包成: ["bash","-lc","<venv>/Scripts/python.exe -m pip install xxx"]
  │                            └─ bash (宿主 Git for Windows, ACL 已授权 jbx-sandbox 读+执行, PATH 已注入) 解析字符串
  │                                 └─ exec 出 <venv>/Scripts/python.exe → pip 写 <venv>/Lib/site-packages (ACL allow_write)
  ├─ app_gateway (sys.executable -m jiuwenclaw.app_gateway)
  └─ web (前端代理)
```

> 层级：officeAce → jiuwenclaw(agent-server) → box-server → runner → bash → python。box-server 是 agent-server 的子进程（agent-server 用 `JiuwenBoxRunner` spawn），不是 officeAce 的子进程。officeAce 退→agent-server 退→box-server 退（runner `stop()` + atexit 兜底级联）。

> 注：agent-core `jiuwenbox.py` provider 把 LLM 字符串命令包成 `[<shell>, <flag>, command]` 送沙箱：装了 Git 用 `["bash","-lc",command]`；未装 Git 按 `sys.platform` 退化为 `["cmd","/c",command]` 或 `["powershell","-Command",command]`。故进程树里 shell 是 python 的父进程（解析层在 shell）。shell 二进制复用宿主机同一份（Git bash / 系统 powershell/cmd）。

## 附录 B：shell / venv / python 路径流转

```
打包时: build-windows-installer.mjs 下载 python-3.x-embed-amd64.zip → tools/python/python.exe
         build-jiuwenclaw-wheel.mjs 打 jiuwenclaw wheel (含 virtualenv 库 + jiuwenbox 源码, 见 §8.1 Q1)
         install-python-wheelhouse.ps1 把 wheel 装进 tools/python/Lib/site-packages/
         (shell 不打包: 复用宿主 Git for Windows bash + 系统 powershell/cmd)

运行时 (agent-server 启动, startup_mode=internal):
  agent-server 调 JiuwenBoxRunner.ensure_running spawn box-server
    (python -m uvicorn jiuwenbox.server.app:app --host 127.0.0.1 --port <8321或随机空闲>)
  runner 内部轮询 /health 至就绪, agent-server 把 runner 返回的实际 host:port 落盘/env 回写 JIUWENCLAW_SANDBOX_URL
  (spawn 用的 python = agent-server 的 sys.executable; 打包态 = tools/python/python.exe, site-packages 已含 jiuwenbox, 见 §8.1 Q1)

运行时首次起沙箱前:
  pip_env.resolve_base_python() → 发现 tools/python/python.exe
  pip_env.ensure_runtime_venv() → virtualenv 以 embeddable python 为基座建 <工作区>/isolation_venv (seed pip)

起沙箱 (_create_windows):
  apply_sandbox_acl 给 jbx-sandbox 授权:
    C:\Windows\System32\ (及 WindowsPowerShell\v1.0) → allow_read  [shell + 系统 dll]
    C:\Program Files\Git\ (若装了 Git)            → allow_read  [bash + mingw/msys dll, 整树]
    tools/python/                                   → allow_read (Execute)
    <工作区>/isolation_venv/                        → allow_write (pip 写 site-packages)
  win_exec._create_process_as_user 注入子进程 PATH:
    <venv>\Scripts ; Git\bin ; System32 ; tools\python

执行 LLM 命令:
  LLM(宿主机): "python -m pip install requests"
  → agent-core jiuwenbox provider 包成 ["bash","-lc", command]
  → box-server exec_in_sandbox (Windows) → pip_env.rewrite_shell_command → 内层 python 改写为 <venv>\Scripts\python.exe
  → runner CreateProcessAsUserW(["bash","-lc","<venv>\Scripts\python.exe -m pip install requests"])
  → bash 解析字符串 → exec python → pip 写 <venv>\Lib\site-packages (已授权) → 不碰宿主系统 site-packages
```
