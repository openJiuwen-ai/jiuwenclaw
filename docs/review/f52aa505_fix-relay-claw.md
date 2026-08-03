# 代码审查报告：fix:接入relay-claw

- **Commit**：`f52aa505671010fc1a406f4b04233dbcdb78d541`
- **作者/日期**：lby / 2026-07-24
- **规模**：6 文件，约 842 增 / 23 删
- **审查重点**：relay-claw 集成、agent-server 侧、jiuwenbox 沙箱子进程生命周期管理
- **审查方法**：`git show` 全量 diff + Read 真实代码（commit 版本逐行核对）+ 关联模块（pip_env.py / local_env_config.py / win_acl.py）签名核对

> 行号引用以该 commit 内 `git show <commit>:<path>` 的内容为准（与当前工作树略有差异——后续 commit 又增补了 win_proxy 端口清理与 policy 指纹逻辑，不在本 commit 审查范围）。

---

## 一、概述

本 commit 在 agent-server 侧引入“自动拉起本地 jiuwenbox-server 子进程”的能力（`startup_mode=internal`，默认值），让 Windows 桌面/单机部署场景下用户无需手动起 jiuwenbox-server。核心做法：

1. 新增 `jiuwenclaw/agentserver/jiuwenbox_runner.py`（570 行）：单例 `JiuwenBoxRunner`，用 `asyncio.create_subprocess_exec` 起 `python -m uvicorn jiuwenbox.server.app:app`，靠 `/health` 轮询确认就绪，`atexit` + `stop()` 双重兜底终止。
2. `app_agentserver.py`：在 `_run` 启动链 `server.start()` 后调 `_ensure_jiuwenbox_internal()`；`finally` 段先 HTTP DELETE 远端沙箱、再 `runner.stop()` 停子进程。
3. `config.py`：把 `startup_mode` 合法集从仅 `external` 扩为 `{internal, external}`，默认改为 `internal`。
4. `process.py` / `sandbox_manager.py`：box-server 侧读 `JIUWENBOX_BUNDLED_PYTHON` / `JIUWENBOX_VENV_DIR` 注入 ACL；Windows 给沙箱子进程补 PATH。
5. `pyproject.toml`：把 `jiuwenbox` 顶包随 `jiuwenclaw` wheel 一起打包发布。

整体方向正确、注释详尽、与 box-server 既有沙箱链路衔接合理。但存在若干并发竞态、资源泄漏、健壮性缺陷，下面按维度展开。

---

## 二、变更范围

| 文件 | 行数 | 作用 |
|---|---|---|
| `jiuwenclaw/agentserver/jiuwenbox_runner.py` | +570 新增 | relay-claw 运行器核心：spawn/health/stop/atexit |
| `jiuwenclaw/app_agentserver.py` | +167 | 启动链接入、端口分配、env 注入、关停顺序 |
| `jiuwenclaw/config.py` | +38 | `startup_mode` schema 放开，默认 internal |
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | +21 | `_create_windows` 读 env 注入 ACL allow_read/allow_write |
| `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py` | +47 | `_build_windows_exec_env` 给沙箱子进程补 PATH |
| `pyproject.toml` | +22 | jiuwenbox 随 wheel 打包、console script、package-data |

---

## 三、架构与设计概述

### 3.1 relay-claw 集成架构

```
agent-server (_run)
  └─ server.start() (AgentWebSocketServer)
  └─ _ensure_jiuwenbox_internal()  [commit 新增]
        ├─ 读 config sandbox.startup_mode / runtime.enabled
        ├─ _allocate_jiuwenbox_port(): socket bind 探测端口
        ├─ 注入 os.environ: JIUWENBOX_VENV_DIR / JIUWENBOX_BUNDLED_PYTHON
        └─ JiuwenBoxRunner.instance().ensure_running(internal, policy_path)
              └─ asyncio.create_subprocess_exec(python -m uvicorn jiuwenbox.server.app:app)
              └─ 轮询 http://127.0.0.1:<port>/health
              └─ set_local_config("JIUWENCLAW_SANDBOX_URL", actual_url) 回写
  └─ finally:
        ├─ shutdown_jiuwenbox_sandboxes()  # HTTP DELETE 给 box-server (清 provider 缓存)
        └─ JiuwenBoxRunner.instance().stop()  # 终止 uvicorn 子进程
```

- agent-core 的 sandbox provider 仍走 HTTP 调 box-server（`JIUWENCLAW_SANDBOX_URL`），relay-claw 只是把“box-server 由谁起”从“外部 Deployment”变为“agent-server 内部 spawn”。**架构合理**：与既有 box-server 沙箱链路解耦，provider 侧无感。
- internal/external 二态切换清晰，external 保持原 K8s 托管语义不变。

### 3.2 与 box-server 链路衔接

- 动态路径（打包 python / venv）经 env 注入 → `process.py:_create_windows` 读 env 拼 ACL，避免在 policy yaml 里写死每机不同的路径。**设计合理**。
- `sandbox_manager.py:_build_windows_exec_env` 给沙箱子进程补 PATH（venv\Scripts 优先），让裸名 `python`/`pip` 解析到 venv——与 ACL 授权点（G3）一致。**衔接正确**。

---

## 四、关键代码检视

### 4.1 进程生命周期管理

**spawn / 复用决策** `jiuwenbox_runner.py:278-311`

```python
owned_match = (
    self._process is not None
    and self._process.returncode is None
    and self._owns_process
    and self._host == host and self._port == port
    and self._spawned_policy_path == policy_path
)
```

🟢 复用判定把 `host/port/policy_path/returncode/owns` 全纳入匹配，避免误复用旧实例。
🟡 **仅比 policy_path，不比 policy 内容**：当运行时 policy 副本被改写（path 不变、内容变）时，`owned_match` 仍命中，box-server 不会重启、`EgressFilter` 不会重建。该 commit 后续才补了 `_policy_fingerprint`（见工作树 `:280-293`），本 commit 存在该缺口。考虑到 MEMORY.md 已记录“box-server root policy load-once，配置变更需重启 box-server”，此处对“副本改写”场景是真实风险。

**stop / 终止** `jiuwenbox_runner.py:541-570`

```python
proc.terminate()
try:
    await asyncio.wait_for(proc.wait(), timeout=60.0)
except asyncio.TimeoutError:
    proc.kill(); await proc.wait()
```

🟢 60s grace 给 uvicorn lifespan shutdown（`shutdown_all_sandboxes` 三段式 teardown）足够时间，并明确警告“SIGKILL 后 sandbox-daemon 孤儿可能残留”——对真实问题有清醒认知。
🟡 **Windows 上 `proc.terminate()` = `TerminateProcess` 即时强杀**（`app_agentserver.py:208-210` 注释自己也承认），不会给 uvicorn 跑 lifespan shutdown 的机会。也就是说 Windows 上“先 DELETE 再 stop”的精心排序在 internal 模式下意义有限：`runner.stop()` 一调 terminate 就强杀，lifespan 里的 `shutdown_all_sandboxes` 根本跑不到，DELETE 漏网的沙箱只能靠 daemon 自身超时或成孤儿。这是 Windows 桌面（本 commit 主战场）上的真实缺口。

**atexit 兜底** `jiuwenbox_runner.py:457-510`

🟢 跨平台分支正确：Windows 走 `proc.terminate()/kill()` 同步 API，Linux 走 `os.kill(SIGTERM)` + 探活 + SIGKILL。避免了“Windows 不识别 SIGTERM”的常见坑。
🟡 atexit 等待上限仅 3s（`:482`、`:498`），与 `stop()` 的 60s 不一致；atexit 路径（主进程异常退出）下若有活沙箱，3s 内 lifespan 跑不完 → 孤儿。属于已知的“兜底≠优雅”折中，可接受但建议在日志里更显著提示。

### 4.2 子进程输出捕获

**pump** `jiuwenbox_runner.py:405-427`

🟢 持续 drain stdout/stderr，防止管道堆积阻塞子进程；stderr 滚动尾部 80 行便于失败反查。
🔴 **`_pump_stream` 用 `logger.debug` 转发**（`:423`、`:427`），而 agent-server 默认 INFO 级别。这意味着 box-server 所有运行期日志（含 Windows 沙箱 ACL/spawn/runner 上报）在默认配置下**全被过滤**，沙箱内部失败无法定位。工作树后续 commit 已将其改为 `logger.info`（`:545`），证实这是本 commit 的真实痛点。对“relay-claw 排障”目标而言是阻断性缺陷。

### 4.3 端口 / 资源管理

**端口分配** `app_agentserver.py:104-138`

```python
sock.bind((host, preferred))
return preferred  # preferred 空闲
...
sock.bind((host, 0))
allocated = sock.getsockname()[1]
```

🟡 **TOCTOU race**：探测空闲到 uvicorn bind 之间窗口，端口可能被占。注释已承认（`:110-112`），属 best-effort，可接受。
🔴 **socket 资源泄漏路径**：`finally: sock.close()`（`:137-138`）看似兜底，但 `try` 块里 `return preferred`（`:119`）后 finally 会执行——OK；然而 `except OSError` 分支里又 `sock.close()` 再新建 socket（`:123-124`），若新建的 `socket.socket(...)` 自身成功但 `sock.bind((host,0))` 抛 `OSError`，则进入内层 `except`，此时第二个 sock **未 close**（外层 finally 只 close 名字 `sock`，已被重新绑定但内层异常路径里没 close）。实际上 Python 的 finally 会在异常沿调用栈传播前执行，且 `sock` 此时指向第二个 socket，所以 `finally` 会 close 它——**经复核此处不泄漏**。撤回该 🔴，降为 🟡：逻辑绕、可读性差，建议改写为先 close 再判断，或用 `with` 上下文。

**回写真实 url** `app_agentserver.py:238-242`

🟡 `set_local_config("JIUWENCLAW_SANDBOX_URL", actual_url)` 只写 tip/env，不落 config.yaml 持久层；进程重启后端口可能再变，回写值失效——但每次启动都重新分配，逻辑自洽，可接受。

### 4.4 配置加载与校验

**startup_mode 放开** `config.py`

🟢 `_VALID_SANDBOX_STARTUP_MODES = ("external","internal")`、默认 `internal`，与 develop（jiuwenswarm）一致；`_normalize_sandbox_startup_mode` 大小写/空格归一化处理得当。
🟡 **runner 内部对非法值静默回落**：`jiuwenbox_runner.py:244-246` `if normalized_mode not in ("internal","external"): normalized_mode = "internal"`。config 层已做校验会抛 `ValueError`，但 runner 自己又兜一层“静默改 internal”，吞掉非法输入。两处校验口径不一，建议 runner 至少 log warning 而非无声改值。

### 4.5 env 注入与安全

**透传 `os.environ`** `jiuwenbox_runner.py:327` `env = dict(os.environ)`

🔴 **凭据/敏感 env 全量透传给子进程**：agent-server 进程 env 里可能含 `JIUWENCLAW_*` 密钥、token、DB 口令等，原样灌进 box-server 子进程 env。box-server 再把 env 经 `_build_windows_exec_env` 衍生给沙箱子进程——虽然 `_build_windows_exec_env` 只构造 PATH，但 `RuntimeExecRequest.env` 沿用 `request.env`（来自上游 agent-core），box-server 自身 env 不直接进沙箱。然而 box-server 子进程 env 仍是攻击面：任何能读 `/proc/<pid>/environ` 或 box-server 自身 RCE 的人可拿到 agent-server 全部凭据。建议显式 allowlist 透传而非 `dict(os.environ)` 全量复制。

**`app_agentserver.py:207-216` 设 `os.environ`**

🔴 **进程全局可变状态污染**：`os.environ["JIUWENBOX_VENV_DIR"] = ...` 直接写主进程 env，而非只传给子进程 env。这会让 agent-server 自身后续所有子进程（含其他 spawn）都继承这两个变量；若 venv 路径含用户名/敏感目录，泄漏面扩大。应改为构造一份独立 env dict 传给 `ensure_running`，runner 已支持 `env` 透传——但当前 `ensure_running` 签名没有 env 参数，内部硬编码 `dict(os.environ)`。需联动改造。

**路径穿越 / 命令注入**

🟢 spawn 命令是固定列表 `[sys.executable, "-m", "uvicorn", "jiuwenbox.server.app:app", "--host", host, "--port", str(port)]`，不经 shell，`host/port` 来自配置/整数，注入面小。
🟡 `policy_path` 经 `resolve_policy_path` 解析为绝对路径后透传，未做“必须在 configs_dir 下”的收束校验（`:158` `.resolve()` 后只判 `is_file`）。但调用方只传固定文件名（`windows-policy.yaml`/`default-policy.yaml`），实际不可控，风险低。

### 4.6 并发与竞态

🟡 **单例 + 模块级 `asyncio.Lock`**（`jiuwenbox_runner.py:202`、`_INSTANCE`）。`asyncio.Lock` 与事件循环绑定——若 agent-server 在不同事件循环里调 `instance()`（如测试或未来多 loop 场景），`_lock` 会绑到首个 loop，跨 loop 使用抛 `RuntimeError`。当前单 loop 部署下无碍，但属于隐式耦合。
🟡 `_ensure_jiuwenbox_internal` 在 `_run` 启动链同步 await，与 `finally` 段的 `runner.stop()` 都在主 loop，串行无竞态。但若未来有“热重载配置触发重启 box-server”路径，需保证也走 `ensure_running` 的锁，否则 stop/spawn 交错可能损坏 `_process` 状态。

### 4.7 与 box-server 的交互

🟢 关停顺序“先 DELETE 远端沙箱 → 再 stop 子进程”（`app_agentserver.py:185-220`）逻辑正确：DELETE 需 box-server 活着响应。
🟢 `shutdown_jiuwenbox_sandboxes` 走 `asyncio.to_thread`（`:199`）避免同步 httpx 堵 loop。
🔴 但如 4.1 所述，Windows 上 `runner.stop()` 即时强杀，box-server lifespan shutdown 跑不到——DELETE 漏网的沙箱在 Windows 上无优雅清理路径。需在 Windows 上改用 `os.kill(pid, CTRL_BREAK_EVENT)` 或 job object 促成 graceful，或明确接受“Windows 不保证 graceful”并在 docs 标注。

---

## 五、优点

1. **注释质量极高**：每个设计决策都引用 docs 章节（§4.2/§4.3/§8.1 Q4）、解释 why，对后续维护极其友好。
2. **跨平台细节扎实**：Windows `TerminateProcess` vs Linux `SIGTERM`、`PR_SET_PDEATHSIG`、`os.kill(pid,0)` 探活，均正确处理。
3. **自包含**：runner 只依赖 stdlib + httpx，无 jiuwenswarm 自引用，移植边界清晰。
4. **失败不阻断主进程**：spawn 失败只 warning，agent-server 照常起；沙箱任务发起时 provider 自会报错。fail-soft 取舍合理。
5. **stderr 滚动缓冲**：便于子进程启动失败时反查 uvicorn 导入错误，实战价值高。
6. **policy 变更触发重启**：`_spawned_policy_path` 比对，避免老进程用旧 policy 服务新沙箱（与 MEMORY.md“load-once”认知一致）。

---

## 六、问题与风险

| 级别 | 位置 | 问题 |
|---|---|---|
| 🔴 | `jiuwenbox_runner.py:327` | `env = dict(os.environ)` 全量透传，凭据/敏感 env 灌进 box-server 子进程，扩大攻击面 |
| 🔴 | `app_agentserver.py:213,216` | 直接写 `os.environ` 污染主进程全局 env；应构造独立 env 传子进程 |
| 🔴 | `jiuwenbox_runner.py:423,427` | pump 用 `logger.debug`，默认 INFO 下 box-server 全部运行日志被过滤，排障阻断（后续 commit 已修） |
| 🔴 | `app_agentserver.py:208-210`/`jiuwenbox_runner.py:541-542` | Windows `proc.terminate()` 即时强杀，lifespan shutdown 跑不到，活沙箱成孤儿 |
| 🟡 | `jiuwenbox_runner.py:278-285` | 仅比 policy_path 不比内容，运行时副本改写不触发重启（后续 commit 已补指纹） |
| 🟡 | `app_agentserver.py:104-138` | 端口分配 TOCTOU + 可读性差（已承认 best-effort） |
| 🟡 | `jiuwenbox_runner.py:244-246` | 非法 startup_mode 静默改 internal，与 config 层 ValueError 口径不一 |
| 🟡 | `jiuwenbox_runner.py:202` | 模块级 `asyncio.Lock` 绑定首个 loop，多 loop 场景隐患 |
| 🟡 | `jiuwenbox_runner.py:482,498` | atexit 等待 3s 与 stop() 60s 不一致，异常退出下活沙箱孤儿 |
| 🟡 | `jiuwenbox_runner.py:158` | `policy_path` resolve 后未收束到 configs_dir，可控性低但实际风险小 |

---

## 七、改进建议

1. **env 透传收口（高优）**：`ensure_running` 增 `extra_env: dict | None` 参数；`app_agentserver.py` 把 `JIUWENBOX_VENV_DIR`/`BUNDLED_PYTHON` 放进独立 dict 传入，runner 合并时用 `env = {**allowlist_base, **extra_env}` 而非 `dict(os.environ)`。allowlist 显式列出 box-server 需要的（`PATH`/`SystemRoot`/`ProgramFiles`/`JIUWENBOX_*`/`PYTHONPATH`），屏蔽凭据。
2. **pump 日志级别（高优，后续已修）**：保持 `logger.info`，确保 box-server 运行期日志默认可见。
3. **Windows graceful stop（中优）**：Windows 下用 Job Object + `TerminateJobObject` 或 `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)` 促成 uvicorn lifespan；若不可行，则在 docs 明确“Windows internal 模式不保证 graceful，活沙箱可能需手动清”。
4. **policy 指纹（中优，后续已修）**：本 commit 应同步带上 `_policy_fingerprint`，覆盖运行时副本改写场景。
5. **端口分配改写（低优）**：用 `with contextlib.closing(socket.socket(...)) as sock:` 包裹，先 close 再 bind(0)，提升可读性、消除绕弯。
6. **非法 startup_mode（低优）**：runner 内对非法值 `logger.warning` 后再回落，与 config 层口径对齐。
7. **atexit 等待一致性（低优）**：atexit 至少给到 10-15s（兼顾单个沙箱 teardown），或在日志显著标注“可能遗留孤儿”。
8. **单测**：补 `JiuwenBoxRunner` 的 owned_match 决策矩阵、stop 超时、atexit 路径单测（mock subprocess + health）。

---

## 八、小结

本 commit 是 relay-claw 接入的关键一步，架构方向正确（internal spawn + HTTP 复用既有 provider 链路），跨平台处理与注释质量值得称道。但存在 **4 处 🔴**：env 全量透传的凭据泄漏面、`os.environ` 全局污染、pump 日志级别导致排障阻断、Windows 强杀致活沙箱孤儿。其中后两项已被后续 commit 修复，前两项仍待收口。建议合并前至少处理 env 透传收口与 pump 日志级别；Windows graceful stop 可作为后续 issue 跟踪。
