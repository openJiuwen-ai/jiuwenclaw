# 代码审查报告

- **Commit**：`21024d62531a8d403a8597fa60ccc52233b34f02`
- **信息**：fix:增加调试日志（作者 lby，2026-07-24）
- **体量**：约 57 增 4 删，3 文件
- **分支**：enterprise_dev_windowbox（父 commit：`f52aa505` fix:接入relay-claw）
- **审查员视角**：Windows 沙箱功能适配链路
- **审查日期**：2026-08-01

---

## 概述

本 commit 在 Windows 沙箱链路三处补日志：

1. `jiuwenbox/src/jiuwenbox/server/runtime/process.py`（+14）：沙箱创建期 ACL 施加后、两跳 runner 拉起后各加一条 `logger.info`。
2. `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py`（+5）：`exec_in_sandbox` 在 Windows 路径补 PATH 后加一条 `logger.debug`。
3. `jiuwenclaw/app_agentserver.py`（+42）：`_ensure_jiuwenbox_internal` 启动分支的 6 处早退/落点补 `logger.info`，`_run` 关停的两步各补前后日志，并改一处 `warning` 文案前缀。

总体定位：**正式可观测性增强，而非临时调试遗留**。日志语句无 `print`、无脏调试语句，行宽与既有风格一致，路径/凭据/token 泄露风险低，并保留了 `if` 早退等控制流不变。但仍有几处级别选择（`info` vs `debug`）与热路径敏感度问题，建议按下文收敛。

---

## 变更范围

| 文件 | 增 | 删 | 性质 |
|---|---|---|---|
| `jiuwenbox/src/jiuwenbox/server/runtime/process.py` | +14 | 0 | 仅日志，加在 `_create_windows` 内（ACL 后 / two-hop spawn 后） |
| `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py` | +5 | 0 | 仅日志，加在 `exec_in_sandbox` 的 Windows 分支 |
| `jiuwenclaw/app_agentserver.py` | +42 | 4 | 6 条 `info` + 关停两步前后日志；夹带 1 处 `warning` 文案前缀统一、1 处 `if` 嵌套层级调整（详见下文） |

---

## 改动逐项分析

### 1. `jiuwenbox/src/jiuwenbox/server/runtime/process.py`

#### 🟢 process.py:2818-2826（ACL applied info）

```python
logger.info(
    "[SandboxWin] %s ACL applied: workspace=%s, allow_read=%s, allow_write=%s "
    "(bundled_python=%s, venv=%s)",
    sandbox_id, workspace,
    policy.windows.filesystem.allow_read or [],
    allow_write_paths,
    bundled_python or "<未注入>",
    venv_dir or "<未注入>",
)
```

- **合理性**：放在 `apply_sandbox_acl` 之后，把"ACL 施加后的真实路径集合"落盘，对排查 `Permission denied` 至关重要（注释 2869-2873 行反复强调 workspace 不存在 → ACL 跳过 → 受限 token 写不了的高频故障）。一次性事件，每沙箱一条，**非热路径**。
- **级别**：`info` 偏高频，但沙箱创建是低频语义，可接受；若想更克制可改 `debug`。
- **泄露**：`workspace` 是用户 home 下路径（`~/.office-claw/.jiuwenclaw/jiuwenbox/<id>`），含用户名；非凭据/token。`bundled_python`/`venv_dir` 同理。**无敏感凭据**，但应确认日志 sink（控制台/文件）的访问控制匹配用户隐私要求。

#### 🟢 process.py:2847-2852（runner spawned info）

```python
logger.info(
    "[SandboxWin] %s runner spawned (two-hop): pid=%s, workspace=%s, "
    "proxy_port=%s-%s, state=SUSPENDED",
    sandbox_id, runner_pid, workspace, proxy_start, proxy_end,
)
```

- **合理性**：two-hop spawn 是 Windows 沙箱最关键步骤，CREATED_SUSPENDED 状态一旦出错（卡住未 ResumeThread / 第一跳失败），后续 IPC 全断。落 `pid` + `proxy_port` 范围对定位"sandbox 挂起"类故障极有用。
- **级别**：`info` 合理，低频。
- **泄露**：`pid`、`proxy_port` 均非敏感。

#### 🟡 process.py:2963-2967 / 3002-3005（既有日志，非本次新增）

代码里已存在两条类似的 `logger.info`（`windows toolpaths injected`、`sandbox-writable temp injected`），本次未改，说明 **logger 风格早已稳定在 info**，本次新增与之对齐。可作为"不该把本次新增长期留在 info"的反向佐证——但既然历史如此，本次一致即可，不必强改。

### 2. `jiuwenbox/src/jiuwenbox/server/sandbox_manager.py`

#### 🟡 sandbox_manager.py:786-790（exec 路径 debug）

```python
logger.debug(
    "[SandboxWin] exec sandbox=%s cmd=%s workdir=%s PATH=%s",
    sandbox_id, request.command, request.workdir,
    exec_env.get("PATH", ""),
)
```

- **合理性**：唯一一处用 `debug` 而非 `info`，这是**正确选择**——`exec_in_sandbox` 是热路径，每条用户命令都会走，必须避免 info 级刷屏。
- **泄露**：`request.command` 是用户命令行（list[str]），**可能含业务参数 / 偶尔含敏感数据**（用户传给 agent 的 prompt token、API key、文件内容 cat）。注意 sandbox_manager.py 上方注释（772-775）已说明 audit 行本就覆盖 command/workdir + stdout/stderr tail，故 debug 日志的"重复"是有意的——为运行时排查 PATH 解析失败（bash 裸名 0xC0000142）提供线索。
- **风险**：若 `LOG_LEVEL=DEBUG` 被生产打开，命令行会进文件日志。**建议在审计下沉用 `repr` 截断**或在文档明示 DEBUG 级别不用于生产。

### 3. `jiuwenclaw/app_agentserver.py`

本文件 +42 行是体量最大的一处，逐条看是否"仅日志"还是夹带逻辑改动。

#### 🟢 app_agentserver.py:168-172（external 早退 info）

```python
if (endpoint.get("startup_mode") or "internal") != "internal":
    logger.info(
        "[AgentServer][sandbox] startup_mode=external, skip jiuwenbox spawn "
        "(box-server 由外部托管)"
    )
    return
```

- 纯日志，早退语义不变。**对部署排障有用**——external 模式下 agent-server 不拉子进程，但用户常误以为"沙箱没启动=bug"，这条日志明确"由外部托管，故不 spawn"。

#### 🟢 app_agentserver.py:180-184（sandbox 未启用 info）

```python
if not bool(runtime.get("enabled")):
    logger.info(
        "[AgentServer][sandbox] startup_mode=internal but sandbox not enabled "
        "(JIUWENCLAW_SANDBOX_ENABLED!=1), skip jiuwenbox spawn"
    )
    return
```

- 同上，纯日志。日志里直接点名 env 变量名 `JIUWENCLAW_SANDBOX_ENABLED`，对运维**极友好**。

#### 🟢 app_agentserver.py:206-211（spawn 前 platform/host/port/policy 落点）

```python
logger.info(
    "[AgentServer][sandbox] platform=%s, spawn box-server at %s:%d, policy=%s (%s)",
    sys.platform, host, preferred_port, policy_filename,
    policy_path or "<内置默认, 未在仓库/site-packages 找到>",
)
```

- 这条是本次最有价值的可观测性增强：一次性把 `平台/host/port/policy 文件名/policy 真实路径或回落提示` 集中输出，定位"policy 找不到 → 走内置默认"与"端口换随机"链路时省去多次附加上下文。
- **泄露**：`policy_path` 可能含用户名；无凭据。

#### 🟢 app_agentserver.py:229-233（env 注入 info）

```python
logger.info(
    "[AgentServer][sandbox] injected env: JIUWENBOX_VENV_DIR=%s, "
    "JIUWENBOX_BUNDLED_PYTHON=%s",
    venv_dir, bundled_python.parent,
)
```

- 纯日志，对 docs §4.3 描述的"动态路径注入"链路有用。
- **泄露**：venv / python 目录含用户名；无敏感凭据。

#### 🟢 app_agentserver.py:236-238（spawning box-server）

```python
logger.info(
    "[AgentServer][sandbox] spawning box-server (startup_mode=internal)..."
)
```

- 标记进入 `ensure_running` 调用前。UAC 弹窗可能阻塞数十秒（见 process.py:2847-2852 注释），有这条日志能区分"卡在 ensure_running"与"卡在更早的分支"。合理。

#### 🟢 app_agentserver.py:251-255（spawn failed warning 文案改）

```python
-            "[AgentServer] jiuwenbox internal spawn failed (%s:%d)%s",
+            "[AgentServer][sandbox] jiuwenbox internal spawn failed (%s:%d)%s",
```

- 仅前缀 `[AgentServer]` → `[AgentServer][sandbox]`，与本文件其他新增日志统一命名空间。**非夹带逻辑**。

#### 🟡 app_agentserver.py:260-265（box-server ready info + if 层级调整）

```python
    actual_url = runner.base_url
    if actual_url and actual_url != url:
        set_local_config("JIUWENCLAW_SANDBOX_URL", actual_url)
-    logger.info("[AgentServer] jiuwenbox internal ready, sandbox url=%s", actual_url)
+    logger.info(
+        "[AgentServer][sandbox] box-server ready at %s (url %s config), "
+        "sandbox_id 按需 lazy 创建",
+        actual_url, "回写" if actual_url != url else "沿用",
+    )
```

- **这是唯一一处看起来像"夹带逻辑"的地方**，但仔细比对 diff：
  - 原代码：`logger.info` 在 `if` 块内（只有 url 被回写时才打日志）。
  - 新代码：`logger.info` 在 `if` 块外（无论是否回写都打日志）。
  - 这改了**日志触达条件**，但未改 `set_local_config` 的调用条件（仍在 `if` 内）。属于"日志覆盖范围扩大"的可观测性改进，**非逻辑变更**——`set_local_config` 的语义完全不变。但 review 时**容易误判**，建议在 commit message 或注释里点一句"将 ready 日志移出 if 块以覆盖 url 沿用情况"。
- `"回写" if actual_url != url else "沿用"` 用三元表达式做条件文案，简洁。

#### 🟢 app_agentserver.py:198-200（step 1 DELETE 前后）

```python
logger.info("[AgentServer][sandbox] step 1: DELETE 远端沙箱 (box-server 活着)")
released = await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
logger.info("[AgentServer][sandbox] step 1 done: released=%s", released)
```

- 加了 `released =` 接收返回值（原本是 `await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)` 直接丢弃返回值），然后用日志打出来。`shutdown_jiuwenbox_sandboxes` 本就 `-> int`（sandbox_lifecycle.py:52），返回值本来就存在，**这次只是把它接住并落盘**，未改函数行为。属于"充分利用既有返回值"——合理。
- step 1/step 2 的前后日志在关停故障排查时极有用（区分"DELETE 卡住"vs"runner.stop() 卡住"vs"session_history.shutdown 卡住"）。

#### 🟢 app_agentserver.py:216-221（step 2 stop 前 owned info）

```python
runner = JiuwenBoxRunner.instance()
owned = runner.get_owned_endpoint()
logger.info(
    "[AgentServer][sandbox] step 2: stop box-server 子进程 (owned=%s)",
    owned,
)
await runner.stop()
```

- 新增 `owned = runner.get_owned_endpoint()` 调用并落盘。`get_owned_endpoint` 是只读 getter（jiuwenbox_runner.py:270-277，只读 `self._process` / `self._owns_process`），**无副作用**。这一步在 external 模式（未 own 子进程）会输出 `owned=None`，正好用日志区分"是 external 模式 no-op"还是"我 own 的子进程被停了"。合理。

#### 🟢 app_agentserver.py:221（step 2 done）

```python
logger.info("[AgentServer][sandbox] step 2 done")
```

- 标记 runner.stop() 已返回。配合 step 2 前的日志能算 `runner.stop()` 耗时（结合 atexit 的 `_set_exit_reason`）。合理。

---

## 优点

1. **未夹带逻辑改动**：app_agentserver.py 的 42 行虽大，但每处都能对应到"补一条 info 标记分支落点 / 利用既有返回值打日志 / 统一日志前缀"，没有借机改控制流或语义（除 ready 日志移出 `if` 块外属"日志覆盖范围扩大"，非业务逻辑变更）。
2. **日志级别大体克制**：唯一的热路径 `exec_in_sandbox` 用 `debug` 而非 `info`，避免每条命令刷屏，是正确的级别判断。
3. **运维友好**：多处日志直接点名 env 变量名（`JIUWENCLAW_SANDBOX_ENABLED`）、配置项名（`startup_mode`）、policy 文件名（`windows-policy.yaml`），并标注"由外部托管""按需 lazy 创建"等意图说明，远胜纯路径输出。
4. **关停链路覆盖**：step 1/step 2 的前后日志把"先 DELETE 远端沙箱、再停 box-server 子进程"的顺序约束（注释 185-192 行强调的）以日志形式显式化，对 Windows 上 `TerminateProcess` 强杀导致孤儿沙箱的场景（注释 208-210 行）排查极有用。
5. **未泄露凭据/token**：全程无 password / token / API key 落盘。`get_sandbox_user_password()`（process.py:2831）虽在新增日志附近，但日志只打 `pid`/`workspace`/`proxy_port`，未碰 password。

---

## 问题与风险

### 🟡 P2 — `exec_in_sandbox` debug 日志含命令行，DEBUG 开启时有泄露面

**位置**：`sandbox_manager.py:786-790`

`request.command` 是用户传给沙箱的命令行 `list[str]`，可能含 prompt 内容、文件路径、偶发 API key。本 commit 选 `debug` 级别是对的，但：
- `app_agentserver.py:57-58` 显示启动时 `LogManager.get_all_loggers().values()` 全部 `set_level(logging.INFO)`；但 `utils.py:463` 显示 `LOG_LEVEL` 环境变量可覆盖控制台级别。运维一旦把 `LOG_LEVEL=DEBUG` 打开，命令行会进控制台/文件日志。
- 上方 audit 行（802-815）本就记录 command + stdout/stderr tail，debug 日志与之**信息重复**——但 debug 的意义在 PATH 解析失败时回看 env，可保留 PATH 但截断 command。

**建议**：保留 `PATH` 字段（排查重点），`command` 用 `_truncate_for_audit` 同款截断，或仅打 `command[0]`（裸名解析才是 PATH 失败的根因）。

### 🟡 P3 — process.py ACL applied 日志用 `info` 而非 `debug`，与 sandbox_manager.py 不一致

**位置**：`process.py:2818-2826`

`_create_windows` 内已有 4 条 info（2963、3002、本次 2818、2847），每次创建沙箱刷 4 条 info。沙箱创建虽非热路径，但在频繁创建/销毁的会话场景（按需 lazy 创建）下仍偏多。`sandbox_manager.py` 的 exec 路径已选 debug，建议 process.py 的 ACL/toolpath/temp 这类"创建期一次性诊断"也下放到 debug，仅在 spawn 失败时升 info/warning。

### 🟡 P3 — ready 日志移出 `if` 块的语义改动未在注释点明

**位置**：`app_agentserver.py:260-265`

原代码 ready 日志只在 url 回写分支内，新代码移到 `if` 外覆盖"沿用"情况。这是合理改进，但 diff 形态上"删一条 + 加一条 + 改文案"，review 时容易误以为是逻辑变更。建议加一行注释：`# ready 日志移出 if: external 沿用 url 时也要落 ready 标记`。

### 🟢 P4 — 日志含用户名路径，需评估 sink 访问控制

`workspace`、`venv_dir`、`bundled_python.parent`、`policy_path` 均含用户 home 路径（含用户名 `liubuyu` / `jbx-sandbox`）。非凭据，但属个人可识别信息。若日志文件被采集到集中式日志系统（ELK 等），需确认访问控制。**当前 commit 无问题**，仅作合规提示。

---

## 改进建议

1. **P2 截断命令行**：`sandbox_manager.py:786-790` 的 `request.command` 用 `_truncate_for_audit` 或仅打 `command[0]`，避免 DEBUG 级别下命令行参数全量落盘。
2. **P3 统一创建期日志级别**：`process.py` 的 ACL applied / toolpaths injected / temp injected 三条考虑下放到 `debug`，仅保留 `runner spawned (state=SUSPENDED)` 这条关键里程碑在 info，减少单次创建的 info 噪音。
3. **P3 注释点明 ready 日志移出 if 的意图**：`app_agentserver.py:260` 上一行加注释，避免后续 review 误判。
4. **P4 评估日志 sink 访问控制**：在 `docs/window沙箱.md` 或运维手册里注明 info 日志含用户名路径，需匹配日志文件 ACL。
5. **可选 - 统一日志前缀**：`[SandboxWin]`（process.py / sandbox_manager.py）vs `[AgentServer][sandbox]`（app_agentserver.py）两种前缀并存。本次未引入新风格，但若后续要做日志聚合查询，建议统一为 `[sandbox]` 子命名空间 + 模块后缀。

---

## 小结

本 commit 是一次**质量合格的可观测性增强**，不是临时调试遗留：日志语句规范（无 print、无 TODO、无调试桩），级别选择基本合理（热路径用 debug），未借机夹带逻辑变更（除 ready 日志移出 if 块属覆盖范围扩大，语义不变）。最值得关注的两点：

1. **`exec_in_sandbox` 的 debug 日志含完整命令行**，在 `LOG_LEVEL=DEBUG` 打开时有泄露面，建议截断。
2. **`process.py` 创建期 4 条 info 偏密**，与 `sandbox_manager.py` 的 debug 风格不一致，建议把诊断性日志下放到 debug。

其余均为风格与文档层面的轻微改进建议，不阻断合入。
